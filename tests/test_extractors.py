"""
Unit tests for field / product extraction (no PDF binaries required).
Run: pytest -q
"""

from __future__ import annotations

import re

from app.extractor.clause_extractor import ClauseExtractor
from app.extractor.field_extractor import FieldExtractor
from app.extractor.product_extractor import ProductExtractor
from app.parser.table_parser import ExtractedTable, TableParser
from app.services.export_service import ExportService
from app.models.schemas import ProductItem, TenderInformation, TenderResult


RAILWAY_SAMPLE = """
MUMBAI CENTRAL DIVISION-S AND T/WESTERN RLY
TENDER DOCUMENT
Tender No: DYCSTE_Works_PSSA_02R                 Closing Date/Time: 18/06/2026 15:00
--------------------------------------------------------------------------------
Dy.CSTE/WORKS/MMCT acting for and on behalf of The President of India invites E-Tenders against Tender No DYCSTE_Works_PSSA_02R Closing Date/Time 18/06/2026 15:00 Hrs.

1. NIT HEADER
Name of Work
Appointment of Project Supervision services Agency for providing Project Supervision Services for various signaling projects of S&T department in Mumbai Division, Western Railway
Bidding type Normal Tender Tender Type Open
Bidding System Two Packet System Tender Closing Date Time 18/06/2026 15:00
Date Time Of Uploading Tender 11/05/2026 13:36 Pre-Bid Conference Required No
Advertised Value 100877479.68 Tendering Section DY.CSTE/W
Bidding Style Single Rate for Tender Bidding Unit Above/Below/Par
Earnest Money (Rs.) 2017600.00 Validity of Offer ( Days) 120
Tender Doc. Cost (Rs.) 0.00 Period of Completion 24 Months
Contract Type Works - General Contract Category Expenditure
Bidding Start Date 04/06/2026 Are JV allowed to bid Yes
Number of JV Member Allowed 3 Are Consortium allowed to bid No
Number of Consortium Member Allowed 0 Ranking Order For Bids Lowest to Highest
Expenditure Type Capital (Works)

Payment Terms:
Payment shall be made as per Railway Board guidelines.

Liquidated Damages:
LD @ 0.5% per week subject to maximum of 10% of contract value.

S.No. Item Item Qty Qty Unit Unit Rate Basic Value Escl.(%) Amount Bidding
Code Unit
Schedule () A-Key Personnels 17395123.20
1 24.00 Month 254118.55 6098845.20 AT Par 6098845.20
1
Description:- Team Leader cum Project Manager (1 Nos. for 24 Months)
2 72.00 Month 156892.75 11296278.00 AT Par 11296278.00
2
Description:- Resident Engineer/S&T (3 Nos. Each for 24 Months)
S.No. Item Item Qty Qty Unit Unit Rate Basic Value Escl.(%) Amount Bidding
Code Unit
Schedule () B-Non-Key Personnel 83482356.48
1 360.00 Month 70890.38 25520536.80 AT Par 25520536.80
1
Description:- Site Engineer (S&T),Signal (15 Nos. Each for 24 Months)
2 120.00 Month 70890.38 8506845.60 AT Par 8506845.60
2
Description:- Site Engineer (S&T),Telecom (5 Nos. Each for 24 Months)
3 72.00 Month 70436.70 5071442.40 AT Par 5071442.40
3
Description:- Site Engineer (Civil/Building)(3 Nos. Each for 24 Months)
4 48.00 Month 71864.28 3449485.44 AT Par 3449485.44
4
Description:- Site Engineer (Electrical/Power)(2 Nos. Each for 24 Months)
5 48.00 Month 71032.73 3409571.04 AT Par 3409571.04
5
Description:- Site Engineer (Electrical/TRD)(2 Nos. Each for 24 Months)
6 120.00 Month 80104.14 9612496.80 AT Par 9612496.80
6
Description:- Design Expert S&T (Signal)(5 Nos. Each for 24 Months)
7 96.00 Month 81000.00 7776000.00 AT Par 7776000.00
7
Description:- Sector Expert S&T (IT/Networking)(4 Nos. Each for 24 Months)
8 120.00 Month 29618.86 3554263.20 AT Par 3554263.20
8
Description:- Computer Operator cum Stenographer(5 Nos. Each for 24 Months)
9 96.00 Month 38776.20 3722515.20 AT Par 3722515.20
9
Description:- Draftsman/AutoCAD Operator(4 Nos. Each for 24 Months)
10 96.00 Month 28440.00 2730240.00 AT Par 2730240.00
10
Description:- Clerk (Finance and accounting)(4 Nos. Each for 24 Months)
11 96.00 Month 28440.00 2730240.00 AT Par 2730240.00
11
Description:- Legal and contract manager(4 Nos. Each for 24 Months)
12 336.00 Month 22020.00 7398720.00 AT Par 7398720.00
12
Description:- Technician / Chainman/helper/office attendant (14 Nos. Each for 24 Months)
Schedule Total 100877479.68
Some junk footer 99 99 1.00 Month 1.00 1.00 AT Par 1.00
99
Description:- Should not be extracted

Contact Officer: Dy.CSTE/Works
Email: dycste@example.com
"""


