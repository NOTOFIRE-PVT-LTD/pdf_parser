"""
Export service — JSON, Excel, CSV.

Flat sheet: one row per BOQ line. Column order is fixed. Every cell comes from
parsed NIT/PDF fields only — missing values stay null (never invented).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.models.schemas import ProductItem, TenderResult
from app.utils.product_name import (
    extract_item_period,
    extract_item_specs,
    normalize_product_description,
)

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")

# Fixed portal-style columns — order must not change.
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
    "preBidMeeting",
    "biddingType",
    "biddingSystem",
    "dateClosing",
    "published",
    "bidValidity",
    "biddingUnit",
    "tenderType",
    "contractType",
    "contractCategory",
    "allowJointVenture",
    "biddingStyle",
    "itemNo",
    "itemCode",
    "itemName",
    "itemQty",
    "itemUnit",
    "itemRate",
    "itemTotal",
    "itemBaseValue",
    "itemCategory",
    "itemSpecs",
    "productName",
    "itemDescription",
    "itemPeriod",
    "schedule",
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
        if isinstance(value, str) and not value.strip():
            return None
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
        writer = csv.DictWriter(buffer, fieldnames=FLAT_EXCEL_COLUMNS, extrasaction="ignore")
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
        """Tender-level columns — 1:1 from parsed NIT fields; null if absent."""
        info = result.tender_information
        work = self._cell(info.name_of_work)
        return {
            "title": work,
            "tenderNo": self._cell(info.tender_no),
            # Never copy tenderNo into referenceNo — only if PDF had it
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
            "preBidMeeting": self._cell(info.pre_bid_conference),
            "biddingType": self._cell(info.bidding_type),
            "biddingSystem": self._cell(info.bidding_system),
            "dateClosing": self._cell(info.closing_date_time),
            "published": self._cell(info.published_date),
            "bidValidity": self._cell(info.bid_validity_days),
            "biddingUnit": None,
            "tenderType": self._cell(info.tender_type),
            "contractType": self._cell(info.contract_type),
            "contractCategory": self._cell(info.contract_category),
            "allowJointVenture": self._cell(info.number_of_jv_member_allowed),
            "biddingStyle": self._cell(info.bidding_style),
        }

    def _product_row(self, header: dict[str, Any], product: ProductItem) -> dict[str, Any]:
        """Item-level columns — BOQ values + description-derived fields from PDF text only."""
        full_desc = normalize_product_description(product.description)
        name = self._name_from_pdf(full_desc, product.product_name)
        # Specs / period are substrings of the PDF description, or null
        specs = extract_item_specs(full_desc)
        period = extract_item_period(full_desc)
        if specs and full_desc and specs.lower() not in full_desc.lower():
            specs = None
        if period and full_desc and period.lower() not in full_desc.lower():
            period = None

        row = dict(header)
        row.update(
            {
                "biddingUnit": self._cell(product.bidding_unit),
                "itemNo": self._cell(product.s_no),
                "itemCode": self._cell(product.item_code),
                "itemName": name,
                "itemQty": self._cell(product.item_qty),
                "itemUnit": self._cell(product.qty_unit),
                "itemRate": self._cell(product.unit_rate),
                "itemTotal": self._cell(product.amount),
                "itemBaseValue": self._cell(product.basic_value),
                "itemCategory": self._cell(product.schedule),
                "itemSpecs": self._cell(specs),
                "productName": name,
                "itemDescription": self._cell(full_desc),
                "itemPeriod": self._cell(period),
                "schedule": self._cell(product.schedule),
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
