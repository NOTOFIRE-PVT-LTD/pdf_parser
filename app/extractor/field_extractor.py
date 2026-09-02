"""
Rule-based field extraction from free text.

Tuned for IREPS / Indian Railway tender PDFs:
  Division/Zone header, Tender No, Closing Date/Time,
  Name of Work, Advertised Value, Earnest Money, etc.
"""

from __future__ import annotations

import re

from app.models.schemas import (
    ContactDetails,
    EligibilityInfo,
    FinancialInfo,
    ImportantDates,
    TenderInformation,
)
from app.utils.patterns import (
    AMOUNT_PATTERN,
    CONTACT_KEYWORDS,
    DATE_KEYWORDS,
    DATE_PATTERN,
    DIVISION_ZONE_HEADER,
    DOCUMENT_PATTERNS,
    ELIGIBILITY_KEYWORDS,
    EMAIL_PATTERN,
    FINANCIAL_KEYWORDS,
    PHONE_PATTERN,
    TENDER_INFO_KEYWORDS,
    URL_PATTERN,
)
from app.utils.text_utils import collapse_whitespace, truncate


class FieldExtractor:
    """Extract structured tender fields from plain text."""

    # Labels that commonly follow "Name of Work" in NIT HEADER tables
    _NIT_NEXT_LABELS = (
        r"bidding\s*type|tender\s*type|bidding\s*system|tender\s*closing|"
        r"advertised\s*value|tendering\s*section|earnest\s*money|"
        r"period\s+of\s+completion|contract\s*type|pre-?bid|"
        r"date\s*time\s*of\s*uploading|validity\s+of\s+offer"
    )

    # Stop words that must never be part of Tender No
    _TENDER_NO_STOP = re.compile(
        r"(?i)\b(?:closing|date|time|hrs?|bidders?|manual|against|"
        r"name\s+of\s+work|advertised|earnest|period|invites?)\b"
    )

    # Clean IREPS-style tender numbers only (no spaces / sentences)
    _TENDER_NO_TOKEN = re.compile(
        r"\b([A-Za-z][A-Za-z0-9]*(?:[_\-./][A-Za-z0-9]+)+|[A-Za-z0-9]+_[A-Za-z0-9_\-./]+)\b"
    )

    def extract_tender_information(
        self,
        text: str,
        tables: list | None = None,
    ) -> TenderInformation:
        data = self._extract_by_keywords(text, TENDER_INFO_KEYWORDS)

        # Prefer NIT HEADER block (Railway IREPS layout from your PDF)
        nit = self._extract_nit_header_block(text)
        for key, value in nit.items():
            if value and not data.get(key):
                data[key] = value

        # Also read key/value pairs from extracted tables
        if tables:
            from_tables = self._extract_from_nit_tables(tables)
            for key, value in from_tables.items():
                if value and (not data.get(key) or key == "name_of_work"):
                    if key == "name_of_work" and data.get("name_of_work"):
                        if len(value) > len(data["name_of_work"]):
                            data[key] = value
                    elif key == "tender_no":
                        cleaned = self._clean_tender_no(value)
                        if cleaned:
                            data[key] = cleaned
                    else:
                        data[key] = value

        # Dedicated Name of Work capture (multi-line until next NIT label)
        if not data.get("name_of_work") or len(data.get("name_of_work") or "") < 40:
            now = self._extract_name_of_work(text)
            if now and (not data.get("name_of_work") or len(now) > len(data["name_of_work"] or "")):
                data["name_of_work"] = now

        # Division / Zone from top header: "....DIVISION.../WESTERN RLY"
        div, zone = self._extract_division_zone(text)
        if div and not data.get("division_name"):
            data["division_name"] = div
        if zone and not data.get("zone"):
            data["zone"] = zone

        # Closing Date/Time — always prefer dedicated parser
        closing = self._extract_closing_datetime(text)
        if closing:
            data["closing_date_time"] = closing

        # Tender No — always run dedicated cleaner (keyword path often grabs whole lines)
        dedicated_no = self._extract_tender_no(text)
        if dedicated_no:
            data["tender_no"] = dedicated_no
        elif data.get("tender_no"):
            data["tender_no"] = self._clean_tender_no(data["tender_no"])

        # Hint maps for amount / period fields
        for amount_field in ("advertised_value", "earnest_money"):
            if data.get(amount_field):
                refined = self._refine_value(data[amount_field], "amount")
                data[amount_field] = truncate(collapse_whitespace(refined), 500)

        if data.get("closing_date_time"):
            data["closing_date_time"] = truncate(
                collapse_whitespace(data["closing_date_time"]), 100
            )

        if data.get("name_of_work"):
            data["name_of_work"] = truncate(
                collapse_whitespace(data["name_of_work"]), 1000
            )

        # Final sanitize short fields so UI never shows paragraph dumps
        data["tender_no"] = self._clean_tender_no(data.get("tender_no"))
        if data.get("period_of_completion"):
            data["period_of_completion"] = self._clean_period(data["period_of_completion"])
        if data.get("number_of_jv_member_allowed"):
            data["number_of_jv_member_allowed"] = self._clean_jv_count(
                data["number_of_jv_member_allowed"]
            )
        if data.get("division_name"):
            data["division_name"] = self._clean_division(data["division_name"])
        if data.get("zone"):
            data["zone"] = self._clean_zone(data["zone"])

        return TenderInformation(**{k: data.get(k) for k in TenderInformation.model_fields})

    def extract_financial(self, text: str) -> FinancialInfo:
        data = self._extract_by_keywords(text, FINANCIAL_KEYWORDS, value_hint="amount")
        # Mirror railway primary fields when present
        if not data.get("estimated_value"):
            adv = self._find_labeled_value(
                text, TENDER_INFO_KEYWORDS["advertised_value"], value_hint="amount"
            )
            if adv:
                data["estimated_value"] = adv
        if not data.get("emd"):
            emd = self._find_labeled_value(
                text, TENDER_INFO_KEYWORDS["earnest_money"], value_hint="amount"
            )
            if emd:
                data["emd"] = emd
        if not data.get("currency"):
            data["currency"] = self._infer_currency(text)
        return FinancialInfo(**data)

    def extract_dates(self, text: str) -> ImportantDates:
        data = self._extract_by_keywords(text, DATE_KEYWORDS, value_hint="date")
        if not data.get("bid_submission_end"):
            closing = self._extract_closing_datetime(text)
            if closing:
                data["bid_submission_end"] = closing
        if not data.get("completion_period"):
            poc = self._find_labeled_value(
                text, TENDER_INFO_KEYWORDS["period_of_completion"]
            )
            if poc:
                data["completion_period"] = poc
        return ImportantDates(**data)

    def extract_eligibility(self, text: str) -> EligibilityInfo:
        data = self._extract_by_keywords(text, ELIGIBILITY_KEYWORDS)
        return EligibilityInfo(**data)

    def extract_contact(self, text: str) -> ContactDetails:
        data = self._extract_by_keywords(text, CONTACT_KEYWORDS)
        if not data.get("email"):
            m = EMAIL_PATTERN.search(text)
            if m:
                data["email"] = m.group(0)
        if not data.get("phone"):
            phone = self._find_near_label(text, CONTACT_KEYWORDS["phone"], PHONE_PATTERN)
            if phone:
                data["phone"] = phone
        if not data.get("website"):
            m = URL_PATTERN.search(text)
            if m:
                data["website"] = m.group(0).rstrip(".,);")
        return ContactDetails(**data)

    def extract_documents(self, text: str) -> list[str]:
        found: list[str] = []
        lower = text.lower()
        section = self._slice_section(
            text,
            [
                r"documents?\s+required",
                r"list\s+of\s+documents?",
                r"enclosures?",
                r"checklist\s+of\s+documents?",
                r"mandatory\s+documents?",
            ],
        )
        search_in = section or text
        for name, pattern in DOCUMENT_PATTERNS:
            if pattern.search(search_in) or pattern.search(lower):
                if name not in found:
                    found.append(name)
        return found

    # ------------------------------------------------------------------
    # Railway-specific helpers
    # ------------------------------------------------------------------

    def _extract_division_zone(self, text: str) -> tuple[str | None, str | None]:
        """
        Parse header line:
          MUMBAI CENTRAL DIVISION-S AND T/WESTERN RLY
        Prefer the line immediately above 'TENDER DOCUMENT'.
        """
        # Prefer line above TENDER DOCUMENT
        m = re.search(
            r"(?im)^(?P<header>.+?)\s*\n\s*TENDER\s+DOCUMENT\b",
            text,
        )
        if m:
            header = collapse_whitespace(m.group("header")) or ""
            if "/" in header and "page" not in header.lower():
                left, right = header.split("/", 1)
                return left.strip() or None, right.strip() or None

        for match in DIVISION_ZONE_HEADER.finditer(text[:2500]):
            div = collapse_whitespace(match.group("div"))
            zone = collapse_whitespace(match.group("zone"))
            if div and zone and "page" not in div.lower():
                # Skip run-date style lines
                if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", div):
                    continue
                return div, zone
        return None, None

    def _extract_closing_datetime(self, text: str) -> str | None:
        m = re.search(
            r"(?i)(?:tender\s+)?closing\s*date(?:\s*/?\s*time|\s+time)?\s*[:\-–]?\s*"
            r"(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            text,
        )
        if m:
            return m.group(1).strip()
        return None

    def _extract_tender_no(self, text: str) -> str | None:
        """
        Extract only the tender number token.

        Prefers header line:
          Tender No: DYCSTE_Works_PSSA_02R
        Never returns the following Closing Date / paragraph text.
        """
        patterns = [
            # Header: Tender No: CODE   Closing Date/Time: ...
            r"(?im)(?:^|\n)\s*tender\s*no\.?\s*[:\-–]\s*([A-Za-z0-9][A-Za-z0-9_\-./]*)",
            # Mid sentence: against Tender No CODE Closing...
            r"(?i)against\s+tender\s*no\.?\s*[:\-–]?\s*([A-Za-z0-9][A-Za-z0-9_\-./]*)",
            r"(?i)tender\s*no\.?\s*[:\-–]?\s*([A-Za-z0-9][A-Za-z0-9_\-./]*)",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if not m:
                continue
            cleaned = self._clean_tender_no(m.group(1))
            if cleaned:
                return cleaned
        return None

    def _clean_tender_no(self, value: str | None) -> str | None:
        if not value:
            return None
        value = collapse_whitespace(value) or ""
        # Cut at stop words / closing date remnants
        value = self._TENDER_NO_STOP.split(value, maxsplit=1)[0].strip(" :-–/\t")
        # First whitespace-separated token only
        token = value.split()[0] if value.split() else ""
        token = token.strip(" .,;:|\"'")
        # Must look like an ID (has underscore/hyphen/slash or alnum code), not a sentence
        if not token or " " in token:
            return None
        if len(token) < 3 or len(token) > 80:
            return None
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$", token):
            return None
        # Reject pure words like "Open" / "Normal"
        if token.isalpha() and len(token) < 8:
            return None
        return token

    @staticmethod
    def _clean_period(value: str) -> str:
        m = re.search(
            r"(?i)\b(\d+(?:\.\d+)?\s*(?:months?|days?|years?|weeks?))\b",
            value,
        )
        if m:
            return collapse_whitespace(m.group(1)) or value
        m2 = re.search(r"\b(\d+)\b", value)
        return m2.group(1) if m2 else truncate(collapse_whitespace(value), 50) or value

    @staticmethod
    def _clean_jv_count(value: str) -> str:
        m = re.search(r"\b(\d+)\b", value)
        return m.group(1) if m else truncate(collapse_whitespace(value), 20) or value

    @staticmethod
    def _clean_division(value: str) -> str | None:
        value = collapse_whitespace(value) or ""
        # Keep only left side if slash-separated header leaked in
        if "/" in value and len(value) < 120:
            value = value.split("/", 1)[0].strip()
        # Reject paragraph dumps
        if len(value) > 120 or value.lower().startswith("dy."):
            return truncate(value, 120)
        return value or None

    @staticmethod
    def _clean_zone(value: str) -> str | None:
        value = collapse_whitespace(value) or ""
        value = value.split()[0:3]
        value = " ".join(value)
        if len(value) > 40:
            value = value[:40]
        return value or None

    def _extract_name_of_work(self, text: str) -> str | None:
        """
        Capture full Name of Work from NIT HEADER.

        In Railway PDFs the label and value are often without a colon, and the
        value spans until the next header field (e.g. Bidding type).
        """
        pattern = re.compile(
            rf"(?is)name\s+of\s+(?:the\s+)?work\s*[:\-–]?\s*"
            rf"(?P<val>.+?)"
            rf"(?=\n\s*(?:{self._NIT_NEXT_LABELS})\b|\Z)"
        )
        m = pattern.search(text)
        if not m:
            return None
        val = collapse_whitespace(m.group("val"))
        if not val or self._is_placeholder(val) or self._looks_like_label(val):
            return None
        # Drop trailing table labels accidentally included
        val = re.split(
            rf"(?i)\s(?=(?:{self._NIT_NEXT_LABELS})\b)",
            val,
            maxsplit=1,
        )[0].strip()
        return truncate(val, 1000)

    def _extract_nit_header_block(self, text: str) -> dict[str, str | None]:
        """Pull labeled fields from the '1. NIT HEADER' section when present."""
        out: dict[str, str | None] = {}
        m = re.search(r"(?is)(?:\d+\.\s*)?nit\s*header\b(.*?)(?=\n\s*(?:\d+\.\s*)?[A-Z][A-Za-z ].{0,40}\n|\Z)", text)
        block = m.group(1) if m else text[:6000]

        # Name of Work first (longest field)
        out["name_of_work"] = self._extract_name_of_work(block)

        pairs = {
            "advertised_value": r"advertised\s*value",
            "earnest_money": r"earnest\s*money(?:\s*\(?\s*rs\.?\s*\)?)?",
            "period_of_completion": r"period\s+of\s+completion",
            "number_of_jv_member_allowed": r"number\s+of\s+jv\s+member(?:s)?\s+allowed",
            "closing_date_time": r"tender\s*closing\s*date(?:\s*time)?|closing\s*date(?:\s*/?\s*time)?",
            "bidding_type": r"bidding\s*type",
            "tender_type": r"tender\s*type",
            "contract_type": r"contract\s*type",
            "tender_doc_cost": r"tender\s*doc\.?\s*cost(?:\s*\(?\s*rs\.?\s*\)?)?",
            "bid_validity_days": r"validity\s+of\s+offer(?:\s*\(?\s*days?\s*\)?)?",
            "bidding_system": r"bidding\s*system",
            "pre_bid_conference": r"pre-?bid\s+conference(?:\s+required)?",
            "bidding_style": r"bidding\s*style",
            "contract_category": r"contract\s*category",
            "published_date": r"date\s*time\s*of\s*uploading\s*tender|uploading\s*tender",
            "reference_no": r"reference\s*(?:no\.?|number)",
            "status": r"tender\s*status|status\s*of\s*tender",
        }
        for field, label in pairs.items():
            hint = "amount" if field in {
                "advertised_value",
                "earnest_money",
                "tender_doc_cost",
            } else None
            if field == "closing_date_time":
                hint = "datetime"
            if field == "published_date":
                hint = "datetime"
            if field == "bid_validity_days":
                hint = "days"
            # Same-line: Label <spaces> Value  (common in 4-column NIT tables)
            pm = re.search(
                rf"(?im)(?:{label})\s*[:\-–]?\s+(?P<val>\S[^\n]*?)(?=\s{{2,}}[A-Z][a-z]|\s{{2,}}Are\s|\s{{2,}}Number\s|\n|$)",
                block,
            )
            if pm:
                out[field] = truncate(
                    collapse_whitespace(self._refine_value(pm.group("val"), hint)),
                    500,
                )
                continue
            val = self._find_labeled_value(block, [label], value_hint=hint)
            if val:
                out[field] = val
        return out

    def _extract_from_nit_tables(self, tables: list) -> dict[str, str | None]:
        """Read NIT header tables — label cell followed by value cell."""
        out: dict[str, str | None] = {}
        label_map = [
            ("name_of_work", re.compile(r"(?i)^name\s+of\s+(?:the\s+)?work$")),
            ("advertised_value", re.compile(r"(?i)^advertised\s*value$")),
            ("earnest_money", re.compile(r"(?i)^earnest\s*money")),
            ("period_of_completion", re.compile(r"(?i)^period\s+of\s+completion$")),
            ("number_of_jv_member_allowed", re.compile(r"(?i)^number\s+of\s+jv\s+member")),
            ("closing_date_time", re.compile(r"(?i)^(?:tender\s+)?closing\s*date")),
            ("tender_no", re.compile(r"(?i)^tender\s*no\.?$")),
        ]

        for table in tables:
            rows = [table.headers] + table.rows if table.headers else table.rows
            for row in rows:
                cells = [collapse_whitespace(str(c or "")) or "" for c in row]
                self._apply_nit_row(cells, label_map, out)
        return out

    def _apply_nit_row(
        self,
        cells: list[str],
        label_map: list[tuple[str, re.Pattern[str]]],
        out: dict[str, str | None],
    ) -> None:
        for i, cell in enumerate(cells):
            field = self._nit_field_for_cell(cell.strip(), label_map)
            if not field:
                continue
            value = self._next_nonempty_cell(cells, i + 1)
            if not value or self._is_placeholder(value):
                continue
            if field in {"advertised_value", "earnest_money"}:
                value = self._refine_value(value, "amount")
            elif field == "closing_date_time":
                value = self._refine_value(value, "datetime")
            if field == "name_of_work" and out.get(field):
                if len(value) > len(out[field] or ""):
                    out[field] = truncate(value, 1000)
            elif not out.get(field):
                limit = 1000 if field == "name_of_work" else 500
                out[field] = truncate(value, limit)

    @staticmethod
    def _nit_field_for_cell(
        cell: str, label_map: list[tuple[str, re.Pattern[str]]]
    ) -> str | None:
        for field, pattern in label_map:
            if pattern.search(cell):
                return field
        return None

    @staticmethod
    def _next_nonempty_cell(cells: list[str], start: int) -> str | None:
        for j in range(start, len(cells)):
            if cells[j].strip():
                return cells[j].strip()
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_by_keywords(
        self,
        text: str,
        keyword_map: dict[str, list[str]],
        value_hint: str | None = None,
    ) -> dict[str, str | None]:
        result: dict[str, str | None] = {k: None for k in keyword_map}
        for field, patterns in keyword_map.items():
            hint = value_hint
            if field in {"advertised_value", "earnest_money"}:
                hint = "amount"
            if field == "closing_date_time":
                hint = "datetime"
            if field == "tender_no":
                hint = "tender_no"
            value = self._find_labeled_value(text, patterns, value_hint=hint)
            if field == "tender_no":
                value = self._clean_tender_no(value)
            result[field] = truncate(collapse_whitespace(value), 500) if value else None
        return result

    def _find_labeled_value(
        self,
        text: str,
        label_patterns: list[str],
        value_hint: str | None = None,
    ) -> str | None:
        for label in label_patterns:
            pattern = re.compile(
                rf"(?im)(?:^|\n)\s*(?:{label})\s*[:\-–]\s*(?P<val>[^\n]+)"
            )
            m = pattern.search(text)
            if m:
                val = m.group("val").strip()
                if val and not self._is_placeholder(val):
                    return self._refine_value(val, value_hint)

            pattern2 = re.compile(
                rf"(?im)(?:^|\n)\s*(?:{label})\s*[:\-–]?\s*\n\s*(?P<val>[^\n]+)"
            )
            m2 = pattern2.search(text)
            if m2:
                val = m2.group("val").strip()
                if val and not self._is_placeholder(val) and not self._looks_like_label(val):
                    return self._refine_value(val, value_hint)

            # Inline "Label value" without colon (common in NIT HEADER tables)
            if value_hint == "amount":
                val_group = (
                    r"(?P<val>(?:Rs\.?|INR|₹)?\s*[\d,][\d,.]*(?:\s*(?:Lakhs?|Lacs?|Crores?|Cr\.?))?)"
                )
            elif value_hint in {"date", "datetime"}:
                val_group = (
                    rf"(?P<val>{DATE_PATTERN.pattern}(?:\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?)"
                )
            elif value_hint == "tender_no":
                # Strict: no spaces — ID token only
                val_group = r"(?P<val>[A-Za-z0-9][A-Za-z0-9_\-./]*)"
            else:
                # Text fields: keep units like "24 Months" (do not prefer bare digits)
                val_group = r"(?P<val>[A-Za-z0-9][A-Za-z0-9 /\-_.%]{1,200})"

            pattern3 = re.compile(rf"(?i)(?:{label})\s{{1,6}}{val_group}")
            m3 = pattern3.search(text)
            if m3:
                val = m3.group("val").strip(" .;|")
                if val and not self._is_placeholder(val):
                    return self._refine_value(val, value_hint)
        return None

    def _refine_value(self, value: str, value_hint: str | None) -> str:
        value = value.strip().strip("|").strip()
        if value_hint == "tender_no":
            cleaned = self._clean_tender_no(value)
            if cleaned:
                return cleaned
            parts = value.split()
            return parts[0] if parts else value

        # For long "Name of Work" keep more of the line (cut at double-space / next label)
        if value_hint not in {"amount", "date", "datetime"}:
            # Stop at next known NIT HEADER label on the same line
            value = re.split(
                r"\s{2,}|\t|\|(?=\s)|(?=\b(?:Tender No|Closing Date|Advertised Value|"
                r"Earnest Money|Contract Type|Contract Category|Bidding Start|"
                r"Are JV|Are Consortium|Tender Doc|Validity of Offer|"
                r"Tendering Section|Bidding Style|Bidding Unit)\b)",
                value,
                maxsplit=1,
                flags=re.I,
            )[0].strip()
        else:
            value = re.split(r"\s{2,}|\t|\|", value)[0].strip()

        if value_hint == "amount":
            m = AMOUNT_PATTERN.search(value)
            if m:
                return m.group(0).strip()
        if value_hint in {"date", "datetime"}:
            m = re.search(
                rf"{DATE_PATTERN.pattern}(?:\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?",
                value,
                re.I,
            )
            if m:
                return m.group(0).strip()
            return value
        if value_hint == "days":
            m = re.search(r"\b(\d{1,4})\b", value)
            if m:
                return m.group(1)
            return value
        return value

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        v = value.strip().lower()
        return v in {"", "-", "--", "na", "n/a", "nil", "none", "null", "tbd", "..."}

    @staticmethod
    def _looks_like_label(value: str) -> bool:
        return bool(re.match(r"^[A-Za-z ]{3,40}:?\s*$", value)) and ":" not in value[1:]

    @staticmethod
    def _infer_currency(text: str) -> str | None:
        if re.search(r"\b(?:INR|Rs\.?|₹|rupees?)\b", text, re.I):
            return "INR"
        if re.search(r"\b(?:USD|\$)\b", text):
            return "USD"
        if re.search(r"\b(?:EUR|€)\b", text):
            return "EUR"
        return None

    def _find_near_label(
        self,
        text: str,
        label_patterns: list[str],
        value_pattern: re.Pattern[str],
    ) -> str | None:
        for label in label_patterns:
            for m in re.finditer(rf"(?im)(?:{label}).{{0,80}}", text):
                window = text[m.start() : m.end() + 40]
                vm = value_pattern.search(window)
                if vm:
                    return vm.group(0).strip()
        return None

    def _slice_section(self, text: str, heading_patterns: list[str]) -> str | None:
        for hp in heading_patterns:
            m = re.search(rf"(?im)^(?:\d+[\).\s]+)?(?:{hp})\b.*$", text)
            if not m:
                continue
            start = m.end()
            end_m = re.search(
                r"(?m)^(?:\d+[\).\s]+)?[A-Z][A-Za-z0-9 /\-]{4,60}$",
                text[start : start + 8000],
            )
            end = start + (end_m.start() if end_m else min(4000, len(text) - start))
            return text[start:end]
        return None
