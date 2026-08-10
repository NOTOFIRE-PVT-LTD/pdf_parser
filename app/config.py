"""
Tender Parser — application configuration.

Central settings for paths, OCR, extraction thresholds, and optional AI backends.
AI providers are disabled by default; enable via environment variables when ready.
"""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root: .../Pdf Parser
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    export_dir: Path = PROJECT_ROOT / "data" / "exports"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"

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
    # Allow single-row continuation tables after header
    table_min_rows: int = 1

    # Optional AI backends
    ai_provider: str = "none"  # none | ollama | openai | anthropic | gemini
    ai_enabled: bool = False
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-3-5-haiku-latest"
    gemini_model: str = "gemini-1.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # Chunked AI extraction: when AI is enabled, the whole document is split
    # into page-group chunks and each one is sent to the model in turn, so a
    # large multi-hundred-page tender gets fully read instead of only its
    # first ~28k characters. Tune down for slower/cheaper models, up for
    # models with large context windows.
    ai_chunk_max_pages: int = 6
    ai_chunk_max_chars: int = 12000


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings
