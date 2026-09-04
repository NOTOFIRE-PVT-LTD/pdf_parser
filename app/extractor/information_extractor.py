"""
Information extractor — AI-first PDF analysis + rule-based fallback.

Each tender PDF layout differs. When AI keys are configured, the model reads
the document and saved user instructions to extract products. Rule extractors
run only as fallback / merge seed when AI is unavailable or returns nothing.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.extractor.clause_extractor import ClauseExtractor
from app.extractor.field_extractor import FieldExtractor
from app.extractor.product_extractor import ProductExtractor
from app.models.schemas import DocumentMeta, ProductItem, TenderInformation, TenderResult
from app.parser.table_parser import ExtractedTable
from app.services.ai.pdf_extract_agent import ai_available, extract_with_ai
from app.services.product_sanitize import sanitize_products

logger = logging.getLogger(__name__)


class InformationExtractor:
    """AI-first tender extraction with offline rule fallback."""

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
        settings = get_settings()
        warnings: list[str] = []

        # Always gather lightweight rule-based header/financial as seed
        rule_info = self.fields.extract_tender_information(text, tables=tables)
        financial = self.fields.extract_financial(text)
        dates = self.fields.extract_dates(text)
        eligibility = self.fields.extract_eligibility(text)
        contact = self.fields.extract_contact(text)
        documents = self.fields.extract_documents(text)
        clauses = self.clauses.extract(text)

        products: list[ProductItem] = []
        info = rule_info

        if ai_available(settings):
            try:
                ai_info, ai_products, err = extract_with_ai(text, tables=tables, settings=settings)
                if ai_products:
                    products = sanitize_products(ai_products)
                    info = self._merge_info(rule_info, ai_info)
                    logger.info("AI extract: %s products", len(products))
                else:
                    warnings.append(
                        f"AI extract returned no products ({err or 'empty'}); using rule parser."
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"AI extract failed: {exc}")
                logger.warning("AI extract failed: %s", exc)

        if not products:
            products = sanitize_products(self.products.extract(text, tables=tables))
            if not warnings and ai_available(settings):
                warnings.append("Fell back to rule-based product extraction.")

        result = TenderResult(
            tender_information=info,
            financial=financial,
            dates=dates,
            eligibility=eligibility,
            contact_details=contact,
            documents_required=documents,
            products=products,
            important_clauses=clauses,
            raw_text=text,
        )
        if meta is not None:
            result.meta = meta
            if warnings:
                meta.warnings.extend(warnings)
        return result

    @staticmethod
    def _merge_info(
        rule: TenderInformation,
        ai: TenderInformation | None,
    ) -> TenderInformation:
        """Prefer non-empty AI fields; keep rule values when AI left null."""
        if ai is None:
            return rule
        data = rule.model_dump()
        for key, value in ai.model_dump().items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            cur = data.get(key)
            if cur is None or (isinstance(cur, str) and len(str(value)) >= len(str(cur))):
                data[key] = value
        return TenderInformation(**data)
