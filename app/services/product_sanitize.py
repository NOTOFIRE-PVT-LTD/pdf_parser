"""Post-process extracted product rows — drop junk, merge duplicates."""

from __future__ import annotations

import re
from typing import Any

from app.extractor.product_extractor import POINTER_TEXT, ProductExtractor
from app.models.schemas import ProductItem
from app.services.name_learning import get_name_learning
from app.utils.product_name import (
    extract_product_name,
    is_clause_or_junk,
    normalize_product_description,
    recheck_product_name,
)

_JUNK_DESC = re.compile(
    r"(?i)^(s\.?no\.?|item\s*code|item\s*qty|description\s*:?-?|"
    r"i\s*/\s*we\s+the|a\)\s*technical|b\)\s*financial|c\)\s*avail|"
    r"\(a\)|\(b\)|\(c\)|technical|financial|availability|"
    r"schedule\s*total|grand\s*total|meaning\s+of\s+similar)\b"
)


def _clean_description(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"[\r\n\t]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?i)^description\s*[:\-–]\s*", "", text).strip()
    text = re.sub(r"(?i)^item\s*(?:details?|description|title)\s*[:\-–]\s*", "", text).strip()
    return text.lstrip("-–— ").strip() or None


def _is_valid_row(data: dict[str, Any], desc: str | None) -> bool:
    qty = str(data.get("item_qty") or "").strip()
    rate = str(data.get("unit_rate") or "").strip()
    amount = str(data.get("amount") or "").strip()
    sno = str(data.get("s_no") or "").strip()
    has_qty = bool(re.search(r"\d", qty))
    has_money = bool(re.search(r"\d", rate) or re.search(r"\d", amount))
    has_desc = bool(desc and len(desc) > 3)
    has_sno = bool(sno and re.fullmatch(r"\d+", sno))
    schedule = str(data.get("schedule") or "")

    if desc and (_JUNK_DESC.search(desc) or is_clause_or_junk(desc)):
        return False
    item_code = str(data.get("item_code") or "")
    if item_code and POINTER_TEXT.search(item_code):
        return False
    if desc and desc.lower() in {
        "item code", "item qty", "qty unit", "unit rate",
        "basic value", "amount", "bidding unit", "description",
        "item title", "item details", "catalogue id",
    }:
        return False
    # GeM Item Category rows: description + serial is enough (qty is bid-level)
    if has_desc and has_sno and "gem" in schedule.lower():
        return True
    if not has_sno and not (has_qty and (has_money or has_desc)):
        if not (has_desc and (has_qty or has_money or data.get("item_code"))):
            return False
    if has_qty and (has_money or has_desc or data.get("item_code")):
        return True
    return has_desc and (has_money or has_qty or has_sno)


def sanitize_products(raw_products: list[Any]) -> list[ProductItem]:
    """Drop junk rows; merge duplicates. Keep rows even if name is unresolved."""
    cleaned: list[ProductItem] = []
    for p in raw_products:
        if isinstance(p, ProductItem):
            data = dict(p)
        elif isinstance(p, dict):
            allowed = set(ProductItem.model_fields)
            data = {k: v for k, v in p.items() if k in allowed}
        else:
            continue

        sno = str(data.get("s_no") or "").strip()
        if sno and not re.fullmatch(r"\d+", sno):
            continue

        desc = normalize_product_description(_clean_description(data.get("description")))
        if not _is_valid_row(data, desc):
            continue

        learning = get_name_learning()
        if desc and learning.is_rejected(desc):
            continue

        data["s_no"] = sno or None
        data["description"] = desc

        # Prefer learned / existing name; heuristic is only a soft assist.
        # Do NOT drop the row when name cannot be derived — AI + user
        # instructions own naming / work-vs-product decisions.
        name = None
        existing = str(data.get("product_name") or "").strip() or None
        if existing and desc and not is_clause_or_junk(existing):
            name = recheck_product_name(desc, existing, extra_verbs=learning.extra_verbs)
        if not name and desc:
            name = learning.resolve(desc)
        if not name and desc:
            name = extract_product_name(desc)
            name = recheck_product_name(desc, name, extra_verbs=learning.extra_verbs)
        if name and is_clause_or_junk(name):
            name = None
        data["product_name"] = name

        if data.get("escalation"):
            data["escalation"] = re.sub(
                r"(?i)at\s*par", "AT Par", str(data["escalation"])
            ).strip()
        if not data.get("bidding_unit") or str(data.get("bidding_unit")).lower() in {
            "none", "null", "",
        }:
            data["bidding_unit"] = None
        cleaned.append(ProductItem(**data))

    merged = ProductExtractor._normalize_schedule_items(cleaned)
    return _renumber_sequential(merged)


def _renumber_sequential(items: list[ProductItem]) -> list[ProductItem]:
    """Fill missing S.No. only — never overwrite a serial taken from the PDF."""
    for idx, item in enumerate(items, start=1):
        sno = str(item.s_no or "").strip()
        if not sno:
            item.s_no = str(idx)
    return items
