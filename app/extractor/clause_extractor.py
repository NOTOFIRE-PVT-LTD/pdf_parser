"""
Important clause / section extractor.

Locates headed sections (Payment Terms, LD, Warranty, etc.) and captures
the following body text until the next major heading.
"""

from __future__ import annotations

import re

from app.models.schemas import ImportantClause
from app.utils.patterns import CLAUSE_HEADINGS
from app.utils.text_utils import truncate


class ClauseExtractor:
    """Extract important contractual clauses from tender text."""

    def extract(self, text: str) -> list[ImportantClause]:
        clauses: list[ImportantClause] = []
        for clause_type, patterns in CLAUSE_HEADINGS.items():
            content, page_hint = self._find_section(text, patterns)
            if content:
                clauses.append(
                    ImportantClause(
                        clause_type=clause_type,
                        title=clause_type,
                        content=truncate(content, 4000),
                        page_number=page_hint,
                    )
                )
        return clauses

    def _find_section(
        self,
        text: str,
        patterns: list[str],
    ) -> tuple[str | None, int | None]:
        for pat in patterns:
            # Optional form-feed page markers we may inject later: \x0cPAGE:N\x0c
            regex = re.compile(
                rf"(?im)^(?:\d+[\.\)]\s*)?(?:{pat})\s*:?\s*$"
                rf"|(?:^|\n)\s*(?:{pat})\s*[:\-–]\s*",
            )
            m = regex.search(text)
            if not m:
                # Also allow inline headings mid-paragraph
                regex2 = re.compile(rf"(?i)(?:{pat})\s*[:\-–]\s*", re.MULTILINE)
                m = regex2.search(text)
            if not m:
                continue

            start = m.end()
            window = text[start : start + 6000]
            # End at next clause-like or numbered major heading
            end_m = re.search(
                r"(?m)^(?:\d+[\.\)]\s*)?[A-Z][A-Za-z0-9&/\- ]{4,70}\s*:?\s*$",
                window[80:] if len(window) > 80 else window,
            )
            if end_m:
                body = window[: 80 + end_m.start() if len(window) > 80 else end_m.start()]
            else:
                body = window[:2500]

            body = body.strip()
            if len(body) < 20:
                continue

            page_hint = self._page_near(text, m.start())
            return body, page_hint
        return None, None

    @staticmethod
    def _page_near(text: str, pos: int) -> int | None:
        """If text contains PAGE markers, return nearest preceding page number."""
        marker = re.compile(r"\[\[PAGE:(\d+)\]\]")
        last = None
        for m in marker.finditer(text[: pos + 1]):
            last = int(m.group(1))
        return last
