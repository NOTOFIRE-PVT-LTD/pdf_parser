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


# Longest-first so "includes" wins over "in", "specification" over "spec".
_SPACED_WORDS: tuple[str, ...] = tuple(
    sorted(
        {
            "specification",
            "specifications",
            "installation",
            "transportation",
            "commissioning",
            "accessories",
            "consolidating",
            "including",
            "includes",
            "following",
            "apparatus",
            "provision",
            "necessary",
            "insulated",
            "polythene",
            "regulating",
            "excavation",
            "refilling",
            "equipment",
            "materials",
            "location",
            "detector",
            "complete",
            "suitable",
            "crossing",
            "blowing",
            "drawing",
            "fixing",
            "paging",
            "thermal",
            "imager",
            "pocket",
            "trench",
            "latest",
            "without",
            "through",
            "throughout",
            "ramming",
            "supply",
            "wiring",
            "inner",
            "outer",
            "pair",
            "self",
            "talk",
            "back",
            "cable",
            "clamps",
            "earth",
            "work",
            "site",
            "after",
            "before",
            "does",
            "not",
            "the",
            "this",
            "that",
            "with",
            "from",
            "and",
            "for",
            "per",
            "dia",
            "box",
            "new",
            "main",
            "tail",
            "also",
            "into",
            "onto",
            "road",
            "track",
            "axle",
        },
        key=len,
        reverse=True,
    )
)

_ACRONYMS: tuple[str, ...] = tuple(
    sorted(
        {
            "SQMM",
            "HDPE",
            "RDSO",
            "IRS",
            "DWC",
            "RCC",
            "PBT",
            "FRP",
            "PVC",
            "LED",
            "IPS",
            "ASM",
            "OFC",
            "VHF",
            "FMS",
            "UTP",
            "CAT",
            "GI",
            "CT",
        },
        key=len,
        reverse=True,
    )
)

_TWO_LETTER_WORDS = {
    "to",
    "or",
    "in",
    "on",
    "by",
    "of",
    "as",
    "no",
    "it",
    "at",
    "an",
    "if",
    "is",
    "be",
    "so",
    "we",
}

_DESC_CHROME = re.compile(
    r"(?i)(?:tender\s+no\s*:|closing\s+date\s*/?\s*time\s*:|"
    r"\bitem\s+breakup\b|schedule\s*schedule|"
    r"page\s+\d+\s+of\s+\d+|run\s+date\s*/?\s*time|"
    r"s\.?\s*no\.?\s+item\s+(?:no|code|qty|item)|"
    r"\[\[PAGE:\d+\]\])"
)

_CONSEC_SERIALS = re.compile(
    r"(?<![A-Za-z0-9.])(?:\d{1,3})(?:\s+\d{1,3}){1,}(?![A-Za-z0-9.])"
)


def _is_joinable_token(token: str) -> bool:
    return len(token) == 1 and (token.isalnum() or token in ".-/")


def _split_known_acronyms(text: str) -> str | None:
    if not text.isupper() or len(text) < 4:
        return None
    parts: list[str] = []
    i = 0
    while i < len(text):
        hit = next((a for a in _ACRONYMS if text.startswith(a, i)), None)
        if not hit:
            return None
        parts.append(hit)
        i += len(hit)
    return " ".join(parts) if len(parts) > 1 else parts[0]


def _segment_letter_run(text: str) -> str:
    if len(text) <= 1:
        return text
    split = _split_known_acronyms(text)
    if split:
        return split
    if text.isupper() and 2 <= len(text) <= 5:
        return text
    low = text.lower()
    parts: list[str] = []
    i = 0
    while i < len(low):
        hit = next((w for w in _SPACED_WORDS if low.startswith(w, i)), None)
        if not hit:
            two = low[i : i + 2]
            rest = low[i + 2 :]
            if two in _TWO_LETTER_WORDS and (
                not rest
                or any(rest.startswith(w) for w in _SPACED_WORDS)
                or rest[:2] in _TWO_LETTER_WORDS
            ):
                hit = two
        if hit:
            parts.append(text[i : i + len(hit)])
            i += len(hit)
            continue
        acr = re.match(r"[A-Z]{2,6}", text[i:])
        if acr:
            parts.append(text[i : i + acr.end()])
            i += acr.end()
            continue
        rest = text[i:]
        rest_low = rest.lower()
        tail = next((w for w in _TWO_LETTER_WORDS if rest_low.endswith(w) and len(rest) > len(w)), None)
        if tail:
            parts.append(rest[: -len(tail)])
            parts.append(rest[-len(tail) :])
        else:
            parts.append(rest)
        break
    return " ".join(p for p in parts if p)


