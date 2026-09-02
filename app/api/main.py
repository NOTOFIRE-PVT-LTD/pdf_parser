"""
Optional FastAPI HTTP API for Tender Parser.

Streamlit is the primary UI; this API enables programmatic access.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import get_settings
from app.models.schemas import ExtractionStatus, TenderResult
from app.services.export_service import ExportService
from app.services.pipeline import TenderPipeline

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Offline tender/NIT PDF extraction API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = TenderPipeline(settings)
exporter = ExportService(settings)
_RESULTS: dict[str, TenderResult] = {}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@app.post("/api/v1/parse")
async def parse_tender(
    file: UploadFile = File(...),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    job_id = str(uuid.uuid4())
    dest = settings.upload_dir / f"{job_id}_{Path(file.filename).name}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    try:
        result = pipeline.process(dest)
    finally:
        dest.unlink(missing_ok=True)

    _RESULTS[job_id] = result

    if result.status == ExtractionStatus.OCR_FAILED:
        return JSONResponse(
            status_code=422,
            content={
                "job_id": job_id,
                "status": result.status.value,
                "error": result.meta.error if result.meta else "OCR failed",
            },
        )
    if result.status == ExtractionStatus.FAILED:
        raise HTTPException(
            status_code=500,
            detail=result.meta.error if result.meta else "Extraction failed",
        )

    payload = result.to_export_dict()
    payload["job_id"] = job_id
    payload["status"] = result.status.value
    payload["meta"] = result.meta.model_dump(mode="json") if result.meta else None
    return payload


@app.get("/api/v1/export/{job_id}/{fmt}")
def export_result(job_id: str, fmt: str) -> Response:
    result = _RESULTS.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found. Parse a PDF first.")

    fmt = fmt.lower()
    if fmt == "json":
        return Response(
            content=exporter.to_json_bytes(result),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
        )
    if fmt in {"xlsx", "excel"}:
        return Response(
            content=exporter.to_excel_bytes(result),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.xlsx"'},
        )
    if fmt == "csv":
        return Response(
            content=exporter.to_csv_bytes(result, which="products"),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{job_id}_products.csv"'},
        )
    raise HTTPException(status_code=400, detail="Format must be json, xlsx, or csv.")
