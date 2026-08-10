"""
Ollama local LLM provider (optional online-to-localhost).

Enable with:
  AI_ENABLED=true
  AI_PROVIDER=ollama
  OLLAMA_BASE_URL=http://localhost:11434
  OLLAMA_MODEL=llama3.2
"""

from __future__ import annotations

import json
import urllib.request

from app.config import Settings
from app.services.ai.base import AIExtractionService


class OllamaAIExtractionService(AIExtractionService):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_enabled(self) -> bool:
        return bool(self.settings.ai_enabled and self.settings.ai_provider == "ollama")

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")
