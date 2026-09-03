"""Mistral Chat Completions HTTP client."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MODEL = "mistral-small-latest"


def chat_completion(
    api_key: str,
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout: int = 90,
) -> str:
    """Call Mistral chat completions; return assistant message content."""
    if not api_key or not api_key.strip():
        raise ValueError("MISTRAL_API_KEY not set")

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if exc.code in {401, 403, 429}:
            logger.debug("Mistral HTTP %s: %s", exc.code, detail)
        else:
            logger.warning("Mistral HTTP %s: %s", exc.code, detail)
        exc.detail_text = detail  # type: ignore[attr-defined]
        raise

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Mistral returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Mistral returned empty content")
    return content
