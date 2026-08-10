"""
Product / schedule extraction.

Goal: extract EVERY schedule / BOQ line item from the tender.
Merges table extraction + IREPS text parsing + description pairing,
across multiple schedules and pages. Junk/header/legal rows are dropped,
but valid items are never discarded just because numbering restarts.
"""

from __future__ import annotations

import bisect
import re
from typing import Any

from app.models.schemas import ProductItem
from app.parser.table_parser import ExtractedTable, TableParser
from app.utils.patterns import DESCRIPTION_LINE, PRODUCT_SECTION_HINTS
from app.utils.text_utils import collapse_whitespace, truncate

# Qty units seen across IREPS schedules (personnel + materials/NS items)
_UNIT = (
    r"Months?|Numbers?|Nos?\.?|Sets?|Each|Kgs?|Mtrs?|Metres?|Meters?|"
    r"Units?|Lumpsum|Job|Hour|Hrs?|Day|Days?|Pair|Pairs|Lot|Lots|"
    r"Cum|Cmt|Sqm|Rmt|Kg|Ton|Tonne|Litres?|Ltr|Packet|Pkts?"
)

# Item codes: numeric (1, 12) OR alphanumeric (NS1, NS12, A1, …)
_CODE = r"[A-Za-z]{1,6}\d{1,5}|\d{1,6}"

# Layout A: S.No Qty Unit Rate Basic Escl Amount  (code on next line)
IREPS_ROW_NO_CODE = re.compile(
    rf"(?i)^\s*(?P<sno>\d{{1,4}})\s+"
    rf"(?P<qty>[\d,]+\.\d{{2}})\s+"
    rf"(?P<unit>{_UNIT})\s+"
    rf"(?P<rate>[\d,]+\.\d{{2}})\s+"
    rf"(?P<basic>[\d,]+\.\d{{2}})\s+"
    rf"(?P<escl>AT\s*Par|[\d,]+\.?\d*\s*%?)\s+"
    rf"(?P<amount>[\d,]+\.\d{{2}})\s*"
    rf"(?P<bidunit>Rs\.?|INR)?\s*$"
)

# Layout B/C: S.No ItemCode Qty Unit Rate Basic Escl Amount (inline)
# Covers numeric codes AND NS1 / NS12 style codes
FLEX_SCHEDULE_ROW = re.compile(
    rf"(?is)(?<![0-9A-Za-z.])(?P<sno>\d{{1,4}})\s+"
    rf"(?P<code>{_CODE})\s+"
    rf"(?P<qty>[\d,]+\.\d{{2}}|\d+)\s+"
    rf"(?P<unit>{_UNIT})\s+"
    rf"(?P<rate>[\d,]+\.\d{{2}}|\d+)\s+"
    rf"(?P<basic>[\d,]+\.\d{{2}}|\d+)\s+"
    rf"(?P<escl>AT\s*Par|[\d,]+\.?\d*\s*%?)\s+"
    rf"(?P<amount>[\d,]+\.\d{{2}}|\d+)"
    rf"(?:\s+(?P<bidunit>Rs\.?|INR))?",
    re.IGNORECASE,
)

# Code alone on a line (numeric or NS1)
ITEM_CODE_ONLY = re.compile(rf"(?i)^\s*(?P<code>{_CODE})\s*$")
# "NS1 Description:- …" or "8 Description:- …"
ITEM_CODE_WITH_DESC = re.compile(
    rf"(?i)^\s*(?P<code>{_CODE})\s+description\s*[:\-–]\s*(?P<desc>.+?)\s*$"
)

JUNK_DESC = re.compile(
    r"(?i)^(s\.?no\.?|item\s*code|item\s*qty|qty\s*unit|unit\s*rate|"
    r"basic\s*value|bidding\s*unit|description\s*:?-?|"
    r"i\s*/\s*we\s+the|a\)\s*technical|b\)\s*financial|c\)\s*avail|"
    r"\(a\)|\(b\)|\(c\)|schedule\s*total|grand\s*total)\b"
)

# Some NITs give a schedule a single lump-sum "row" whose Item Code / value
# is literally a pointer to a separate item-breakup annexure elsewhere in the
# document (e.g. Item Code = "Please see Item Breakup for details."), with
# the schedule's total amount attached. Once the annexure itself is parsed
# separately (its own real line items), keeping this pointer row too would
# double the schedule's value in any downstream sum. It carries no
# information a real line item does.
POINTER_TEXT = re.compile(r"(?i)please\s+see|see\s+(?:item\s+)?break\s*up|see\s+annexure|refer\s+annexure")


