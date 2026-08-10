"""
Factory for AI extraction backends.

Online providers: openai | anthropic | gemini
Local: ollama
Offline default: none
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.services.ai.base import AIExtractionService, NoOpAIExtractionService
from app.services.ai.ollama_provider import OllamaAIExtractionService
from app.services.ai.openai_provider import OpenAICompatibleExtractionService


def get_ai_service(
    settings: Settings | None = None,
    *,
    provider_override: str | None = None,
    api_key_override: str | None = None,
    force_enabled: bool = False,
) -> AIExtractionService:
    settings = settings or get_settings()
    provider = (provider_override or settings.ai_provider or "none").lower().strip()

    enabled = force_enabled or settings.ai_enabled
    if not enabled or provider in ("none", "", None):
        return NoOpAIExtractionService()

    if provider == "ollama":
        return OllamaAIExtractionService(settings)
    if provider in {"openai", "anthropic", "gemini"}:
        return OpenAICompatibleExtractionService(
            settings,
            provider=provider,
            api_key_override=api_key_override,
        )
    return NoOpAIExtractionService()
