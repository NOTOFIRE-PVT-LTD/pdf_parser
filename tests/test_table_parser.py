"""
Unit tests for TableParser's handling of multi-page schedules that don't
repeat their header row, and tables whose real header is buried behind
caption/title rows. Both patterns showed up in real NIT PDFs where the
naive "first row is always the header" assumption silently dropped most of
a schedule's line items.

Run: pytest -q
"""

from __future__ import annotations

from app.parser.table_parser import TableParser


def test_normalize_table_reuses_fallback_header_for_headerless_continuation():
    """
    A continuation page whose raw table starts with a real data row (no
    repeated header) must not sacrifice that row as a fake header — it
    should reuse the previous page's product header/mapping instead.
    """
    tp = TableParser()
    fallback_headers = [
        "S.No.", "Item Code", "Item Qty", "Qty Unit",
        "Unit Rate", "Basic Value", "Escl.(%)", "Amount", "Bidding Unit",
    ]
    fallback_mapped = TableParser.map_headers(fallback_headers)

    raw = [
        ["13", "c", "1.00", "Station", "62075.00", "62075.00", "AT Par", "62075.00", "Above/Below/Par"],
        ["", "Description:- Some item text", "", "", "", "", "", "", ""],
        ["14", "c", "2.00", "Numbers", "6578.17", "13156.34", "AT Par", "13156.34", "Above/Below/Par"],
    ]
    table = tp._normalize_table(
        raw, page_number=2, fallback_headers=fallback_headers, fallback_mapped=fallback_mapped
    )
    assert table is not None
    assert table.headers == fallback_headers
    assert table.mapped_headers == fallback_mapped
    # All 3 raw rows are kept as data — none sacrificed as a fake header.
    assert len(table.rows) == 3
    assert table.rows[0][0] == "13"
    assert table.is_product_table


def test_normalize_table_drops_letterhead_noise_but_keeps_schedule_titles():
    """
    A continuation-page table can contain page letterhead/footer text
    pdfplumber picked up inside the table's bounding box (e.g. a stray
    "ender N" fragment of "Tender No", or the document's full letterhead
    block). That must be dropped, not glued onto a real item's description
    as a fake continuation — but a genuine "Schedule () X-Title..." marker
    row, which is also long free text in the same first cell, must survive:
    ProductExtractor needs it to attribute the following items correctly.
    """
    tp = TableParser()
    fallback_headers = [
        "S.No.", "Item Code", "Item Qty", "Qty Unit",
        "Unit Rate", "Basic Value", "Escl.(%)", "Amount", "Bidding Unit",
    ]
    fallback_mapped = TableParser.map_headers(fallback_headers)

    raw = [
        ["ender N", "", "", "", "", "", "", "", ""],
        ["Schedule () B-SUPPLY OF FERROUS MATERIALS", "", "", "", "", "", "", "4230482.00", "Above/\nBelow/P\nar"],
        ["DYCSTE-C-NGP-S AND T/SOUTH EAST CENTRAL RLY\nTENDER DOCUMENT", "", "", "", "", "", "", "", ""],
        ["13", "c", "1.00", "Station", "62075.00", "62075.00", "AT Par", "62075.00", "Above/Below/Par"],
    ]
    table = tp._normalize_table(
        raw, page_number=7, fallback_headers=fallback_headers, fallback_mapped=fallback_mapped
    )
    assert table is not None
    row_first_cells = [r[0] for r in table.rows]
    assert "ender N" not in row_first_cells
    assert not any("TENDER DOCUMENT" in c for c in row_first_cells)
    assert any(c.startswith("Schedule () B-SUPPLY OF FERROUS MATERIALS") for c in row_first_cells)
    assert "13" in row_first_cells


def test_normalize_table_skips_caption_rows_to_find_buried_header():
    """
    Some NITs put one or two caption/title rows before the real header
    ("Schedule ...", "Item- 1 / SOR items") in a differently-shaped table
    (e.g. an item-breakup annexure). The real header a few rows down must
    still be found, and the skipped captions kept as a schedule-title hint
    instead of being discarded.
    """
    tp = TableParser()
    raw = [
        ["Schedule", "Schedule A-Annexure - Z (Schedule-1)", "", "", "", "", ""],
        ["Item- 1", "SOR items", "", "", "", "", ""],
        ["S No.", "Item No", "Description of Item", "Unit", "Qty", "Rate", "Amount"],
        ["1", "1", "Excavation of trench", "Metre", "8000.00", "9.00", "72000.00"],
        ["2", "2", "Another trench item", "Metre", "500.00", "8.00", "4000.00"],
    ]
    table = tp._normalize_table(raw, page_number=8)
    assert table is not None
    assert table.headers == ["S No.", "Item No", "Description of Item", "Unit", "Qty", "Rate", "Amount"]
    assert table.is_product_table
    # First row is the synthesized schedule-title hint, not real data.
    assert "Schedule A-Annexure" in table.rows[0][0]
    assert table.rows[1][0] == "1"
    assert table.rows[2][0] == "2"
    assert len(table.rows) == 3  # 1 synthetic caption row + 2 real item rows


