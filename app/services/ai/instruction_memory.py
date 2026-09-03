"""Persistent user instructions — loaded before every agent generation."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_INSTRUCTIONS = 40
_MAX_RULE_CHARS = 400


def _path() -> Path:
    return get_settings().cache_dir / "agent_instructions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_raw() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"instructions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("instructions"), list):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load agent instructions: %s", exc)
    return {"instructions": []}


def _save_raw(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_instructions() -> list[dict[str, Any]]:
    return list(_load_raw().get("instructions") or [])


def format_for_prompt(*, max_items: int = 25) -> str:
    """Block injected into system prompt before generation."""
    items = list_instructions()
    if not items:
        return ""
    lines = []
    for i, item in enumerate(items[-max_items:], start=1):
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"{i}. {text}")
    if not lines:
        return ""
    return (
        "SAVED USER INSTRUCTIONS (always follow these before generating):\n"
        + "\n".join(lines)
    )


def add_instruction(text: str, *, source: str = "user") -> bool:
    """Append a durable rule if new; keep list capped."""
    rule = re.sub(r"\s+", " ", (text or "").strip())
    if len(rule) < 12:
        return False
    if len(rule) > _MAX_RULE_CHARS:
        rule = rule[: _MAX_RULE_CHARS - 1].rstrip() + "…"

    data = _load_raw()
    items: list[dict[str, Any]] = list(data.get("instructions") or [])
    key = rule.lower()
    for existing in items:
        if str(existing.get("text") or "").strip().lower() == key:
            return False

    items.append({
        "text": rule,
        "source": source,
        "created": _now(),
    })
    data["instructions"] = items[-_MAX_INSTRUCTIONS:]
    _save_raw(data)
    return True


def clear_instructions() -> None:
    _save_raw({"instructions": []})


@lru_cache
def get_instruction_store_path() -> str:
    return str(_path())
