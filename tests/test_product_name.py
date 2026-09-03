"""Tests for product name vs full description parsing."""

from __future__ import annotations

from app.models.schemas import ProductItem, TenderInformation, TenderResult
from app.services.export_service import ExportService
from app.services.product_sanitize import sanitize_products
from app.utils.product_name import (
    extract_product_name,
    is_work_description,
    normalize_product_description,
    recheck_product_name,
)


def test_supply_of_stripped_from_name_not_description():
    raw = "Supply of Disconnect Terminal Block, Screw less type, as per RDSO Spec"
    assert extract_product_name(raw) == "Disconnect Terminal Block, Screw less type"
    assert normalize_product_description(raw) == raw


def test_installation_scope_stripped():
    raw = (
        "Installation, wiring, testing & commissioning of Micro Processor based "
        "Remote Terminal Unit (RTU). Inspection: Consignee."
    )
    assert extract_product_name(raw) == "Micro Processor based Remote Terminal Unit (RTU)"
    assert "Inspection: Consignee" in (normalize_product_description(raw) or "")


def test_hiring_of_stripped():
    raw = "Hiring of Skilled Labour assistance SE/JE for cable laying"
    assert extract_product_name(raw) == "Skilled Labour assistance SE/JE for cable laying"


def test_personnel_qty_suffix_removed_from_name_only():
    raw = "Team Leader cum Project Manager (1 Nos. for 24 Months)"
    assert extract_product_name(raw) == "Team Leader cum Project Manager"
    assert normalize_product_description(raw) == raw


def test_rtu_supply_keeps_model_in_name():
    raw = (
        'Supply of Micro Processor based Remote Terminal Unit (RTU) with 64 digital '
        "input & 16 analog input with DOT Matrix Printer. Inspection: RDSO"
    )
    name = extract_product_name(raw)
    assert name is not None
    assert "Remote Terminal Unit (RTU)" in name
    assert not name.lower().startswith("supply of")
    assert "Inspection" not in name
    assert "Inspection: RDSO" in (normalize_product_description(raw) or "")


def test_sanitize_sets_product_name_separate_from_description():
    items = sanitize_products([
        ProductItem(
            s_no="1",
            item_qty="5.00",
            unit_rate="100.00",
            description="Supply of standard 19in rack mountable router",
        )
    ])
    assert len(items) == 1
    assert items[0].description == "Supply of standard 19in rack mountable router"
    assert items[0].product_name == "standard 19in rack mountable router"


def test_supply_installation_testing_commissioning_stripped():
    raw = (
        "Supply, Installation, Testing & Commissioning of Microprocessor based "
        "Networkable analog fire alarm control panel"
    )
    assert extract_product_name(raw) == (
        "Microprocessor based Networkable analog fire alarm control panel"
    )
    assert normalize_product_description(raw) == raw


def test_supply_installation_no_spaces_after_commas():
    raw = (
        "Supply,Installation,Testing &Commissioning of Linear Heat Sensing(LHS) cable"
    )
    assert extract_product_name(raw) == "Linear Heat Sensing(LHS) cable"


def test_cpvc_pipe_stripped():
    raw = (
        "Supply, Installation, Testing &Commissioning of CPVC pipe and allother "
        "associated fittings as per RDSO Specific"
    )
    name = extract_product_name(raw)
    assert name is not None
    assert name.startswith("CPVC pipe")
    assert not name.lower().startswith("supply")


def test_co2_extinguisher_unchanged_prefix():
    raw = (
        "CO2 type fire extinguisher capacity 4.5 Kg capacity ISI marked, "
        "with all accessories, operating bracket"
    )
    name = extract_product_name(raw)
    assert name is not None
    assert name.startswith("CO2 type fire extinguisher")
    assert "Supply" not in name


def test_commissioning_without_of():
    raw = "Supply, installation, testing and commissioning Fault isolator"
    assert extract_product_name(raw) == "Fault isolator"


def test_supervision_programming_stripped():
    raw = (
        "Supervision, Programming, Installation, Testing and Commissioning o f "
        "Aspiration Smoke detector system"
    )
    name = extract_product_name(raw)
    assert name is not None
    assert "Supervision" not in name
    assert "Installation" not in name.split()[0] if name else True
    assert name.lower().startswith("aspiration")


def test_recheck_rejects_clause():
    junk = "1.2 Meaning of similar works: Any Signalling Work"
    assert extract_product_name(junk) is None


def test_recheck_strips_leftover_scope():
    bad = "Supply, installation, testing and commissioning Fault isolator"
    fixed = recheck_product_name(
        "Supply, installation, testing and commissioning Fault isolator as per RDSO",
        bad,
    )
    assert fixed == "Fault isolator"