GENERIC_SAMPLE = """
NOTICE INVITING TENDER
NIT No: NIT/PWD/2024/118
Tender No: TND-2024-7781
Name of Work: Supply of Desktop Computers and Peripherals
Organisation: Public Works Department
Estimated Cost: Rs. 25,00,000
EMD: Rs. 50,000
Closing Date/Time: 30-06-2024 17:00
Period of Completion: 90 days

Documents Required:
GST Certificate, PAN, ITR, Balance Sheet

Payment Terms:
Payment shall be made within 30 days.

Bill of Quantities
1. Desktop Computer - Intel i5 - Qty: 50 Nos
2. Laser Printer A4 - Qty: 10 Units
"""


def test_tender_no_not_polluted_with_extra_text():
    messy = """
MUMBAI CENTRAL DIVISION-S AND T/WESTERN RLY
TENDER DOCUMENT
Tender No: DYCSTE_Works_PSSA_02R                 Closing Date/Time: 18/06/2026 15:00
Dy.CSTE/WORKS/MMCT invites E-Tenders against Tender No DYCSTE_Works_PSSA_02R Closing Date/Time 18/06/2026 15:00 Hrs. Bidders will be able to submit their original/revised bids upto closing date and time only.
Name of Work
Appointment of Project Supervision services Agency
Advertised Value 100877479.68
Earnest Money (Rs.) 2017600.00
Period of Completion 24 Months
Number of JV Member Allowed 3
"""
    info = FieldExtractor().extract_tender_information(messy)
    assert info.tender_no == "DYCSTE_Works_PSSA_02R"
    assert "Closing" not in (info.tender_no or "")
    assert "Bidders" not in (info.tender_no or "")
    assert " " not in (info.tender_no or "")

    info = FieldExtractor().extract_tender_information(RAILWAY_SAMPLE)
    assert info.tender_no == "DYCSTE_Works_PSSA_02R"
    assert info.closing_date_time and "18/06/2026" in info.closing_date_time
    assert info.division_name and "MUMBAI CENTRAL" in info.division_name.upper()
    assert info.zone and "WESTERN" in info.zone.upper()
    assert info.name_of_work and "Project Supervision" in info.name_of_work
    assert info.advertised_value and "100877479" in info.advertised_value.replace(",", "")
    assert info.earnest_money and "2017600" in info.earnest_money.replace(",", "")
    assert info.period_of_completion and "24" in info.period_of_completion
    assert info.number_of_jv_member_allowed == "3"
    assert info.bidding_type == "Normal Tender"
    assert info.tender_type == "Open"
    assert info.contract_type == "Works - General"
    assert info.contract_category == "Expenditure"
    assert info.tendering_section == "DY.CSTE/W"
    assert info.pre_bid_required == "No"
    assert info.jv_allowed == "Yes"
    assert info.consortium_allowed == "No"
    assert info.consortium_members_allowed == "0"
    assert info.ranking_order == "Lowest to Highest"
    assert info.reference_no == "DYCSTE_Works_PSSA_02R"
    assert info.status is None  # IREPS NIT PDFs usually omit tender status
    assert info.pdf_url is None  # no document URL in the NIT text


def test_no_duplicate_products_with_different_sno():
    """Same item extracted twice with different S.No. must collapse to one row."""
    dupes = [
        ProductItem(
            s_no="1",
            item_code="1",
            item_qty="24.00",
            qty_unit="Month",
            unit_rate="254118.55",
            basic_value="6098845.20",
            amount="6098845.20",
            description="Team Leader",
            schedule="Schedule A",
        ),
        ProductItem(
            s_no="20",
            item_code="1",
            item_qty="24.00",
            qty_unit="Month",
            unit_rate="254118.55",
            basic_value="6098845.20",
            amount="6098845.20",
            description="Team Leader",
            schedule="Schedule A",
        ),
        ProductItem(
            s_no="00",
            item_code="9",
            item_qty="96.00",
            qty_unit="Month",
            unit_rate="38776.20",
            amount="3722515.20",
            description="Draftsman/AutoCAD Operator",
        ),
        ProductItem(
            s_no="9",
            item_code="9",
            item_qty="96.00",
            qty_unit="Month",
            unit_rate="38776.20",
            amount="3722515.20",
            description="Draftsman/AutoCAD Operator",
        ),
    ]
    products = ProductExtractor()._merge_all(dupes)
    assert len(products) == 2
    team = next(p for p in products if p.item_code == "1")
    assert team.s_no == "1"  # prefer matching item_code / better serial
    draftsman = next(p for p in products if p.item_code == "9")
    assert draftsman.s_no == "9"


