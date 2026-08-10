"""
AI provider configuration lives entirely in .env — the app reads it at
startup via app.config.get_settings() and never writes to it. There is no
in-app "enter your API key" flow: add GEMINI_API_KEY / OPENAI_API_KEY /
ANTHROPIC_API_KEY (plus AI_ENABLED=true, AI_PROVIDER=<name>) to your .env
file directly and restart the app.
"""

from __future__ import annotations

from app.config import get_settings

PROVIDER_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def has_saved_key(provider: str) -> bool:
    """True if this provider's API key is set (from .env, via get_settings())."""
    provider = provider.strip().lower()
    return bool(getattr(get_settings(), f"{provider}_api_key", None))
