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

import pandas as pd

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

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")

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
        if t in {"n", "no", "false", "not required", "0", "nil", "na", "n/a"}:
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
        buffer = io.BytesIO()
        pd.DataFrame(rows, columns=FLAT_EXCEL_COLUMNS).to_excel(
            buffer, sheet_name="Tender Data", index=False, engine="openpyxl"
        )
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
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8-sig")

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

        return {
            "title": work,
            "tenderNo": self._cell(info.tender_no),
            "referenceNo": self._cell(info.reference_no),
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
            "closingAt": self._cell(closing),
            "publishedAt": self._cell(published),
            "biddingStartDate": self._cell(start),
            "pdfUrl": self._cell(info.pdf_url),
            "biddingType": self._cell(info.bidding_type),
            "tenderType": self._cell(info.tender_type),
            "tenderingSection": self._cell(info.tendering_section or info.bidding_system),
            "contractType": self._cell(info.contract_type),
            "contractCategory": self._cell(info.contract_category),
            "expenditureType": self._cell(info.expenditure_type),
            "biddingStyle": self._cell(info.bidding_style),
            "biddingUnit": None,
            "preBidRequired": self._yes_no(pre_bid_raw),
            "preBidDate": self._cell(pre_bid_date),
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
                "itemCode": self._cell(product.item_code),
                "itemDescription": self._cell(full_desc),
                "itemQty": self._cell(product.item_qty),
                "itemUnit": self._cell(product.qty_unit),
                "itemUnitRate": self._cell(product.unit_rate),
                "itemBasicValue": self._cell(product.basic_value),
                "itemAmount": self._cell(product.amount),
                "itemCategory": self._cell(product.schedule),
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
