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
from app.parser.pdf_parser import PdfParser, PdfDocument
from app.parser.table_parser import TableParser
from app.utils.text_utils import clean_text

logger = logging.getLogger(__name__)


class TenderPipeline:
    """End-to-end offline tender extraction pipeline."""

    def __init__(
        self,
        settings: Settings | None = None,
        pdf_parser: PdfParser | None = None,
        ocr_service: OcrService | None = None,
        table_parser: TableParser | None = None,
        information_extractor: InformationExtractor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pdf_parser = pdf_parser or PdfParser(self.settings)
        self.ocr_service = ocr_service or OcrService(self.settings, self.pdf_parser)
        self.table_parser = table_parser or TableParser(self.settings)
        self.extractor = information_extractor or InformationExtractor()

    def process(
        self,
        path: str | Path,
        progress_callback=None,
    ) -> TenderResult:
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
        pdf_doc = self.pdf_parser.open_and_parse(path)

        if pdf_doc.password_required:
            return TenderResult(
                status=ExtractionStatus.FAILED,
                meta=DocumentMeta(
                    filename=path.name,
                    pdf_type=PdfType.ENCRYPTED,
                    encrypted=True,
                    error="This PDF is encrypted. Password-protected PDFs are not supported.",
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

        try:
            text, ocr_used, ocr_pages = self._resolve_text(pdf_doc, path, progress)
        except _OcrFailed as exc:
            meta.error = exc.message
            meta.warnings.append(exc.message)
            return TenderResult(status=ExtractionStatus.OCR_FAILED, meta=meta)
        meta.ocr_used = ocr_used

        progress("Extracting tables", 0.55)
        text = self._inject_page_markers(text, pdf_doc, ocr_used, ocr_pages)
        tables = self.table_parser.extract_tables(path)

        progress("Extracting information", 0.75)
        result = self.extractor.extract(text, tables=tables, meta=meta)
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
        progress_callback=None,
    ) -> list[TenderResult]:
        total = max(len(paths), 1)
        results: list[TenderResult] = []
        for idx, path in enumerate(paths):
            def nested(stage: str, frac: float, i=idx) -> None:
                if progress_callback:
                    progress_callback(f"[{i + 1}/{total}] {stage}", (i + frac) / total)

            results.append(self.process(path, progress_callback=nested))
        return results

    def _resolve_text(
        self,
        pdf_doc: PdfDocument,
        path: Path,
        progress,
    ) -> tuple[str, bool, list | None]:
        text = pdf_doc.full_text
        if pdf_doc.pdf_type not in (PdfType.SCANNED, PdfType.MIXED):
            return text, False, None

        progress("Running OCR", 0.25)
        page_targets = None
        if pdf_doc.pdf_type == PdfType.MIXED:
            threshold = self.settings.text_density_threshold
            page_targets = [
                p.page_number for p in pdf_doc.pages
                if p.is_image_heavy or p.char_count < threshold
            ]

        ocr_result = self.ocr_service.ocr_pdf(path, page_numbers=page_targets)
        if not ocr_result.success:
            if pdf_doc.pdf_type == PdfType.SCANNED and not text.strip():
                raise _OcrFailed(ocr_result.error or "OCR failed")
            return text, False, None

        ocr_pages = ocr_result.pages
        if pdf_doc.pdf_type == PdfType.SCANNED:
            return ocr_result.full_text, True, ocr_pages

        ocr_map = {p.page_number: p.text for p in ocr_pages}
        threshold = self.settings.text_density_threshold
        chunks = [
            ocr_map[p.page_number] if p.page_number in ocr_map and (
                p.is_image_heavy or p.char_count < threshold
            ) else p.text
            for p in pdf_doc.pages
        ]
        return clean_text("\n\n".join(chunks)), True, ocr_pages

    def _inject_page_markers(
        self, text: str, pdf_doc: PdfDocument, ocr_used: bool, ocr_pages
    ) -> str:
        if ocr_used and pdf_doc.pdf_type == PdfType.SCANNED and ocr_pages:
            return clean_text(
                "\n\n".join(f"[[PAGE:{p.page_number}]]\n{p.text}" for p in ocr_pages)
            )
        if not pdf_doc.pages:
            return text

        threshold = self.settings.text_density_threshold
        ocr_map = {p.page_number: p.text for p in ocr_pages} if ocr_used and ocr_pages else {}
        parts = []
        for page in pdf_doc.pages:
            body = page.text
            if ocr_map and (page.is_image_heavy or page.char_count < threshold):
                body = ocr_map.get(page.page_number, page.text)
            parts.append(f"[[PAGE:{page.page_number}]]\n{body}")
        return clean_text("\n\n".join(parts))


class _OcrFailed(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
