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
    is_work_description,
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
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"(?i)^description\s*[:\-–]\s*", "", text).strip()
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

    if desc and (_JUNK_DESC.search(desc) or is_clause_or_junk(desc)):
        return False
    item_code = str(data.get("item_code") or "")
    if item_code and POINTER_TEXT.search(item_code):
        return False
    if desc and desc.lower() in {
        "item code", "item qty", "qty unit", "unit rate",
        "basic value", "amount", "bidding unit", "description",
    }:
        return False
    # Rows without S.No. must look like real BOQ lines (qty + money)
    if not has_sno and not (has_qty and has_money):
        return False
    if has_qty and (has_money or has_desc or data.get("item_code")):
        return True
    return has_desc and (has_money or has_qty)


def sanitize_products(raw_products: list[Any]) -> list[ProductItem]:
    """Drop junk rows; merge duplicates across extraction passes."""
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
        if desc and (is_work_description(desc) or learning.is_rejected(desc)):
            continue

        data["s_no"] = sno or None
        data["description"] = desc
        name = learning.resolve(desc) if desc else None
        if not name and desc:
            name = extract_product_name(desc)
        name = recheck_product_name(desc, name, extra_verbs=learning.extra_verbs)
        if not name or is_clause_or_junk(name):
            continue
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
    """Assign 1..N in final document order (fixes per-schedule restarts and gaps)."""
    for idx, item in enumerate(items, start=1):
        item.s_no = str(idx)
    return items
