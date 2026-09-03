"""
Generic product-name extraction + learning from user corrections.

No PDF-specific rules: scope phrases are detected by tender verb grammar.
Corrections are saved and reused across all future PDFs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Tender / BOQ scope verbs (domain lexicon — not tied to any single PDF).
_SCOPE_VERBS: frozenset[str] = frozenset({
    "supply", "installation", "testing", "commissioning", "manufacture",
    "transportation", "wiring", "design", "provision", "execution", "hiring",
    "appointment", "erection", "fabrication", "delivery", "maintenance",
    "repair", "replacement", "upgradation", "upgrading", "setting", "fixing",
    "laying", "providing", "procurement", "construction", "deployment",
    "integration", "calibration", "overhauling", "servicing", "supervision",
    "programming",
})

# Leftover scope at start of a name after first pass (recheck).
_SCOPE_LEAD = re.compile(
    r"(?i)^(?:"
    r"(?:supply|installation|testing|commissioning|supervision|programming|"
    r"manufacture|transportation|wiring|design|provision|execution|hiring|"
    r"appointment|erection|fabrication|delivery|maintenance|repair|"
    r"replacement|procurement|construction|integration|calibration|"
    r"servicing|overhauling|upgradation|laying|providing)"
    r"[\s,/&]+"
    r")+"
)

_CLAUSE_JUNK = re.compile(
    r"(?i)(?:"
    r"meaning\s+of\s+similar\s+works|similar\s+works|"
    r"i\s*/\s*we\s+the|technical\s+bid|financial\s+bid|"
    r"^\d+\.\d+\s+(?:meaning|scope|general|definition|similar)"
    r")"
)

# Civil / execution WORK paragraphs — not supplyable products
_WORK_PHRASE = re.compile(
    r"(?i)(?:"
    r"re-?instatement|"
    r"excavation\s+of|"
    r"refilling\s+(?:of\s+)?(?:the\s+)?trench|"
    r"repairing\s+to\s+original\s+state|"
    r"payment\s+(?:will|wifi|shall)\s+be\s+made|"
    r"pro-?rata\s+basis|"
    r"work\s+includes|"
    r"shall\s+be\s+done\s+as\s+per|"
    r"alongside\s+the\s+track|"
    r"all\s+kinds?\s+of\s+soil|"
    r"clearing\s+of\s+route|"
    r"cable\s+trench\s+as\s+per|"
    r"headway\s+above|"
    r"while\s+track\s+crossing|"
    r"minimum\s+depth\s+will\s+be\s+taken|"
    r"extant\s+practice|"
    r"instructions\s+of\s+railway\s+engineer|"
    r"conforming\s+to\s+distances\s+as\s+per\s+cable\s+route"
    r")"
)

_WORK_LEAD = re.compile(
    r"(?i)^(re-?instatement|excavation|refilling|dismantling|"
    r"clearing|trenching|earthwork|backfilling|repairing)\b"
)

_SUPPLY_PRODUCT_HINT = re.compile(
    r"(?i)\b(?:"
    r"cable|relay|switch|panel|module|transformer|battery|charger|"
    r"signal|point\s+machine|axle\s+counter|led|display|rack|"
    r"connector|terminal|fuse|breaker|ips|ups|modem|router|"
    r"antenna|camera|sensor|meter|gauge|clamp|bracket|housing|"
    r"card|pcb|processor|server|printer|scanner|wire|conductor|"
    r"joint\s+kit|hdpe|gi\s+pipe|ofc|fibre|fiber"
    r")\b"
)

_MAX_NAME_CHARS = 140

_DESC_PREFIX = re.compile(r"(?i)^description\s*[:\-–]\s*")

_TRAILING_QTY = re.compile(
    r"(?i)\s*\(\s*\d+\s*nos?\.?\s*(?:each\s+)?(?:for\s+\d+\s*months?)?\s*\)\s*$"
)
_TRAILING_FOR_PERIOD = re.compile(
    r"(?i)\s*\(\s*\d+\s*nos?\.?\s*each\s+for\s+\d+\s*months?\s*\)\s*$"
)

_NAME_TAIL_SPLIT = re.compile(
    r"(?i)(?:\.\s*|\s+)(?:inspection\s*:|as\s+per\s+|conforming\s+to\s+|"
    r"suitable\s+for\s+|to\s+suit\s+|make\s*:|model\s*:|"
    r"spec(?:ification)?\.?\s*(?:no\.?)?\s*[A-Z0-9/])"
)

# Cut BOQ fluff after the real product noun phrase
_BOQ_FLUFF_CUT = re.compile(
    r"(?i)\s*(?:"
    r"and\s+all\s+other\s+accessories|"
    r"along\s+with\s+(?:its\s+)?(?:base|back[- ]?box|power)|"
    r"with\s+all\s+(?:required\s+)?(?:fittings\s+and\s+)?accessories|"
    r"as\s+per\s+(?:RDSO|IRS|IS|SEM|Drg)|"
    r"inspection\s*:|"
    r"inspection\s+charges\s*:|"
    r"payment\s+terms?\s*:|"
    r"make\s*[-:]|"
    r"or\s+similar|"
    r"for\s+testing\s+of|"
    r"of\s+medium\s+size\b|"
    r"the\s+weight\s+of\s+the\s+agent\b|"
    r"this\s+work\s+involves\b|"
    r"this\s+includes\b|"
    r"complete\s+with\s+all\b|"
    r"including\s+all\s+(?:the\s+)?accessories\b|"
    r"with\s+auto\s+dialer\b|"
    r"expandable\s+up\s*to\b|"
    r"\[\[PAGE:"
    r").*"
)

# "GSM module for Control panel with backbox" → drop trailing backbox (for-phrase already qualifies)
_DROP_BACKBOX_AFTER_FOR = re.compile(
    r"(?i)(\s+for\s+.+?)\s+with\s+back[- ]?box(?:es)?\s*$"
)


def normalize_product_description(desc: str | None) -> str | None:
    if not desc:
        return None
    text = desc.strip()
    text = _DESC_PREFIX.sub("", text).strip()
    text = text.lstrip("-–— \"'").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    text = re.sub(r"\bo\s+f\b", "of", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _desc_key(desc: str) -> str:
    return re.sub(r"\s+", " ", desc.lower().strip())


def _verb_regex(extra: frozenset[str] = frozenset()) -> str:
    verbs = _SCOPE_VERBS | extra
    return "|".join(sorted((re.escape(v) for v in verbs), key=len, reverse=True))


def _compound_scope_pattern(extra_verbs: frozenset[str] = frozenset()) -> re.Pattern[str]:
    v = _verb_regex(extra_verbs)
    return re.compile(
        rf"(?i)^"
        rf"(?:(?:{v})\s*[,/&]\s*)+"
        rf"(?:(?:{v})\s*)+"
        rf"(?:(?:&|and)\s*)?"
        rf"(?:commissioning\s+)?"
        rf"(?:of\s+)?"
    )


def _simple_scope_patterns(extra_verbs: frozenset[str] = frozenset()) -> list[re.Pattern[str]]:
    v = _verb_regex(extra_verbs)
    return [
        re.compile(rf"(?i)^(?:{v})\s+(?:and|&)\s+(?:{v})\s+of\s+"),
        re.compile(rf"(?i)^(?:{v})\s+of\s+"),
    ]


def _strip_scope_prefix(text: str, extra_verbs: frozenset[str] = frozenset()) -> str:
    while True:
        stripped = _strip_scope_prefix_once(text, extra_verbs)
        if stripped == text:
            return text
        text = stripped


def _strip_scope_prefix_once(text: str, extra_verbs: frozenset[str] = frozenset()) -> str:
    m = _compound_scope_pattern(extra_verbs).match(text)
    if m:
        return text[m.end() :].strip()
    for pat in _simple_scope_patterns(extra_verbs):
        m = pat.match(text)
        if m:
            return text[m.end() :].strip()
    return text


def _strip_learned_prefix(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        try:
            m = re.match(pattern, text)
        except re.error:
            continue
        if m:
            rest = text[m.end() :].strip()
            if rest:
                return rest
    return None


def _finish_name(text: str) -> str | None:
    text = _TRAILING_QTY.sub("", text).strip()
    text = _TRAILING_FOR_PERIOD.sub("", text).strip()

    # Prefer BOQ fluff cut (keeps "with backbox", drops "and all other accessories…")
    fluff = _BOQ_FLUFF_CUT.search(text)
    if fluff and fluff.start() >= 8:
        text = text[: fluff.start()].strip().rstrip(".,;")

    cut = _NAME_TAIL_SPLIT.search(text)
    if cut and cut.start() >= 8:
        text = text[: cut.start()].strip().rstrip(".,;")

    if ". " in text:
        first, rest = text.split(". ", 1)
        if len(first) >= 8 and (rest.lower().startswith("inspection") or len(rest) > 80):
            text = first.strip()

    # Soft trim very long names at a natural break
    if len(text) > _MAX_NAME_CHARS:
        for sep in (", with ", " with ", " including ", " complete "):
            idx = text.lower().find(sep, 20)
            if 20 <= idx <= _MAX_NAME_CHARS:
                text = text[:idx].strip().rstrip(".,;")
                break
        if len(text) > _MAX_NAME_CHARS:
            text = text[:_MAX_NAME_CHARS].rsplit(" ", 1)[0].strip().rstrip(".,;")

    text = text.strip(" .,;")
    # Drop dangling open paren without close
    if text.count("(") > text.count(")"):
        text = text.rsplit("(", 1)[0].strip().rstrip(".,;")
    # "… for Control panel with backbox" → keep the for-phrase, drop backbox
    text = _DROP_BACKBOX_AFTER_FOR.sub(r"\1", text).strip().rstrip(".,;")
    return text or None


def extract_product_name(
    desc: str | None,
    *,
    extra_verbs: frozenset[str] = frozenset(),
    learned_exact: dict[str, str] | None = None,
    learned_prefixes: list[str] | None = None,
) -> str | None:
    """
    Derive product name from BOQ description.

    Order: exact learned mapping → learned prefix patterns → generic scope grammar → tail trim.
    """
    text = normalize_product_description(desc)
    if not text:
        return None

    if learned_exact:
        hit = learned_exact.get(_desc_key(text))
        if hit:
            return recheck_product_name(desc, hit, extra_verbs=extra_verbs)

    if learned_prefixes:
        rest = _strip_learned_prefix(text, learned_prefixes)
        if rest:
            finished = _finish_name(rest)
            if finished:
                return recheck_product_name(desc, finished, extra_verbs=extra_verbs)

    text = _strip_scope_prefix(text, extra_verbs)
    name = _finish_name(text)
    return recheck_product_name(desc, name, extra_verbs=extra_verbs)


def is_clause_or_junk(text: str | None) -> bool:
    if not text or len(text.strip()) < 4:
        return True
    if _CLAUSE_JUNK.search(text):
        return True
    if re.match(r"^\d+\.\d+\s+\w", text):
        return True
    return False


def is_work_description(desc: str | None) -> bool:
    """
    True for civil/execution WORK text (trench, reinstatement, excavation notes),
    which must NOT be listed as products.
    """
    text = normalize_product_description(desc) or ""
    if not text:
        return False

    hits = len(_WORK_PHRASE.findall(text))
    has_supply_hint = bool(_SUPPLY_PRODUCT_HINT.search(text[:160]))

    # Long execution / payment paragraphs
    if len(text) >= 280 and hits >= 1:
        return True
    if hits >= 2 and len(text) >= 160:
        return True
    if hits >= 1 and len(text) >= 500:
        return True

    # Starts like a work item, not a material/equipment line
    if _WORK_LEAD.match(text) and not has_supply_hint:
        return True

    # Reinstatement / trench work titles even when shorter
    if re.search(
        r"(?i)^re-?instatement\s+of\s+(?:platform|track|road|surface)",
        text,
    ):
        return True
    if re.search(r"(?i)^excavation\s+of\s+(?:cable\s+)?trench\b", text):
        return True

    return False


def _has_scope_lead(name: str) -> bool:
    return bool(_SCOPE_LEAD.match(name))


def recheck_product_name(
    desc: str | None,
    name: str | None,
    *,
    extra_verbs: frozenset[str] = frozenset(),
) -> str | None:
    """
    Second-pass validation: strip leftover scope, reject clauses, prevent full-description bleed.
    """
    full = normalize_product_description(desc) or ""
    if not name and full:
        return extract_product_name(full, extra_verbs=extra_verbs)

    if not name:
        return None

    text = name.strip()
    if is_clause_or_junk(text):
        return None

    # Pass 1: strip leftover scope (repeat until stable)
    for _ in range(5):
        prev = text
        text = _strip_scope_prefix(text, extra_verbs)
        if _has_scope_lead(text):
            text = _SCOPE_LEAD.sub("", text).strip()
        finished = _finish_name(text)
        if finished:
            text = finished
        if text == prev:
            break

    # Pass 2: name is nearly the whole description → re-strip from source
    if full and len(text) >= len(full) * 0.82:
        retry = _finish_name(_strip_scope_prefix(full, extra_verbs))
        if retry and len(retry) < len(text):
            text = retry

    # Pass 3: still has scope lead → one more strip from original description
    if _has_scope_lead(text) and full:
        retry = _finish_name(_strip_scope_prefix(full, extra_verbs))
        if retry and not _has_scope_lead(retry):
            text = retry

    text = _finish_name(text) or text
    text = text.strip(" .,;")

    if not text or is_clause_or_junk(text) or _has_scope_lead(text):
        return None

    if full and len(text) > _MAX_NAME_CHARS:
        cut = _NAME_TAIL_SPLIT.search(text)
        if cut:
            text = text[: cut.start()].strip().rstrip(".,;")
        if len(text) > _MAX_NAME_CHARS and ". " in text:
            text = text.split(". ", 1)[0].strip()

    return text or None


def prefix_to_learned_pattern(prefix: str) -> str:
    """Turn a corrected scope prefix into a reusable regex (spacing/comma tolerant)."""
    p = re.escape(prefix.strip())
    p = p.replace(r"\ ", r"\s+")
    p = p.replace(r"\,", r"\s*,\s*")
    p = p.replace(r"\&", r"\s*&\s*")
    return rf"(?i)^{p}"


def infer_prefix(full: str, corrected_name: str) -> str | None:
    """Find scope prefix when user provides the correct product name."""
    full_n = normalize_product_description(full) or ""
    name = corrected_name.strip()
    if not full_n or not name:
        return None
    low_full, low_name = full_n.lower(), name.lower()
    if low_full == low_name:
        return None
    if low_name in low_full:
        idx = low_full.index(low_name)
        if idx > 0:
            return full_n[:idx].strip()
    if low_full.endswith(low_name):
        return full_n[: -len(name)].strip()
    return None


class ProductNameLearning:
    """Persistent store — learns from user corrections across all PDFs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"exact": {}, "prefixes": [], "extra_verbs": [], "rejected": {}}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def exact(self) -> dict[str, str]:
        return self._data.setdefault("exact", {})

    @property
    def prefix_patterns(self) -> list[str]:
        return self._data.setdefault("prefixes", [])

    @property
    def extra_verbs(self) -> frozenset[str]:
        return frozenset(self._data.get("extra_verbs", []))

    @property
    def rejected(self) -> dict[str, str]:
        return self._data.setdefault("rejected", {})

    def learn_reject(self, description: str, reason: str = "work") -> bool:
        """Remember a description is NOT a product (e.g. civil work paragraph)."""
        full = normalize_product_description(description)
        if not full:
            return False
        self.rejected[_desc_key(full)] = reason or "work"
        # Drop any exact name mapping that would revive it
        self.exact.pop(_desc_key(full), None)
        self._save()
        return True

    def is_rejected(self, desc: str | None) -> bool:
        full = normalize_product_description(desc)
        if not full:
            return False
        return _desc_key(full) in self.rejected

    def learn(self, description: str, corrected_name: str) -> bool:
        full = normalize_product_description(description)
        name = (corrected_name or "").strip()
        if not full or not name:
            return False

        self.exact[_desc_key(full)] = name

        prefix = infer_prefix(full, name)
        if prefix:
            pattern = prefix_to_learned_pattern(prefix)
            if pattern not in self.prefix_patterns:
                self.prefix_patterns.append(pattern)
            for word in re.findall(r"[a-z]+", prefix.lower()):
                if word.endswith("ing") or word.endswith("tion"):
                    verbs = self._data.setdefault("extra_verbs", [])
                    if word not in verbs and word not in _SCOPE_VERBS:
                        verbs.append(word)

        self._save()
        return True

    def resolve(self, desc: str | None) -> str | None:
        if self.is_rejected(desc):
            return None
        return extract_product_name(
            desc,
            extra_verbs=self.extra_verbs,
            learned_exact=self.exact,
            learned_prefixes=self.prefix_patterns,
        )

    def stats(self) -> dict[str, int]:
        return {
            "exact": len(self.exact),
            "prefixes": len(self.prefix_patterns),
            "extra_verbs": len(self.extra_verbs),
            "rejected": len(self.rejected),
        }


def extract_item_specs(desc: str | None) -> str | None:
    text = normalize_product_description(desc)
    if not text:
        return None
    for pattern in (
        r"(?i)as\s+per\s+[^.;)]+",
        r"(?i)conforming\s+to\s+[^.;)]+",
        r"(?i)spec(?:ification)?\.?\s*(?:no\.?)?\s*[A-Z0-9/\-]+[^.;)]*",
        r"(?i)inspection\s*:\s*[^.)]+",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(0).strip()
    return None


def extract_item_period(desc: str | None) -> str | None:
    text = normalize_product_description(desc)
    if not text:
        return None
    m = re.search(
        r"(?i)\(\s*\d+\s*nos?\.?\s*(?:each\s+)?for\s+(\d+\s*months?)\s*\)",
        text,
    )
    if m:
        return m.group(1)
    m2 = re.search(r"(?i)\bfor\s+(\d+\s*months?)\b", text)
    return m2.group(1) if m2 else None