class ProductExtractor:
    """Extract every product / schedule item being procured."""

    def __init__(self, table_parser: TableParser | None = None) -> None:
        self.table_parser = table_parser or TableParser()

    def extract(
        self,
        text: str,
        tables: list[ExtractedTable] | None = None,
    ) -> list[ProductItem]:
        collected: list[ProductItem] = []
        current_schedule: str | None = None

        # 1) Tables (all product-like tables, all pages)
        if tables:
            for table in tables:
                schedule_hint = self._schedule_from_headers_or_rows(table)
                if schedule_hint:
                    current_schedule = schedule_hint
                if table.is_product_table or self._looks_like_schedule_table(table):
                    collected.extend(
                        self._from_schedule_table(table, schedule=current_schedule)
                    )

        # 2) IREPS plain-text schedule parser (full document — every page)
        collected.extend(self._from_ireps_text(text))

        # 3) Whole-document flexible regex sweep (catches broken line wraps)
        collected.extend(self._from_flex_regex(text))

        # 4) Pair every Description:- with the amounts row above it
        #    (strong for NS1 / Numbers layouts and multi-page BOQs)
        collected.extend(self._from_description_anchors(text))

        # 5) Paragraph / numbered list fallback under BOQ sections
        if not collected:
            collected.extend(self._from_paragraphs(text))

        # Merge duplicates, keep richest row for each identity
        products = self._merge_all(collected)

        return products

    # ------------------------------------------------------------------
    # Table path
    # ------------------------------------------------------------------

    def _from_schedule_table(
        self,
        table: ExtractedTable,
        schedule: str | None = None,
    ) -> list[ProductItem]:
        items: list[ProductItem] = []
        mapped = table.mapped_headers or self.table_parser.map_headers(table.headers)

        for row in table.rows:
            if not any((c or "").strip() for c in row):
                continue

            joined = " ".join(c for c in row if c).strip()
            lower = joined.lower()

            if lower.startswith("schedule") and "item qty" not in lower:
                schedule = collapse_whitespace(joined)
                continue
            if re.search(r"(?i)\b(total|grand total|sub\s*-?\s*total)\b", lower):
                if not re.match(r"^\d+\b", joined):
                    continue
            # Skip repeated header rows inside multipage tables
            if "item qty" in lower and "unit rate" in lower:
                continue

            def cell(field: str) -> str | None:
                idx = mapped.get(field)
                if idx is None or idx >= len(row):
                    return None
                return collapse_whitespace(row[idx])

            desc_only = None
            dm = re.search(r"(?i)description\s*[:\-–]\s*(.+)", joined)
            if dm:
                desc_only = collapse_whitespace(dm.group(1))

            s_no = cell("s_no")
            item_qty = cell("item_qty")
            item_code = cell("item_code")

            is_continuation = items and (
                desc_only
                or (
                    not (s_no and re.fullmatch(r"\d+", s_no.strip()))
                    and not item_qty
                    and (cell("description") or joined)
                )
            )
            if s_no and re.fullmatch(r"\d+", s_no.strip()):
                is_continuation = False

            if is_continuation:
                prev = items[-1]
                extra = desc_only or cell("description") or joined
                if extra and not re.match(r"(?i)^description\s*[:\-–]?\s*$", extra):
                    extra = re.sub(r"(?i)^description\s*[:\-–]\s*", "", extra).strip()
                    extra = extra.lstrip("-–— ").strip()
                    prev.description = truncate(
                        collapse_whitespace(((prev.description or "") + " " + extra).strip()),
                        2000,
                    )
                if table.page_number not in prev.page_numbers:
                    prev.page_numbers.append(table.page_number)
                continue

            s_no_is_serial = bool(s_no and re.fullmatch(r"\d+", s_no.strip()))

            if not s_no_is_serial:
                # Try parsing the whole joined row as a priced IREPS-style line first.
                flex = FLEX_SCHEDULE_ROW.search(joined)
                if flex:
                    items.append(self._item_from_flex(flex, schedule, table.page_number))
                    continue
                # Fall through: many BOQs (equipment / goods schedules) have no
                # item-code or pricing columns at all — just S.No / Description /
                # Unit / Qty. Don't drop these rows just because there is no
                # recognizable numeric serial or price data; accept them off the
                # mapped headers as long as there is real content.

            description = cell("description")
            if description:
                description = re.sub(r"(?i)^description\s*[:\-–]\s*", "", description).strip()
                description = description.lstrip("-–— ").strip()

            # Accept row if it has qty OR rate/amount OR description
            if not any([item_qty, cell("unit_rate"), cell("amount"), description, item_code]):
                continue

            items.append(
                ProductItem(
                    s_no=s_no.strip() if s_no_is_serial else None,
                    item_code=item_code,
                    item_qty=item_qty,
                    qty_unit=cell("qty_unit"),
                    unit_rate=cell("unit_rate"),
                    basic_value=cell("basic_value"),
                    escalation=cell("escalation"),
                    amount=cell("amount"),
                    bidding_unit=cell("bidding_unit") or "Rs.",
                    description=truncate(description, 2000) if description else None,
                    schedule=schedule,
                    page_numbers=[table.page_number],
                )
            )

        return items

    # ------------------------------------------------------------------
    # Text paths
    # ------------------------------------------------------------------

    @staticmethod
    def _line_offsets(source: str) -> list[int]:
        """Start-of-line character offsets for `source`, aligned index-for-index
        with `source.splitlines()` (used to look up which page a line came from)."""
        offsets: list[int] = []
        pos = 0
        for line in source.splitlines(keepends=True):
            offsets.append(pos)
            pos += len(line)
        return offsets

    @staticmethod
    def _page_lookup(source: str):
        """
        Return lookup(offset) -> page number | None, based on the [[PAGE:n]]
        markers the pipeline injects into the text before extraction.

        The text-sweep extraction methods below (`_from_ireps_text`,
        `_from_flex_regex`, `_from_description_anchors`) each make a full pass
        over the document independently; concatenating their results without
        this would order items by "which pass found them" rather than by
        where they actually sit in the tender, which is what previously made
        product order look scrambled whenever a row had no S.No. Recovering a
        real page number lets `_sort_key` order those rows by true document
        position instead.
        """
        marks = [(m.start(), int(m.group(1))) for m in re.finditer(r"\[\[PAGE:(\d+)\]\]", source)]
        if not marks:
            return lambda _offset: None
        positions = [p for p, _ in marks]
        pages = [n for _, n in marks]

        def lookup(offset: int) -> int | None:
            idx = bisect.bisect_right(positions, offset) - 1
            return pages[idx] if idx >= 0 else None

        return lookup

    def _from_ireps_text(self, text: str) -> list[ProductItem]:
        """
        Parse IREPS schedule blocks.

        Real PDF text layout (common):
          1 24.00 Month 254118.55 6098845.20 AT Par 6098845.20
          1
          Description:- Team Leader ...

        Alternate layout (inline code):
          1 1 360.00 Month 70890.38 ... AT Par ... Rs.
          Description:- Site Engineer ...
        """
        items: list[ProductItem] = []
        schedule: str | None = None
        awaiting_description = False

        chunks = self._schedule_chunks(text)
        # Always include full text so items outside detected "Schedule" headers
        # (or after page breaks) are not skipped.
        search_regions: list[tuple[int, str]] = list(chunks)
        if not any(w == text for _, w in search_regions):
            search_regions.append((0, text))

        for region_offset, region in search_regions:
            lines = region.splitlines()
            line_offsets = self._line_offsets(region)
            page_at = self._page_lookup(region)
            i = 0
            skip_until_schedule = False
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue

                lower = line.lower()
                if (
                    re.match(r"(?i)^schedule\b", line)
                    and "item qty" not in lower
                    and "s.no" not in lower
                ):
                    if re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                        awaiting_description = False
                        skip_until_schedule = True
                        i += 1
                        continue
                    schedule = self._clean_schedule_title(line)
                    awaiting_description = False
                    skip_until_schedule = False
                    i += 1
                    continue

                if re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                    awaiting_description = False
                    skip_until_schedule = True
                    i += 1
                    continue

                if skip_until_schedule:
                    i += 1
                    continue

                dm = DESCRIPTION_LINE.match(line)
                if dm and items and awaiting_description:
                    desc = collapse_whitespace(dm.group("desc")) or ""
                    items[-1].description = truncate(desc.lstrip("-–— ").strip(), 2000)
                    awaiting_description = False
                    i += 1
                    continue
                if dm:
                    i += 1
                    continue

                # Layout A: amounts row WITHOUT item code on same line
                rm_a = IREPS_ROW_NO_CODE.match(line)
                if rm_a:
                    item = self._item_from_ireps_no_code(rm_a, schedule)
                    item.source_pos = region_offset + line_offsets[i]
                    pg = page_at(line_offsets[i])
                    if pg:
                        item.page_numbers = [pg]
                    i += 1
                    i, awaiting_description = self._attach_code_and_desc(
                        item, lines, i, awaiting_description=True
                    )
                    items.append(item)
                    continue

                # Layout B: S.No + ItemCode + amounts on one (possibly wrapped) line
                window = line
                for j in range(1, 3):
                    if i + j < len(lines):
                        nxt = lines[i + j].strip()
                        if (
                            nxt
                            and not DESCRIPTION_LINE.match(nxt)
                            and not ITEM_CODE_ONLY.match(nxt)
                            and not ITEM_CODE_WITH_DESC.match(nxt)
                            and not nxt.lower().startswith("schedule")
                        ):
                            window = window + " " + nxt

                rm_b = FLEX_SCHEDULE_ROW.search(window)
                if rm_b:
                    item = self._item_from_flex(rm_b, schedule, None)
                    item.source_pos = region_offset + line_offsets[i]
                    pg = page_at(line_offsets[i])
                    if pg:
                        item.page_numbers = [pg]
                    i += 1
                    i, awaiting_description = self._attach_code_and_desc(
                        item, lines, i, awaiting_description=True
                    )
                    items.append(item)
                    continue

                awaiting_description = False
                i += 1

        return items

    def _attach_code_and_desc(
        self,
        item: ProductItem,
        lines: list[str],
        i: int,
        awaiting_description: bool,
    ) -> tuple[int, bool]:
        """Consume following Item Code / Description lines after an amounts row."""
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines):
            cd = ITEM_CODE_WITH_DESC.match(lines[i].strip())
            if cd:
                item.item_code = self._normalize_item_code(cd.group("code"))
                desc = collapse_whitespace(cd.group("desc")) or ""
                item.description = truncate(desc.lstrip("-–— ").strip(), 2000)
                if not item.item_code and item.s_no:
                    item.item_code = item.s_no
                return i + 1, False
            cm = ITEM_CODE_ONLY.match(lines[i].strip())
            if cm:
                item.item_code = self._normalize_item_code(cm.group("code"))
                # Only mirror numeric codes into s_no (NS1 is not an S.No.)
                if (
                    item.item_code
                    and item.item_code.isdigit()
                    and (not item.s_no or item.s_no == item.item_code)
                ):
                    item.s_no = item.item_code
                i += 1
        if not item.item_code and item.s_no:
            item.item_code = item.s_no
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines):
            d2 = DESCRIPTION_LINE.match(lines[i].strip())
            if d2:
                desc = collapse_whitespace(d2.group("desc")) or ""
                # Append continuation lines that are part of the description
                j = i + 1
                parts = [desc]
                while j < len(lines):
                    nxt = lines[j].strip()
                    if not nxt:
                        break
                    if (
                        DESCRIPTION_LINE.match(nxt)
                        or IREPS_ROW_NO_CODE.match(nxt)
                        or FLEX_SCHEDULE_ROW.match(nxt)
                        or ITEM_CODE_ONLY.match(nxt)
                        or ITEM_CODE_WITH_DESC.match(nxt)
                        or re.match(r"(?i)^schedule\b", nxt)
                        or re.match(r"(?i)^(?:page\s+\d+|s\.?no\.?)\b", nxt)
                    ):
                        break
                    parts.append(nxt)
                    j += 1
                    if len(" ".join(parts)) > 1800:
                        break
                item.description = truncate(
                    collapse_whitespace(" ".join(parts)).lstrip("-–— ").strip(),
                    2000,
                )
                return j, False
        return i, awaiting_description and not bool(item.description)

    @staticmethod
    def _normalize_item_code(code: str | None) -> str | None:
        if not code:
            return None
        code = code.strip()
        if not code:
            return None
        if code.isdigit():
            return str(int(code))
        return code.upper()

    def _from_flex_regex(self, text: str) -> list[ProductItem]:
        """Sweep full document / schedule regions for numeric rows + descriptions."""
        items: list[ProductItem] = []
        chunks = self._schedule_chunks(text)
        regions: list[tuple[int, str]] = list(chunks)
        if not any(w == text for _, w in regions):
            regions.append((0, text))

        for region_offset, region in regions:
            schedule = None
            hm = re.search(r"(?im)^(schedule\b(?!\s*total)[^\n]*)", region)
            if hm:
                schedule = self._clean_schedule_title(hm.group(1))

            cut = re.search(r"(?im)^(?:schedule\s+)?(?:total|grand\s+total)\b", region)
            # Only cut when this region starts at a schedule header; full-text
            # region may contain many schedules — do not truncate at first total.
            if cut and re.match(r"(?is)^\s*schedule\b", region):
                active = region[: cut.start()]
            else:
                active = region
            lines = active.splitlines()
            line_offsets = self._line_offsets(active)
            page_at = self._page_lookup(active)

            # Track schedule totals inside full-document sweeps
            local_schedule = schedule
            skip = False
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line and re.match(r"(?i)^schedule\b", line) and "item qty" not in line.lower():
                    if re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                        skip = True
                        i += 1
                        continue
                    local_schedule = self._clean_schedule_title(line)
                    skip = False
                    i += 1
                    continue
                if line and re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                    skip = True
                    i += 1
                    continue
                if skip:
                    i += 1
                    continue
                rm_a = IREPS_ROW_NO_CODE.match(line) if line else None
                if rm_a:
                    item = self._item_from_ireps_no_code(rm_a, local_schedule)
                    item.source_pos = region_offset + line_offsets[i]
                    pg = page_at(line_offsets[i])
                    if pg:
                        item.page_numbers = [pg]
                    i, _ = self._attach_code_and_desc(
                        item, lines, i + 1, awaiting_description=True
                    )
                    items.append(item)
                    continue
                rm_b = FLEX_SCHEDULE_ROW.match(line) if line else None
                if rm_b:
                    item = self._item_from_flex(rm_b, local_schedule, None)
                    item.source_pos = region_offset + line_offsets[i]
                    pg = page_at(line_offsets[i])
                    if pg:
                        item.page_numbers = [pg]
                    i, _ = self._attach_code_and_desc(
                        item, lines, i + 1, awaiting_description=True
                    )
                    items.append(item)
                    continue
                i += 1

            if not re.match(r"(?is)^\s*schedule\b", region):
                # Full-document region already walked line-by-line above
                continue

            for m in FLEX_SCHEDULE_ROW.finditer(active):
                line_start = active.rfind("\n", 0, m.start()) + 1
                line_end = active.find("\n", m.start())
                if line_end < 0:
                    line_end = len(active)
                line = active[line_start:line_end].strip()
                if IREPS_ROW_NO_CODE.match(line):
                    continue
                item = self._item_from_flex(m, schedule, None)
                item.source_pos = region_offset + m.start()
                pg = page_at(m.start())
                if pg:
                    item.page_numbers = [pg]
                tail = active[m.end() : m.end() + 500]
                dm = re.search(r"(?is)description\s*[:\-–]\s*([^\n]+)", tail)
                if dm:
                    item.description = truncate(
                        collapse_whitespace(dm.group(1)).lstrip("-–— ").strip(),
                        2000,
                    )
                items.append(item)

        return items

    def _from_description_anchors(self, text: str) -> list[ProductItem]:
        """Pair every Description:- with the nearest amounts row above it."""
        items: list[ProductItem] = []
        lines = text.splitlines()
        line_offsets = self._line_offsets(text)
        page_at = self._page_lookup(text)
        blocked = False
        for idx, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            lower = line.lower()
            if (
                re.match(r"(?i)^schedule\b", line)
                and "item qty" not in lower
                and "s.no" not in lower
            ):
                if re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                    blocked = True
                else:
                    blocked = False
                continue
            if re.search(r"(?i)^(?:schedule\s+)?(?:total|grand\s+total)\b", line):
                blocked = True
                continue
            if blocked:
                continue

            dm = DESCRIPTION_LINE.match(line)
            if not dm:
                # Also "NS1 Description:- ..." on one line without prior code line
                cd = ITEM_CODE_WITH_DESC.match(line)
                if not cd:
                    continue
                desc = collapse_whitespace(cd.group("desc")) or ""
                desc = desc.lstrip("-–— ").strip()
                if not desc or JUNK_DESC.search(desc):
                    continue
                # Look above for amounts row with this code or bare sno+qty
                item = self._find_row_above(lines, idx)
                if item is None:
                    item = ProductItem(
                        item_code=self._normalize_item_code(cd.group("code")),
                        description=truncate(desc, 2000),
                    )
                else:
                    item.item_code = item.item_code or self._normalize_item_code(
                        cd.group("code")
                    )
                    item.description = truncate(desc, 2000)
                if item.source_pos is None:
                    item.source_pos = line_offsets[idx]
                pg = page_at(line_offsets[idx])
                if pg and not item.page_numbers:
                    item.page_numbers = [pg]
                items.append(item)
                continue

            desc = collapse_whitespace(dm.group("desc")) or ""
            desc = desc.lstrip("-–— ").strip()
            if not desc or JUNK_DESC.search(desc):
                continue
            # Gather multi-line description body
            j = idx + 1
            parts = [desc]
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    break
                if (
                    DESCRIPTION_LINE.match(nxt)
                    or IREPS_ROW_NO_CODE.match(nxt)
                    or FLEX_SCHEDULE_ROW.match(nxt)
                    or ITEM_CODE_ONLY.match(nxt)
                    or ITEM_CODE_WITH_DESC.match(nxt)
                    or re.match(r"(?i)^schedule\b", nxt)
                    or re.match(r"(?i)^(?:page\s+\d+|s\.?no\.?)\b", nxt)
                ):
                    break
                parts.append(nxt)
                j += 1
                if len(" ".join(parts)) > 1800:
                    break
            full_desc = truncate(
                collapse_whitespace(" ".join(parts)).lstrip("-–— ").strip(),
                2000,
            )
            item = self._find_row_above(lines, idx)
            if item is None:
                item = ProductItem(s_no=str(len(items) + 1), description=full_desc)
            else:
                item.description = full_desc
            if item.source_pos is None:
                item.source_pos = line_offsets[idx]
            pg = page_at(line_offsets[idx])
            if pg and not item.page_numbers:
                item.page_numbers = [pg]
            items.append(item)
        return items

    def _find_row_above(self, lines: list[str], desc_idx: int) -> ProductItem | None:
        """Walk upward from a Description line to find the amounts / code row."""
        schedule = None
        for k in range(desc_idx - 1, max(-1, desc_idx - 12), -1):
            if k < 0:
                break
            prev = lines[k].strip()
            if not prev:
                continue
            if re.match(r"(?i)^schedule\b", prev) and "item qty" not in prev.lower():
                schedule = self._clean_schedule_title(prev)
                continue
            rm_b = FLEX_SCHEDULE_ROW.match(prev)
            if rm_b:
                return self._item_from_flex(rm_b, schedule, None)
            rm_a = IREPS_ROW_NO_CODE.match(prev)
            if rm_a:
                item = self._item_from_ireps_no_code(rm_a, schedule)
                # Code may be on the line between amounts and description
                for mid in range(k + 1, desc_idx):
                    cm = ITEM_CODE_ONLY.match(lines[mid].strip())
                    if cm:
                        item.item_code = self._normalize_item_code(cm.group("code"))
                        break
                if not item.item_code and item.s_no:
                    item.item_code = item.s_no
                return item
            # "NS1 730.00 Day 954.00 ..." without S.No. on same line
            code_row = re.match(
                rf"(?i)^\s*(?P<code>{_CODE})\s+"
                rf"(?P<qty>[\d,]+\.\d{{2}})\s+"
                rf"(?P<unit>{_UNIT})\s+"
                rf"(?P<rate>[\d,]+\.\d{{2}})\s+"
                rf"(?P<basic>[\d,]+\.\d{{2}})\s+"
                rf"(?P<escl>AT\s*Par|[\d,]+\.?\d*\s*%?)\s+"
                rf"(?P<amount>[\d,]+\.\d{{2}})\s*"
                rf"(?P<bidunit>Rs\.?|INR)?\s*$",
                prev,
            )
            if code_row:
                # S.No. may be alone on the line above
                sno = None
                for up in range(k - 1, max(-1, k - 4), -1):
                    if up < 0:
                        break
                    alone = re.match(r"^\s*(\d{1,4})\s*$", lines[up].strip())
                    if alone:
                        sno = alone.group(1)
                        break
                return ProductItem(
                    s_no=sno,
                    item_code=self._normalize_item_code(code_row.group("code")),
                    item_qty=code_row.group("qty"),
                    qty_unit=code_row.group("unit"),
                    unit_rate=code_row.group("rate"),
                    basic_value=code_row.group("basic"),
                    escalation=collapse_whitespace(code_row.group("escl")),
                    amount=code_row.group("amount"),
                    bidding_unit=(code_row.groupdict().get("bidunit") or "Rs."),
                    schedule=schedule,
                )
        return None

    @staticmethod
    def _clean_schedule_title(line: str) -> str:
        """Keep schedule name; drop trailing schedule total amount if present."""
        title = collapse_whitespace(line) or line.strip()
        title = re.sub(r"\s+[\d,]+\.\d{2}\s*$", "", title).strip()
        return title

    @staticmethod
    def _item_from_ireps_no_code(
        m: re.Match[str], schedule: str | None
    ) -> ProductItem:
        sno = str(int(m.group("sno")))
        return ProductItem(
            s_no=sno,
            item_code=None,
            item_qty=m.group("qty"),
            qty_unit=m.group("unit"),
            unit_rate=m.group("rate"),
            basic_value=m.group("basic"),
            escalation=collapse_whitespace(m.group("escl")),
            amount=m.group("amount"),
            bidding_unit=(m.groupdict().get("bidunit") or "Rs."),
            schedule=schedule,
        )

    def _from_paragraphs(self, text: str) -> list[ProductItem]:
        section = self._product_section(text)
        if not section:
            return []

        items: list[ProductItem] = []
        pattern = re.compile(
            r"(?m)^\s*(?:(?:\d+(?:\.\d+)*)[\)\.]|[\-\*•])\s+(?P<body>.+?)(?=\n\s*(?:(?:\d+(?:\.\d+)*)[\)\.]|[\-\*•])\s+|\n\n|\Z)",
            re.DOTALL,
        )
        for idx, m in enumerate(pattern.finditer(section), start=1):
            body = collapse_whitespace(m.group("body")) or ""
            if len(body) < 5 or JUNK_DESC.search(body):
                continue
            qty, unit = self._parse_qty_unit(body)
            items.append(
                ProductItem(
                    s_no=str(idx),
                    description=truncate(body, 2000),
                    item_qty=qty,
                    qty_unit=unit,
                )
            )
        return items

    def _product_section(self, text: str) -> str | None:
        m = PRODUCT_SECTION_HINTS.search(text)
        if not m:
            return None
        start = m.start()
        chunk = text[start : start + 40000]
        cut = re.search(
            r"(?im)^(?:\d+[\).\s]+)?(?:general\s+conditions|special\s+conditions|"
            r"eligibility|payment\s+terms|instructions\s+to\s+bidders)\b",
            chunk[200:],
        )
        if cut:
            chunk = chunk[: 200 + cut.start()]
        return chunk

    def _schedule_chunks(self, text: str) -> list[tuple[int, str]]:
        """
        Split `text` into per-"Schedule ..." windows.

        Returns (start_offset, chunk_text) pairs — the start_offset is each
        chunk's absolute position in the original `text`, needed so callers
        can convert a chunk-local match/line offset back into a global
        document position (and from there, a real page number) instead of an
        offset that's only meaningful within that one chunk.
        """
        chunks: list[tuple[int, str]] = []
        matches = list(re.finditer(r"(?im)^(?P<title>schedule\b(?!\s*total)[^\n]*)", text))
        for idx, m in enumerate(matches):
            start = m.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(text), start + 30000)
            window = text[start:end]
            cut = re.search(
                r"(?im)^(?:schedule\s+)?(?:total|grand\s+total)\b|"
                r"^(?:\d+\.\s*)?(?:general\s+conditions|special\s+conditions|"
                r"eligibility|payment\s+terms|instructions\s+to\s+bidders|"
                r"standard\s+general|annexure|corrigendum)\b",
                window[80:],
            )
            if cut:
                window = window[: 80 + cut.start()]
            chunks.append((start, window))
        return chunks

    @staticmethod
    def _item_from_flex(m: re.Match[str], schedule: str | None, page: int | None) -> ProductItem:
        code = m.group("code")
        if code and code.isdigit():
            code = str(int(code))
        elif code:
            code = code.upper()
        return ProductItem(
            s_no=str(int(m.group("sno"))) if m.group("sno").isdigit() else m.group("sno"),
            item_code=code,
            item_qty=m.group("qty"),
            qty_unit=m.group("unit"),
            unit_rate=m.group("rate"),
            basic_value=m.group("basic"),
            escalation=collapse_whitespace(m.group("escl")),
            amount=m.group("amount"),
            bidding_unit=(m.groupdict().get("bidunit") or "Rs."),
            schedule=schedule,
            page_numbers=[page] if page else [],
        )

    # ------------------------------------------------------------------
    # Merge / normalize
    # ------------------------------------------------------------------

    def _merge_all(self, items: list[ProductItem]) -> list[ProductItem]:
        """Union all items; hard-dedupe so the same row never repeats."""
        merged: dict[tuple[Any, ...], ProductItem] = {}

        for item in items:
            if not self._is_valid_item(item):
                continue
            item = self._normalize_serial(item)
            key = self._content_key(item)
            if key not in merged:
                merged[key] = item
                continue
            merged[key] = self._prefer_item(merged[key], item)

        # Second pass: same schedule letter + S.No. must be one row
        by_sno: dict[tuple[Any, ...], ProductItem] = {}
        for item in merged.values():
            sk = self._schedule_sno_key(item)
            if sk is None:
                # Keep under a unique fallback so we don't drop it
                by_sno[("nosched", id(item))] = item
                continue
            if sk not in by_sno:
                by_sno[sk] = item
                continue
            by_sno[sk] = self._prefer_item(by_sno[sk], item)

        out = list(by_sno.values())
        out.sort(key=self._sort_key)

        for p in out:
            if p.escalation:
                p.escalation = collapse_whitespace(
                    re.sub(r"(?i)at\s*par", "AT Par", p.escalation)
                )
            if not p.bidding_unit or str(p.bidding_unit).lower() in {"none", "null"}:
                p.bidding_unit = "Rs."
            if p.description:
                p.description = p.description.lstrip("-–— ").strip()
            if p.schedule:
                p.schedule = self._clean_schedule_title(p.schedule)
        return out

    def _sort_key(self, p: ProductItem) -> tuple:
        """
        Group by schedule (falling back to item-code prefix, e.g. NS/B/A, when
        no schedule was captured), then order by true document position within
        each group — exact character offset when known (`source_pos`), else
        the real page number. A correctly-extracted S.No. already increases
        through the document, so position-based ordering reproduces S.No.
        order for well-formed schedules for free; it also avoids the failure
        mode where exactly one row in a group happened to have its S.No.
        captured (siblings didn't) and got yanked to the front of the group
        out of its true position just because it had a number and they didn't.
        S.No. is only used as the ordering signal when NO position info is
        available at all (e.g. hand-built ProductItems in unit tests), falling
        back further to stable original extraction order when even that is
        absent. This whole scheme replaces the old fallback of sorting by
        item_qty, which scrambled the list into a qty-lexicographic jumble
        whenever S.No. was missing.
        """
        group = self._group_key(p)
        page = min(p.page_numbers) if p.page_numbers else None
        if p.source_pos is not None:
            position_rank, position_value = 0, p.source_pos
        elif page is not None:
            position_rank, position_value = 1, page
        else:
            position_rank, position_value = 2, 0

        if position_rank < 2:
            return (group, position_rank, position_value, 0, 0)

        sno = None
        if p.s_no and p.s_no.strip().isdigit():
            sno = int(p.s_no.strip())
        return (group, position_rank, position_value, 0 if sno is not None else 1, sno or 0)

    def _group_key(self, p: ProductItem) -> tuple[int, str]:
        letter = self._schedule_letter(p.schedule)
        if letter:
            return (0, letter)
        if p.schedule:
            return (0, p.schedule.strip().lower())
        code = (p.item_code or "").strip()
        m = re.match(r"^([A-Za-z]+)", code)
        if m:
            return (1, m.group(1).upper())
        return (2, "")

    @staticmethod
    def _schedule_letter(schedule: str | None) -> str:
        if not schedule:
            return ""
        s = schedule.strip().lower()
        m = re.search(r"schedule\s*(?:\(\)\s*)?([a-z])\b", s)
        if m:
            return m.group(1)
        m = re.search(r"\b([a-z])\s*[-–]", s)
        if m:
            return m.group(1)
        return ""

    def _schedule_sno_key(self, item: ProductItem) -> tuple[Any, ...] | None:
        sno = (item.s_no or "").strip()
        if not sno.isdigit():
            return None
        letter = self._schedule_letter(item.schedule)
        if letter:
            return ("sched_sno", letter, int(sno))
        # No schedule: identity still held by content key; don't collapse A#1 with B#1
        return None

    @staticmethod
    def _norm_num(value: str | None) -> str:
        if not value:
            return ""
        v = re.sub(r"[,\s]", "", str(value))
        try:
            f = float(v)
            if f.is_integer():
                return str(int(f))
            return f"{f:.2f}".rstrip("0").rstrip(".")
        except ValueError:
            return v.lower()

    @staticmethod
    def _norm_desc(value: str | None) -> str:
        if not value:
            return ""
        text = re.sub(r"\s+", " ", value.strip().lower())
        text = re.sub(r"(?i)^description\s*[:\-–]\s*", "", text)
        return text.lstrip("-–— ").strip()[:50]

    def _content_key(self, item: ProductItem) -> tuple[Any, ...]:
        """
        Deduplicate by WHAT the row is.

        Schedule is intentionally NOT part of the key: the same BOQ line is often
        extracted once with a schedule title and once without (page break / table).
        Item Code / description keep distinct rows that share the same rate
        (e.g. Clerk vs Legal both at 28440.00).
        """
        code = self._normalize_item_code(item.item_code) or ""
        code = code.lower()
        qty = self._norm_num(item.item_qty)
        rate = self._norm_num(item.unit_rate)
        amount = self._norm_num(item.amount)
        desc = self._norm_desc(item.description)
        sno = self._norm_num(item.s_no)

        # Prefer identity that includes item code (IREPS S.No. ≈ Item Code)
        if code and qty and rate:
            return ("crt", code, qty, rate)
        if sno and qty and rate and amount:
            return ("sqra", sno, qty, rate, amount)
        if qty and rate and amount and desc:
            return ("qrad", qty, rate, amount, desc)
        if qty and rate and desc:
            return ("qrd", qty, rate, desc)
        if amount and desc:
            return ("ad", amount, desc)
        if qty and rate and amount:
            return ("qra", qty, rate, amount)
        return ("fallback", code or sno, qty, rate, amount, desc)

    def _prefer_item(self, a: ProductItem, b: ProductItem) -> ProductItem:
        """Keep the richer / more consistent of two duplicate rows."""
        # Prefer longer description
        if (b.description or "") and len(b.description or "") > len(a.description or ""):
            a.description = b.description
        for attr in (
            "item_code",
            "item_qty",
            "qty_unit",
            "unit_rate",
            "basic_value",
            "escalation",
            "amount",
            "bidding_unit",
            "schedule",
        ):
            if not getattr(a, attr) and getattr(b, attr):
                setattr(a, attr, getattr(b, attr))

        # Prefer S.No. that matches item_code (common in IREPS)
        a_sno = (a.s_no or "").strip()
        b_sno = (b.s_no or "").strip()
        code = (a.item_code or b.item_code or "").strip()
        if code and b_sno == code and a_sno != code:
            a.s_no = b_sno
        elif self._serial_score(b_sno, code) > self._serial_score(a_sno, code):
            a.s_no = b_sno

        for pn in b.page_numbers:
            if pn not in a.page_numbers:
                a.page_numbers.append(pn)
        if a.source_pos is None and b.source_pos is not None:
            a.source_pos = b.source_pos
        return a

    @staticmethod
    def _serial_score(sno: str, code: str) -> int:
        if not sno or not sno.isdigit():
            return -10
        n = int(sno)
        if n <= 0:
            return -20
        # "00" / leading-zero junk
        if sno != str(n) and sno.startswith("0"):
            return -15
        score = 0
        if code and sno == code:
            score += 50
        # Prefer reasonable serials (1..200)
        if 1 <= n <= 200:
            score += 10
        else:
            score -= 5
        # Prefer smaller serial when both valid (page artifacts are often large)
        score += max(0, 30 - n)
        return score

    @staticmethod
    def _normalize_serial(item: ProductItem) -> ProductItem:
        sno = (item.s_no or "").strip()
        if not sno:
            # If item_code is a simple integer, use it as s_no
            code = (item.item_code or "").strip()
            if code.isdigit() and int(code) > 0:
                item.s_no = str(int(code))
            return item
        if not sno.isdigit():
            item.s_no = None
            return item
        n = int(sno)
        if n <= 0:
            item.s_no = None
            return item
        # Normalize "01" -> "1"
        item.s_no = str(n)
        return item

    @staticmethod
    def _identity_key(item: ProductItem) -> tuple[Any, ...]:
        # Kept for compatibility; content key is used for dedupe.
        return ProductExtractor()._content_key(item)

    @staticmethod
    def _is_valid_item(item: ProductItem) -> bool:
        sno = (item.s_no or "").strip()
        if sno:
            if not re.fullmatch(r"\d+", sno):
                return False
            if int(sno) <= 0:
                return False
        desc = (item.description or "").strip()
        if desc and JUNK_DESC.search(desc):
            return False
        if item.item_code and POINTER_TEXT.search(item.item_code):
            return False
        has_qty = bool(item.item_qty and re.search(r"\d", item.item_qty))
        has_money = bool(
            (item.unit_rate and re.search(r"\d", item.unit_rate))
            or (item.amount and re.search(r"\d", item.amount))
        )
        has_desc = bool(desc and len(desc) > 3)
        if has_qty and (has_money or has_desc or item.item_code):
            return True
        if has_desc and (has_qty or has_money or item.item_code):
            return True
        return False

    @staticmethod
    def _parse_qty_unit(text: str) -> tuple[str | None, str | None]:
        m = re.search(
            r"(?i)(?:qty|quantity|nos?\.?)\s*[:\-]?\s*(?P<qty>[\d,]+(?:\.\d+)?)\s*(?P<unit>[A-Za-z%]+)?",
            text,
        )
        if m:
            return m.group("qty"), m.group("unit")
        m2 = re.search(
            r"(?i)\b(?P<qty>[\d,]+(?:\.\d+)?)\s*(?P<unit>nos?\.?|units?|kg|kgs|mt|tonnes?|litres?|ltr|meters?|mtr|sets?|pcs?|pieces?|month|months)\b",
            text,
        )
        if m2:
            return m2.group("qty"), m2.group("unit")
        return None, None

    @staticmethod
    def _looks_like_schedule_table(table: ExtractedTable) -> bool:
        headers = " ".join(table.headers).lower()
        keys = (
            "item qty",
            "qty unit",
            "unit rate",
            "basic value",
            "bidding unit",
            "item code",
            "escl",
            "s.no",
            "quantity",
        )
        hits = sum(1 for k in keys if k in headers)
        return hits >= 2

    @staticmethod
    def _schedule_from_headers_or_rows(table: ExtractedTable) -> str | None:
        for cell in table.headers:
            if re.search(r"(?i)^schedule\b", cell or ""):
                return collapse_whitespace(cell)
        for row in table.rows[:5]:
            joined = " ".join(c for c in row if c)
            if re.search(r"(?i)^schedule\b", joined) and "item qty" not in joined.lower():
                return collapse_whitespace(joined)
        return None

    # Backwards-compatible helpers used by merge.sanitize_products
    @staticmethod
    def _normalize_schedule_items(items: list[ProductItem]) -> list[ProductItem]:
        return ProductExtractor()._merge_all(items)

    @staticmethod
    def _is_clean_schedule(items: list[ProductItem]) -> bool:
        if len(items) < 1:
            return False
        valid = sum(1 for p in items if ProductExtractor._is_valid_item(p))
        return valid >= max(1, int(len(items) * 0.6))

    @staticmethod
    def _keep_primary_schedule_block(items: list[ProductItem]) -> list[ProductItem]:
        """Keep all valid items (multi-schedule safe). Legacy name retained for callers."""
        return ProductExtractor()._merge_all(items)
