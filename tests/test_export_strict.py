"""Ensure Excel/CSV export never invents data."""

from __future__ import annotations

from app.models.schemas import ProductItem, TenderInformation, TenderResult
from app.services.export_service import ExportService, FLAT_EXCEL_COLUMNS
from app.services.product_sanitize import sanitize_products


def test_export_leaves_missing_fields_null_not_guessed():
    result = TenderResult(
        tender_information=TenderInformation(
            name_of_work="Test Work",
            tender_no="T-99",
            zone="WESTERN",
            number_of_jv_member_allowed="3",
        ),
        products=sanitize_products([
            ProductItem(
                s_no="1",
                item_qty="10.00",
                unit_rate="100.00",
                amount="1000.00",
                description="Supply of Widget A",
            ),
        ]),
    )
    row = ExportService().build_flat_rows(result)[0]

    assert row["status"] is None
    assert row["referenceNo"] is None
    # Zone without RLY/Railway must NOT be copied into railway
    assert row["railway"] is None
    assert row["zone"] == "WESTERN"
    assert row["allowJointVenture"] == "3"
    assert row["advertisedValue"] is None
    assert row["productName"] == "Widget A"
    assert row["itemDescription"] == "Supply of Widget A"
    assert row["itemName"] == "Widget A"
    assert row["biddingUnit"] is None


def test_railway_exported_only_when_set_from_pdf():
    result = TenderResult(
        tender_information=TenderInformation(
            name_of_work="Work",
            zone="WESTERN RLY",
            railway="WESTERN RLY",
        ),
        products=[
            ProductItem(
                s_no="7",
                description="Supply of Relay module with backbox",
                product_name="Relay module with backbox",
                item_qty="2",
            ),
        ],
    )
    row = ExportService().build_flat_rows(result)[0]
    assert row["railway"] == "WESTERN RLY"
    assert row["itemNo"] == "7"  # PDF serial preserved


def test_csv_matches_excel_columns():
    result = TenderResult(
        tender_information=TenderInformation(tender_no="X-1", name_of_work="ABC"),
        products=[
            ProductItem(
                s_no="1",
                description="Supply of GSM module for Control panel",
                product_name="GSM module for Control panel",
                item_qty="1",
                amount="100",
            ),
        ],
    )
    exporter = ExportService()
    csv_bytes = exporter.to_csv_bytes(result, which="products")
    text = csv_bytes.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header == ",".join(FLAT_EXCEL_COLUMNS)
    assert "GSM module for Control panel" in text
    assert len(csv_bytes) > 50


def test_rejects_ungrounded_product_name():
    result = TenderResult(
        tender_information=TenderInformation(name_of_work="Work"),
        products=[
            ProductItem(
                s_no="1",
                description="Supply of Fire Ball",
                product_name="Completely Invented Widget XYZ",
            ),
        ],
    )
    row = ExportService().build_flat_rows(result)[0]
    assert row["productName"] is None
    assert row["itemName"] is None
    assert row["itemDescription"] == "Supply of Fire Ball"
