"""Shared helpers for AI JSON merge / parse."""

from __future__ import annotations

import json
import re
from typing import Any

from app.extractor.product_extractor import POINTER_TEXT, ProductExtractor
from app.models.schemas import (
    ContactDetails,
    EligibilityInfo,
    FinancialInfo,
    ImportantClause,
    ImportantDates,
    ProductItem,
    TenderInformation,
    TenderResult,
)

CHUNK_SYSTEM_PROMPT = """You are an expert Indian Railway / IREPS tender (NIT) extraction engine.

You are being shown ONE SECTION of a larger tender PDF, not the whole document — the
document has been split into consecutive sections so you can read all of it. The
section may begin/contain markers like [[PAGE:12]] showing the original page number;
use them only for your own orientation, never copy them into your output.

Read this section carefully and extract every piece of structured data it actually
contains. Do not assume a fixed template — tender documents vary a lot in layout, so
look for the information itself (schedule tables, key/value header blocks, prose
paragraphs describing eligibility or payment terms, etc.) rather than expecting one
exact format. It is completely normal for a section to be entirely one kind of content
(e.g. a section that is pure BOQ/schedule will have every tender_information field
null but many products; a section that is legal boilerplate may have zero products).

Return ONLY valid JSON (no markdown, no commentary) with this exact shape:
{
  "tender_information": {
    "name_of_work": string|null,
    "tender_no": string|null,
    "closing_date_time": string|null,
    "division_name": string|null,
    "zone": string|null,
    "advertised_value": string|null,
    "earnest_money": string|null,
    "period_of_completion": string|null,
    "number_of_jv_member_allowed": string|null
  },
  "financial": {
    "estimated_value": string|null,
    "emd": string|null,
    "tender_fee": string|null,
    "security_deposit": string|null,
    "performance_guarantee": string|null,
    "currency": string|null
  },
  "dates": {
    "published_date": string|null,
    "start_date": string|null,
    "bid_submission_start": string|null,
    "bid_submission_end": string|null,
    "technical_bid_opening": string|null,
    "financial_bid_opening": string|null,
    "pre_bid_meeting": string|null,
    "completion_period": string|null
  },
  "eligibility": {
    "minimum_turnover": string|null,
    "years_of_experience": string|null,
    "similar_work_required": string|null,
    "oem_authorization": string|null,
    "msme_exemption": string|null,
    "startup_exemption": string|null
  },
  "products": [
    {
      "s_no": string|null,
      "item_code": string|null,
      "item_qty": string|null,
      "qty_unit": string|null,
      "unit_rate": string|null,
      "basic_value": string|null,
      "escalation": string|null,
      "amount": string|null,
      "bidding_unit": string|null,
      "description": string|null,
      "schedule": string|null
    }
  ],
  "documents_required": [string],
  "important_clauses": [
    {"clause_type": string, "title": string|null, "content": string|null}
  ],
  "contact_details": {
    "officer_name": string|null,
    "phone": string|null,
    "email": string|null,
    "office_address": string|null,
    "website": string|null
  }
}

Rules:
- Extract EVERY product / schedule / BOQ line item found in this section — do not
  summarize, sample, or stop early. Every row, from every schedule, on every page in
  this section, becomes one entry in "products".
- Include partial products: if a row only has a description and quantity with no
  price, or only a code and amount, still include it with the missing fields null.
  Do not discard a row just because it doesn't match a template exactly.
- tender_no must be ONLY the identifying code, never a sentence or a trailing
  "Closing Date" fragment.
- description must be the item's own text (no leading "Description:-" or "-").
- important_clauses: capture headed sections like Payment Terms, Liquidated Damages,
  Warranty, Penalty, Eligibility Criteria, Scope of Work, Special Conditions — use the
  heading as clause_type and the body text as content.
- Never invent a value that is not actually present in this section. Use null (or an
  empty list) for anything this section does not contain — that is the expected,
  correct answer for most fields on most sections.
"""