def test_same_row_with_and_without_schedule_is_one_product():
    """Page-break extracts without schedule must not duplicate scheduled rows."""
    dupes = [
        ProductItem(
            s_no="11",
            item_code="11",
            item_qty="96.00",
            qty_unit="Month",
            unit_rate="28440.00",
            basic_value="2730240.00",
            amount="2730240.00",
            description="Legal and contract manager(4 Nos. Each for 24 Months)",
            schedule="Schedule () B-Non-Key Personnel",
        ),
        ProductItem(
            s_no="11",
            item_code="11",
            item_qty="96.00",
            qty_unit="Month",
            unit_rate="28440.00",
            basic_value="2730240.00",
            amount="2730240.00",
            description="Legal and contract manager(4 Nos. Each for 24 Months)",
            schedule=None,
        ),
        ProductItem(
            s_no="1",
            item_code="1",
            item_qty="360.00",
            qty_unit="Month",
            unit_rate="70890.38",
            amount="25520536.80",
            description="Site Engineer (S&T),Signal",
            schedule="Schedule () B-Non-Key Personnel",
        ),
    ]
    products = ProductExtractor()._merge_all(dupes)
    assert len(products) == 2
    assert [p.s_no for p in products] == ["1", "11"]
    legal = next(p for p in products if p.s_no == "11")
    assert legal.schedule and "B-Non-Key" in legal.schedule


def test_railway_products_from_text():
    products = ProductExtractor().extract(RAILWAY_SAMPLE)
    # Schedule A (2) + Schedule B (12)
    assert len(products) == 14
    sched_a = [p for p in products if p.schedule and "A-Key" in p.schedule]
    sched_b = [p for p in products if p.schedule and "B-Non-Key" in p.schedule]
    assert len(sched_a) == 2
    assert len(sched_b) == 12
    assert sched_a[0].description and "Team Leader" in sched_a[0].description
    assert sched_a[0].item_qty == "24.00"
    assert sched_a[0].item_code == "1"
    assert sched_b[0].item_qty == "360.00"
    assert sched_b[0].description and "Site Engineer" in sched_b[0].description
    assert sched_b[0].escalation and "Par" in sched_b[0].escalation
    assert sched_b[1].item_qty == "120.00"
    assert sched_b[11].s_no == "12"
    assert "Technician" in (sched_b[11].description or "")
    # Junk serial 99 after schedule total must not appear
    assert all(p.s_no != "99" for p in products)


def test_railway_products_inline_item_code_layout():
    """Older / cleaned extracts with S.No + ItemCode on one line still work."""
    sample = """
Schedule B-Non-Key Personnel
1 1 360.00 Month 70890.38 25520536.80 AT Par 25520536.80 Rs.
Description:- Site Engineer (S&T),Signal (15 Nos. Each for 24 Months)
2 2 120.00 Month 70890.38 8506845.60 AT Par 8506845.60 Rs.
Description:- Site Engineer (S&T),Telecom (5 Nos. Each for 24 Months)
"""
    products = ProductExtractor().extract(sample)
    assert len(products) == 2
    assert products[0].item_code == "1"
    assert products[0].item_qty == "360.00"
    assert "Site Engineer" in (products[0].description or "")


def test_ns_item_codes_and_numbers_unit():
    """Materials schedule with NS1.. codes and Numbers/Day units (multi-page style)."""
    sample = """
Schedule () A-Supply Items
10 NS1 730.00 Day 954.00 696420.00 AT Par 696420.00
Description:- Hiring of Skilled Labour assistance SE/JE for cable laying
11 NS2 30.00 Numbers 3658.00 109740.00 AT Par 109740.00
Description:- Slave telephone of Electronic LC Gate communication system
12 NS3 6.00 Numbers 14500.00 87000.00 AT Par 87000.00
Description:- Master Telephone of Electronic LC gate communication with Voice Logger
13 NS4 20.00 Numbers 8500.00 170000.00 AT Par 170000.00
Description:- 24V DC Power supply Unit with MF battery for LC Gate Telephone System
14 NS5 4.00 Numbers 12000.00 48000.00 AT Par 48000.00
Description:- Transportation, Installation, testing commissioning of 25 Watt VHF set
15 NS6 20.00 Numbers 9500.00 190000.00 AT Par 190000.00
Description:- Supply of 24F fully loaded FMS suitable for Railway OFC System
16 NS7 50.00 Numbers 450.00 22500.00 AT Par 22500.00
Description:- Supply of Optical fibre Patch cords with SC-LC connectors
17 NS8 5.00 Numbers 35000.00 175000.00 AT Par 175000.00
Description:- Supply of digital Multi meter fluke make model 289
18 NS9 5.00 Numbers 8000.00 40000.00 AT Par 40000.00
Description:- Supply And Transportation of PA screen Cable Roll at Site
19 NS10 1.00 Numbers 185000.00 185000.00 AT Par 185000.00
Description:- Automatic Fusion Splicer for single mode Optical fibre cable
20 NS11 2.00 Numbers 22000.00 44000.00 AT Par 44000.00
Description:- Supply of Optical Power meter wave length 1310 nm and 1550 nm
21 NS12 3.00 Numbers 15000.00 45000.00 AT Par 45000.00
Description:- Supply of Optical Fibre Joint Closure
"""
    products = ProductExtractor().extract(sample)
    assert len(products) >= 12
    assert [p.s_no for p in products[:11]] == [str(i) for i in range(10, 21)]
    assert products[0].item_code == "NS1"
    assert products[0].qty_unit and "Day" in products[0].qty_unit
    assert "Skilled Labour" in (products[0].description or "")
    assert products[1].item_code == "NS2"
    assert products[1].qty_unit and "Number" in products[1].qty_unit
    assert "Slave telephone" in (products[1].description or "")
    assert any(p.item_code == "NS11" for p in products)