def test_sanitize_preserves_pdf_serial_numbers():
    items = sanitize_products([
        ProductItem(s_no="1", item_qty="1", amount="100", description="Supply of Widget Alpha"),
        ProductItem(s_no="2", item_qty="1", amount="200", description="Supply of Widget Beta"),
        ProductItem(s_no="6", item_qty="1", amount="300", description="Supply of Widget Gamma"),
        ProductItem(
            s_no="1",
            item_qty="1",
            amount="400",
            description="Supply of Widget Delta",
            schedule="Schedule B",
        ),
        ProductItem(
            s_no="3",
            item_qty="1",
            amount="500",
            description="Supply of Widget Epsilon",
            schedule="Schedule B",
        ),
    ])
    serials = [p.s_no for p in items]
    # PDF serials kept — never rewrite whole list to fabricated 1..N
    assert serials != ["1", "2", "3", "4", "5"]
    assert set(serials) == {"1", "2", "3", "6"}
    assert serials.count("1") == 2  # schedule restart allowed


def test_sanitize_fills_missing_serial_only():
    items = sanitize_products([
        ProductItem(
            s_no=None,
            item_qty="1",
            amount="100",
            description="Supply of Missing Serial Widget",
        ),
        ProductItem(
            s_no="9",
            item_qty="1",
            amount="200",
            description="Supply of Kept Serial Widget",
        ),
    ])
    by_name = {p.product_name: p.s_no for p in items}
    assert by_name.get("Kept Serial Widget") == "9"
    # Missing serial gets a document-order fill-in only
    assert by_name.get("Missing Serial Widget") not in {None, "", "9"}
    assert str(by_name.get("Missing Serial Widget")).isdigit()


def test_excel_export_name_vs_description_columns():
    result = TenderResult(
        tender_information=TenderInformation(tender_no="T-1"),
        products=sanitize_products([
            ProductItem(
                s_no="1",
                item_code="NS1",
                item_qty="5.00",
                qty_unit="Numbers",
                unit_rate="177112.00",
                amount="885560.00",
                description=(
                    "Supply of Micro Processor based Remote Terminal Unit (RTU) with extras. "
                    "Inspection: RDSO"
                ),
            ),
        ]),
    )
    row = ExportService().build_flat_rows(result)[0]
    assert row["productName"] == "Micro Processor based Remote Terminal Unit (RTU) with extras"
    assert "Supply of" in (row["itemDescription"] or "")
    assert "Inspection: RDSO" in (row["itemDescription"] or "")
    assert row["productName"] != row["itemDescription"]


def test_user_style_product_names():
    assert extract_product_name(
        "Supply, installation, testing and commissioning of Relay module with "
        "backbox and all other accessories as per RDSO/SPN/217/2025 ver-3.1 or latest. "
        "Inspection: RDSO"
    ) == "Relay module with backbox"
    assert extract_product_name(
        "Supply, installation, testing and commissioning of GSM module for Control "
        "panel with backbox and all other accessories as per RDSO"
    ) == "GSM module for Control panel"
    assert extract_product_name(
        "Automatic Fire Ball for Fire-Extinguishing of Medium size - the weight of "
        "the agent is not less than 1.0 kg. Inspection: RDSO"
    ) == "Automatic Fire Ball for Fire-Extinguishing"
    assert extract_product_name(
        "Supply of heavy duty Heat Gun of minimum 2000 Watt with LCD display "
        "(for temperature) for testing of heat sensors in S&T installation. Make-BOSCH"
    ) == "heavy duty Heat Gun of minimum 2000 Watt with LCD display (for temperature)"


def test_civil_work_paragraphs_are_not_products():
    work = (
        "RE-INSTATEMENT OF PLATFORM AND REPAIRING TO ORIGINAL STATE AFTER CABLE LAYING, "
        "EXCAVATION OF TRENCH IN ALL KIND OF SOIL AND REFILLING OF TRENCH AND "
        "REINSTATEMENT OF TRACK WHILE TRACK CROSSING."
    )
    trench = (
        "Excavation of cable trench as per cable route plan, 1.2 Mtr. deep and of "
        "0.3 Mtr. to 0.6 Mtr. wide at bottom without brick alongside the track in "
        "all kinds of soil, conforming to distances as per cable route plan and "
        "refilling. This work includes clearing of route from bushes etc."
    )
    assert is_work_description(work)
    assert is_work_description(trench)
    assert not is_work_description("Supply of 6 Quad Jelly Filled Cable 0.9mm")
