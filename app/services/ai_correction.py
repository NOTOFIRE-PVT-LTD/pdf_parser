"""AI agent facade — Notofire general chat + tender fixes via LangGraph."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.schemas import ProductItem
from app.services.ai.agent_graph import (
    apply_product_changes,
    capture_instruction_from_user,
    run_agent,
)
from app.services.ai.token_budget import compact_history

logger = logging.getLogger(__name__)

# One HTTP call with 500+ rows times out. We still finish the WHOLE list in
# one user message by looping these chunks behind the scenes.
_CHUNK_SIZE = 35


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


ProgressCb = Callable[[int, int, int], None]  # done_rows, total_rows, batch_no


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


def _is_timeout(exc: BaseException | str) -> bool:
    blob = str(exc).lower()
    return "timed out" in blob or "timeout" in blob


def _friendly_timeout() -> str:
    return (
        "AI request timed out on one batch. "
        "Large lists are fixed in small batches automatically — try the same message again."
    )


def _run_chunk(
    instruction: str,
    chunk: list[ProductItem],
    *,
    offset: int,
    total: int,
    history: list[dict[str, str]],
    chat_id: str,
) -> tuple[list[dict], str, str | None, str]:
    """Returns remapped changes, reply, error, provider."""
    chunk_instruction = instruction
    if total > _CHUNK_SIZE:
        chunk_instruction = (
            f"{instruction}\n\n"
            f"(Batch rows {offset + 1}–{offset + len(chunk)} of {total}. "
            f"Use 0-based index within THIS batch only.)"
        )
    try:
        state = run_agent(
            instruction=chunk_instruction,
            products=chunk,
            history=history,
            chat_id=chat_id,
        )
    except Exception as exc:  # noqa: BLE001
        return [], "", str(exc), ""

    err = state.get("error")
    reply = (state.get("reply") or "").strip()
    provider = state.get("provider") or ""
    remapped: list[dict] = []
    for ch in state.get("changes") or []:
        if not isinstance(ch, dict):
            continue
        idx = ch.get("index")
        if not isinstance(idx, int):
            continue
        remapped.append({**ch, "index": idx + offset})
    if err and not remapped:
        return [], reply, str(err), provider
    return remapped, reply, None, provider


def chat_ai(
    instruction: str,
    products: list[ProductItem] | None = None,
    history: list[ChatMessage] | None = None,
    settings: Settings | None = None,
    chat_id: str = "global",
    *,
    capture_instruction: bool = True,
    on_progress: ProgressCb | None = None,
) -> AiFixResult:
    """General chat OR tender fix.

    Large product lists are processed in internal batches, but the full list
    is completed in a single user message (one shot from the user's view).
    """
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
    if capture_instruction:
        try:
            capture_instruction_from_user(instruction, settings)
        except Exception as exc:  # noqa: BLE001
            logger.debug("instruction capture skipped: %s", exc)

    hist = _history_dicts(history)

    try:
        if not products:
            state = run_agent(
                instruction=instruction,
                products=[],
                history=hist,
                chat_id=chat_id,
            )
            err = state.get("error")
            reply = (state.get("reply") or "").strip()
            summary = (state.get("summary") or "").strip()
            provider = state.get("provider") or ""
            if err and not reply:
                msg = _friendly_timeout() if _is_timeout(err) else str(err)
                return AiFixResult(0, 0, msg, error=str(err), provider=provider)
            return AiFixResult(
                0, 0,
                summary or "Done.",
                reply=reply or summary or "Done.",
                provider=provider,
            )

        all_changes: list[dict] = []
        first_reply = ""
        provider = ""
        failed_batches = 0
        n = len(products)
        batch_no = 0

        for offset in range(0, n, _CHUNK_SIZE):
            batch_no += 1
            chunk = products[offset : offset + _CHUNK_SIZE]
            if on_progress:
                try:
                    on_progress(offset, n, batch_no)
                except Exception:  # noqa: BLE001
                    pass

            remapped, reply, err, prov = _run_chunk(
                instruction,
                chunk,
                offset=offset,
                total=n,
                history=hist if offset == 0 else [],
                chat_id=chat_id,
            )
            if prov:
                provider = prov
            if reply and not first_reply:
                first_reply = reply

            if err and not remapped:
                # Retry once with half-size batches for this window
                if _is_timeout(err) and len(chunk) > 15:
                    mid = max(10, len(chunk) // 2)
                    recovered = False
                    for sub_off in (0, mid):
                        if sub_off >= len(chunk):
                            break
                        sub = chunk[sub_off : sub_off + mid]
                        r2, _, e2, p2 = _run_chunk(
                            instruction,
                            sub,
                            offset=offset + sub_off,
                            total=n,
                            history=[],
                            chat_id=chat_id,
                        )
                        if p2:
                            provider = p2
                        if r2:
                            all_changes.extend(r2)
                            recovered = True
                        elif e2:
                            failed_batches += 1
                    if recovered:
                        continue
                failed_batches += 1
                logger.warning("Batch %s failed: %s", batch_no, err)
                continue

            all_changes.extend(remapped)

        if on_progress:
            try:
                on_progress(n, n, batch_no)
            except Exception:  # noqa: BLE001
                pass

        if not all_changes and not first_reply:
            msg = (
                _friendly_timeout()
                if failed_batches
                else "No changes returned. Try rephrasing the instruction."
            )
            return AiFixResult(0, 0, msg, error="timeout" if failed_batches else "empty", provider=provider)

        updated = removed = 0
        if all_changes:
            updated, removed = apply_product_changes(products, all_changes)

        summary = ""
        if updated or removed:
            summary = f"{updated} updated, {removed} removed."
        reply = first_reply or summary or "Done."
        if failed_batches and (updated or removed):
            reply = (
                f"{reply}\n\n"
                f"Finished the full list with {failed_batches} batch(es) skipped "
                f"(timeout/network). Say the same instruction again to retry those."
            )
        elif n > _CHUNK_SIZE and (updated or removed):
            reply = f"{reply}\n\nProcessed all **{n}** rows in one go."
        return AiFixResult(
            updated=updated,
            removed=removed,
            summary=summary or "Done.",
            reply=reply,
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent failed: %s", exc)
        msg = _friendly_timeout() if _is_timeout(exc) else str(exc)
        return AiFixResult(0, 0, msg, error=str(exc))


def chat_ai_fix(
    products: list[ProductItem],
    instruction: str,
    history: list[ChatMessage] | None = None,
    settings: Settings | None = None,
) -> AiFixResult:
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


def _parse_response(raw: str) -> dict:
    from app.services.ai.agent_graph import _parse_json

    return _parse_json(raw)


def _validate_name(desc: str, name: str) -> bool:
    from app.services.ai.agent_graph import _validate_name as _v

    return _v(desc, name)