def test_split_description_label_on_own_line():
    """PDF text often puts 'Description:-' alone, body on the next line(s)."""
    sample = """
Schedule () A-Supply
1 08 5.00 Numbers 177112.00 885560.00 AT Par 885560.00
Description:-
Supply of Disconnect Terminal Block, Screw less type, as per RDSO Spec
2 09 10.00 Numbers 2500.00 25000.00 AT Par 25000.00
Description:-
Supply of embedded software for RTU to suit GSM/GPRS/4G
3 10 2.00 Numbers 4500.00 9000.00 AT Par 9000.00
Description:- Q-Series Neutral Line Relay (QN1 Type), 12F/4B, 24V DC
"""
    products = ProductExtractor().extract(sample)
    assert len(products) == 3
    assert all((p.description or "").strip() for p in products)
    assert "Disconnect Terminal Block" in (products[0].description or "")
    assert "embedded software" in (products[1].description or "")
    assert "Neutral Line Relay" in (products[2].description or "")
    # Each product keeps its own description (no cross-wiring)
    assert "Disconnect" not in (products[1].description or "")
    assert "Disconnect" not in (products[2].description or "")


def test_description_after_wrapped_bid_and_page_break():
    """Real IREPS extract: Below/P + ar + page header, then Description:-."""
    sample = """
Schedule () A-Supply
Above/
41 5.00 Numbers 177112.00 885560.00 AT Par 885560.00 Below/P
ar
Page 6 of 18 Run Date/Time: 19/06/2026 16:08:28

HOWRAH DIVISION-S AND T/EASTERN RLY
TENDER DOCUMENT
Tender No: SDSTE-LCGATE-05NOs-26 Closing Date/Time: 06/07/2026 15:00
Description:- "Supply of Micro Processor based Remote Terminal Unit (RTU) with 64 digital input &
16 analog input with DOT Matrix Printer. Inspection: RDSO"
Above/
42 5.00 Numbers 19811.00 99055.00 AT Par 99055.00 Below/P
ar
Description:- Installation, wiring, testing & commissioning of Micro Processor based Remote
Terminal Unit (RTU). Inspection: Consignee.
Above/
46 5.00 Numbers 20166.00 100830.00 AT Par 100830.00 Below/P
46 ar
Description:- Testing commissioning of total system including local report system, firewall, static IP
to bring DATA of RTUs through GPRS to CMU
"""
    products = ProductExtractor().extract(sample)
    by_sno = {p.s_no: p for p in products}
    assert "41" in by_sno and "42" in by_sno and "46" in by_sno
    assert by_sno["41"].description and "Remote Terminal Unit" in by_sno["41"].description
    assert by_sno["42"].description and "Installation, wiring" in by_sno["42"].description
    assert by_sno["46"].description and "Testing commissioning" in by_sno["46"].description


def test_station_unit_item_keeps_description():
    """Design/BOQ rows use Qty Unit = Station (not Numbers/Month)."""
    sample = """
Schedule () A-Supply
Above/
114 5.00 Station 58797.00 293985.00 AT Par 293985.00 Below/P
ar
Description:- Design of circuits in connection with preparation of wiring diagrams as per approved
Signalling Plan and submission of indoor as well as outdoor completion documents for alteration
work. It includes Route Section Plan (RSP), Selection Table (ST), Locking Table (LT) and Control Panel
Diagram (CPD). All drawings will be prepared in CAD. Inspection: Consignee.
"""
    products = ProductExtractor().extract(sample)
    assert len(products) == 1
    p = products[0]
    assert p.s_no == "114"
    assert p.qty_unit and "Station" in p.qty_unit
    assert p.description and "Design of circuits" in p.description
    assert "Control Panel" in p.description