_JUNK_DESC = re.compile(
    r"(?i)^(s\.?no\.?|item\s*code|item\s*qty|description\s*:?-?|"
    r"i\s*/\s*we\s+the|a\)\s*technical|b\)\s*financial|c\)\s*avail|"
    r"\(a\)|\(b\)|\(c\)|technical|financial|availability|"
    r"schedule\s*total|grand\s*total)\b"
)


def parse_json_content(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.I)
    if fence:
        content = fence.group(1).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_description(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"(?i)^description\s*[:\-–]\s*", "", text).strip()
    text = text.lstrip("-–— ").strip()
    return text or None


def sanitize_products(raw_products: list[Any]) -> list[ProductItem]:
    """Drop junk rows; keep every valid schedule/product line item."""
    cleaned: list[ProductItem] = []
    for p in raw_products:
        if isinstance(p, ProductItem):
            # dict(p), not p.model_dump(): source_pos is an internal-only,
            # exclude=True field used solely for ordering rows with no S.No.
            # by document position — model_dump() drops it (by design, so it
            # never appears in JSON/Excel/CSV exports), which would silently
            # reset ordering every time products round-trip through here.
            data = dict(p)
        elif isinstance(p, dict):
            allowed = set(ProductItem.model_fields)
            data = {k: v for k, v in p.items() if k in allowed}
        else:
            continue

        sno = str(data.get("s_no") or "").strip()
        # Allow missing s_no only if description+qty exist; else require integer s_no
        if sno and not re.fullmatch(r"\d+", sno):
            continue

        qty = str(data.get("item_qty") or "").strip()
        rate = str(data.get("unit_rate") or "").strip()
        amount = str(data.get("amount") or "").strip()
        desc = _clean_description(data.get("description"))

        has_qty = bool(re.search(r"\d", qty))
        has_money = bool(re.search(r"\d", rate) or re.search(r"\d", amount))
        has_desc = bool(desc and len(desc) > 3)

        if desc and _JUNK_DESC.search(desc):
            continue
        item_code = str(data.get("item_code") or "")
        if item_code and POINTER_TEXT.search(item_code):
            continue
        if desc and desc.lower() in {
            "item code",
            "item qty",
            "qty unit",
            "unit rate",
            "basic value",
            "amount",
            "bidding unit",
            "description",
        }:
            continue

        # Keep if it looks like a real line item
        if not ((has_qty and (has_money or has_desc or data.get("item_code"))) or (has_desc and has_qty)):
            # Also keep description+money without qty (rare)
            if not (has_desc and has_money):
                continue

        data["s_no"] = sno or None
        data["description"] = desc
        if data.get("escalation"):
            data["escalation"] = re.sub(
                r"(?i)at\s*par", "AT Par", str(data["escalation"])
            ).strip()
        if not data.get("bidding_unit") or str(data.get("bidding_unit")).lower() in {
            "none",
            "null",
            "",
        }:
            data["bidding_unit"] = "Rs."

        cleaned.append(ProductItem(**data))

    # Merge duplicates across sources; do NOT drop multi-schedule items
    return ProductExtractor._normalize_schedule_items(cleaned)


_PAGE_MARKER = re.compile(r"\[\[PAGE:(\d+)\]\]")


