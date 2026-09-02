"""
Unit tests for field / product extraction (no PDF binaries required).
Run: pytest -q
"""

from __future__ import annotations

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