def test_products_from_schedule_table():
    headers = [
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
    table = ExtractedTable(
        page_number=1,
        headers=headers,
        rows=[
            ["1", "1", "360.00", "Month", "70890.38", "25520536.80", "AT Par", "25520536.80", "Rs."],
            ["Description:- Site Engineer (S&T),Signal (15 Nos. Each for 24 Months)"],
            ["2", "2", "360.00", "Month", "70890.38", "25520536.80", "AT Par", "25520536.80", "Rs."],
            ["", "", "", "", "", "", "", "", "Description:- Site Engineer (S&T),Telecom"],
        ],
        mapped_headers=TableParser.map_headers(headers),
        is_product_table=True,
    )
    # Pad short continuation rows to header length for realism
    padded = []
    for row in table.rows:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
            if "Description" in row[0]:
                # put description text into a free cell style used by extractor join
                pass
        padded.append(row)
    table.rows = padded

    products = ProductExtractor().extract("", tables=[table])
    assert len(products) == 2
    assert products[0].item_qty == "360.00"
    assert products[0].description and "Site Engineer" in products[0].description


def test_priced_row_fragment_not_glued_onto_previous_description():
    """A wrap of the NEXT item's AT Par / amounts line must not append to the previous description."""
    sample = """
Schedule () A-SOR items - Loop Lines
A 3.00 Numbers 15727.00 47181.00 AT Par 47181.00
Description:- Supply of colour light signal post 3.6 mtrs. and ladder
3
11655.00 AT Par 11655.00
A 5.00 Numbers 2331.00 11655.00 AT Par 11655.00
Description:- Fixing of Calling-on Signal unit on signal post
5
"""
    products = ProductExtractor().extract(sample)
    by_sno = {p.s_no: p for p in products if ProductExtractor._schedule_letter(p.schedule) == "a"}
    assert "3" in by_sno and "5" in by_sno
    desc3 = by_sno["3"].description or ""
    assert "colour light signal" in desc3
    assert "AT Par" not in desc3
    assert "11655.00" not in desc3
    assert "Calling-on" in (by_sno["5"].description or "")

    headers = [
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
    mapped = TableParser.map_headers(headers)
    table = ExtractedTable(
        page_number=1,
        headers=headers,
        rows=[
            ["1", "A", "3.00", "Numbers", "15727.00", "47181.00", "AT Par", "47181.00", ""],
            ["Description:- Supply of colour light signal post 3.6 mtrs."],
            ["", "", "", "", "", "", "AT Par", "11655.00", ""],
            ["2", "A", "5.00", "Numbers", "2331.00", "11655.00", "AT Par", "11655.00", ""],
            ["Description:- Fixing of Calling-on Signal unit"],
        ],
        mapped_headers=mapped,
        is_product_table=True,
    )
    padded = []
    for row in table.rows:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        padded.append(row)
    table.rows = padded
    table_items = ProductExtractor()._from_schedule_table(table)
    first = next(p for p in table_items if p.s_no == "1")
    assert "colour light signal" in (first.description or "")
    assert "AT Par" not in (first.description or "")
    assert "11655.00" not in (first.description or "")


def test_nit_value_span_stops_at_next_label():
    extractor = FieldExtractor()
    labels = [
        ("tender_type", re.compile(r"(?i)^tender\s*type$")),
        ("bidding_system", re.compile(r"(?i)^bidding\s*system$")),
    ]
    assert extractor._collect_value_span(
        ["Tender Type", "Open", "Bidding System", "Single Packet System"],
        1,
        labels,
    ) == "Open"


def test_products_from_generic_boq_table_without_pricing_columns():
    """
    Real-world equipment BOQs often have no Item Code / Rate / Amount columns at
    all — just a serial, description, unit and quantity (optionally broken out
    per site, with a final total Qty column). These must not be silently
    dropped just because they don't match the priced IREPS personnel-schedule
    row shape.
    """
    headers = ["SN", "Description", "Unit", "Mumbai", "Pune", "Qty"]
    table = ExtractedTable(
        page_number=44,
        headers=headers,
        rows=[
            ["", "SCHA: Equipment for Central Railway", "", "", "", ""],
            ["1", "Supply of standard 19in rack mountable router", "Nos.", "30", "8", "38"],
            ["2", "Supply of standard 19in rack mountable switch", "Nos.", "13", "59", "72"],
        ],
        mapped_headers=TableParser.map_headers(headers),
        is_product_table=True,
    )
    products = ProductExtractor().extract("", tables=[table])
    assert len(products) == 2
    assert products[0].s_no == "1"
    assert products[0].item_qty == "38"
    assert products[0].qty_unit == "Nos."
    assert products[0].description and "router" in products[0].description
    assert products[1].s_no == "2"
    assert products[1].item_qty == "72"


def test_documents_and_contact():
    docs = FieldExtractor().extract_documents(GENERIC_SAMPLE)
    contact = FieldExtractor().extract_contact(RAILWAY_SAMPLE)
    assert "GST Certificate" in docs
    assert contact.email and "@" in contact.email


def test_clauses():
    clauses = ClauseExtractor().extract(RAILWAY_SAMPLE)
    types = {c.clause_type for c in clauses}
    assert "Payment Terms" in types


def test_export_json_excel_csv():
    result = TenderResult(
        tender_information=TenderInformation(
            name_of_work="Test Work",
            tender_no="T-1",
        ),
        documents_required=["GST Certificate"],
    )
    exporter = ExportService()
    data = exporter.to_json_str(result)
    assert "name_of_work" in data
    assert "tender_no" in data
    assert len(exporter.to_excel_bytes(result)) > 100
    assert len(exporter.to_csv_bytes(result, which="summary")) > 10


def test_schedule_title_and_item_code_normalization():
    """
    IREPS captions often look like "Schedule () 01-..." or
    "Schedule Schedule 02-...". Item Code is frequently omitted and the
    serial is the code — export should fill itemCode from S.No. in that case.
    """
    assert (
        ProductExtractor._clean_schedule_title("Schedule () 01-Supply Portion-I")
        == "Schedule 01-Supply Portion-I"
    )
    assert (
        ProductExtractor._clean_schedule_title("Schedule Schedule 02-Supply Portion-II")
        == "Schedule 02-Supply Portion-II"
    )
    assert (
        ProductExtractor._clean_schedule_title("Schedule () A-Key Personnels 17395123.20")
        == "Schedule A-Key Personnels"
    )
    assert (
        ProductExtractor._clean_schedule_title(
            "Schedule A-SOR items - Loop Lines 6148966.15 Above/ Below/P ar"
        )
        == "Schedule A-SOR items - Loop Lines"
    )

    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="1",
                item_qty="10.00",
                unit_rate="100.00",
                amount="1000.00",
                description="Supply of Widget A",
                schedule="Schedule () 01-Supply Portion-I",
            ),
            ProductItem(
                s_no="2",
                item_code="NS2",
                item_qty="5.00",
                unit_rate="50.00",
                amount="250.00",
                description="Supply of Widget B",
                schedule="Schedule Schedule 02-Supply Portion-II",
            ),
        ]
    )
    by_sno = {p.s_no: p for p in products}
    assert by_sno["1"].item_code == "1"
    assert by_sno["1"].schedule == "Schedule 01-Supply Portion-I"
    assert by_sno["2"].item_code == "NS2"
    assert by_sno["2"].schedule == "Schedule 02-Supply Portion-II"

    row = ExportService().build_flat_rows(
        TenderResult(
            tender_information=TenderInformation(tender_no="T-1", name_of_work="Work"),
            products=products,
        )
    )[0]
    assert row["itemCode"] == "1"
    assert row["itemCategory"] == "Schedule 01-Supply Portion-I"


