"""
Table extraction and multi-page merge.

Detects tables via pdfplumber + PyMuPDF, normalizes headers using
PRODUCT_HEADER_ALIASES, and stitches continuation tables across pages
when column signatures match.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf

from app.config import Settings, get_settings
from app.utils.patterns import PRODUCT_HEADER_ALIASES
from app.utils.text_utils import collapse_whitespace

logger = logging.getLogger(__name__)

# Short tokens that must not steal a more specific neighbouring column
# (e.g. "unit" must not claim "Unit Rate"; "qty" must not claim "Qty Unit").
_WEAK_HEADER_ALIASES = {
    "sl",
    "sn",
    "sr",
    "#",
    "no",
    "qty",
    "amt",
    "code",
    "unit",
    "rate",
    "nos",
    "product",
}

# pdfplumber table_settings tried when the default line-based extract is weak.
_FALLBACK_TABLE_SETTINGS: list[dict[str, Any]] = [
    {
        "vertical_strategy": "lines",
        "horizontal_strategy": "text",
        "snap_tolerance": 4,
        "intersection_tolerance": 5,
        "text_y_tolerance": 3,
    },
    {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "min_words_vertical": 2,
        "min_words_horizontal": 1,
        "snap_tolerance": 3,
        "intersection_tolerance": 5,
        "text_x_tolerance": 2,
        "text_y_tolerance": 3,
    },
]


@dataclass
class ExtractedTable:
    page_number: int
    headers: list[str]
    rows: list[list[str]]
    mapped_headers: dict[str, int] = field(default_factory=dict)  # canonical → col idx
    is_product_table: bool = False


class TableParser:
    """Extract and merge tables from a PDF."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract_tables(
        self,
        path: str | Path,
        password: str | None = None,
    ) -> list[ExtractedTable]:
        path = Path(path)
        tables: list[ExtractedTable] = []
        open_kwargs: dict[str, Any] = {}
        if password:
            open_kwargs["password"] = password

        fitz_by_page, fitz_page_count = self._pymupdf_tables(path, password)

        try:
            pdf_ctx = pdfplumber.open(path, **open_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Table extraction failed to open PDF with pdfplumber: %s", exc)
            pdf_ctx = None

        last_product_headers: list[str] | None = None
        last_product_mapped: dict[str, int] | None = None

        try:
            plumber_pages = list(pdf_ctx.pages[: self.settings.max_pages]) if pdf_ctx else []
            n_pages = max(len(plumber_pages), fitz_page_count)
            for i in range(n_pages):
                page_number = i + 1
                raw_candidates: list[list[list[Any]]] = list(fitz_by_page.get(page_number, []))
                page = plumber_pages[i] if i < len(plumber_pages) else None
                if page is not None:
                    raw_candidates.extend(self._plumber_page_tables(page, fallback=False))

                parsed_list = self._parse_raw_list(
                    raw_candidates,
                    page_number,
                    last_product_headers,
                    last_product_mapped,
                )
                if self._page_extraction_weak(parsed_list) and page is not None:
                    extra = self._plumber_page_tables(page, fallback=True)
                    parsed_list.extend(
                        self._parse_raw_list(
                            extra,
                            page_number,
                            last_product_headers,
                            last_product_mapped,
                        )
                    )

                for parsed in self._select_page_tables(parsed_list):
                    tables.append(parsed)
                    if parsed.is_product_table:
                        last_product_headers = parsed.headers
                        last_product_mapped = parsed.mapped_headers
        finally:
            if pdf_ctx is not None:
                pdf_ctx.close()

        return self.merge_multipage_tables(tables)

    def _plumber_page_tables(
        self, page: Any, *, fallback: bool
    ) -> list[list[list[Any]]]:
        raw_tables: list[list[list[Any]]] = []
        settings_list: list[dict[str, Any] | None]
        if fallback:
            settings_list = list(_FALLBACK_TABLE_SETTINGS)
        else:
            settings_list = [None]
        for settings in settings_list:
            try:
                if settings:
                    found = page.extract_tables(table_settings=settings) or []
                else:
                    found = page.extract_tables() or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Table extraction failed on a page: %s", exc)
                continue
            for raw in found:
                if self._raw_table_plausible(raw):
                    raw_tables.append(raw)
        return raw_tables

    def _pymupdf_tables(
        self, path: Path, password: str | None
    ) -> tuple[dict[int, list[list[list[Any]]]], int]:
        out: dict[int, list[list[list[Any]]]] = {}
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PyMuPDF table extraction failed to open PDF: %s", exc)
            return out, 0
        try:
            if doc.is_encrypted and not doc.authenticate(password or ""):
                return out, 0
            max_pages = min(doc.page_count, self.settings.max_pages)
            for i in range(max_pages):
                try:
                    finder = doc[i].find_tables()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("PyMuPDF find_tables failed on page %d: %s", i + 1, exc)
                    continue
                found: list[list[list[Any]]] = []
                for tab in getattr(finder, "tables", None) or []:
                    try:
                        extracted = tab.extract()
                    except Exception:  # noqa: BLE001
                        continue
                    if self._raw_table_plausible(extracted):
                        found.append(extracted)
                if found:
                    out[i + 1] = found
            return out, max_pages
        finally:
            doc.close()

    def _parse_raw_list(
        self,
        raw_tables: list[list[list[Any]]],
        page_number: int,
        fallback_headers: list[str] | None,
        fallback_mapped: dict[str, int] | None,
    ) -> list[ExtractedTable]:
        parsed: list[ExtractedTable] = []
        for raw in raw_tables:
            try:
                table = self._normalize_table(
                    raw,
                    page_number=page_number,
                    fallback_headers=fallback_headers,
                    fallback_mapped=fallback_mapped,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Table normalization failed on page %d: %s", page_number, exc)
                continue
            if table and len(table.rows) >= 1:
                parsed.append(table)
        return parsed

    @staticmethod
    def _raw_table_plausible(raw: list[list[Any]] | None) -> bool:
        if not raw or len(raw) < 1:
            return False
        cols = max((len(r) for r in raw if r is not None), default=0)
        return 1 <= cols <= 25

    def _page_extraction_weak(self, tables: list[ExtractedTable]) -> bool:
        product = [t for t in tables if t.is_product_table]
        if not product:
            return True
        best = max(product, key=self._table_quality)
        if len(best.mapped_headers) < 3:
            return True
        return not self._numeric_fields_sane(best.mapped_headers, best.rows)

    def _select_page_tables(self, parsed_list: list[ExtractedTable]) -> list[ExtractedTable]:
        if not parsed_list:
            return []
        unique: list[ExtractedTable] = []
        for table in sorted(parsed_list, key=self._table_quality, reverse=True):
            if any(self._is_duplicate_table(table, kept) for kept in unique):
                continue
            unique.append(table)
        return unique

    def _table_quality(self, table: ExtractedTable) -> tuple:
        filled = sum(1 for row in table.rows for cell in row if (cell or "").strip())
        sane = 1 if self._numeric_fields_sane(table.mapped_headers, table.rows) else 0
        return (
            sane,
            1 if table.is_product_table else 0,
            len(table.mapped_headers),
            len(table.rows),
            filled,
        )

    @staticmethod
    def _is_duplicate_table(a: ExtractedTable, b: ExtractedTable) -> bool:
        if a.is_product_table and b.is_product_table:
            if set(a.mapped_headers.keys()) == set(b.mapped_headers.keys()):
                return True
        return TableParser._row_text_overlap(a, b) and abs(len(a.headers) - len(b.headers)) <= 1

    @staticmethod
    def _row_text_overlap(a: ExtractedTable, b: ExtractedTable) -> bool:
        def cells(table: ExtractedTable) -> set[str]:
            values: set[str] = set()
            for row in table.rows[:6]:
                for cell in row:
                    text = (cell or "").strip().lower()[:60]
                    if len(text) >= 4:
                        values.add(text)
            return values

        left, right = cells(a), cells(b)
        if not left or not right:
            return False
        overlap = len(left & right)
        return overlap >= min(3, min(len(left), len(right)))

    def _normalize_table(
        self,
        raw: list[list[Any]],
        page_number: int,
        fallback_headers: list[str] | None = None,
        fallback_mapped: dict[str, int] | None = None,
    ) -> ExtractedTable | None:
        if not raw or len(raw) < self.settings.table_min_rows:
            return None

        cleaned: list[list[str]] = []
        for row in raw:
            cleaned.append(
                [collapse_whitespace(str(c) if c is not None else "") or "" for c in row]
            )

        # Drop fully empty rows
        cleaned = [r for r in cleaned if any(c.strip() for c in r)]
        if len(cleaned) < self.settings.table_min_rows:
            return None

        cleaned = self._merge_wrapped_header_rows(cleaned)

        header_row = cleaned[0]
        schedule_caption: str | None = None

        if (
            fallback_headers
            and len(header_row) == len(fallback_headers)
            and not self._looks_like_header_row(header_row)
        ):
            # This "table" is a continuation with no repeated header — reuse
            # the last product table's header/mapping and treat every row
            # (including this first one) as data. Drop rows that are really
            # page letterhead/footer text pdfplumber picked up inside the
            # table's bounding box (e.g. "DYCSTE-...-RLY TENDER DOCUMENT", or
            # a stray "ender N" fragment of "Tender No") — left in, these
            # get glued onto the previous real item's description as a fake
            # continuation, or occasionally fabricate a bogus row outright.
            headers = list(fallback_headers)
            mapped = dict(fallback_mapped or {})
            data_rows = [r for r in cleaned if not self._looks_like_stray_noise_row(r)]
        elif not self._looks_like_header_row(header_row):
            # A NEW (differently-shaped) table whose real header isn't row 0
            # either — some NITs put one or two caption rows first, e.g.
            # ["Schedule", "Schedule A-Annexure..."] then ["Item- 1", "SOR
            # items"], before the actual "S No. | Item No. | Description |
            # Unit | Qty | Rate | Amount" header. Scan a few rows ahead for
            # one that actually looks like a header; keep the skipped rows'
            # text as a schedule-title hint instead of just discarding them.
            header_idx = None
            for idx in range(1, min(4, len(cleaned) - 1) + 1):
                if self._looks_like_header_row(cleaned[idx]):
                    header_idx = idx
                    break
            if header_idx is not None:
                caption_text = collapse_whitespace(
                    " ".join(c for row in cleaned[:header_idx] for c in row if c.strip())
                )
                if caption_text:
                    schedule_caption = caption_text
                header_row = cleaned[header_idx]
                data_rows = cleaned[header_idx + 1 :]
                headers = [h if h else f"col_{idx}" for idx, h in enumerate(header_row)]
                mapped = self.map_headers(headers)
            else:
                # No real header found nearby — fall back to the original
                # naive behavior rather than guessing further.
                data_rows = cleaned[1:]
                headers = [h if h else f"col_{idx}" for idx, h in enumerate(header_row)]
                mapped = self.map_headers(headers)
        else:
            data_rows = cleaned[1:]
            headers = [h if h else f"col_{idx}" for idx, h in enumerate(header_row)]
            mapped = self.map_headers(headers)

        if schedule_caption:
            # Prepend a synthetic "Schedule ..." row so ProductExtractor's
            # existing schedule-title detection (which looks for a row
            # starting with "schedule") picks up the caption we recovered,
            # instead of losing it when the caption rows were consumed here.
            data_rows = [[schedule_caption] + [""] * (len(header_row) - 1)] + data_rows

        headers, mapped, data_rows = self._align_table_width(headers, mapped, data_rows)

        is_product = self._looks_like_product_table(mapped, headers)
        if is_product and not self._numeric_fields_sane(mapped, data_rows):
            # Headers matched, but qty/rate/amount cells are clearly shifted.
            # Keep the table for NIT key/value scraping, but do not treat it
            # as a BOQ — the text parser is safer than mis-aligned columns.
            is_product = False

        return ExtractedTable(
            page_number=page_number,
            headers=headers,
            rows=data_rows,
            mapped_headers=mapped,
            is_product_table=is_product,
        )

    def _merge_wrapped_header_rows(self, cleaned: list[list[str]]) -> list[list[str]]:
        """
        IREPS / GeM headers often wrap onto a second line:

            S.No. | Item | Item Qty | ... | Bidding
                  | Code |          |     | Unit

        Merge those continuation tokens into the header before we decide
        what the columns are. A real data row is never merged.
        """
        if len(cleaned) < 2:
            return cleaned
        rows = [list(r) for r in cleaned]
        header = rows[0]
        consumed = 0
        for i in range(1, min(3, len(rows))):
            nxt = rows[i]
            if not self._is_header_continuation(header, nxt):
                break
            header = self._combine_header_rows(header, nxt)
            consumed = i
        if consumed:
            return [header] + rows[consumed + 1 :]
        return rows

    def _is_header_continuation(self, header_row: list[str], next_row: list[str]) -> bool:
        if not header_row or not next_row:
            return False
        if not (
            self._looks_like_header_row(header_row)
            or self._looks_like_partial_header(header_row)
        ):
            return False
        if self._looks_like_data_row(next_row):
            return False
        filled = [c.strip() for c in next_row if c.strip()]
        if not filled:
            return False
        if any(len(c) > 40 for c in filled):
            return False
        if all(len(c) <= 24 and not re.search(r"\d{2,}", c) for c in filled):
            merged = self._combine_header_rows(header_row, next_row)
            if len(self.map_headers(merged)) > len(self.map_headers(header_row)):
                return True
            return any(
                re.fullmatch(
                    r"(?i)code|unit|no\.?|particulars?|of\s+item|description|qty|rate|amount",
                    c,
                )
                for c in filled
            )
        if self._looks_like_header_row(next_row):
            return True
        return False

    @staticmethod
    def _looks_like_partial_header(row: list[str]) -> bool:
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) < 2:
            return False
        headerish = sum(
            1
            for c in cells
            if re.search(
                r"(?i)\b(?:s\.?\s*no|item|quantity|qty|unit|rate|amount|desc|code|escl)\b",
                c,
            )
        )
        return headerish >= 2 and not TableParser._looks_like_data_row(row)

    @staticmethod
    def _combine_header_rows(a: list[str], b: list[str]) -> list[str]:
        n = max(len(a), len(b))
        left = list(a) + [""] * (n - len(a))
        right = list(b) + [""] * (n - len(b))
        merged: list[str] = []
        for x, y in zip(left, right):
            x, y = x.strip(), y.strip()
            if not y or y.lower() in x.lower():
                merged.append(x)
            elif not x:
                merged.append(y)
            elif x.lower() in y.lower():
                merged.append(y)
            else:
                merged.append(f"{x} {y}".strip())
        return merged

    def _align_table_width(
        self,
        headers: list[str],
        mapped: dict[str, int],
        data_rows: list[list[str]],
    ) -> tuple[list[str], dict[str, int], list[list[str]]]:
        width = len(headers)
        if data_rows:
            width = max(width, max(len(r) for r in data_rows))
        if width > len(headers):
            headers = headers + [f"col_{idx}" for idx in range(len(headers), width)]
            mapped = self.map_headers(headers)
        data_rows = self._fit_rows(data_rows, len(headers), mapped)
        return headers, mapped, data_rows

    @staticmethod
    def _fit_rows(
        rows: list[list[str]], n: int, mapped: dict[str, int]
    ) -> list[list[str]]:
        desc_idx = mapped.get("description")
        fitted: list[list[str]] = []
        for row in rows:
            cells = list(row)
            if len(cells) < n:
                cells.extend([""] * (n - len(cells)))
            elif len(cells) > n:
                extras = [c for c in cells[n:] if c.strip()]
                cells = cells[:n]
                if extras:
                    blob = " ".join(extras)
                    target = desc_idx if desc_idx is not None and desc_idx < n else n - 1
                    cells[target] = (cells[target] + " " + blob).strip()
            fitted.append(cells)
        return fitted

    @staticmethod
    def _looks_like_header_row(row: list[str]) -> bool:
        """
        True if `row` looks like real column-label text (a header); False if
        it looks like a numeric/currency-heavy data row instead. A genuine
        header should populate most of its columns with distinct text labels
        — a mid-schedule continuation row is mostly numbers.
        """
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) < max(1, len(row) // 2):
            return False
        numeric_like = sum(
            1
            for c in cells
            if re.fullmatch(r"[\d,]+\.?\d*\s*%?", c) or c.strip().lower() == "at par"
        )
        return numeric_like < max(1, len(cells) // 2)

    @staticmethod
    def _looks_like_data_row(row: list[str]) -> bool:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            return False
        numeric_like = sum(
            1
            for c in cells
            if re.fullmatch(r"[\d,]+\.?\d*\s*%?", c) or c.strip().lower() == "at par"
        )
        return numeric_like >= max(1, len(cells) // 2)

    @staticmethod
    def _looks_like_stray_noise_row(row: list[str]) -> bool:
        """
        True if `row` is page letterhead/footer noise that pdfplumber picked
        up inside a table's bounding box, rather than genuine schedule data.

        A real continuation row either has an empty first (S.No.) cell — a
        Description:- continuation, or an item-code+amounts row — or a short
        plausible serial number there. A non-empty, non-numeric fragment in
        that position with nothing else in the row (e.g. "ender N", a stray
        slice of "Tender No") is isolated noise; a long multi-line blob there
        (e.g. "...RLY\\nTENDER DOCUMENT") is letterhead text, even if a page
        number or date fragment happens to also land in another cell.

        Legitimate "Schedule () X-Title..." marker rows are NOT noise even
        though they're long free-text in that same first cell — they carry
        the schedule name ProductExtractor attributes subsequent items to,
        and are recognized the same way it recognizes them (starts with the
        word "schedule").
        """
        if not row:
            return False
        first = row[0].strip()
        if not first:
            return False
        if re.fullmatch(r"\d{1,5}", first):
            return False
        if re.match(r"(?i)^schedule\b", first):
            return False
        rest_populated = any(c.strip() for c in row[1:])
        return not rest_populated or len(first) > 15 or "\n" in first

    @staticmethod
    def _norm_header_label(text: str) -> str:
        norm = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
        return re.sub(r"\s+", " ", norm)

    @staticmethod
    def map_headers(headers: list[str]) -> dict[str, int]:
        """Map table headers to canonical product fields (longest unique match)."""
        candidates: list[tuple[int, int, int, str]] = []
        for idx, header in enumerate(headers):
            norm = TableParser._norm_header_label(header)
            if not norm:
                continue
            tokens = norm.split()
            for canonical, aliases in PRODUCT_HEADER_ALIASES.items():
                best_score = 0
                best_alias_len = 0
                for alias in aliases:
                    alias_norm = TableParser._norm_header_label(alias)
                    if not alias_norm:
                        continue
                    score = TableParser._alias_score(norm, tokens, alias_norm)
                    if score > best_score:
                        best_score = score
                        best_alias_len = len(alias_norm)
                if best_score > 0:
                    candidates.append((best_score, best_alias_len, idx, canonical))

        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        mapped: dict[str, int] = {}
        used_idx: set[int] = set()
        used_canonical: set[str] = set()
        for score, _alias_len, idx, canonical in candidates:
            if score < 50:
                continue
            if idx in used_idx or canonical in used_canonical:
                continue
            mapped[canonical] = idx
            used_idx.add(idx)
            used_canonical.add(canonical)
        return mapped

    @staticmethod
    def _alias_score(norm: str, tokens: list[str], alias: str) -> int:
        alias_tokens = alias.split()
        weak = alias in _WEAK_HEADER_ALIASES
        if norm == alias:
            return 100 + len(alias)
        if norm.startswith(alias + " "):
            base = 80 + len(alias)
            return min(base, 55) if weak else base
        if TableParser._consecutive_tokens(tokens, alias_tokens):
            base = 70 + len(alias)
            if weak and alias_tokens != tokens:
                return 45
            return base
        if weak:
            return 0
        if alias in norm:
            return 40 + len(alias)
        return 0

    @staticmethod
    def _consecutive_tokens(tokens: list[str], alias_tokens: list[str]) -> bool:
        if not alias_tokens or len(alias_tokens) > len(tokens):
            return False
        n = len(alias_tokens)
        return any(tokens[i : i + n] == alias_tokens for i in range(len(tokens) - n + 1))

    @staticmethod
    def _numeric_fields_sane(mapped: dict[str, int], rows: list[list[str]]) -> bool:
        data = [r for r in rows if TableParser._looks_like_data_row(r)]
        if len(data) < 1:
            return True
        for field in ("item_qty", "unit_rate", "amount", "basic_value"):
            idx = mapped.get(field)
            if idx is None:
                continue
            values = [
                (row[idx] if idx < len(row) else "")
                for row in data
            ]
            nonempty = [
                v.strip()
                for v in values
                if v and v.strip() and not re.match(r"(?i)^description\s*[:\-–]", v.strip())
            ]
            if len(nonempty) < 1:
                continue
            numeric = sum(1 for v in nonempty if re.search(r"\d", v))
            if numeric / len(nonempty) < 0.5:
                return False
        return True

    @staticmethod
    def _looks_like_product_table(mapped: dict[str, int], headers: list[str]) -> bool:
        joined = " ".join(h.lower() for h in headers)
        # IREPS eligibility / declaration tables look like S.No+Description but are not BOQ
        if re.search(
            r"(?i)confirmation\s*required|remarks\s*allowed|documents?\s*uploading|"
            r"special\s*condition|eligibility",
            joined,
        ):
            return False
        product_signals = {
            "s_no",
            "item_code",
            "item_qty",
            "qty_unit",
            "unit_rate",
            "basic_value",
            "description",
            "amount",
        }
        if len(product_signals.intersection(mapped.keys())) >= 2:
            # Require qty/rate/amount OR item_code — description+sno alone is often legal text
            if mapped.keys() & {"item_qty", "qty_unit", "unit_rate", "amount", "item_code", "basic_value"}:
                return True
            return False
        keywords = (
            "qty",
            "quantity",
            "item",
            "description",
            "particular",
            "unit",
            "uom",
            "specification",
            "item code",
            "unit rate",
            "basic value",
            "bidding unit",
            "escl",
        )
        hits = sum(1 for k in keywords if k in joined)
        return hits >= 3 and bool(
            re.search(r"(?i)\b(?:qty|quantity|rate|amount|unit\s*rate|item\s*code)\b", joined)
        )

    def merge_multipage_tables(self, tables: list[ExtractedTable]) -> list[ExtractedTable]:
        """
        Merge consecutive product tables that share the same column signature.

        Handles the common NIT case where a BOQ continues on the next page
        with a repeated header row (which we strip).
        """
        if not tables:
            return []

        merged: list[ExtractedTable] = []
        current: ExtractedTable | None = None

        for table in tables:
            if current is None:
                current = ExtractedTable(
                    page_number=table.page_number,
                    headers=list(table.headers),
                    rows=[list(r) for r in table.rows],
                    mapped_headers=dict(table.mapped_headers),
                    is_product_table=table.is_product_table,
                )
                continue

            if self._same_signature(current, table):
                rows = table.rows
                # Drop repeated header if first data row mirrors headers
                if rows and self._row_matches_headers(rows[0], current.headers):
                    rows = rows[1:]
                current.rows.extend(rows)
                current.is_product_table = current.is_product_table or table.is_product_table
            else:
                merged.append(current)
                current = ExtractedTable(
                    page_number=table.page_number,
                    headers=list(table.headers),
                    rows=[list(r) for r in table.rows],
                    mapped_headers=dict(table.mapped_headers),
                    is_product_table=table.is_product_table,
                )

        if current is not None:
            merged.append(current)
        return merged

    @staticmethod
    def _same_signature(a: ExtractedTable, b: ExtractedTable) -> bool:
        if len(a.headers) != len(b.headers):
            return False
        # Prefer mapped canonical keys when available
        if a.mapped_headers and b.mapped_headers:
            return set(a.mapped_headers.keys()) == set(b.mapped_headers.keys())
        norm_a = [re.sub(r"\W+", "", h.lower()) for h in a.headers]
        norm_b = [re.sub(r"\W+", "", h.lower()) for h in b.headers]
        return norm_a == norm_b

    @staticmethod
    def _row_matches_headers(row: list[str], headers: list[str]) -> bool:
        if len(row) != len(headers):
            return False
        matches = 0
        for cell, header in zip(row, headers):
            if not cell:
                continue
            if re.sub(r"\W+", "", cell.lower()) == re.sub(r"\W+", "", header.lower()):
                matches += 1
        return matches >= max(1, len(headers) // 2)
