"""
AI-first PDF tender extraction.

Each PDF layout differs (GeM / IREPS / custom). The model reads the document
text, follows saved USER INSTRUCTIONS, and returns products + header fields.
No portal-specific hardcoding — rules extractors are only a fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import Settings, get_settings
from app.models.schemas import ProductItem, TenderInformation
from app.parser.table_parser import ExtractedTable
from app.services.ai.agent_graph import _call_llm, _parse_json
from app.services.ai.instruction_memory import format_for_prompt
from app.services.name_learning import get_name_learning
from app.utils.product_name import normalize_product_description
from app.utils.text_utils import collapse_whitespace, truncate

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 14000
_MAX_CHUNKS = 8
_MAX_PRODUCTS_PER_CHUNK = 80

_EXTRACT_SYSTEM = """You analyze ONE tender/bid/NIT PDF excerpt and extract structured data.
Layouts vary (GeM, IREPS, custom BOQ, scanned OCR) — discover the structure yourself.
Do NOT invent rates, quantities, names, or codes. If a field is missing, use null.

Follow SAVED USER INSTRUCTIONS strictly (naming style, what is a product vs work, exclusions).

Return ONLY valid JSON:
{
  "tender": {
    "tender_no": null,
    "name_of_work": null,
    "zone": null,
    "railway": null,
    "division_name": null,
    "advertised_value": null,
    "earnest_money": null,
    "period_of_completion": null,
    "closing_date_time": null,
    "published_date": null,
    "bidding_type": null,
    "tender_type": null,
    "contract_type": null,
    "bid_validity_days": null,
    "status": null,
    "reference_no": null
  },
  "products": [
    {
      "s_no": "1",
      "item_code": null,
      "product_name": "short name from description only",
      "description": "full item text from PDF",
      "item_qty": null,
      "qty_unit": null,
      "unit_rate": null,
      "basic_value": null,
      "amount": null,
      "bidding_unit": null,
      "schedule": null
    }
  ]
}

