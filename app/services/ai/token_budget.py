"""Compact token helpers — keep LLM context small."""

from __future__ import annotations

import re


def approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars / token for mixed EN/HI)."""
    return max(1, len(text or "") // 4)


def clip_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def compact_history(
    messages: list[dict[str, str]],
    *,
    max_messages: int = 10,
    max_chars_each: int = 600,
    max_total_chars: int = 4000,
) -> list[dict[str, str]]:
    """Keep only recent turns and truncate each message."""
    trimmed: list[dict[str, str]] = []
    total = 0
    for msg in messages[-max_messages:]:
        role = msg.get("role") or "user"
        content = clip_text(msg.get("content") or "", max_chars_each)
        if not content:
            continue
        if total + len(content) > max_total_chars and trimmed:
            break
        trimmed.append({"role": role, "content": content})
        total += len(content)
    return trimmed


_TENDER_HINTS = re.compile(
    r"\b("
    r"fix|product|row|boq|tender|pdf|excel|supply|installation|"
    r"commission|junk|clause|qty|amount|extract|parse|name|"
    r"hat[aao]|sahi|galat|delete|remove|update"
    r")\b",
    re.I,
)


def looks_like_tender_task(text: str, has_products: bool) -> bool:
    """Cheap intent heuristic — avoids an extra LLM classify call."""
    if not has_products:
        return False
    t = (text or "").strip()
    if not t:
        return False
    if len(t) < 8 and not _TENDER_HINTS.search(t):
        return False  # "hi", "ok" → general chat
    return bool(_TENDER_HINTS.search(t))
