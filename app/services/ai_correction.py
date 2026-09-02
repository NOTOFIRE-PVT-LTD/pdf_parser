"""AI agent — fix product names via Google Gemini."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.schemas import ProductItem
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


_FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
)


def _normalize_model(model: str) -> str:
    m = model.strip()
    if m.startswith("models/"):
        m = m[7:]
    return m


def _models_to_try(settings: Settings) -> list[str]:
    primary = _normalize_model(settings.gemini_model)
    out: list[str] = []
    for m in [primary, *_FALLBACK_MODELS]:
        if m and m not in out:
            out.append(m)
    return out


def _call_gemini_once(settings: Settings, prompt: str, model: str) -> str:
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not set in .env")
    model = _normalize_model(model)
    base = settings.gemini_base_url.rstrip("/")
    url = f"{base}/models/{urllib.parse.quote(model)}:generateContent?key={settings.gemini_api_key}"
    body = json.dumps({
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise ValueError("Gemini returned empty content")
    return parts[0].get("text") or ""


def _call_gemini(settings: Settings, prompt: str) -> str:
    last_error: Exception | None = None
    for model in _models_to_try(settings):
        try:
            return _call_gemini_once(settings, prompt, model)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                logger.warning("Gemini model unavailable: %s — trying next", model)
                continue
            raise
    if last_error:
        raise last_error
    raise ValueError("No Gemini model available")


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
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        logger.warning("Gemini HTTP error: %s %s", exc.code, detail)
        return AiFixResult(0, 0, f"Gemini API error ({exc.code}). Check GEMINI_API_KEY and GEMINI_MODEL.", error=detail)
    except urllib.error.URLError as exc:
        logger.warning("Gemini unreachable: %s", exc)
        return AiFixResult(0, 0, "Gemini API not reachable. Check internet connection.", error=str(exc))
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
