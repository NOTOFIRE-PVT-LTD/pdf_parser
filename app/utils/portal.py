"""Detect tender portal / document family from PDF text."""

from __future__ import annotations

import re

_GEM = re.compile(
    r"(?i)\b(?:gem\.gov\.in|government\s+e[\-\s]?marketplace|\bgem\b|"
    r"bid\s*number|gem\s*bid|catalogue\s*id|category\s*id|"
    r"ministry\s*/\s*department|buyer\s*details)\b"
)
_IREPS = re.compile(
    r"(?i)\b(?:ireps|nit\s*header|western\s*rly|eastern\s*rly|"
    r"northern\s*rly|southern\s*rly|tender\s+document|"
    r"advertised\s*value|period\s+of\s+completion|"
    r"schedule\s*\(\s*\)\s*[A-Z]|description\s*:\-)\b"
)


def detect_portal(text: str) -> str:
    """
    Return 'gem' | 'ireps' | 'generic'.

    Used only to prefer the right extractors — never invents field values.
    """
    sample = (text or "")[:12000]
    gem_hits = len(_GEM.findall(sample))
    ireps_hits = len(_IREPS.findall(sample))
    if gem_hits >= 2 and gem_hits > ireps_hits:
        return "gem"
    if ireps_hits >= 2:
        return "ireps"
    if gem_hits >= 1:
        return "gem"
    return "generic"
