"""
Information extractor — orchestrates field, product, and clause extraction.

Single entry point for rule-based parsing from PDF text + tables.
"""

from __future__ import annotations

from app.extractor.clause_extractor import ClauseExtractor
from app.extractor.field_extractor import FieldExtractor
from app.extractor.product_extractor import ProductExtractor
from app.models.schemas import DocumentMeta, TenderResult
from app.parser.table_parser import ExtractedTable
from app.services.product_sanitize import sanitize_products


class InformationExtractor:
    """Rule-based tender information extraction."""

    def __init__(
        self,
        field_extractor: FieldExtractor | None = None,
        product_extractor: ProductExtractor | None = None,
        clause_extractor: ClauseExtractor | None = None,
    ) -> None:
        self.fields = field_extractor or FieldExtractor()
        self.products = product_extractor or ProductExtractor()
        self.clauses = clause_extractor or ClauseExtractor()

    def extract(
        self,
        text: str,
        tables: list[ExtractedTable] | None = None,
        meta: DocumentMeta | None = None,
    ) -> TenderResult:
        result = TenderResult(
            tender_information=self.fields.extract_tender_information(text, tables=tables),
            financial=self.fields.extract_financial(text),
            dates=self.fields.extract_dates(text),
            eligibility=self.fields.extract_eligibility(text),
            contact_details=self.fields.extract_contact(text),
            documents_required=self.fields.extract_documents(text),
            products=sanitize_products(self.products.extract(text, tables=tables)),
            important_clauses=self.clauses.extract(text),
            raw_text=text,
        )
        if meta is not None:
            result.meta = meta
        return result
