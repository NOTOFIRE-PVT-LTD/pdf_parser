"""AI agent facade — Notofire general chat + tender fixes via LangGraph."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.schemas import ProductItem
from app.services.ai.agent_graph import apply_product_changes, run_agent
from app.services.ai.token_budget import compact_history

logger = logging.getLogger(__name__)


@dataclass
class AiFixResult:
    updated: int
    removed: int
    summary: str
    reply: str = ""
    error: str | None = None
    provider: str = ""


@dataclass
class ChatMessage:
    role: str  # user | assistant
    content: str


def resolve_ai_provider(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    preferred = (settings.ai_provider or "mistral").strip().lower()
    if preferred == "mistral" and settings.mistral_api_key:
        return "mistral"
    if preferred == "gemini" and settings.gemini_api_key:
        return "gemini"
    if settings.mistral_api_key:
        return "mistral"
    if settings.gemini_api_key:
        return "gemini"
    return None


def _history_dicts(history: list[ChatMessage] | None) -> list[dict[str, str]]:
    raw = [
        {"role": m.role, "content": m.content}
        for m in (history or [])
        if m.role in {"user", "assistant"} and (m.content or "").strip()
    ]
    return compact_history(raw)


def chat_ai(
    instruction: str,
    products: list[ProductItem] | None = None,
    history: list[ChatMessage] | None = None,
    settings: Settings | None = None,
    chat_id: str = "global",
) -> AiFixResult:
    """General chat OR tender fix — products optional."""
    settings = settings or get_settings()
    instruction = (instruction or "").strip()
    if not instruction:
        return AiFixResult(0, 0, "No instruction given.", error="empty")
    if not resolve_ai_provider(settings):
        return AiFixResult(
            0, 0,
            "Set MISTRAL_API_KEY in .env (recommended) or GEMINI_API_KEY.",
            error="no_key",
        )

    products = products or []
    try:
        state = run_agent(
            instruction=instruction,
            products=products,
            history=_history_dicts(history),
            chat_id=chat_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent failed: %s", exc)
        return AiFixResult(0, 0, str(exc), error=str(exc))

    err = state.get("error")
    reply = (state.get("reply") or "").strip()
    if err and not reply:
        blob = str(err).lower()
        if "429" in blob or "rate" in blob:
            msg = "Rate limit exceeded. Wait a minute or use another API key."
        elif "no_key" in blob:
            msg = "Set MISTRAL_API_KEY in .env."
        else:
            msg = str(err)
        return AiFixResult(0, 0, msg, error=str(err), provider=state.get("provider") or "")

    changes = state.get("changes") or []
    updated = removed = 0
    if products and changes:
        updated, removed = apply_product_changes(products, changes)

    summary = (state.get("summary") or "").strip()
    if not summary:
        if updated or removed:
            summary = f"{updated} updated, {removed} removed."
        else:
            summary = ""

    return AiFixResult(
        updated=updated,
        removed=removed,
        summary=summary or "Done.",
        reply=reply or summary or "Done.",
        provider=state.get("provider") or "",
    )


def chat_ai_fix(
    products: list[ProductItem],
    instruction: str,
    history: list[ChatMessage] | None = None,
    settings: Settings | None = None,
) -> AiFixResult:
    """Backward-compatible tender fix entrypoint."""
    return chat_ai(
        instruction,
        products=products,
        history=history,
        settings=settings,
    )


def apply_ai_fix(
    products: list[ProductItem],
    instruction: str,
    settings: Settings | None = None,
) -> AiFixResult:
    return chat_ai_fix(products, instruction, history=None, settings=settings)


# Keep parse helpers for unit tests
def _parse_response(raw: str) -> dict:
    from app.services.ai.agent_graph import _parse_json

    return _parse_json(raw)


def _validate_name(desc: str, name: str) -> bool:
    from app.services.ai.agent_graph import _validate_name as _v

    return _v(desc, name)