def chunk_text_by_pages(text: str, max_pages: int = 6, max_chars: int = 12000) -> list[str]:
    """
    Split page-marked document text (as injected by TenderPipeline, e.g.
    "[[PAGE:1]]\\n...text...[[PAGE:2]]\\n...") into consecutive chunks so the
    whole document — not just its first N characters — can be sent to an AI
    model in a series of requests.

    Each chunk holds up to `max_pages` pages, but is cut short earlier if it
    would otherwise exceed `max_chars` (a handful of dense pages shouldn't
    blow out a single request). A chunk always contains at least one page
    even if that single page alone exceeds `max_chars`.

    Text with no [[PAGE:n]] markers (e.g. plain text in tests, or text that
    for some reason wasn't marked) falls back to a plain character-count
    split so chunking still works.
    """
    if not text:
        return []

    marks = list(_PAGE_MARKER.finditer(text))
    if not marks:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)] or [text]

    spans: list[tuple[int, int]] = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        spans.append((start, end))

    chunks: list[str] = []
    chunk_start = spans[0][0]
    chunk_end = chunk_start
    pages_in_chunk = 0
    for start, end in spans:
        would_be_len = end - chunk_start
        if pages_in_chunk > 0 and (pages_in_chunk >= max_pages or would_be_len > max_chars):
            chunks.append(text[chunk_start:chunk_end])
            chunk_start = start
            pages_in_chunk = 0
        chunk_end = end
        pages_in_chunk += 1
    if chunk_end > chunk_start:
        chunks.append(text[chunk_start:chunk_end])
    return chunks


def build_chunk_prompt(chunk: str, index: int, total: int) -> str:
    return f"Section {index} of {total} of the tender PDF:\n\n{chunk}"


def merge_chunk_into_result(result: TenderResult, data: dict[str, Any]) -> TenderResult:
    """
    Fold one chunk's AI extraction into the running `result` during chunked
    document processing.

    Designed to be called once per chunk, accumulating across the whole
    document:
      - scalar fields (tender_information/financial/dates/eligibility/contact):
        fill only if still null — the first chunk to report a field wins, so
        a later, unrelated chunk can't clobber a value already found.
      - products / documents_required: always UNION, never replace — every
        chunk can contribute rows found nowhere else.
      - important_clauses: merged by clause_type, keeping the longer content
        whenever the same clause_type is reported by more than one chunk.
    """
    export = result.to_export_dict()

    for section in (
        "tender_information",
        "financial",
        "dates",
        "eligibility",
        "contact_details",
    ):
        incoming = data.get(section)
        if not isinstance(incoming, dict):
            continue
        for k, v in incoming.items():
            if k not in export[section]:
                continue
            if v in (None, "", []):
                continue
            if export[section].get(k) in (None, "", []):
                export[section][k] = v

    ai_products_raw = data.get("products") if isinstance(data.get("products"), list) else []
    products = sanitize_products(list(result.products) + list(ai_products_raw))

    if isinstance(data.get("documents_required"), list) and data["documents_required"]:
        export["documents_required"] = list(
            dict.fromkeys(list(export["documents_required"]) + list(data["documents_required"]))
        )

    clauses_by_type: dict[str, ImportantClause] = {}
    for c in export["important_clauses"]:
        if isinstance(c, dict) and c.get("clause_type"):
            clauses_by_type[c["clause_type"]] = ImportantClause(**c)
    if isinstance(data.get("important_clauses"), list):
        for c in data["important_clauses"]:
            if not (isinstance(c, dict) and c.get("clause_type")):
                continue
            allowed = set(ImportantClause.model_fields)
            candidate = ImportantClause(**{k: v for k, v in c.items() if k in allowed})
            existing = clauses_by_type.get(candidate.clause_type)
            if existing is None or len(candidate.content or "") > len(existing.content or ""):
                clauses_by_type[candidate.clause_type] = candidate

    tno = export["tender_information"].get("tender_no")
    if isinstance(tno, str) and tno.strip():
        export["tender_information"]["tender_no"] = tno.strip().split()[0].strip(" .,;:")

    return TenderResult(
        status=result.status,
        meta=result.meta,
        tender_information=TenderInformation(**export["tender_information"]),
        financial=FinancialInfo(**export["financial"]),
        dates=ImportantDates(**export["dates"]),
        eligibility=EligibilityInfo(**export["eligibility"]),
        products=products,
        documents_required=list(export["documents_required"]),
        important_clauses=list(clauses_by_type.values()),
        contact_details=ContactDetails(**export["contact_details"]),
        raw_text=result.raw_text,
        tables=result.tables,
    )
