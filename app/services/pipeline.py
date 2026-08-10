"""
Main tender processing pipeline.

PDF → detect type → OCR if needed → tables → information extraction → result.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.extractor.information_extractor import InformationExtractor
from app.models.schemas import DocumentMeta, ExtractionStatus, PdfType, TenderResult
from app.ocr.ocr_service import OcrService
from app.parser.pdf_parser import PdfParser
from app.parser.table_parser import TableParser
from app.services.ai.factory import get_ai_service
from app.utils.text_utils import clean_text

logger = logging.getLogger(__name__)


class TenderPipeline:
    """End-to-end tender extraction pipeline (offline + optional online AI)."""

    def __init__(
        self,
        settings: Settings | None = None,
        pdf_parser: PdfParser | None = None,
        ocr_service: OcrService | None = None,
        table_parser: TableParser | None = None,
        information_extractor: InformationExtractor | None = None,
        ai_service=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pdf_parser = pdf_parser or PdfParser(self.settings)
        self.ocr_service = ocr_service or OcrService(self.settings, self.pdf_parser)
        self.table_parser = table_parser or TableParser(self.settings)
        ai = ai_service if ai_service is not None else get_ai_service(self.settings)
        self.extractor = information_extractor or InformationExtractor(ai_service=ai)

    def process(
        self,
        path: str | Path,
        password: str | None = None,
        enrich_with_ai: bool = False,
        progress_callback=None,
    ) -> TenderResult:
        """
        Process a single PDF.

        progress_callback: optional callable(stage: str, fraction: float)
        """
        path = Path(path)
        meta = DocumentMeta(
            filename=path.name,
            file_size_bytes=path.stat().st_size if path.exists() else None,
            processed_at=datetime.now(timezone.utc),
        )

        def progress(stage: str, frac: float) -> None:
            if progress_callback:
                progress_callback(stage, frac)

        progress("Opening PDF", 0.05)
        pdf_doc = self.pdf_parser.open_and_parse(path, password=password)

        if pdf_doc.password_required:
            return TenderResult(
                status=ExtractionStatus.PASSWORD_REQUIRED,
                meta=DocumentMeta(
                    filename=path.name,
                    pdf_type=PdfType.ENCRYPTED,
                    encrypted=True,
                    error=pdf_doc.error,
                    processed_at=meta.processed_at,
                    file_size_bytes=meta.file_size_bytes,
                ),
            )

        if pdf_doc.error and not pdf_doc.pages:
            return TenderResult(
                status=ExtractionStatus.FAILED,
                meta=DocumentMeta(
                    filename=path.name,
                    error=pdf_doc.error,
                    processed_at=meta.processed_at,
                    file_size_bytes=meta.file_size_bytes,
                ),
            )

        meta.page_count = pdf_doc.page_count
        meta.pdf_type = pdf_doc.pdf_type
        meta.encrypted = pdf_doc.encrypted

        text = pdf_doc.full_text
        ocr_used = False
        ocr_pages = None

        # OCR for scanned / mixed pages with little text
        if pdf_doc.pdf_type in (PdfType.SCANNED, PdfType.MIXED):
            progress("Running OCR", 0.25)
            page_targets = None
            if pdf_doc.pdf_type == PdfType.MIXED:
                page_targets = [
                    p.page_number
                    for p in pdf_doc.pages
                    if p.is_image_heavy or p.char_count < self.settings.text_density_threshold
                ]
            ocr_result = self.ocr_service.ocr_pdf(
                path, page_numbers=page_targets, password=password
            )
            if not ocr_result.success:
                # For fully scanned PDFs, OCR failure is fatal for extraction quality
                if pdf_doc.pdf_type == PdfType.SCANNED and not text.strip():
                    meta.error = ocr_result.error
                    meta.warnings.append(ocr_result.error or "OCR failed")
                    return TenderResult(status=ExtractionStatus.OCR_FAILED, meta=meta)
                meta.warnings.append(ocr_result.error or "OCR partially failed")
            else:
                ocr_used = True
                ocr_pages = ocr_result.pages
                if pdf_doc.pdf_type == PdfType.SCANNED:
                    text = ocr_result.full_text
                else:
                    # Merge OCR text into sparse pages
                    ocr_map = {p.page_number: p.text for p in ocr_result.pages}
                    chunks: list[str] = []
                    for page in pdf_doc.pages:
                        if page.page_number in ocr_map and (
                            page.is_image_heavy
                            or page.char_count < self.settings.text_density_threshold
                        ):
                            chunks.append(ocr_map[page.page_number])
                        else:
                            chunks.append(page.text)
                    text = clean_text("\n\n".join(chunks))

        meta.ocr_used = ocr_used

        # Inject page markers for clause page hints
        progress("Extracting tables", 0.55)
        if ocr_used and pdf_doc.pdf_type == PdfType.SCANNED and ocr_pages:
            text = clean_text(
                "\n\n".join(f"[[PAGE:{p.page_number}]]\n{p.text}" for p in ocr_pages)
            )
        elif pdf_doc.pages:
            marked_parts = [
                f"[[PAGE:{page.page_number}]]\n{page.text}" for page in pdf_doc.pages
            ]
            # For mixed OCR, overlay OCR text onto marked pages where sparse
            if ocr_used and ocr_pages:
                ocr_map = {p.page_number: p.text for p in ocr_pages}
                marked_parts = []
                for page in pdf_doc.pages:
                    body = ocr_map.get(page.page_number, page.text) if (
                        page.is_image_heavy
                        or page.char_count < self.settings.text_density_threshold
                    ) else page.text
                    marked_parts.append(f"[[PAGE:{page.page_number}]]\n{body}")
            text = clean_text("\n\n".join(marked_parts))

        tables = self.table_parser.extract_tables(path, password=password)

        progress("Extracting information", 0.75)

        def ai_progress(stage: str, frac: float) -> None:
            # AI chunk extraction (when enabled) can take a while on large
            # PDFs — map its own 0..1 progress into the tail of the overall
            # bar so the UI keeps moving instead of sitting at "Extracting
            # information" for the whole multi-chunk run.
            progress(stage, min(0.75 + frac * 0.22, 0.97))

        result = self.extractor.extract(
            text,
            tables=tables,
            enrich_with_ai=enrich_with_ai,
            meta=meta,
            progress_callback=ai_progress,
        )
        result.status = ExtractionStatus.COMPLETED
        result.meta = meta
        result.tables = [
            {
                "page_number": t.page_number,
                "headers": t.headers,
                "row_count": len(t.rows),
                "is_product_table": t.is_product_table,
            }
            for t in tables
        ]

        progress("Done", 1.0)
        return result

    def process_many(
        self,
        paths: list[str | Path],
        password: str | None = None,
        enrich_with_ai: bool = False,
        progress_callback=None,
    ) -> list[TenderResult]:
        results: list[TenderResult] = []
        total = max(len(paths), 1)
        for idx, path in enumerate(paths):
            def nested(stage: str, frac: float, i=idx) -> None:
                overall = (i + frac) / total
                if progress_callback:
                    progress_callback(f"[{i + 1}/{total}] {stage}", overall)

            results.append(
                self.process(
                    path,
                    password=password,
                    enrich_with_ai=enrich_with_ai,
                    progress_callback=nested,
                )
            )
        return results
