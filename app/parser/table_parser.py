"""
Table extraction and multi-page merge.

Detects tables via pdfplumber, normalizes headers using PRODUCT_HEADER_ALIASES,
and stitches continuation tables across pages when column signatures match.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from app.config import Settings, get_settings
from app.utils.patterns import PRODUCT_HEADER_ALIASES
from app.utils.text_utils import collapse_whitespace

logger = logging.getLogger(__name__)


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

        try:
            pdf_ctx = pdfplumber.open(path, **open_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Table extraction failed to open PDF: %s", exc)
            return []

        with pdf_ctx as pdf:
            # A BOQ/schedule that spans many pages doesn't always repeat its
            # header row on continuation pages — pdfplumber then hands back a
            # per-page table whose first row is really a mid-schedule data
            # row. Left alone, that row gets sacrificed as a fake header
            # (losing one real item every time) and the rest of the page is
            # never recognized as product data. Track the last confirmed
            # product table's header/column-mapping so a same-width table
            # whose "header" doesn't actually look like one can reuse it.
            last_product_headers: list[str] | None = None
            last_product_mapped: dict[str, int] | None = None
            for i, page in enumerate(pdf.pages[: self.settings.max_pages]):
                try:
                    raw_tables = page.extract_tables() or []
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Table extraction failed on page %d: %s", i + 1, exc)
                    continue
                for raw in raw_tables:
                    try:
                        parsed = self._normalize_table(
                            raw,
                            page_number=i + 1,
                            fallback_headers=last_product_headers,
                            fallback_mapped=last_product_mapped,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Table normalization failed on page %d: %s", i + 1, exc)
                        continue
                    if parsed and len(parsed.rows) >= 1:
                        tables.append(parsed)
                        if parsed.is_product_table:
                            last_product_headers = parsed.headers
                            last_product_mapped = parsed.mapped_headers

        return self.merge_multipage_tables(tables)

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

        is_product = self._looks_like_product_table(mapped, headers)

        return ExtractedTable(
            page_number=page_number,
            headers=headers,
            rows=data_rows,
            mapped_headers=mapped,
            is_product_table=is_product,
        )

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
    def map_headers(headers: list[str]) -> dict[str, int]:
        """Map table headers to canonical product fields."""
        mapped: dict[str, int] = {}
        for idx, header in enumerate(headers):
            norm = re.sub(r"[^a-z0-9.#]+", " ", header.lower()).strip()
            norm = re.sub(r"\s+", " ", norm)
            for canonical, aliases in PRODUCT_HEADER_ALIASES.items():
                if canonical in mapped:
                    continue
                for alias in aliases:
                    if norm == alias or norm.startswith(alias + " ") or alias in norm.split():
                        mapped[canonical] = idx
                        break
                if canonical in mapped:
                    break
                # Fuzzy contains
                for alias in aliases:
                    if alias in norm:
                        mapped[canonical] = idx
                        break
        return mapped

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