Rules:
- products = real supplyable / catalogue / BOQ line items only (per user instructions).
- Skip legal clauses, eligibility text, declarations, page headers.
- product_name must use words that appear in that item's description.
- Keep description as clean single-line text from the PDF.
- Extract EVERY item visible in THIS excerpt (do not stop early).
"""


def ai_available(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not getattr(settings, "ai_enabled", True):
        return False
    if not getattr(settings, "ai_extract_enabled", True):
        return False
    return bool(settings.mistral_api_key or settings.gemini_api_key)


def _tables_digest(tables: list[ExtractedTable] | None, *, limit: int = 6) -> str:
    if not tables:
        return ""
    parts: list[str] = []
    for t in tables[:limit]:
        if not (t.is_product_table or t.mapped_headers):
            continue
        headers = ", ".join(t.headers[:12])
        rows_preview = []
        for row in t.rows[:12]:
            cells = [collapse_whitespace(str(c or "")) or "" for c in row[:10]]
            if any(cells):
                rows_preview.append(" | ".join(cells))
        if not rows_preview:
            continue
        parts.append(
            f"TABLE page={t.page_number} headers=[{headers}]\n"
            + "\n".join(rows_preview)
        )
    return "\n\n".join(parts)


def _chunk_text(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= _CHUNK_CHARS:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < _MAX_CHUNKS:
        end = min(len(text), start + _CHUNK_CHARS)
        # Prefer break near a page marker / schedule boundary
        window = text[start:end]
        cut = max(
            window.rfind("[[PAGE:"),
            window.rfind(" Schedule "),
            window.rfind(" Description"),
            window.rfind(". "),
        )
        if cut > _CHUNK_CHARS * 0.55:
            end = start + cut
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


def _system_prompt() -> str:
    base = _EXTRACT_SYSTEM
    learned = format_for_prompt(max_items=30)
    learning = get_name_learning()
    extra_bits: list[str] = []
    if learned:
        extra_bits.append(learned)
    # Compact name-learning hints (user corrections)
    try:
        exact = list((learning.exact or {}).items())[-15:]
        if exact:
            lines = [f"- {k[:80]} → {v}" for k, v in exact if k and v]
            if lines:
                extra_bits.append(
                    "LEARNED PRODUCT NAME EXAMPLES (from user corrections):\n"
                    + "\n".join(lines)
                )
    except Exception:  # noqa: BLE001
        pass
    if not extra_bits:
        return base
    return base + "\n\n" + "\n\n".join(extra_bits)


def _row_to_product(raw: dict[str, Any]) -> ProductItem | None:
    if not isinstance(raw, dict):
        return None
    desc = normalize_product_description(
        str(raw.get("description") or raw.get("itemDescription") or "")
    )
    name = collapse_whitespace(str(raw.get("product_name") or raw.get("productName") or ""))
    if name:
        name = truncate(name, 200)
    if not desc and name:
        desc = name
    if not desc or len(desc) < 4:
        return None
    sno = str(raw.get("s_no") or raw.get("itemSerialNo") or "").strip() or None
    if sno and not re.fullmatch(r"\d+", sno):
        sno = None

    def _s(key: str, *alts: str) -> str | None:
        for k in (key, *alts):
            v = raw.get(k)
            if v is None:
                continue
            t = collapse_whitespace(str(v))
            if t and t.lower() not in {"null", "none", "n/a"}:
                return t
        return None

    return ProductItem(
        s_no=sno,
        item_code=_s("item_code", "itemCode"),
        product_name=name or None,
        description=truncate(desc, 8000),
        item_qty=_s("item_qty", "itemQty", "qty"),
        qty_unit=_s("qty_unit", "itemUnit", "unit"),
        unit_rate=_s("unit_rate", "itemUnitRate", "rate"),
        basic_value=_s("basic_value", "itemBasicValue"),
        amount=_s("amount", "itemAmount", "itemTotal"),
        bidding_unit=_s("bidding_unit", "biddingUnit"),
        schedule=_s("schedule", "itemCategory"),
    )


def _merge_tender(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (extra or {}).items():
        if v is None:
            continue
        s = collapse_whitespace(str(v))
        if not s or s.lower() in {"null", "none"}:
            continue
        cur = out.get(k)
        if not cur or (isinstance(cur, str) and len(s) > len(str(cur))):
            out[k] = s
    return out


def _dedupe_products(items: list[ProductItem]) -> list[ProductItem]:
    seen: set[str] = set()
    out: list[ProductItem] = []
    for p in items:
        key = re.sub(
            r"\s+",
            " ",
            f"{p.s_no}|{p.item_code}|{(p.description or '')[:120]}".lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    # Fill missing serials in order
    for i, p in enumerate(out, start=1):
        if not p.s_no:
            p.s_no = str(i)
    return out


def _extract_chunk(
    settings: Settings,
    *,
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    tables_digest: str,
    want_tender: bool,
) -> tuple[dict[str, Any], list[ProductItem]]:
    user_parts = [
        f"Excerpt {chunk_index + 1}/{total_chunks} of the PDF text:",
        chunk[:_CHUNK_CHARS],
    ]
    if chunk_index == 0 and tables_digest:
        user_parts.append("Sample product-like tables from the PDF:")
        user_parts.append(tables_digest[:6000])
    if not want_tender:
        user_parts.append(
            "Focus on products only; set tender fields to null unless clearly visible."
        )
    user_parts.append(
        f"Return at most {_MAX_PRODUCTS_PER_CHUNK} products from THIS excerpt."
    )

    raw, _provider = _call_llm(
        settings,
        [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        json_mode=True,
        temperature=0.0,
    )
    parsed = _parse_json(raw)
    tender = parsed.get("tender") if isinstance(parsed.get("tender"), dict) else {}
    products_raw = parsed.get("products") if isinstance(parsed.get("products"), list) else []
    products: list[ProductItem] = []
    for row in products_raw[:_MAX_PRODUCTS_PER_CHUNK]:
        item = _row_to_product(row)
        if item:
            products.append(item)
    return tender, products


def extract_with_ai(
    text: str,
    tables: list[ExtractedTable] | None = None,
    settings: Settings | None = None,
) -> tuple[TenderInformation | None, list[ProductItem], str | None]:
    """
    Returns (tender_info_or_None, products, error).

    error is set when AI is unavailable or completely fails.
    """
    settings = settings or get_settings()
    if not ai_available(settings):
        return None, [], "ai_unavailable"

    chunks = _chunk_text(text)
    if not chunks:
        return None, [], "empty_text"

    tables_digest = _tables_digest(tables)
    tender_acc: dict[str, Any] = {}
    products: list[ProductItem] = []
    last_err: str | None = None

    for i, chunk in enumerate(chunks):
        try:
            tender, chunk_products = _extract_chunk(
                settings,
                chunk=chunk,
                chunk_index=i,
                total_chunks=len(chunks),
                tables_digest=tables_digest if i == 0 else "",
                want_tender=(i == 0),
            )
            if i == 0:
                tender_acc = _merge_tender(tender_acc, tender)
            else:
                tender_acc = _merge_tender(tender_acc, tender)
            products.extend(chunk_products)
            logger.info(
                "AI extract chunk %s/%s → %s products",
                i + 1,
                len(chunks),
                len(chunk_products),
            )
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("AI extract chunk %s failed: %s", i + 1, exc)
            continue

    products = _dedupe_products(products)
    info = None
    if tender_acc:
        allowed = set(TenderInformation.model_fields)
        info = TenderInformation(
            **{k: tender_acc.get(k) for k in allowed if tender_acc.get(k)}
        )
    if not products and last_err:
        return info, [], last_err
    return info, products, None
