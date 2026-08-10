"""
Information extractor façade.

Orchestrates field, product, clause, and document extraction from text + tables.
Optional AI enricher can refine results without changing call sites.
"""

from __future__ import annotations

from typing import Callable

from app.extractor.clause_extractor import ClauseExtractor
from app.extractor.field_extractor import FieldExtractor
from app.extractor.product_extractor import ProductExtractor
from app.models.schemas import DocumentMeta, TenderResult
from app.parser.table_parser import ExtractedTable
from app.services.ai.base import AIExtractionService
from app.services.ai.merge import sanitize_products


class InformationExtractor:
    """Rule-based extractor with an optional AI enrichment hook."""

    def __init__(
        self,
        field_extractor: FieldExtractor | None = None,
        product_extractor: ProductExtractor | None = None,
        clause_extractor: ClauseExtractor | None = None,
        ai_service: AIExtractionService | None = None,
    ) -> None:
        self.fields = field_extractor or FieldExtractor()
        self.products = product_extractor or ProductExtractor()
        self.clauses = clause_extractor or ClauseExtractor()
        self.ai_service = ai_service

    def extract(
        self,
        text: str,
        tables: list[ExtractedTable] | None = None,
        enrich_with_ai: bool = False,
        meta: DocumentMeta | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> TenderResult:
        products = sanitize_products(self.products.extract(text, tables=tables))
        result = TenderResult(
            tender_information=self.fields.extract_tender_information(text, tables=tables),
            financial=self.fields.extract_financial(text),
            dates=self.fields.extract_dates(text),
            eligibility=self.fields.extract_eligibility(text),
            contact_details=self.fields.extract_contact(text),
            documents_required=self.fields.extract_documents(text),
            products=products,
            important_clauses=self.clauses.extract(text),
            raw_text=text,
        )
        # Attach meta before AI enrichment (not after) so chunk progress
        # warnings the AI service appends actually land somewhere visible,
        # instead of being written to a meta object the caller replaces later.
        if meta is not None:
            result.meta = meta

        if enrich_with_ai and self.ai_service and self.ai_service.is_enabled():
            result = self.ai_service.enrich(result, text, progress_callback=progress_callback)
            # Final cleanup after AI merge
            result.products = sanitize_products(result.products)

        return result