def test_ns_item_not_merged_with_same_sno_schedule_a():
    """NS4 must not collapse into Schedule A serial 4 during the sno pass."""
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="4",
                item_code="051160",
                item_qty="100.00",
                qty_unit="Metre",
                unit_rate="10.00",
                amount="1000.00",
                description="Supply of cable",
                schedule="Schedule A-Schedule A - Supply of Cables and relays",
            ),
            ProductItem(
                s_no="4",
                item_code="NS4",
                item_qty="2000.00",
                qty_unit="Metre",
                unit_rate="1.24",
                amount="2480.00",
                description="Placing of warning tape while closing the trench",
                schedule="Schedule A-Schedule A - Supply of Cables and relays",
            ),
            ProductItem(
                s_no="1",
                item_code="NS1",
                item_qty="1.00",
                qty_unit="Numbers",
                unit_rate="10.00",
                amount="10.00",
                description="First NS item",
                schedule="Schedule D-NS SCHEDULE",
            ),
        ]
    )
    by_code = {(p.item_code or "").upper(): p for p in products}
    assert "NS4" in by_code
    assert "051160" in by_code
    ns4 = by_code["NS4"]
    assert ns4.item_qty == "2000.00"
    assert ns4.schedule and "NS" in ns4.schedule
    assert ProductExtractor._schedule_letter(ns4.schedule) == "d"


def test_qty_less_serial_description_folds_into_ns_item():
    """Page-break '21 Description:-' must not sit beside NS21 as a second row."""
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="21",
                item_code="NS21",
                item_qty="7.00",
                qty_unit="Numbers",
                unit_rate="28868.70",
                amount="202080.90",
                description="Supply, installation, testing & commissioning of Wireless Access Point",
                schedule="Schedule D-NS SCHEDULE",
            ),
            ProductItem(
                s_no="21",
                item_code="21",
                description=(
                    "Supply, installation, testing & commissioning of "
                    "Wireless Access Point with 3000 Mbps"
                ),
                schedule="Schedule D-NS SCHEDULE",
            ),
        ]
    )
    matches = [p for p in products if (p.s_no == "21" or (p.item_code or "").upper() == "NS21")]
    assert len(matches) == 1
    ns21 = matches[0]
    assert ns21.item_code == "NS21"
    assert ns21.item_qty == "7.00"
    assert "Wireless Access Point" in (ns21.description or "")


def test_ns_code_amounts_with_serial_on_description_line():
    """IREPS wrap: 'NS4 2000.00 Metre …' then '4 Description:- …'."""
    sample = """
Schedule () A-Supply of Cables 1000.00
1 051160 100.00 Metre 10.00 1000.00 AT Par 1000.00 Rs.
Description:- Supply of cable
Schedule () D-NS SCHEDULE 21824682.99
NS3 1.00 Numbers 100.00 100.00 AT Par 100.00 Rs.
3 Description:- Previous NS item
NS4 2000.00 Metre 1.24 2480.00 AT Par 2480.00 Rs.
4 Description:- Placing of warning tape while closing the trench
NS5 10.00 Numbers 50.00 500.00 AT Par 500.00 Rs.
5 Description:- Next NS item
"""
    products = ProductExtractor().extract(sample)
    by_code = {(p.item_code or "").upper(): p for p in products}
    assert "NS4" in by_code
    assert "NS5" in by_code
    ns4 = by_code["NS4"]
    assert ns4.item_qty == "2000.00"
    assert ns4.unit_rate == "1.24"
    assert ns4.amount == "2480.00"
    assert "warning tape" in (ns4.description or "").lower()
    assert ns4.schedule and "NS" in ns4.schedule
    assert any(
        (p.item_code or "") == "051160"
        or (p.s_no == "1" and p.schedule and "A-Supply" in p.schedule)
        for p in products
    )


