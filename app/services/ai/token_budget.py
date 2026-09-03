"""Compact token helpers — keep LLM context small."""

from __future__ import annotations

import re


def approx_tokens(text: str) -> int:
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


_GREETING = re.compile(
    r"(?i)^(hi|hii|hello|hey|ok|okay|thanks|thank\s*you|thx|bye)\W*$"
)


def looks_like_tender_task(text: str, has_products: bool) -> bool:
    """Route to product-fix when products are loaded (unless tiny greeting)."""
    if not has_products:
        return False
    t = (text or "").strip()
    if not t:
        return False
    if _GREETING.match(t):
        return False
    return True