def _finalize_spaced_run(tokens: list[str]) -> str:
    joined = "".join(tokens)
    if re.fullmatch(r"[\d./-]+", joined):
        return joined
    chunks = re.findall(r"\d+(?:\.\d+)?|[A-Za-z]+|[./-]+", joined)
    if not chunks:
        return joined
    parts = [_segment_letter_run(ch) if ch.isalpha() else ch for ch in chunks]
    text = " ".join(parts)
    text = re.sub(r"\s+\.\s+", ".", text)
    text = re.sub(r"\s+/\s+", "/", text)
    return text


def repair_spaced_text(value: str | None) -> str | None:
    """Rejoin PDF letter-spaced fragments: 'S u p p l y' → 'Supply'."""
    if not value:
        return value
    tokens = value.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if _is_joinable_token(tokens[i]):
            j = i + 1
            while j < len(tokens) and _is_joinable_token(tokens[j]):
                j += 1
            run = tokens[i:j]
            if len(run) >= 2:
                out.append(_finalize_spaced_run(run))
            else:
                out.extend(run)
            i = j
            continue
        out.append(tokens[i])
        i += 1
    text = " ".join(out)
    text = re.sub(r"(?i)\b([a-z]{2,})\s+(ing|ed)\b", r"\1\2", text)

    def _two(match: re.Match[str]) -> str:
        word = match.group(1) + match.group(2)
        return word if word.lower() in _TWO_LETTER_WORDS else match.group(0)

    text = re.sub(r"\b([A-Za-z])\s+([A-Za-z])\b", _two, text)
    return collapse_whitespace(text) or text


def strip_description_chrome(value: str | None) -> str | None:
    """Drop page headers / tender footers glued onto a BOQ description."""
    if not value:
        return value
    cut = _DESC_CHROME.search(value)
    if cut:
        value = value[: cut.start()].strip()
    value = _CONSEC_SERIALS.sub(
        lambda m: (
            ""
            if _is_consecutive_serials(m.group(0))
            else m.group(0)
        ),
        value,
    )
    return collapse_whitespace(value) or None


def _is_consecutive_serials(chunk: str) -> bool:
    nums = [int(x) for x in chunk.split()]
    if len(nums) < 2:
        return False
    return all(nums[i] + 1 == nums[i + 1] for i in range(len(nums) - 1))


def is_list_serial(value: str | None) -> bool:
    """True for a BOQ S.No. (1–999), not a 5–6 digit railway SOR code."""
    if not value:
        return False
    raw = str(value).strip()
    if not re.fullmatch(r"\d+", raw):
        return False
    n = int(raw)
    return 1 <= n <= 999


def clean_item_description(value: str | None) -> str | None:
    """Repair spacing, drop chrome, collapse leftover whitespace."""
    text = collapse_whitespace(value)
    text = repair_spaced_text(text)
    text = strip_description_chrome(text)
    return text or None


def truncate(value: str | None, max_len: int = 2000) -> str | None:
    if not value:
        return value
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3].rstrip() + "..."


def clean_schedule_title(line: str | None) -> str:
    """Normalize IREPS/GeM schedule labels for export (itemCategory)."""
    title = collapse_whitespace(line) or (line or "").strip()
    if not title:
        return title
    # Drop trailing schedule total and wrapped bidding-unit ("Above/Below/Par")
    title = re.sub(
        r"(?i)\s+[\d,]+\.\d{2}(?:\s+above\s*/\s*below[\s/Par]*)?\s*$",
        "",
        title,
    ).strip()
    # Empty schedule-letter slot: "Schedule () 01-Supply Portion-I"
    title = re.sub(r"\(\s*\)", " ", title)
    title = re.sub(r"\[\s*\]", " ", title)
    # Caption merge can repeat the word: "Schedule Schedule 02-..."
    title = re.sub(r"(?i)\b(schedule)\b(?:\s+\1\b)+", r"\1", title)
    title = collapse_whitespace(title) or title
    title = re.sub(r"(?i)^schedule\b", "Schedule", title)
    return title.strip()


def lines_of(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
