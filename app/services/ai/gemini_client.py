"""Shared Gemini HTTP helpers — auth keys, model discovery, retries."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

FALLBACK_MODELS = (
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-flash-latest",
)

# Retired or blocked for new API keys — skip even if ListModels still lists them.
DEPRECATED_MODELS = {
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro",
    "gemini-1.5-pro-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
}

# Non-text modalities. These match the flash/pro name filter but cannot answer
# a JSON text prompt, so they must never enter the try-order.
NON_TEXT_MARKERS = (
    "embedding",
    "image",
    "tts",
    "audio",
    "vision",
    "video",
    "live",
    "veo",
    "imagen",
    "computer-use",
)

_RETRYABLE_STATUS = {429, 503}
_MAX_RETRIES = 3
# Enough to route around a dead or quota-blocked model without letting the
# worst case (every model timing out) run for minutes behind a UI spinner.
MAX_MODELS_TRIED = 4


class _ModelUnresponsive(RuntimeError):
    """One model timed out or was unreachable — try the next one."""


def normalize_model(model: str) -> str:
    m = model.strip()
    if m.startswith("models/"):
        m = m[7:]
    return m


def headers(key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }


def _usable_model(name: str) -> bool:
    return normalize_model(name) not in DEPRECATED_MODELS


def discover_models(key: str, base_url: str = DEFAULT_BASE_URL) -> list[str]:
    """List flash/pro models this key can call via generateContent."""
    url = f"{base_url.rstrip('/')}/models"
    req = urllib.request.Request(url, method="GET", headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        if exc.code in {401, 403}:
            raise RuntimeError(
                "Gemini API key rejected. Copy the full key from "
                "https://aistudio.google.com/apikey and restart the app. "
                f"HTTP {exc.code}: {detail[:300]}"
            ) from exc
        logger.warning("Gemini ListModels failed (%s): %s", exc.code, detail[:200])
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini ListModels failed: %s", exc)
        return []

    found: list[str] = []
    for entry in body.get("models") or []:
        name = normalize_model(str(entry.get("name") or ""))
        methods = entry.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        lower = name.lower()
        if any(marker in lower for marker in NON_TEXT_MARKERS):
            continue
        if any(tag in lower for tag in ("flash", "pro")) and _usable_model(name):
            found.append(name)
    found.sort(
        key=lambda n: (
            0 if "lite" in n.lower() else 1,
            0 if "flash" in n.lower() else 1,
            n,
        )
    )
    return found[:8]


def resolve_models(
    key: str,
    configured: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    cache: list[str] | None = None,
) -> list[str]:
    """Build model try-order: configured (if valid) + discovered + fallbacks."""
    if cache:
        return [m for m in cache if _usable_model(m)][:MAX_MODELS_TRIED]

    configured = normalize_model(configured)
    if not _usable_model(configured):
        configured = ""

    discovered = discover_models(key, base_url)
    out: list[str] = []
    if configured and _usable_model(configured):
        out.append(configured)
    for model in discovered:
        if model not in out:
            out.append(model)
    for model in FALLBACK_MODELS:
        if model not in out and _usable_model(model):
            out.append(model)
    out = out[:MAX_MODELS_TRIED]
    if cache is not None:
        cache[:] = out
    return out


def _model_unavailable(detail: str) -> bool:
    lower = detail.lower()
    return "not found" in lower or "no longer available" in lower


def post_generate(
    key: str,
    model: str,
    payload: dict,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 75,
) -> dict:
    """POST generateContent with short retries on 429/503."""
    model = normalize_model(model)
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    body_bytes = json.dumps(payload).encode("utf-8")
    last_error: urllib.error.HTTPError | None = None
    stripped_thinking = False

    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers(key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            # A hung model is not worth retrying at the same address — the
            # caller's model-fallback loop is the retry. Fail fast so it can
            # move on instead of burning another `timeout` seconds here.
            raise _ModelUnresponsive(f"{model} did not respond within {timeout}s: {exc}") from exc
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = exc.read().decode("utf-8", errors="ignore")
            if (
                exc.code == 400
                and not stripped_thinking
                and "thinkingConfig" in json.dumps(payload.get("generationConfig") or {})
            ):
                # This model doesn't accept thinkingConfig — resend without it
                # instead of reporting the model as broken.
                logger.info("Gemini %s rejected thinkingConfig — retrying without it", model)
                reduced = dict(payload)
                gen = dict(reduced.get("generationConfig") or {})
                gen.pop("thinkingConfig", None)
                reduced["generationConfig"] = gen
                body_bytes = json.dumps(reduced).encode("utf-8")
                stripped_thinking = True
                continue
            if exc.code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                wait = 2**attempt
                logger.info(
                    "Gemini %s busy (%s) — retry %d/%d in %ss",
                    model,
                    exc.code,
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue
            exc.detail_text = detail  # type: ignore[attr-defined]
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"Gemini request failed for {model}")


def text_from_body(body: dict) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini empty response: {body}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise RuntimeError(f"Gemini empty text: {body}")
    return text


def generate_with_fallback(
    key: str,
    configured_model: str,
    payload: dict,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 75,
    model_cache: list[str] | None = None,
) -> str:
    """Try models in order; skip to next on 404 or exhausted 429/503 retries."""
    models = resolve_models(key, configured_model, base_url=base_url, cache=model_cache)
    last_error: Exception | None = None

    for model in models:
        try:
            body = post_generate(key, model, payload, base_url=base_url, timeout=timeout)
            text = text_from_body(body)
            if model_cache is not None and model_cache and model_cache[0] != model:
                # Promote the model that actually answered. Without this, a
                # model that hangs stays pinned at the front of the cached
                # try-order and every later call pays its timeout again.
                model_cache[:] = [model] + [m for m in model_cache if m != model]
            return text
        except urllib.error.HTTPError as exc:
            detail = getattr(exc, "detail_text", None) or exc.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(f"Gemini HTTP {exc.code} ({model}): {detail[:500]}")
            if exc.code in {404, 400} and _model_unavailable(detail):
                logger.warning("Gemini model unavailable: %s — trying next", model)
                continue
            if exc.code in _RETRYABLE_STATUS:
                logger.warning("Gemini model overloaded: %s (%s) — trying next", model, exc.code)
                continue
            if exc.code in {401, 403}:
                raise RuntimeError(
                    "Gemini API key rejected. Update GEMINI_API_KEY in .env and restart. "
                    f"HTTP {exc.code}: {detail[:300]}"
                ) from exc
            raise last_error from exc
        except (_ModelUnresponsive, TimeoutError, urllib.error.URLError) as exc:
            last_error = RuntimeError(f"Gemini {model} unreachable: {exc}")
            logger.warning("Gemini model unresponsive: %s — trying next", model)
            continue

    hint = (
        "All Gemini models are busy or unavailable. Wait a minute and retry, "
        "or set GEMINI_MODEL=gemini-3.6-flash in .env."
    )
    if last_error:
        raise RuntimeError(f"{hint} Last error: {last_error}") from last_error
    raise RuntimeError(hint)
