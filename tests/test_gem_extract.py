"""GeM / multi-portal extraction smoke tests — based on real PDF shapes."""

from __future__ import annotations

from app.extractor.field_extractor import FieldExtractor
from app.extractor.product_extractor import ProductExtractor
from app.models.schemas import ProductItem, TenderInformation, TenderResult
from app.parser.table_parser import ExtractedTable
from app.services.export_service import ExportService
from app.services.product_sanitize import sanitize_products
from app.utils.portal import detect_portal
from app.utils.product_name import normalize_product_description


_GEM_SNIPPET = """
(cid:1) Bid Number: GEM/2026/B/7967463
Dated: 27-08-2026
Bid Details
Bid End Date/Time 10-09-2026 16:00:00
Bid Opening Date/Time 10-09-2026 16:30:00
Bid Offer Validity (From End Date) 120 (Days)
Ministry/State Name Ministry Of Ports, Shipping And Waterways
Department Name Department Of Public Enterprises
Total Quantity 6
Bid Details
ADDRESSABLE FIRE DETECTION SYSTEM_AFDS_BY 531 ,
ONBOARD SPARES_OBS_FOR AFDS _BY 531 ,
INSTALLATION_COMMISSIONING_I_C_SPARES FOR AFDS BY 531 ,
ADDRESSABLE FIRE DETECTION SYSTEM_AFDS_BY 532 ,
ONBOARD SPARES_OBS_FOR AFDS BY 532 ,
TRAINING FOR AFDS BY 532
Item Category
GeMARPTS Searched Strings used
BOQ Title ADDRESSABLE FIRE DETECTION SYSTEM FOR NGMV
EMD Amount 50000
Type of Bid Two Packet Bid
"""


_IREPS_SNIPPET = """
AJMER DIVISION-S AND T/NORTH WESTERN RLY
TENDER DOCUMENT
Tender No: SNT-AII-19-2026-27 Closing Date/Time: 30/06/2026 15:30
1. NIT HEADER
Name of Work Ajmer Division: Provision of Automatic Fire Detection System
Bidding type Normal Tender
Tender Type Open Bidding System Single Packet System
Advertised Value 20663314.17
Earnest Money (Rs.) 413300.00 Validity of Offer ( Days) 60
I/we have downloaded the tender documents from Indian Railway website
www.ireps.gov.in
"""


def test_detect_gem_portal():
    assert detect_portal(_GEM_SNIPPET) == "gem"


def test_gem_bid_fields_and_item_category_products():
    info = FieldExtractor().extract_tender_information(_GEM_SNIPPET)
    assert info.tender_no == "GEM/2026/B/7967463"
    assert info.closing_date_time == "10-09-2026 16:00:00"
    assert info.name_of_work and "FIRE DETECTION" in info.name_of_work.upper()
    assert info.earnest_money == "50000"

    products = sanitize_products(ProductExtractor().extract(_GEM_SNIPPET))
    assert len(products) == 6
    assert "AFDS_BY 531" in (products[0].description or "")
    assert products[0].s_no == "1"


def test_ireps_division_zone_not_polluted():
    info = FieldExtractor().extract_tender_information(_IREPS_SNIPPET)
    assert info.tender_no == "SNT-AII-19-2026-27"
    assert info.zone == "NORTH WESTERN RLY"
    assert info.railway == "NORTH WESTERN RLY"
    assert info.division_name and "AJMER" in info.division_name.upper()
    assert "Bidding type" not in (info.division_name or "")
    assert info.railway != "website"


def test_description_strips_leaked_next_item_amounts():
    dirty = (
        "Supply of 2 x 10G Adapter Interface without 10G SFP+ Modules for LER Routers. "
        "D5 1.00 Numbers 60289.44 60289.44 AT Par 60289.44 Rs."
    )
    clean = normalize_product_description(dirty)
    assert clean is not None
    assert "D5 1.00" not in clean
    assert "10G Adapter" in clean


def test_gem_csv_columns_aligned_after_description():
    result = TenderResult(
        tender_information=TenderInformation(
            tender_no="GEM/2026/B/1",
            name_of_work="Supply of tools",
        ),
        products=sanitize_products([
            ProductItem(
                s_no="1",
                description="Heavy duty Heat Gun, 2000 Watt, LCD display",
                item_qty="3",
                qty_unit="Nos",
                unit_rate="2500.00",
                amount="7500.00",
                schedule="GeM Item Category",
            ),
        ]),
    )
    row = ExportService().build_flat_rows(result)[0]
    assert row["tenderNo"] == "GEM/2026/B/1"
    assert row["itemDescription"] == "Heavy duty Heat Gun, 2000 Watt, LCD display"
    assert row["itemQty"] == "3"
    assert row["itemUnit"] == "Nos"
    assert row["itemUnitRate"] == "2500.00"
    assert row["itemAmount"] == "7500.00"


def test_gem_catalogue_table_still_works():
    table = ExtractedTable(
        page_number=1,
        headers=["Sl. No", "Item Title", "Quantity", "Unit", "Unit Price", "Total Price"],
        rows=[
            ["1", "Fire Ball 1kg Automatic", "10", "Nos", "500.00", "5000.00"],
            ["2", "Heat Gun 2000W LCD", "2", "Nos", "3500.00", "7000.00"],
        ],
        mapped_headers={
            "s_no": 0,
            "description": 1,
            "item_qty": 2,
            "qty_unit": 3,
            "unit_rate": 4,
            "amount": 5,
        },
        is_product_table=True,
    )
    raw = ProductExtractor().extract(
        "Government e-Marketplace Bid Number GEM/2024/B/1 Catalogue ID details",
        tables=[table],
    )
    products = sanitize_products(raw)
    assert len(products) >= 2
    assert products[0].item_qty == "10"
