"""
Online AI extraction providers (OpenAI, Anthropic Claude, Google Gemini).

Enable from Streamlit sidebar or .env:
  AI_ENABLED=true
  AI_PROVIDER=openai|anthropic|gemini
  OPENAI_API_KEY=...
  ANTHROPIC_API_KEY=...
  GEMINI_API_KEY=...
"""

from __future__ import annotations

import json
import logging
import urllib.request

from app.config import Settings
from app.services.ai.base import AIExtractionService
from app.services.ai.gemini_client import generate_with_fallback

logger = logging.getLogger(__name__)


class OpenAICompatibleExtractionService(AIExtractionService):
    """Real HTTP implementations for OpenAI / Anthropic / Gemini."""

    def __init__(
        self,
        settings: Settings,
        provider: str = "openai",
        api_key_override: str | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider.lower().strip()
        self.api_key_override = api_key_override
        self._gemini_resolved_models: list[str] = []

    def _api_key(self) -> str | None:
        if self.api_key_override:
            return self.api_key_override.strip() or None
        key_map = {
            "openai": self.settings.openai_api_key,
            "anthropic": self.settings.anthropic_api_key,
            "gemini": self.settings.gemini_api_key,
        }
        return key_map.get(self.provider)

    def is_enabled(self) -> bool:
        if not self.settings.ai_enabled and not self.api_key_override:
            return False
        return bool(self._api_key()) and self.provider in {"openai", "anthropic", "gemini"}

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "openai":
            return self._openai_chat(system_prompt, user_prompt)
        if self.provider == "anthropic":
            return self._anthropic_chat(system_prompt, user_prompt)
        if self.provider == "gemini":
            return self._gemini_chat(system_prompt, user_prompt)
        raise ValueError(f"Unsupported provider: {self.provider}")

    def _openai_chat(self, system_prompt: str, user_prompt: str) -> str:
        model = getattr(self.settings, "openai_model", None) or "gpt-4o-mini"
        payload = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def _anthropic_chat(self, system_prompt: str, user_prompt: str) -> str:
        model = getattr(self.settings, "anthropic_model", None) or "claude-3-5-haiku-latest"
        payload = {
            "model": model,
            "max_tokens": 8000,
            "temperature": 0,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key() or "",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        parts = body.get("content") or []
        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return "\n".join(texts)

    def _gemini_chat(self, system_prompt: str, user_prompt: str) -> str:
        key = self._api_key()
        if not key:
            raise RuntimeError("Gemini API key is missing")

        configured = (getattr(self.settings, "gemini_model", None) or "").strip()
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": system_prompt + "\n\n" + user_prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        base_url = getattr(self.settings, "gemini_base_url", None) or "https://generativelanguage.googleapis.com/v1beta"
        return generate_with_fallback(
            key,
            configured,
            payload,
            base_url=base_url,
            model_cache=self._gemini_resolved_models,
        )
