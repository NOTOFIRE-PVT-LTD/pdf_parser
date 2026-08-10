"""
Export service — JSON, Excel, CSV.

Produces downloadable bytes / files from a TenderResult without mutating it.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.models.schemas import TenderResult


# Leading characters Excel/Sheets treat as the start of a formula. Values pulled
# from untrusted PDF text (e.g. a crafted BOQ description) must not be allowed
# to reach a spreadsheet cell unescaped, or opening the export can execute a
# formula (CSV/Excel formula injection).
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


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

    def to_json_str(self, result: TenderResult, indent: int = 2) -> str:
        return json.dumps(result.to_export_dict(), indent=indent, ensure_ascii=False)

    def to_json_bytes(self, result: TenderResult) -> bytes:
        return self.to_json_str(result).encode("utf-8")

    def to_excel_bytes(self, result: TenderResult) -> bytes:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            self._section_df(result.tender_information.model_dump()).to_excel(
                writer, sheet_name="Tender Information", index=False
            )
            self._section_df(result.financial.model_dump()).to_excel(
                writer, sheet_name="Financial", index=False
            )
            self._section_df(result.dates.model_dump()).to_excel(
                writer, sheet_name="Dates", index=False
            )
            self._section_df(result.eligibility.model_dump()).to_excel(
                writer, sheet_name="Eligibility", index=False
            )
            self._section_df(result.contact_details.model_dump()).to_excel(
                writer, sheet_name="Contact", index=False
            )

            products = [p.model_dump() for p in result.products]
            if products:
                for p in products:
                    if isinstance(p.get("page_numbers"), list):
                        p["page_numbers"] = ",".join(str(x) for x in p["page_numbers"])
                products = [self._sanitize_row(p) for p in products]
                pd.DataFrame(products).to_excel(writer, sheet_name="Products", index=False)
            else:
                pd.DataFrame(
                    columns=[
                        "s_no",
                        "item_code",
                        "item_qty",
                        "qty_unit",
                        "unit_rate",
                        "basic_value",
                        "escalation",
                        "amount",
                        "bidding_unit",
                        "description",
                    ]
                ).to_excel(writer, sheet_name="Products", index=False)

            docs = pd.DataFrame(
                {"document": [self._sanitize_cell(d) for d in result.documents_required]}
            )
            docs.to_excel(writer, sheet_name="Documents Required", index=False)

            clauses = [self._sanitize_row(c.model_dump()) for c in result.important_clauses]
            pd.DataFrame(clauses or [{"clause_type": None, "content": None}]).to_excel(
                writer, sheet_name="Clauses", index=False
            )

            if result.meta:
                self._section_df(result.meta.model_dump(mode="json")).to_excel(
                    writer, sheet_name="Meta", index=False
                )
        buffer.seek(0)
        return buffer.read()

    def to_csv_bytes(self, result: TenderResult, which: str = "products") -> bytes:
        """
        Export a flat CSV.
        which: products | summary | documents | clauses
        """
        buffer = io.StringIO()
        if which == "products":
            rows = [p.model_dump() for p in result.products]
            for r in rows:
                if isinstance(r.get("page_numbers"), list):
                    r["page_numbers"] = ",".join(str(x) for x in r["page_numbers"])
            if not rows:
                rows = [{"product_name": None}]
            rows = [self._sanitize_row(r) for r in rows]
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        elif which == "documents":
            writer = csv.DictWriter(buffer, fieldnames=["document"])
            writer.writeheader()
            for d in result.documents_required:
                writer.writerow({"document": self._sanitize_cell(d)})
        elif which == "clauses":
            rows = [c.model_dump() for c in result.important_clauses] or [
                {"clause_type": None, "title": None, "content": None, "page_number": None}
            ]
            rows = [self._sanitize_row(r) for r in rows]
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
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
    def _section_df(data: dict[str, Any]) -> pd.DataFrame:
        rows = [
            {"field": k, "value": ExportService._sanitize_cell(ExportService._stringify(v))}
            for k, v in data.items()
        ]
        return pd.DataFrame(rows)

    @staticmethod
    def _stringify(value: Any) -> Any:
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return value

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
            elif section == "important_clauses":
                flat["clauses_count"] = len(data)
        return flat
