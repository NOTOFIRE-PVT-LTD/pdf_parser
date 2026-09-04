"""Unit tests for AI PDF extract helpers (no live API)."""

from __future__ import annotations

from app.models.schemas import ProductItem
from app.services.ai.pdf_extract_agent import (
    _chunk_text,
    _dedupe_products,
    _merge_tender,
    _row_to_product,
)


def test_row_to_product_from_ai_json():
    item = _row_to_product({
        "s_no": "2",
        "product_name": "GSM module",
        "description": "Supply of GSM module for Control panel",
        "item_qty": "11",
        "qty_unit": "Numbers",
        "unit_rate": "100",
        "amount": "1100",
    })
    assert item is not None
    assert item.s_no == "2"
    assert item.product_name == "GSM module"
    assert "GSM module" in (item.description or "")
    assert item.item_qty == "11"


def test_row_to_product_rejects_empty():
    assert _row_to_product({"product_name": "", "description": ""}) is None
    assert _row_to_product("bad") is None


def test_dedupe_and_fill_serial():
    items = _dedupe_products([
        ProductItem(description="Alpha widget supply item", product_name="Alpha"),
        ProductItem(description="Alpha widget supply item", product_name="Alpha"),
        ProductItem(s_no="9", description="Beta widget supply item", product_name="Beta"),
    ])
    assert len(items) == 2
    assert items[0].s_no == "1"
    assert items[1].s_no == "9"


def test_merge_tender_prefers_longer():
    out = _merge_tender(
        {"tender_no": "A", "name_of_work": "Short"},
        {"tender_no": None, "name_of_work": "Much longer name of work text"},
    )
    assert out["tender_no"] == "A"
    assert "longer" in out["name_of_work"]


def test_chunk_text_splits_long():
    text = ("word " * 5000).strip()
    chunks = _chunk_text(text)
    assert len(chunks) >= 2
    assert all(chunks)
