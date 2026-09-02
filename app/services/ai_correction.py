"""AI agent — fix product names via Google Gemini."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.schemas import ProductItem
from app.services.ai.gemini_client import generate_with_fallback
from app.services.name_learning import get_name_learning
from app.services.product_sanitize import _renumber_sequential
from app.utils.product_name import recheck_product_name

logger = logging.getLogger(__name__)

_SYSTEM = """You fix tender BOQ extraction rows. Return ONLY valid JSON — no markdown.

Rules:
- product_name MUST use words from that row's description only — never invent text
- Remove scope phrases: supply, installation, testing, commissioning, supervision, etc.
- Keep only the actual product/item name
- Set remove=true only for non-product junk (clauses, headings, empty rows)
- Follow the user's instruction

Output format:
{"changes":[{"index":0,"product_name":"...","remove":false},...],"summary":"one line"}
Include only rows that need a change."""


@dataclass
class AiFixResult:
    updated: int
    removed: int
    summary: str
    error: str | None = None


def _compact_rows(products: list[ProductItem]) -> list[dict]:
    rows = []
    for i, p in enumerate(products):
        rows.append({
            "index": i,
            "s_no": p.s_no,
            "product_name": p.product_name,
            "description": (p.description or "")[:500],
            "qty": p.item_qty,
            "amount": p.amount,
        })
    return rows


def _validate_name(desc: str, name: str) -> bool:
    if not name or not desc or len(name.strip()) < 2:
        return False
    nl, dl = name.lower(), desc.lower()
    if nl in dl:
        return True
    words = [w for w in re.findall(r"\w+", nl) if len(w) > 2]
    if not words:
        return False
    hits = sum(1 for w in words if w in dl)
    return hits / len(words) >= 0.55


def _parse_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


_MODEL_CACHE: list[str] = []


def _call_gemini(settings: Settings, prompt: str) -> str:
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not set in .env")
    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    return generate_with_fallback(
        settings.gemini_api_key,
        settings.gemini_model,
        payload,
        base_url=settings.gemini_base_url,
        timeout=75,
        model_cache=_MODEL_CACHE,
    )


def apply_ai_fix(
    products: list[ProductItem],
    instruction: str,
    settings: Settings | None = None,
) -> AiFixResult:
    settings = settings or get_settings()
    instruction = (instruction or "").strip()
    if not instruction:
        return AiFixResult(0, 0, "No instruction given.", error="empty")
    if not products:
        return AiFixResult(0, 0, "No products to fix.", error="empty")
    if not settings.gemini_api_key:
        return AiFixResult(
            0, 0,
            "Set GEMINI_API_KEY in .env file",
            error="no_key",
        )

    prompt = (
        f"User instruction:\n{instruction}\n\n"
        f"Rows:\n{json.dumps(_compact_rows(products), ensure_ascii=False, indent=2)}"
    )

    try:
        raw = _call_gemini(settings, prompt)
        parsed = _parse_response(raw)
    except RuntimeError as exc:
        logger.warning("Gemini error: %s", exc)
        return AiFixResult(0, 0, str(exc), error=str(exc))
    except urllib.error.HTTPError as exc:
        detail = getattr(exc, "detail_text", None) or exc.read().decode("utf-8", errors="replace")[:200]
        logger.warning("Gemini HTTP error: %s %s", exc.code, detail)
        return AiFixResult(0, 0, f"Gemini API error ({exc.code}). Try again in a minute.", error=detail)
    except urllib.error.URLError as exc:
        logger.warning("Gemini unreachable: %s", exc)
        return AiFixResult(0, 0, "Gemini API not reachable. Check internet connection.", error=str(exc))
    except (TimeoutError, OSError) as exc:
        logger.warning("Gemini timed out: %s", exc)
        return AiFixResult(
            0, 0,
            "Gemini did not respond in time. Try again, or set a faster "
            "GEMINI_MODEL (e.g. gemini-3-flash-preview) in .env.",
            error=str(exc),
        )
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
        logger.warning("Gemini response parse failed: %s", exc)
        return AiFixResult(0, 0, f"Could not parse Gemini response: {exc}", error=str(exc))

    changes = parsed.get("changes") or []
    summary = str(parsed.get("summary") or "Done.")
    learning = get_name_learning()
    updated = removed = 0
    remove_indices: set[int] = set()

    for ch in changes:
        if not isinstance(ch, dict):
            continue
        idx = ch.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(products):
            continue
        if ch.get("remove"):
            remove_indices.add(idx)
            continue
        new_name = str(ch.get("product_name") or "").strip()
        desc = products[idx].description or ""
        checked = recheck_product_name(desc, new_name, extra_verbs=learning.extra_verbs)
        if not checked or not _validate_name(desc, checked):
            continue
        products[idx].product_name = checked
        learning.learn(desc, checked)
        updated += 1

    if remove_indices:
        products[:] = [p for i, p in enumerate(products) if i not in remove_indices]
        removed = len(remove_indices)

    _renumber_sequential(products)
    return AiFixResult(updated, removed, summary)
