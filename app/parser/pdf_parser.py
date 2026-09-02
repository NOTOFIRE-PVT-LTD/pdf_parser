"""
PDF parsing service.

Uses pdfplumber + PyMuPDF for text extraction and pdf type detection.
Encrypted PDFs are rejected (password-protected files are not supported).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
import pdfplumber

from app.config import Settings, get_settings
from app.models.schemas import PdfType
from app.utils.text_utils import clean_text

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    page_number: int  # 1-based
    text: str
    char_count: int
    is_image_heavy: bool = False
    width: float = 0.0
    height: float = 0.0


@dataclass
class PdfDocument:
    path: Path
    page_count: int = 0
    pdf_type: PdfType = PdfType.UNKNOWN
    encrypted: bool = False
    password_required: bool = False
    pages: list[PageContent] = field(default_factory=list)
    full_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class PdfParser:
    """Extract text and classify PDF as text / scanned / mixed / encrypted."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def open_and_parse(
        self,
        path: str | Path,
        password: str | None = None,
    ) -> PdfDocument:
        path = Path(path)
        doc = PdfDocument(path=path)

        if not path.exists():
            doc.error = f"File not found: {path}"
            return doc

        # --- Encryption probe via PyMuPDF ---
        try:
            fitz_doc = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001
            doc.error = f"Unable to open PDF: {exc}"
            return doc

        try:
            if fitz_doc.is_encrypted:
                doc.encrypted = True
                ok = fitz_doc.authenticate(password or "")
                if not ok:
                    doc.pdf_type = PdfType.ENCRYPTED
                    doc.password_required = True
                    doc.error = "This PDF is encrypted. Password-protected PDFs are not supported."
                    return doc

            doc.page_count = fitz_doc.page_count
            doc.metadata = dict(fitz_doc.metadata or {})

            pages = self._extract_pages_fitz(fitz_doc)
            # Prefer pdfplumber when text PDFs yield denser text
            plumber_pages = self._extract_pages_pdfplumber(path, password)
            pages = self._merge_best_pages(pages, plumber_pages)

            doc.pages = pages
            doc.full_text = clean_text("\n\n".join(p.text for p in pages if p.text))
            doc.pdf_type = self._classify(pages)
            return doc
        finally:
            fitz_doc.close()

    def _extract_pages_fitz(self, fitz_doc: pymupdf.Document) -> list[PageContent]:
        pages: list[PageContent] = []
        max_pages = min(fitz_doc.page_count, self.settings.max_pages)
        for i in range(max_pages):
            try:
                page = fitz_doc.load_page(i)
                text = page.get_text("text") or ""
                text = clean_text(text)
                rect = page.rect
                char_count = len(text.replace(" ", "").replace("\n", ""))
                # Image-heavy heuristic: little text + one or more images
                image_count = len(page.get_images(full=True))
                is_image_heavy = (
                    char_count < self.settings.text_density_threshold and image_count > 0
                )
                pages.append(
                    PageContent(
                        page_number=i + 1,
                        text=text,
                        char_count=char_count,
                        is_image_heavy=is_image_heavy,
                        width=float(rect.width),
                        height=float(rect.height),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("PyMuPDF extraction failed on page %d: %s", i + 1, exc)
                pages.append(
                    PageContent(page_number=i + 1, text="", char_count=0, is_image_heavy=True)
                )
        return pages

    def _extract_pages_pdfplumber(
        self,
        path: Path,
        password: str | None,
    ) -> list[PageContent]:
        pages: list[PageContent] = []
        try:
            open_kwargs: dict[str, Any] = {}
            if password:
                open_kwargs["password"] = password
            with pdfplumber.open(path, **open_kwargs) as pdf:
                for i, page in enumerate(pdf.pages[: self.settings.max_pages]):
                    text = clean_text(page.extract_text() or "")
                    char_count = len(text.replace(" ", "").replace("\n", ""))
                    pages.append(
                        PageContent(
                            page_number=i + 1,
                            text=text,
                            char_count=char_count,
                            is_image_heavy=char_count < self.settings.text_density_threshold,
                            width=float(page.width or 0),
                            height=float(page.height or 0),
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pdfplumber extraction failed: %s", exc)
        return pages

    @staticmethod
    def _merge_best_pages(
        fitz_pages: list[PageContent],
        plumber_pages: list[PageContent],
    ) -> list[PageContent]:
        if not plumber_pages:
            return fitz_pages
        if not fitz_pages:
            return plumber_pages

        merged: list[PageContent] = []
        plumber_map = {p.page_number: p for p in plumber_pages}
        for fp in fitz_pages:
            pp = plumber_map.get(fp.page_number)
            if pp and pp.char_count > fp.char_count:
                merged.append(pp)
            else:
                merged.append(fp)
        return merged

    def _classify(self, pages: list[PageContent]) -> PdfType:
        if not pages:
            return PdfType.UNKNOWN
        scanned_flags = [
            p.is_image_heavy or p.char_count < self.settings.text_density_threshold
            for p in pages
        ]
        scanned_count = sum(1 for f in scanned_flags if f)
        ratio = scanned_count / len(pages)
        if ratio >= self.settings.scanned_page_ratio:
            return PdfType.SCANNED
        if scanned_count > 0:
            return PdfType.MIXED
        return PdfType.TEXT

    def render_page_image(
        self,
        path: str | Path,
        page_number: int,
        dpi: int | None = None,
        password: str | None = None,
    ) -> bytes:
        """Render a single page to PNG bytes (for OCR / preview)."""
        dpi = dpi or self.settings.ocr_dpi
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        doc = pymupdf.open(path)
        try:
            if doc.is_encrypted:
                doc.authenticate(password or "")
            page = doc.load_page(page_number - 1)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
