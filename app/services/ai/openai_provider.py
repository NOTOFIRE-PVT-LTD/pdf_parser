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
import urllib.error
import urllib.request

from app.config import Settings
from app.services.ai.base import AIExtractionService

logger = logging.getLogger(__name__)

# Aliases tried only when ListModels is unavailable. Deprecated 1.5 / 2.0 names
# are omitted — they return 404 on current Google AI Studio keys.
_GEMINI_MODEL_FALLBACKS = (
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-pro-latest",
)
# Retired / 404 on current Google AI Studio keys (as of mid-2026)
_GEMINI_DEPRECATED = {
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro",
    "gemini-1.5-pro-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
}


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
        self._gemini_resolved_models: list[str] | None = None

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
        if configured in _GEMINI_DEPRECATED:
            logger.warning(
                "GEMINI_MODEL=%s is retired; using models available for your key instead",
                configured,
            )
            configured = ""

        models = self._gemini_resolve_models(key, configured)
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
        body_bytes = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None

        for model in models:
            try:
                body = self._gemini_post(key, model, body_bytes)
                return self._gemini_text_from_body(body)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                last_error = self._gemini_http_error(exc.code, detail, model)
                if exc.code in {404, 400} and "not found" in detail.lower():
                    logger.debug("Gemini model unavailable: %s — trying next", model)
                    continue
                if exc.code in {401, 403}:
                    raise last_error from exc
                if exc.code == 429:
                    raise last_error from exc
                raise last_error from exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                raise

        hint = (
            "No Gemini model worked for this API key. "
            "Copy the full key from https://aistudio.google.com/apikey "
            "(new keys start with AQ.), set GEMINI_MODEL=gemini-flash-latest, "
            "and restart the app."
        )
        if last_error:
            raise RuntimeError(f"{hint} Last error: {last_error}") from last_error
        raise RuntimeError(hint)

    def _gemini_resolve_models(self, key: str, configured: str) -> list[str]:
        """Prefer models returned by ListModels for this key (avoids 404 spam)."""
        if self._gemini_resolved_models is not None:
            return self._gemini_resolved_models

        discovered = self._gemini_discover_models(key)
        out: list[str] = []
        if configured and configured not in _GEMINI_DEPRECATED:
            if discovered:
                if configured in discovered:
                    out.append(configured)
            else:
                out.append(configured)
        for model in discovered:
            if model not in out:
                out.append(model)
        if not out:
            for model in _GEMINI_MODEL_FALLBACKS:
                if model not in out:
                    out.append(model)
        self._gemini_resolved_models = out
        if out:
            logger.info("Gemini models for this key: %s", ", ".join(out[:5]))
        return out

    @staticmethod
    def _gemini_headers(key: str) -> dict[str, str]:
        # Native Gemini endpoint — x-goog-api-key works for AIza and AQ auth keys.
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        }

    @staticmethod
    def _gemini_discover_models(key: str) -> list[str]:
        """List models this API key can call (generateContent)."""
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code in {401, 403}:
                raise RuntimeError(
                    "Gemini API key rejected. Copy the full key from "
                    "https://aistudio.google.com/apikey (new keys start with AQ.), "
                    "paste it into GEMINI_API_KEY in .env, and restart the app. "
                    f"HTTP {exc.code}: {detail[:300]}"
                ) from exc
            logger.warning("Gemini ListModels failed (%s): %s", exc.code, detail[:200])
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini ListModels failed: %s", exc)
            return []

        found: list[str] = []
        for entry in body.get("models") or []:
            name = str(entry.get("name") or "")
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            lower = name.lower()
            if any(tag in lower for tag in ("flash", "pro")) and "embedding" not in lower:
                found.append(name)
        found.sort(key=lambda n: (0 if "flash" in n.lower() else 1, n))
        return found[:8]

    @staticmethod
    def _gemini_post(key: str, model: str, body_bytes: bytes) -> dict:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=OpenAICompatibleExtractionService._gemini_headers(key),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _gemini_text_from_body(body: dict) -> str:
        candidates = body.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini empty response: {body}")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            raise RuntimeError(f"Gemini empty text: {body}")
        return text

    @staticmethod
    def _gemini_http_error(code: int, detail: str, model: str) -> RuntimeError:
        if code in {401, 403}:
            return RuntimeError(
                "Gemini API key rejected. Copy the full key from "
                "https://aistudio.google.com/apikey (new keys start with AQ.), "
                "update GEMINI_API_KEY in .env, and restart the app. "
                f"HTTP {code}: {detail[:300]}"
            )
        if code == 429:
            return RuntimeError(
                "Gemini rate limit reached (free tier). Wait a minute and retry, "
                "or lower AI_CHUNK_MAX_PAGES in .env to send fewer requests. "
                f"HTTP 429: {detail[:300]}"
            )
        return RuntimeError(f"Gemini HTTP {code} ({model}): {detail[:500]}")
