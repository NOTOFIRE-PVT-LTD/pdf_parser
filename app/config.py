"""
Tender Parser — application configuration.

Central settings for paths, OCR, extraction thresholds, and optional AI backends.
AI providers are disabled by default; enable via environment variables when ready.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root: .../Pdf Parser
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Serverless platforms (Vercel, AWS Lambda, ...) ship a read-only filesystem
# except for /tmp. Writing to PROJECT_ROOT/data there raises PermissionError
# on the very first request. Detect that and default storage under /tmp
# instead — local development (no such env vars set) is unaffected.
_IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
_DATA_ROOT = Path(tempfile.gettempdir()) / "tender_parser" if _IS_SERVERLESS else PROJECT_ROOT / "data"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Tender Parser"
    app_version: str = "1.0.0"
    debug: bool = False

    # Storage
    upload_dir: Path = _DATA_ROOT / "uploads"
    export_dir: Path = _DATA_ROOT / "exports"
    cache_dir: Path = _DATA_ROOT / "cache"
    learnings_file: Path = _DATA_ROOT / "cache" / "product_name_learnings.json"

    # PDF / OCR
    # Characters below this page density → treat page as scanned / image-only
    text_density_threshold: float = 40.0
    # Fraction of pages that must look scanned to classify whole PDF as scanned
    scanned_page_ratio: float = 0.55
    ocr_lang: str = "eng"
    ocr_dpi: int = 300
    tesseract_cmd: str | None = None  # e.g. r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    # Extraction
    max_pages: int = 500
    table_min_rows: int = 1

    # AI agent — Mistral (preferred) or Gemini
    ai_provider: str = "mistral"
    ai_enabled: bool = True  # master switch (AI_ENABLED)
    ai_extract_enabled: bool = True  # AI analyzes each PDF layout
    mistral_api_key: str | None = None
    mistral_model: str = "mistral-small-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reload_settings() -> Settings:
    """Clear cache after .env changes."""
    get_settings.cache_clear()
    return get_settings()
