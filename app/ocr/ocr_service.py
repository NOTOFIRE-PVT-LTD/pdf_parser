"""
OCR service using Tesseract + OpenCV preprocessing.

Used automatically when the PDF is classified as scanned or mixed.
Fails gracefully with a clear error message if Tesseract is missing — and,
same as that, if OpenCV itself isn't installed. cv2 is optional at import
time (see the try/except below) so this module — and therefore the whole
app, since app.services.pipeline imports OcrService unconditionally — can
still load in a minimal deployment that omits opencv-python-headless (a
~110MB dependency) for image size reasons. OCR then just reports itself as
unavailable via is_available(), the same graceful path already used when
the Tesseract binary itself is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image

try:
    import cv2
    _CV2_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - exercised only where cv2 isn't installed
    cv2 = None  # type: ignore[assignment]
    _CV2_IMPORT_ERROR = exc

from app.config import Settings, get_settings
from app.parser.pdf_parser import PdfParser
from app.utils.text_utils import clean_text

logger = logging.getLogger(__name__)


class OcrError(Exception):
    """Raised when OCR cannot run or returns unusable output."""


@dataclass
class OcrPageResult:
    page_number: int
    text: str
    confidence: float | None = None


@dataclass
class OcrResult:
    pages: list[OcrPageResult]
    full_text: str
    success: bool
    error: str | None = None


class OcrService:
    """Offline OCR via Tesseract with OpenCV image cleanup."""

    def __init__(
        self,
        settings: Settings | None = None,
        pdf_parser: PdfParser | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pdf_parser = pdf_parser or PdfParser(self.settings)
        self._configure_tesseract()

    def _configure_tesseract(self) -> None:
        if self.settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_cmd
            return
        # Common Windows install path
        default_win = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if default_win.exists():
            pytesseract.pytesseract.tesseract_cmd = str(default_win)

    def is_available(self) -> tuple[bool, str | None]:
        if _CV2_IMPORT_ERROR is not None:
            return False, (
                "OCR image-preprocessing dependency (OpenCV/cv2) is not installed in this "
                f"environment ({_CV2_IMPORT_ERROR}). Install opencv-python-headless to enable OCR."
            )
        try:
            version = pytesseract.get_tesseract_version()
            return True, str(version)
        except Exception as exc:  # noqa: BLE001
            return False, (
                "Tesseract OCR is not installed or not on PATH. "
                f"Install from https://github.com/tesseract-ocr/tesseract and retry. ({exc})"
            )

    def ocr_pdf(
        self,
        path: str | Path,
        page_numbers: list[int] | None = None,
        password: str | None = None,
    ) -> OcrResult:
        available, err = self.is_available()
        if not available:
            return OcrResult(pages=[], full_text="", success=False, error=err)

        path = Path(path)
        try:
            import fitz

            doc = fitz.open(path)
            try:
                if doc.is_encrypted:
                    doc.authenticate(password or "")
                total = doc.page_count
            finally:
                doc.close()
        except Exception as exc:  # noqa: BLE001
            return OcrResult(pages=[], full_text="", success=False, error=str(exc))

        targets = page_numbers or list(range(1, min(total, self.settings.max_pages) + 1))
        results: list[OcrPageResult] = []

        try:
            for page_no in targets:
                png = self.pdf_parser.render_page_image(
                    path, page_no, dpi=self.settings.ocr_dpi, password=password
                )
                text, conf = self.ocr_image_bytes(png)
                results.append(OcrPageResult(page_number=page_no, text=text, confidence=conf))
        except OcrError as exc:
            return OcrResult(pages=results, full_text="", success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("OCR failed")
            return OcrResult(
                pages=results,
                full_text="",
                success=False,
                error=f"OCR failed: {exc}",
            )

        full_text = clean_text("\n\n".join(p.text for p in results if p.text))
        if not full_text.strip():
            return OcrResult(
                pages=results,
                full_text="",
                success=False,
                error="OCR completed but extracted no readable text.",
            )
        return OcrResult(pages=results, full_text=full_text, success=True)

    def ocr_image_bytes(self, image_bytes: bytes) -> tuple[str, float | None]:
        """Preprocess with OpenCV and run Tesseract."""
        try:
            image = Image.open(BytesIO(image_bytes)).convert("RGB")
            arr = np.array(image)
            processed = self.preprocess(arr)
            config = f"--oem 3 --psm 6 -l {self.settings.ocr_lang}"
            data = pytesseract.image_to_data(
                processed, config=config, output_type=pytesseract.Output.DICT
            )
            text = pytesseract.image_to_string(processed, config=config)
            confidences = [
                float(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and float(c) >= 0
            ]
            avg_conf = sum(confidences) / len(confidences) if confidences else None
            return clean_text(text), avg_conf
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrError(
                "Tesseract OCR executable not found. "
                "Set TESSERACT_CMD or install Tesseract."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"Image OCR failed: {exc}") from exc

    @staticmethod
    def preprocess(image_bgr_or_rgb: np.ndarray) -> np.ndarray:
        """
        Grayscale → denoise → adaptive threshold for better OCR on scans.
        Accepts RGB (from PIL) and converts to grayscale.
        """
        if cv2 is None:
            raise OcrError(
                "OpenCV (cv2) is not installed in this environment — OCR preprocessing unavailable."
            )
        if image_bgr_or_rgb.ndim == 3:
            gray = cv2.cvtColor(image_bgr_or_rgb, cv2.COLOR_RGB2GRAY)
        else:
            gray = image_bgr_or_rgb

        # Upscale small pages
        h, w = gray.shape[:2]
        if max(h, w) < 1500:
            scale = 1500 / max(h, w)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        denoised = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
        binary = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        return binary