def test_ns_continuation_table_keeps_schedule_d():
    """Page-2 NS rows must inherit Schedule D, not leftover Schedule A."""
    headers = [
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
    mapped = TableParser.map_headers(headers)
    table_a = ExtractedTable(
        page_number=1,
        headers=headers,
        rows=[
            ["Schedule () A-Supply of Cables"] + [""] * 8,
            ["1", "051160", "100.00", "Metre", "10.00", "1000.00", "AT Par", "1000.00", "Rs."],
            ["Description:- Supply of cable"] + [""] * 8,
            ["Schedule () D-NS SCHEDULE"] + [""] * 8,
            ["1", "NS1", "1.00", "Numbers", "10.00", "10.00", "AT Par", "10.00", "Rs."],
            ["Description:- First NS"] + [""] * 8,
            ["3", "NS3", "1.00", "Numbers", "100.00", "100.00", "AT Par", "100.00", "Rs."],
            ["Description:- Previous NS item"] + [""] * 8,
        ],
        mapped_headers=mapped,
        is_product_table=True,
    )
    table_cont = ExtractedTable(
        page_number=2,
        headers=headers,
        rows=[
            ["4", "NS4", "2000.00", "Metre", "1.24", "2480.00", "AT Par", "2480.00", "Rs."],
            ["4 Description:- Placing of warning tape while closing the trench"]
            + [""] * 8,
        ],
        mapped_headers=mapped,
        is_product_table=True,
    )
    products = ProductExtractor().extract("", tables=[table_a, table_cont])
    ns4 = next(p for p in products if (p.item_code or "").upper() == "NS4")
    assert ns4.item_qty == "2000.00"
    assert "warning tape" in (ns4.description or "").lower()
    assert ns4.schedule and "NS" in ns4.schedule
    cable = next(p for p in products if (p.item_code or "") == "051160")
    assert cable.schedule and "A-Supply" in cable.schedule


def test_letter_code_page_break_keeps_serial_and_description():
    """A 5.00 Numbers … / page break / 5 Description:- must stay one Schedule A row."""
    sample = """
Schedule () A-SOR items - Loop Lines 1000.00
A 3.00 Numbers 15727.00 47181.00 AT Par 47181.00
Description:- Supply of colour light signal post
1
A 5.00 Numbers 2331.00 11655.00 AT Par 11655.00
Page 1 of 17 Run Date/Time: 11/08/2026 12:27:43
ASANSOL DIVISION-S AND T/EASTERN RLY
TENDER DOCUMENT
Tender No: 44-SDSTE-ASN-2026-27 Closing Date/Time: 03/09/2026 14:00
Description:- Fixing of Calling-on Signal/A-Sign/AG-Sign unit on signal post
5
A 2.00 Numbers 34117.00 68234.00 AT Par 68234.00
6 Description:- Supply of non metallic FRP junction type route indicator
"""
    products = ProductExtractor().extract(sample)
    sched_a = [
        p for p in products
        if ProductExtractor._schedule_letter(p.schedule) == "a"
        and not ProductExtractor._is_breakup_schedule(p.schedule)
    ]
    by_sno = {p.s_no: p for p in sched_a}
    assert "5" in by_sno
    item5 = by_sno["5"]
    assert (item5.item_code or "").upper() == "A"
    assert item5.item_qty == "5.00"
    assert item5.unit_rate == "2331.00"
    assert "Calling-on" in (item5.description or "")
    serials = [p.s_no for p in sched_a]
    assert serials == sorted(serials, key=lambda s: int(s) if (s or "").isdigit() else 0)


def test_item_breakup_does_not_steal_schedule_a_serial():
    """Item 33 annexure rows must not merge into Schedule A item 5."""
    sample = """
Schedule () A-SOR items - Loop Lines 1000.00
A 5.00 Numbers 2331.00 11655.00 AT Par 11655.00
Description:- Fixing of Calling-on Signal unit on signal post
5
Please see Item Breakup for details. 235830.00 AT Par 235830.00
Description:- Earthing of S&T equipment, relay rack and power equipment
33
Schedule () B-NON SOR items 500.00
B 1.00 Numbers 100.00 100.00 AT Par 100.00
1 Description:- Schedule B first item
3. ITEM BREAKUP
Schedule Schedule A-SOR items - Loop Lines
Item- 33 earth shall be connected to copper flat of size 25x2 mm
S No. Item Description of Item Unit Qty Rate Amount
No
1 1 Supply of basic material to construct unitNumbers 10.00 4720.00 47200.00
2 2 Installation of Unit Maintenance Free Earth Numbers 10.00 3540.00 35400.00
5 5 Supply of 1 x 35 Sq.mm. copper power wire Metre 40.00 177.00 7080.00
Total 235830.00
4. ELIGIBILITY CONDITIONS
Standard Financial Criteria
"""
    products = ProductExtractor().extract(sample)
    sched_a = [
        p for p in products
        if ProductExtractor._schedule_letter(p.schedule) == "a"
        and not ProductExtractor._is_breakup_schedule(p.schedule)
    ]
    by_sno = {p.s_no: p for p in sched_a}
    assert by_sno["5"].item_qty == "5.00"
    assert by_sno["5"].qty_unit and "Number" in by_sno["5"].qty_unit
    assert "Calling-on" in (by_sno["5"].description or "")
    assert (by_sno["5"].item_code or "").upper() == "A"
    assert "33" in by_sno
    assert "Earthing" in (by_sno["33"].description or "")
    assert by_sno["33"].amount == "235830.00"
    assert by_sno["33"].item_qty == "1.00"
    assert by_sno["33"].qty_unit == "Numbers"
    assert by_sno["33"].unit_rate == "235830.00"
    assert (by_sno["33"].item_code or "").upper() == "A"
    assert by_sno["33"].parent_s_no is None

    breakup = [p for p in products if ProductExtractor._is_breakup_schedule(p.schedule)]
    assert len(breakup) >= 3
    assert all(p.parent_s_no == "33" for p in breakup)
    assert all(p.schedule == "Item 33 Breakup" for p in breakup)
    br_by = {p.s_no: p for p in breakup}
    assert br_by["5"].item_qty == "40.00"
    assert br_by["5"].qty_unit and "Metre" in br_by["5"].qty_unit
    assert br_by["5"].amount == "7080.00"
    assert all("Breakup" in (p.schedule or "") for p in breakup)

    sched_b = [
        p for p in products if ProductExtractor._schedule_letter(p.schedule) == "b"
    ]
    assert len(sched_b) == 1
    assert sched_b[0].item_qty == "1.00"


def test_qty_less_serial_stub_folds_into_priced_schedule_row():
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="37",
                item_code="A",
                item_qty="6.00",
                qty_unit="Set",
                unit_rate="15147.00",
                amount="90882.00",
                schedule="Schedule A-SOR items",
            ),
            ProductItem(
                s_no="37",
                item_code="37",
                description="Supply of basic material to construct unit maintenance free earth",
                schedule="Schedule A-SOR items",
            ),
        ]
    )
    matches = [p for p in products if p.s_no == "37"]
    assert len(matches) == 1
    row = matches[0]
    assert row.item_qty == "6.00"
    assert (row.item_code or "").upper() == "A"
    assert "maintenance free earth" in (row.description or "")


