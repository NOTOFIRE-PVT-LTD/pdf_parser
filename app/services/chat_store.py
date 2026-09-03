"""Persist Notofire AI chat history across browser refreshes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.models.schemas import TenderResult

logger = logging.getLogger(__name__)


def _chat_path(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "chat_history.json"


def _serialize_chat(chat: dict) -> dict[str, Any]:
    results = []
    for r in chat.get("results") or []:
        if isinstance(r, TenderResult):
            results.append(r.model_dump(mode="json"))
        elif isinstance(r, dict):
            results.append(r)
    return {
        "id": chat["id"],
        "title": chat.get("title") or "New chat",
        "created": chat.get("created") or "",
        "messages": list(chat.get("messages") or []),
        "results": results,
    }


def _deserialize_chat(raw: dict) -> dict:
    results: list[TenderResult] = []
    for item in raw.get("results") or []:
        try:
            results.append(TenderResult.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skip bad tender result in chat store: %s", exc)
    return {
        "id": str(raw.get("id") or ""),
        "title": str(raw.get("title") or "New chat"),
        "created": str(raw.get("created") or ""),
        "messages": list(raw.get("messages") or []),
        "results": results,
    }


def load_chats(cache_dir: Path) -> tuple[dict[str, dict], str | None]:
    """Return (chats_by_id, active_chat_id). Empty if nothing saved."""
    path = _chat_path(cache_dir)
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load chat history: %s", exc)
        return {}, None

    chats: dict[str, dict] = {}
    for raw in data.get("chats") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        chat = _deserialize_chat(raw)
        if chat["id"]:
            chats[chat["id"]] = chat

    active = data.get("active_chat_id")
    if active not in chats:
        active = next(iter(chats), None)
    return chats, active


def save_chats(cache_dir: Path, chats: dict[str, dict], active_chat_id: str | None) -> None:
    """Write all chats + active id to disk."""
    path = _chat_path(cache_dir)
    payload = {
        "active_chat_id": active_chat_id,
        "chats": [_serialize_chat(c) for c in chats.values()],
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Could not save chat history: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
