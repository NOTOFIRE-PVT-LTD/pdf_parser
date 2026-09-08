"""
Export service — JSON, Excel, CSV.

Flat sheet: one row per BOQ line. Column names match the portal CSV template
exactly. Every cell comes from parsed NIT/PDF fields only — missing → null.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.config import Settings, get_settings
from app.models.schemas import ProductItem, TenderResult
from app.utils.product_name import (
    extract_item_drawing_number,
    extract_item_inspection_agency,
    extract_item_make_brand,
    extract_item_spec_number,
    extract_item_warranty_period,
    normalize_product_description,
)
from app.utils.text_utils import clean_schedule_title

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# IREPS prints dates as dd/mm/yyyy. Portals that parse with Date() treat
# 25/08/2026 as an invalid US month and then drop every row.
_PORTAL_DMY = re.compile(
    r"^\s*(?P<d>\d{1,2})[/\-.](?P<m>\d{1,2})[/\-.](?P<y>\d{4})"
    r"(?:\s+(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?"
    r"(?:\s*(?:hrs?|hours?))?\s*$",
    re.I,
)
_PORTAL_ISO = re.compile(
    r"^\s*(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T\s](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?"
    r"(?:Z|[+-]\d{2}:\d{2})?\s*$",
    re.I,
)

# Exact portal CSV template columns — order must not change.
FLAT_EXCEL_COLUMNS: list[str] = [
    "title",
    "tenderNo",
    "referenceNo",
    "description",
    "zone",
    "railway",
    "division",
    "status",
    "advertisedValue",
    "earnestMoney",
    "tenderDocCost",
    "periodOfCompletion",
    "validityDays",
    "closingAt",
    "publishedAt",
    "biddingStartDate",
    "pdfUrl",
    "biddingType",
    "tenderType",
    "tenderingSection",
    "contractType",
    "contractCategory",
    "expenditureType",
    "biddingStyle",
    "biddingUnit",
    "preBidRequired",
    "preBidDate",
    "jvAllowed",
    "jvMembersAllowed",
    "consortiumAllowed",
    "consortiumMembersAllowed",
    "rankingOrder",
    "signingAuthorityName",
    "signingAuthorityDesignation",
    "itemSerialNo",
    "itemCode",
    "itemDescription",
    "itemQty",
    "itemUnit",
    "itemUnitRate",
    "itemBasicValue",
    "itemAmount",
    "itemCategory",
    "itemSpecNumber",
    "itemDrawingNumber",
    "itemMakeBrand",
    "itemInspectionAgency",
    "itemWarrantyPeriod",
    "productName",
]


class ExportService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def _sanitize_cell(value: Any) -> Any:
        if isinstance(value, str) and value[:1] in _FORMULA_TRIGGER_CHARS:
            return "'" + value
        return value

    @staticmethod
    def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
        return {k: ExportService._sanitize_cell(v) for k, v in row.items()}

    @staticmethod
    def _cell(value: Any) -> Any:
        """Return value for export, or null — never substitute a guess."""
        if value is None:
            return None
        if isinstance(value, str):
            # Keep one clean cell: no newlines / tabs that break CSV columns
            text = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
            text = re.sub(r" +", " ", text).strip()
            if not text:
                return None
            return text
        return value

    @staticmethod
    def _excel_value(value: Any) -> str:
        if value is None:
            return ""
        return _ILLEGAL_XML.sub("", str(value))

    @classmethod
    def _portal_datetime(cls, value: Any) -> str | None:
        """Normalize IREPS dd/mm/yyyy to ISO so portal Date() parsers accept the row."""
        text = cls._cell(value)
        if not text:
            return None
        raw = str(text).strip()
        if re.search(r"(?i)\b(?:na|n/?a|nil|none|not\s+applicable|no)\b", raw) and not re.search(
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", raw
        ):
            return None
        m = _PORTAL_ISO.match(raw) or _PORTAL_DMY.match(raw)
        if not m:
            return None
        try:
            year = int(m.group("y"))
            month = int(m.group("m"))
            day = int(m.group("d"))
        except (TypeError, ValueError):
            return None
        if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
            return None
        hour = m.groupdict().get("H")
        minute = m.groupdict().get("M")
        second = m.groupdict().get("S")
        if hour is None:
            return f"{year:04d}-{month:02d}-{day:02d}"
        try:
            hh = int(hour)
            mm = int(minute or 0)
            ss = int(second or 0)
        except ValueError:
            return f"{year:04d}-{month:02d}-{day:02d}"
        if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
            return f"{year:04d}-{month:02d}-{day:02d}"
        return f"{year:04d}-{month:02d}-{day:02d}T{hh:02d}:{mm:02d}:{ss:02d}"

    @staticmethod
    def _name_from_pdf(description: str | None, product_name: str | None) -> str | None:
        """Export a product name only if it is grounded in the PDF description."""
        name = ExportService._cell(product_name)
        desc = (description or "").lower()
        if not name or not desc:
            return None
        nl = str(name).lower().strip()
        if nl in desc:
            return name
        words = [w for w in nl.split() if len(w) > 2]
        if words and sum(1 for w in words if w in desc) / len(words) >= 0.7:
            return name
        return None

    @staticmethod
    def _yes_no(value: str | None) -> str | None:
        if value is None:
            return None
        t = str(value).strip().lower()
        if not t:
            return None
        if t in {"y", "yes", "true", "required", "1"}:
            return "Yes"
        if t in {"n", "no", "false", "not required", "0", "nil", "na", "n/a", "not applicable"}:
            return "No"
        if re.search(r"(?i)not\s+applicable", t):
            return "No"
        # Keep PDF wording if it's already Yes/No-ish text
        if re.search(r"(?i)\byes\b", t) and not re.search(r"(?i)\bno\b", t):
            return "Yes"
        if re.search(r"(?i)\bno\b", t):
            return "No"
        return ExportService._cell(value)

    def to_json_str(self, result: TenderResult, indent: int = 2) -> str:
        return json.dumps(result.to_export_dict(), indent=indent, ensure_ascii=False)

    def to_json_bytes(self, result: TenderResult) -> bytes:
        return self.to_json_str(result).encode("utf-8")

    def to_excel_bytes(self, result: TenderResult) -> bytes:
        return self.to_combined_excel_bytes([result])

    def to_combined_excel_bytes(self, results: list[TenderResult]) -> bytes:
        rows = self._flat_rows(results)
        wb = Workbook()
        ws = wb.active
        ws.title = "Tender Data"
        ws.append(list(FLAT_EXCEL_COLUMNS))
        for row in rows:
            ws.append([self._excel_value(row.get(col)) for col in FLAT_EXCEL_COLUMNS])
        for col in ws.columns:
            for cell in col:
                cell.number_format = "@"
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.read()

    def to_combined_csv_bytes(self, results: list[TenderResult]) -> bytes:
        """Same portal columns as Excel — full data in one CSV download."""
        rows = self._flat_rows(results)
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=FLAT_EXCEL_COLUMNS,
            extrasaction="ignore",
            restval="",
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {col: "" if row.get(col) is None else row.get(col) for col in FLAT_EXCEL_COLUMNS}
            )
        # UTF-8 without BOM: a leading EF BB BF makes some portals see the
        # first column as "\ufefftitle" and then reject every row.
        return buffer.getvalue().encode("utf-8")

    def _flat_rows(self, results: list[TenderResult]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for result in results:
            rows.extend(self.build_flat_rows(result))
        if not rows:
            rows = [{col: None for col in FLAT_EXCEL_COLUMNS}]
        return [self._sanitize_row(r) for r in rows]

    def build_flat_rows(self, result: TenderResult) -> list[dict[str, Any]]:
        header = self._tender_header(result)
        if not result.products:
            return [self._empty_product_row(header)]
        return [self._product_row(header, product) for product in result.products]

    def _tender_header(self, result: TenderResult) -> dict[str, Any]:
        """Tender-level columns — exact template names; PDF-only values."""
        info = result.tender_information
        dates = result.dates
        work = self._cell(info.name_of_work)

        pre_bid_raw = info.pre_bid_required or info.pre_bid_conference
        pre_bid_date = info.pre_bid_date or dates.pre_bid_meeting
        published = info.published_date or dates.published_date
        closing = info.closing_date_time or dates.bid_submission_end
        start = info.bidding_start_date or dates.bid_submission_start or dates.start_date

        jv_members = self._cell(info.number_of_jv_member_allowed)
        jv_allowed = self._yes_no(info.jv_allowed)
        if jv_allowed is None and jv_members is not None:
            # PDF stated JV member count ⇒ JV is allowed; do not invent Yes otherwise
            jv_allowed = "Yes"

        pre_bid_yn = self._yes_no(pre_bid_raw)
        pre_bid_date = self._portal_datetime(pre_bid_date)
        if pre_bid_yn == "No":
            pre_bid_date = None

        return {
            "title": work,
            "tenderNo": self._cell(info.tender_no),
            "referenceNo": self._cell(info.reference_no) or self._cell(info.tender_no),
            "description": work,
            "zone": self._cell(info.zone),
            "railway": self._cell(info.railway),
            "division": self._cell(info.division_name),
            "status": self._cell(info.status),
            "advertisedValue": self._cell(info.advertised_value),
            "earnestMoney": self._cell(info.earnest_money),
            "tenderDocCost": self._cell(info.tender_doc_cost),
            "periodOfCompletion": self._cell(info.period_of_completion),
            "validityDays": self._cell(info.bid_validity_days),
            "closingAt": self._portal_datetime(closing),
            "publishedAt": self._portal_datetime(published),
            "biddingStartDate": self._portal_datetime(start),
            "pdfUrl": self._cell(info.pdf_url),
            "biddingType": self._cell(info.bidding_type),
            "tenderType": self._cell(info.tender_type),
            "tenderingSection": self._cell(info.tendering_section or info.bidding_system),
            "contractType": self._cell(info.contract_type),
            "contractCategory": self._cell(info.contract_category),
            "expenditureType": self._cell(info.expenditure_type),
            "biddingStyle": self._cell(info.bidding_style),
            "biddingUnit": None,
            "preBidRequired": pre_bid_yn,
            "preBidDate": pre_bid_date,
            "jvAllowed": jv_allowed,
            "jvMembersAllowed": jv_members,
            "consortiumAllowed": self._yes_no(info.consortium_allowed),
            "consortiumMembersAllowed": self._cell(info.consortium_members_allowed),
            "rankingOrder": self._cell(info.ranking_order),
            "signingAuthorityName": self._cell(
                info.signing_authority_name or result.contact_details.officer_name
            ),
            "signingAuthorityDesignation": self._cell(info.signing_authority_designation),
        }

    def _product_row(self, header: dict[str, Any], product: ProductItem) -> dict[str, Any]:
        """Item-level columns — BOQ + description-derived fields from PDF text only."""
        full_desc = normalize_product_description(product.description)
        name = self._name_from_pdf(full_desc, product.product_name)

        spec_no = extract_item_spec_number(full_desc)
        drawing = extract_item_drawing_number(full_desc)
        make = extract_item_make_brand(full_desc)
        inspection = extract_item_inspection_agency(full_desc)
        warranty = extract_item_warranty_period(full_desc)

        row = dict(header)
        row.update(
            {
                "biddingUnit": self._cell(product.bidding_unit),
                "itemSerialNo": self._cell(product.s_no),
                "itemCode": self._cell(product.item_code) or self._cell(product.s_no),
                "itemDescription": self._cell(full_desc),
                "itemQty": self._cell(product.item_qty),
                "itemUnit": self._cell(product.qty_unit),
                "itemUnitRate": self._cell(product.unit_rate),
                "itemBasicValue": self._cell(product.basic_value),
                "itemAmount": self._cell(product.amount),
                "itemCategory": self._cell(
                    clean_schedule_title(product.schedule) if product.schedule else None
                ),
                "itemSpecNumber": self._cell(spec_no),
                "itemDrawingNumber": self._cell(drawing),
                "itemMakeBrand": self._cell(make),
                "itemInspectionAgency": self._cell(inspection),
                "itemWarrantyPeriod": self._cell(warranty),
                "productName": name,
            }
        )
        return row

    @staticmethod
    def _empty_product_row(header: dict[str, Any]) -> dict[str, Any]:
        row = dict(header)
        for col in FLAT_EXCEL_COLUMNS:
            row.setdefault(col, None)
        return row

    def to_csv_bytes(self, result: TenderResult, which: str = "products") -> bytes:
        if which == "products":
            return self.to_combined_csv_bytes([result])
        buffer = io.StringIO()
        if which == "documents":
            writer = csv.DictWriter(buffer, fieldnames=["document"])
            writer.writeheader()
            for d in result.documents_required:
                writer.writerow({"document": self._sanitize_cell(d)})
        else:
            flat = self._flatten_summary(result)
            writer = csv.DictWriter(buffer, fieldnames=["field", "value"])
            writer.writeheader()
            for k, v in flat.items():
                writer.writerow({"field": k, "value": self._sanitize_cell(v)})
        return buffer.getvalue().encode("utf-8-sig")

    def save(
        self,
        result: TenderResult,
        stem: str,
        formats: list[str] | None = None,
    ) -> dict[str, Path]:
        formats = formats or ["json", "xlsx", "csv"]
        out: dict[str, Path] = {}
        base = self.settings.export_dir / stem
        if "json" in formats:
            path = Path(str(base) + ".json")
            path.write_bytes(self.to_json_bytes(result))
            out["json"] = path
        if "xlsx" in formats or "excel" in formats:
            path = Path(str(base) + ".xlsx")
            path.write_bytes(self.to_excel_bytes(result))
            out["xlsx"] = path
        if "csv" in formats:
            path = Path(str(base) + "_products.csv")
            path.write_bytes(self.to_csv_bytes(result, which="products"))
            out["csv"] = path
        return out

    @staticmethod
    def _flatten_summary(result: TenderResult) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for section, data in result.to_export_dict().items():
            if isinstance(data, dict):
                for k, v in data.items():
                    flat[f"{section}.{k}"] = v
            elif section == "documents_required":
                flat[section] = ", ".join(data)
            elif section == "products":
                flat["products_count"] = len(data)
        return flat
