"""Text cleaning and normalization helpers."""

from __future__ import annotations

import re
import unicodedata


_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_PAGE_NOISE = re.compile(
    r"(?:^\s*page\s+\d+(?:\s+of\s+\d+)?\s*$)|(?:^\s*\d+\s*/\s*\d+\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def normalize_unicode(text: str) -> str:
    """NFKC-normalize and fix common PDF artefacts."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    replacements = {
        "\u00a0": " ",
        "\u200b": "",
        "\ufeff": "",
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "•": "-",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def clean_text(text: str) -> str:
    """Normalize whitespace and join hyphenated line breaks."""
    text = normalize_unicode(text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _PAGE_NOISE.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def collapse_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _MULTI_SPACE.sub(" ", value.replace("\n", " ")).strip()
    return cleaned or None


def truncate(value: str | None, max_len: int = 2000) -> str | None:
    if not value:
        return value
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3].rstrip() + "..."


def lines_of(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