def test_normalize_table_no_regression_on_normal_header_first_table():
    tp = TableParser()
    raw = [
        ["S.No.", "Item Code", "Item Qty", "Qty Unit", "Unit Rate", "Basic Value", "Escl.(%)", "Amount", "Bidding Unit"],
        ["1", "A", "10.00", "Kilometre", "92115.00", "921150.00", "AT Par", "921150.00", ""],
    ]
    table = tp._normalize_table(raw, page_number=1)
    assert table is not None
    assert table.headers[0] == "S.No."
    assert len(table.rows) == 1
    assert table.rows[0][0] == "1"


def test_looks_like_header_row():
    tp = TableParser()
    assert tp._looks_like_header_row(
        ["S No.", "Item No", "Description of Item", "Unit", "Qty", "Rate", "Amount"]
    )
    assert not tp._looks_like_header_row(
        ["13", "c", "1.00", "Station", "62075.00", "62075.00", "AT Par", "62075.00", "Above/Below/Par"]
    )
    assert not tp._looks_like_header_row(["Item- 1", "SOR items", "", "", "", "", ""])


def test_map_headers_does_not_swap_qty_unit_and_unit_rate():
    """
    Short aliases like "unit" / "qty" used to claim the wrong column:
    "Unit Rate" became qty_unit and "Qty Unit" became item_qty, so Excel
    rows had rates in the unit column and quantities in the rate column.
    """
    mapped = TableParser.map_headers(
        [
            "S.No.",
            "Item Code",
            "Item Qty",
            "Qty Unit",
            "Unit Rate",
            "Basic Value",
            "Escl.(%)",
            "Amount",
            "Bidding Unit",
        ]
    )
    assert mapped["s_no"] == 0
    assert mapped["item_code"] == 1
    assert mapped["item_qty"] == 2
    assert mapped["qty_unit"] == 3
    assert mapped["unit_rate"] == 4
    assert mapped["basic_value"] == 5
    assert mapped["escalation"] == 6
    assert mapped["amount"] == 7
    assert mapped["bidding_unit"] == 8

    swapped = TableParser.map_headers(
        ["Description of Item", "Unit Rate", "Qty", "Amount"]
    )
    assert swapped["description"] == 0
    assert swapped["unit_rate"] == 1
    assert swapped["item_qty"] == 2
    assert swapped["amount"] == 3
    assert "qty_unit" not in swapped

    generic = TableParser.map_headers(
        ["SN", "Description", "Unit", "Mumbai", "Pune", "Qty"]
    )
    assert generic["s_no"] == 0
    assert generic["description"] == 1
    assert generic["qty_unit"] == 2
    assert generic["item_qty"] == 5


def test_normalize_table_merges_wrapped_ireps_headers():
    """
    IREPS schedule headers wrap onto a second line ("Item" + "Code",
    "Bidding" + "Unit"). Those tokens must be merged into the header, not
    treated as the first data row — otherwise every column shifts.
    """
    tp = TableParser()
    raw = [
        ["S.No.", "Item", "Item Qty", "Qty Unit", "Unit Rate", "Basic Value", "Escl.(%)", "Amount", "Bidding"],
        ["", "Code", "", "", "", "", "", "", "Unit"],
        ["1", "1", "360.00", "Month", "70890.38", "25520536.80", "AT Par", "25520536.80", "Rs."],
        ["", "Description:- Site Engineer (S&T)", "", "", "", "", "", "", ""],
    ]
    table = tp._normalize_table(raw, page_number=1)
    assert table is not None
    assert table.mapped_headers.get("item_code") == 1
    assert table.mapped_headers.get("unit_rate") == 4
    assert table.mapped_headers.get("bidding_unit") == 8
    assert table.rows[0][0] == "1"
    assert table.rows[0][2] == "360.00"
    assert table.rows[0][4] == "70890.38"
    assert table.is_product_table


def test_normalize_table_pads_short_rows_so_columns_stay_aligned():
    tp = TableParser()
    raw = [
        ["S.No.", "Item Code", "Item Qty", "Qty Unit", "Unit Rate"],
        ["1", "A", "10.00"],
    ]
    table = tp._normalize_table(raw, page_number=1)
    assert table is not None
    assert len(table.rows[0]) == 5
    assert table.rows[0][0] == "1"
    assert table.rows[0][2] == "10.00"
    assert table.rows[0][3] == ""
    assert table.rows[0][4] == ""
