"""Tests for flat Excel export."""

from __future__ import annotations

from app.models.schemas import ProductItem, TenderInformation, TenderResult
from app.services.export_service import ExportService, FLAT_EXCEL_COLUMNS
from app.services.product_sanitize import sanitize_products


def test_flat_excel_rows_match_portal_columns():
    result = TenderResult(
        tender_information=TenderInformation(
            name_of_work="Supply of RTU Equipment",
            tender_no="SDSTE-LCGATE-05",
            division_name="HOWRAH DIVISION",
            zone="EASTERN RLY",
            railway="EASTERN RLY",
            advertised_value="1000000.00",
            earnest_money="20000.00",
            period_of_completion="12 Months",
            bidding_type="Normal Tender",
            tender_type="Open",
            contract_type="Supply",
            bid_validity_days="120",
        ),
        products=sanitize_products([
            ProductItem(
                s_no="1",
                item_code="NS1",
                item_qty="5.00",
                qty_unit="Numbers",
                unit_rate="177112.00",
                basic_value="885560.00",
                amount="885560.00",
                bidding_unit="Rs.",
                description="Supply of Micro Processor based Remote Terminal Unit (RTU)",
                schedule="Schedule A-Supply",
            ),
        ]),
    )
    exporter = ExportService()
    rows = exporter.build_flat_rows(result)
    assert len(rows) == 1
    row = rows[0]
    assert set(FLAT_EXCEL_COLUMNS).issubset(set(row.keys()))
    assert row["tenderNo"] == "SDSTE-LCGATE-05"
    assert row["railway"] == "EASTERN RLY"
    assert row["productName"] == "Micro Processor based Remote Terminal Unit (RTU)"
    assert row["itemDescription"] == "Supply of Micro Processor based Remote Terminal Unit (RTU)"
    assert row["productName"] != row["itemDescription"]
    assert len(exporter.to_excel_bytes(result)) > 200
    assert len(exporter.to_combined_csv_bytes([result])) > 50
