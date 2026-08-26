"""Shared regex patterns and field keyword maps for rule-based extraction."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Money / amounts (INR-focused, currency-agnostic enough for most NITs)
# ---------------------------------------------------------------------------
AMOUNT_PATTERN = re.compile(
    r"(?:(?:Rs\.?|INR|₹|USD|\$|EUR|€)\s*)?"
    r"(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?:Lakhs?|Lacs?|Crores?|Cr\.?|Million|Mn|Billion))?",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}"
    r"|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{2,4}"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{2,4})\b",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(
    r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,5}\)?[\s\-]?)?\d{5,10}"
)
URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+",
    re.IGNORECASE,
)

# Label → value on same / next line
LABEL_VALUE = re.compile(
    r"(?P<label>{labels})\s*[:\-–]\s*(?P<value>.+?)(?:\n|$)",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Field keyword maps (first matching label wins)
# Tuned for IREPS / Indian Railway tender documents
# ---------------------------------------------------------------------------
TENDER_INFO_KEYWORDS: dict[str, list[str]] = {
    "name_of_work": [
        r"name\s+of\s+(?:the\s+)?work",
        r"work\s+description",
        r"tender\s*(?:title|name|subject)",
        r"subject",
    ],
    "tender_no": [
        r"tender\s*(?:no\.?|number|#)",
        r"tender\s+ref(?:erence)?",
        r"enquiry\s*(?:no\.?|number)",
    ],
    "closing_date_time": [
        r"closing\s*date\s*/?\s*time",
        r"closing\s*date(?:\s*and\s*time)?",
        r"bid\s*submission\s*(?:end|closing|last|due)",
        r"(?:last|due|closing)\s*date\s*(?:of\s*)?(?:bid|tender)?\s*submission",
        r"due\s*date(?:\s*/?\s*time)?",
    ],
    "division_name": [
        r"division\s*name",
        r"\bdivision\b",
    ],
    "zone": [
        r"\bzone\b",
        r"railway\s*zone",
    ],
    "advertised_value": [
        r"advertised\s*value",
        r"estimated\s*(?:cost|value|amount)",
        r"tender\s*(?:value|cost|amount)",
        r"approx(?:imate)?\s*(?:cost|value)",
        r"project\s*cost",
    ],
    "earnest_money": [
        r"earnest\s*money(?:\s*\(?\s*rs\.?\s*\)?)?",
        r"e\.?m\.?d\.?",
        r"bid\s*security",
    ],
    "period_of_completion": [
        r"period\s+of\s+completion",
        r"completion\s*(?:period|time|schedule)",
        r"delivery\s*period",
        r"contract\s*period",
        r"time\s*(?:of|for)\s*completion",
    ],
    "number_of_jv_member_allowed": [
        r"number\s+of\s+jv\s+member(?:s)?\s+allowed",
        r"jv\s+member(?:s)?\s+allowed",
        r"no\.?\s+of\s+jv\s+member(?:s)?\s+allowed",
        r"maximum\s+(?:no\.?\s+of\s+)?jv\s+members?",
    ],
}

# Header like: MUMBAI CENTRAL DIVISION-S AND T/WESTERN RLY
DIVISION_ZONE_HEADER = re.compile(
    r"(?m)^(?P<div>[A-Z0-9][A-Z0-9 &\-.,]{4,}?)\s*/\s*(?P<zone>[A-Z0-9][A-Z0-9 &\-.,]{1,40})\s*$"
)

FINANCIAL_KEYWORDS: dict[str, list[str]] = {
    "estimated_value": [
        r"estimated\s*(?:cost|value|amount)",
        r"tender\s*(?:value|cost|amount)",
        r"approx(?:imate)?\s*(?:cost|value)",
        r"project\s*cost",
        r"total\s*(?:estimated\s*)?(?:cost|value)",
    ],
    "emd": [
        r"e\.?m\.?d\.?",
        r"earnest\s*money(?:\s*deposit)?",
        r"bid\s*security",
    ],
    "tender_fee": [
        r"tender\s*(?:fee|document\s*fee|cost)",
        r"cost\s+of\s+tender\s+document",
        r"bid\s*processing\s*fee",
        r"document\s*fee",
    ],
    "security_deposit": [
        r"security\s*deposit",
        r"s\.?d\.?",
        r"retention\s*money",
    ],
    "performance_guarantee": [
        r"performance\s*(?:guarantee|security|bank\s*guarantee)",
        r"p\.?b\.?g\.?",
        r"performance\s*bond",
    ],
    "currency": [
        r"currency",
        r"in\s+(?:indian\s+)?rupees?",
    ],
}

DATE_KEYWORDS: dict[str, list[str]] = {
    "published_date": [
        r"published?\s*(?:date|on)",
        r"date\s+of\s+(?:publication|issue|release)",
        r"tender\s*(?:publish|issue)\s*date",
        r"nit\s*date",
    ],
    "start_date": [
        r"(?:tender|work)\s*start\s*date",
        r"commencement\s*date",
        r"date\s+of\s+commencement",
    ],
    "bid_submission_start": [
        r"bid\s*submission\s*start",
        r"start\s*(?:date\s+)?(?:for\s+)?(?:bid|tender)\s*submission",
        r"document\s*download\s*start",
        r"sale\s+(?:of\s+)?(?:tender\s+)?(?:document|form)\s*(?:from|start)",
    ],
    "bid_submission_end": [
        r"bid\s*submission\s*(?:end|closing|last|due)",
        r"(?:last|due|closing)\s*date\s*(?:of\s*)?(?:bid|tender)?\s*submission",
        r"due\s*date",
        r"closing\s*date",
        r"deadline\s*(?:for\s*)?(?:submission|bid)",
    ],
    "technical_bid_opening": [
        r"technical\s*bid\s*opening",
        r"opening\s*(?:of\s*)?technical\s*bid",
        r"tech(?:nical)?\s*bid\s*open",
    ],
    "financial_bid_opening": [
        r"financial\s*bid\s*opening",
        r"opening\s*(?:of\s*)?financial\s*bid",
        r"price\s*bid\s*opening",
        r"commercial\s*bid\s*opening",
    ],
    "pre_bid_meeting": [
        r"pre[-\s]?bid\s*(?:meeting|conference|date)",
        r"pre[-\s]?tender\s*meeting",
    ],
    "completion_period": [
        r"completion\s*(?:period|time|schedule)",
        r"delivery\s*period",
        r"contract\s*period",
        r"time\s*(?:of|for)\s*completion",
        r"period\s+of\s+completion",
    ],
}

ELIGIBILITY_KEYWORDS: dict[str, list[str]] = {
    "minimum_turnover": [
        r"minimum\s*(?:annual\s*)?turnover",
        r"average\s*(?:annual\s*)?turnover",
        r"turnover\s*(?:of|should|must|criterion)",
    ],
    "years_of_experience": [
        r"(?:years?|yrs?)\s+of\s+experience",
        r"minimum\s*experience",
        r"experience\s*(?:of|required|criteria)",
    ],
    "similar_work_required": [
        r"similar\s*(?:work|nature|project)",
        r"works?\s+of\s+similar\s+nature",
        r"past\s+performance",
    ],
    "oem_authorization": [
        r"oem\s*(?:authorization|authorisation|certificate)",
        r"manufacturer(?:'?s)?\s*(?:authorization|authorisation)",
        r"mak",
    ],
    "msme_exemption": [
        r"msme\s*(?:exemption|exempt)",
        r"ssi\s*(?:exemption|exempt)",
        r"exemption\s*(?:for|to)\s*msme",
    ],
    "startup_exemption": [
        r"startup\s*(?:exemption|exempt)",
        r"exemption\s*(?:for|to)\s*startup",
        r"dpiit\s*(?:recognized|recognised)?\s*startup",
    ],
}

CONTACT_KEYWORDS: dict[str, list[str]] = {
    "officer_name": [
        r"(?:contact|nodal|tender)\s*(?:person|officer|authority)",
        r"officer\s*name",
        r"name\s+of\s+(?:the\s+)?(?:officer|engineer|executive)",
        r"for\s+(?:further\s+)?(?:information|queries|clarification)",
    ],
    "phone": [
        r"(?:phone|tel(?:ephone)?|mobile|contact)\s*(?:no\.?|number|#)?",
        r"landline",
    ],
    "email": [
        r"e-?mail(?:\s*(?:id|address))?",
    ],
    "office_address": [
        r"office\s*address",
        r"correspondence\s*address",
        r"postal\s*address",
    ],
    "website": [
        r"website",
        r"web\s*site",
        r"portal\s*url",
        r"e-?procurement\s*(?:portal|website)",
    ],
}

# Clause section headings
CLAUSE_HEADINGS: dict[str, list[str]] = {
    "Payment Terms": [
        r"payment\s*terms?",
        r"terms?\s+of\s+payment",
        r"mode\s+of\s+payment",
    ],
    "Delivery Terms": [
        r"delivery\s*terms?",
        r"delivery\s*(?:schedule|period|conditions?)",
        r"supply\s*terms?",
    ],
    "Warranty": [
        r"warranty",
        r"guarantee\s*(?:period|clause)",
        r"defect\s*liability",
    ],
    "Penalty Clause": [
        r"penalty(?:\s*clause)?",
        r"penalties",
    ],
    "LD Clause": [
        r"liquidated\s*damages?",
        r"\bld\b",
        r"l\.?\s*d\.?\s*clause",
    ],
    "Inspection": [
        r"inspection(?:\s*(?:and|&)\s*testing)?",
        r"quality\s*(?:inspection|assurance|control)",
        r"acceptance\s*testing",
    ],
    "Eligibility Conditions": [
        r"eligibility\s*(?:criteria|conditions?)",
        r"pre[-\s]?qualification",
        r"bidder\s*(?:eligibility|qualification)",
    ],
    "Disqualification Conditions": [
        r"disqualification",
        r"rejection\s*(?:of\s*bids?|criteria)",
        r"grounds?\s+for\s+(?:rejection|disqualification)",
    ],
    "Scope of Work": [
        r"scope\s+of\s+(?:work|supply|services?)",
        r"statement\s+of\s+work",
        r"\bsow\b",
    ],
    "Special Conditions": [
        r"special\s*conditions?(?:\s+of\s+contract)?",
        r"\bscc\b",
        r"additional\s*conditions?",
    ],
    "Technical Requirements": [
        r"technical\s*(?:requirements?|specifications?|criteria)",
        r"functional\s*requirements?",
    ],
}

# Documents commonly required in Indian tenders / NITs
DOCUMENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GST Certificate", re.compile(r"\bgst(?:in)?\b|\bg\.?s\.?t\.?\b", re.I)),
    ("PAN Card", re.compile(r"\bpan\b|\bpermanent\s+account\s+number\b", re.I)),
    ("ITR", re.compile(r"\bitr\b|\bincome\s+tax\s+return", re.I)),
    ("Balance Sheet", re.compile(r"balance\s*sheets?", re.I)),
    ("CA Certificate", re.compile(r"\bca\s*certificate|chartered\s+accountant", re.I)),
    ("Work Order", re.compile(r"work\s*orders?", re.I)),
    ("Completion Certificate", re.compile(r"completion\s*certificates?", re.I)),
    ("ISO Certificate", re.compile(r"\biso\s*\d*|iso\s*certificate", re.I)),
    ("MSME Certificate", re.compile(r"\bmsme\b|\budiyam\b|\bssi\s*certificate", re.I)),
    ("EPF", re.compile(r"\bepf\b|\bprovident\s+fund\b", re.I)),
    ("ESI", re.compile(r"\besi\b|\bemployees['’]?\s+state\s+insurance", re.I)),
    ("Solvency Certificate", re.compile(r"solvency\s*certificate", re.I)),
    ("Power of Attorney", re.compile(r"power\s+of\s+attorney|\bpoa\b", re.I)),
    ("Aadhaar", re.compile(r"\baadhaar\b|\baadhar\b", re.I)),
    ("Cancelled Cheque", re.compile(r"cancelled\s*cheque", re.I)),
    ("Bank Details", re.compile(r"bank\s*(?:details|account|mandate)", re.I)),
    ("Authorization Letter", re.compile(r"authori[sz]ation\s*(?:letter|certificate)", re.I)),
    ("Partnership Deed", re.compile(r"partnership\s*deed", re.I)),
    ("Company Registration", re.compile(r"(?:company|firm)\s*registration|certificate\s+of\s+incorporation", re.I)),
    ("Experience Certificate", re.compile(r"experience\s*certificate", re.I)),
]

# Product / schedule table header synonyms (Railway IREPS + generic BOQ)
PRODUCT_HEADER_ALIASES: dict[str, list[str]] = {
    "s_no": [
        "sl", "s.no", "s.no.", "sno", "sn", "sr", "sr.no", "sr.no.",
        "item no", "item no.", "item#", "#", "no", "no.",
    ],
    "item_code": ["item code", "itemcode", "code", "item id"],
    "item_qty": ["item qty", "qty", "quantity", "qnty", "nos", "no.s", "approx qty"],
    "qty_unit": ["qty unit", "unit", "uom", "unit of measure", "unit of measurement", "units"],
    "unit_rate": ["unit rate", "rate", "unitrate"],
    "basic_value": ["basic value", "basicvalue", "basic amt", "basic amount"],
    "escalation": ["escl", "escl.(%)", "escl(%)", "escalation", "escl %"],
    "amount": ["amount", "amt", "total amount"],
    "bidding_unit": ["bidding unit", "bid unit"],
    "description": [
        "description",
        "particulars",
        "particular",
        "details",
        "item description",
        "name of work",
        "item name",
        "product",
        "product name",
    ],
}

PRODUCT_SECTION_HINTS = re.compile(
    r"(?:bill\s+of\s+quantit(?:y|ies)|\bboq\b|schedule\s*(?:\([a-z0-9]*\))?"
    r"|schedule\s+of\s+(?:items?|rates?|quantit)"
    r"|list\s+of\s+(?:items?|materials?|products?|goods)"
    r"|items?\s+(?:to\s+be\s+)?(?:procured|supplied|purchased)"
    r"|scope\s+of\s+supply|annexure\s*[-\s]*[a-z0-9]*\s*(?:items?|boq))",
    re.IGNORECASE,
)

# IREPS schedule item description line.
# Desc may be empty when the body starts on the next line:
#   Description:-
#   Supply of Disconnect Terminal Block...
DESCRIPTION_LINE = re.compile(
    r"(?im)^\s*description\s*[:\-–]\s*(?P<desc>.*?)\s*$"
)

# Numeric schedule data row (fallback when table extraction is weak)
SCHEDULE_DATA_ROW = re.compile(
    r"(?m)^\s*(?P<sno>\d+)\s+"
    r"(?P<code>[A-Za-z]{1,6}\d{1,5}|\d+)\s+"
    r"(?P<qty>[\d,]+\.?\d*)\s+"
    r"(?P<unit>[A-Za-z%]+)\s+"
    r"(?P<rate>[\d,]+\.?\d*)\s+"
    r"(?P<basic>[\d,]+\.?\d*)\s+"
    r"(?P<escl>AT\s*Par|[\d,]+\.?\d*%?)\s+"
    r"(?P<amount>[\d,]+\.?\d*)\s*"
    r"(?P<bidunit>Rs\.?|INR|\$)?",
    re.IGNORECASE,
)
