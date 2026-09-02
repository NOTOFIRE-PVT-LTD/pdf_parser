"""
Pydantic data models for Tender Parser.

All extraction results flow through these schemas so exporters, UI, and
future AI plugins share one contract. Missing fields are always null.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PdfType(str, Enum):
    TEXT = "text"
    SCANNED = "scanned"
    MIXED = "mixed"
    ENCRYPTED = "encrypted"
    UNKNOWN = "unknown"


class ExtractionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    OCR_REQUIRED = "ocr_required"
    OCR_FAILED = "ocr_failed"
    PASSWORD_REQUIRED = "password_required"
    COMPLETED = "completed"
    FAILED = "failed"


class TenderInformation(BaseModel):
    """
    Primary tender header fields (IREPS / Railway NIT style).

    Example source layout:
      MUMBAI CENTRAL DIVISION-S AND T/WESTERN RLY
      Tender No: ...    Closing Date/Time: ...
      Name of Work / Advertised Value / Earnest Money / etc.
    """

    name_of_work: str | None = None
    tender_no: str | None = None
    closing_date_time: str | None = None
    division_name: str | None = None
    zone: str | None = None
    advertised_value: str | None = None
    earnest_money: str | None = None
    period_of_completion: str | None = None
    number_of_jv_member_allowed: str | None = None
    # NIT header extras (IREPS-style) for flat Excel export
    bidding_type: str | None = None
    tender_type: str | None = None
    contract_type: str | None = None
    tender_doc_cost: str | None = None
    bid_validity_days: str | None = None
    bidding_system: str | None = None
    pre_bid_conference: str | None = None
    bidding_style: str | None = None
    contract_category: str | None = None
    published_date: str | None = None
    reference_no: str | None = None
    status: str | None = None


class FinancialInfo(BaseModel):
    estimated_value: str | None = None
    emd: str | None = None
    tender_fee: str | None = None
    security_deposit: str | None = None
    performance_guarantee: str | None = None
    currency: str | None = None


class ImportantDates(BaseModel):
    published_date: str | None = None
    start_date: str | None = None
    bid_submission_start: str | None = None
    bid_submission_end: str | None = None
    technical_bid_opening: str | None = None
    financial_bid_opening: str | None = None
    pre_bid_meeting: str | None = None
    completion_period: str | None = None


class EligibilityInfo(BaseModel):
    minimum_turnover: str | None = None
    years_of_experience: str | None = None
    similar_work_required: str | None = None
    oem_authorization: str | None = None
    msme_exemption: str | None = None
    startup_exemption: str | None = None


class ProductItem(BaseModel):
    """
    Schedule / BOQ line item (Railway IREPS style).

    Table columns typically:
      S.No. | Item Code | Item Qty | Qty Unit | Unit Rate |
      Basic Value | Escl.(%) | Amount | Bidding Unit
    with Description:- on the next row.
    """

    s_no: str | None = None
    item_code: str | None = None
    item_qty: str | None = None
    qty_unit: str | None = None
    unit_rate: str | None = None
    basic_value: str | None = None
    escalation: str | None = None
    amount: str | None = None
    bidding_unit: str | None = None
    description: str | None = None
    product_name: str | None = None
    schedule: str | None = None
    page_numbers: list[int] = Field(default_factory=list)

    # Internal-only: absolute character offset of this item in the extracted
    # document text, used solely to order rows that have no S.No. by their
    # true document position instead of by which extraction pass found them.
    # Never serialized (not part of the JSON/Excel/CSV export contract).
    source_pos: int | None = Field(default=None, exclude=True, repr=False)

    # Backward-compatible aliases used by search / older UI helpers
    @property
    def item_number(self) -> str | None:
        return self.s_no

    @property
    def product_name_display(self) -> str | None:
        return self.product_name or self.description

    @property
    def quantity(self) -> str | None:
        return self.item_qty

    @property
    def unit(self) -> str | None:
        return self.qty_unit


class ImportantClause(BaseModel):
    clause_type: str
    title: str | None = None
    content: str | None = None
    page_number: int | None = None


class ContactDetails(BaseModel):
    officer_name: str | None = None
    phone: str | None = None
    email: str | None = None
    office_address: str | None = None
    website: str | None = None


class DocumentMeta(BaseModel):
    """Metadata about the source PDF and processing path."""

    filename: str
    page_count: int = 0
    pdf_type: PdfType = PdfType.UNKNOWN
    ocr_used: bool = False
    encrypted: bool = False
    file_size_bytes: int | None = None
    processed_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class TenderResult(BaseModel):
    """
    Canonical extraction output.

    Primary sections for Railway tenders:
      tender_information, products
    Other sections remain for general NITs / future enrichment.
    """

    status: ExtractionStatus = ExtractionStatus.PENDING
    meta: DocumentMeta | None = None
    tender_information: TenderInformation = Field(default_factory=TenderInformation)
    financial: FinancialInfo = Field(default_factory=FinancialInfo)
    dates: ImportantDates = Field(default_factory=ImportantDates)
    eligibility: EligibilityInfo = Field(default_factory=EligibilityInfo)
    products: list[ProductItem] = Field(default_factory=list)
    documents_required: list[str] = Field(default_factory=list)
    important_clauses: list[ImportantClause] = Field(default_factory=list)
    contact_details: ContactDetails = Field(default_factory=ContactDetails)
    raw_text: str | None = Field(default=None, exclude=True)
    tables: list[dict[str, Any]] = Field(default_factory=list, exclude=True)

    def to_export_dict(self) -> dict[str, Any]:
        """JSON/Excel-ready payload without internal-only fields."""
        return {
            "tender_information": self.tender_information.model_dump(),
            "financial": self.financial.model_dump(),
            "dates": self.dates.model_dump(),
            "eligibility": self.eligibility.model_dump(),
            "products": [p.model_dump() for p in self.products],
            "documents_required": self.documents_required,
            "important_clauses": [c.model_dump() for c in self.important_clauses],
            "contact_details": self.contact_details.model_dump(),
        }
