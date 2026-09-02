"""Ensure Excel export never invents data."""

from __future__ import annotations

from app.models.schemas import ProductItem, TenderInformation, TenderResult
from app.services.export_service import ExportService
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
    assert row["allowJointVenture"] == "3"
    assert row["railway"] == "WESTERN"
    assert row["advertisedValue"] is None
    assert row["productName"] == "Widget A"
    assert row["itemDescription"] == "Supply of Widget A"
    assert row["itemName"] == "Widget A"
    assert row["biddingUnit"] is None