def test_identical_amounts_keep_distinct_letter_code_serials():
    """Two Schedule A rows can share qty/rate/amount and must not collapse."""
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="51",
                item_code="A",
                item_qty="50.00",
                qty_unit="Numbers",
                unit_rate="10.00",
                amount="500.00",
                description="Supply of end plate 2.5 mm",
                schedule="Schedule A-SOR items",
            ),
            ProductItem(
                s_no="52",
                item_code="A",
                item_qty="50.00",
                qty_unit="Numbers",
                unit_rate="10.00",
                amount="500.00",
                description="Supply of end stopper 10mm",
                schedule="Schedule A-SOR items",
            ),
        ]
    )
    by_sno = {p.s_no: p for p in products}
    assert "51" in by_sno and "52" in by_sno
    assert "end plate" in (by_sno["51"].description or "")
    assert "end stopper" in (by_sno["52"].description or "")


def test_breakup_parent_qty_from_location_count():
    """Lump-sum Item-N parent fills qty from '01 no. location' so qty × rate = amount."""
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="33",
                item_code="A",
                amount="235830.00",
                description=(
                    "Earthing of S&T equipment, relay rack and power equipment "
                    "to be done at 01 no. location (station/cabin/hut)."
                ),
                schedule="Schedule A-SOR items - Loop Lines",
            ),
            ProductItem(
                s_no="1",
                item_code="1",
                item_qty="10.00",
                qty_unit="Numbers",
                unit_rate="4720.00",
                amount="47200.00",
                description="Supply of basic material to construct unit",
                schedule="Item 33 Breakup",
            ),
            ProductItem(
                s_no="2",
                item_code="2",
                item_qty="10.00",
                qty_unit="Numbers",
                unit_rate="3540.00",
                amount="35400.00",
                description="Installation of Unit Maintenance Free Earth",
                schedule="Item 33 Breakup",
            ),
        ]
    )
    parent = next(p for p in products if p.s_no == "33" and not ProductExtractor._is_breakup_schedule(p.schedule))
    children = [p for p in products if ProductExtractor._is_breakup_schedule(p.schedule)]
    assert parent.item_qty == "1.00"
    assert parent.qty_unit == "Numbers"
    assert parent.unit_rate == "235830.00"
    assert parent.amount == "235830.00"
    assert parent.parent_s_no is None
    assert len(children) == 2
    assert all(p.parent_s_no == "33" for p in children)
    assert all(p.schedule == "Item 33 Breakup" for p in children)


