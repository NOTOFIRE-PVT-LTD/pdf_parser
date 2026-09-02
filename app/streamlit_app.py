"""
TenderTools — PDF → Excel converter UI.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app.config import get_settings
from app.models.schemas import ExtractionStatus, TenderResult
from app.services.ai_correction import apply_ai_fix
from app.services.export_service import ExportService
from app.services.pipeline import TenderPipeline
from app.static.icons import LOGO_ICON, PDF_ICON, XLS_ICON
from app.services.name_learning import get_name_learning
from app.services.product_sanitize import _renumber_sequential
from app.utils.activity_feed import ORDER, STAGE_MESSAGES, render_activity_html

settings = get_settings()
CSS_PATH = Path(__file__).resolve().parent / "static" / "css" / "styles.css"


def _inject_css() -> None:
    if CSS_PATH.exists():
        st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _init_state() -> None:
    if "results" not in st.session_state:
        st.session_state.results = []


def _render_header() -> None:
    st.markdown(
        f"""
        <div class="tt-header">
          <div class="tt-brand">
            <div class="tt-logo">{LOGO_ICON}</div>
            <div class="tt-brand-text">
              <h2>TenderTools</h2>
              <p>Simplify Your Work</p>
            </div>
          </div>
          <div class="tt-header-actions">
            <div class="tt-theme-toggle">
              <span class="active">☀️</span>
              <span>🌙</span>
            </div>
            <span class="tt-help-link">
              <span class="tt-help-icon">?</span> Help
            </span>
          </div>
        </div>
        <div class="tt-hero">
          <div class="tt-hero-badge">Fast &bull; Secure &bull; Accurate</div>
          <h1>PDF &rarr; <span class="excel">Excel</span></h1>
          <p class="subtitle">Upload tender PDF and get products with exact names in Excel format.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_features() -> None:
    st.markdown(
        """
        <div class="tt-features">
          <div class="tt-feature">
            <div class="tt-feature-icon blue">⚡</div>
            <h4>Saves Time</h4>
            <p>Get clean Excel in seconds</p>
          </div>
          <div class="tt-feature">
            <div class="tt-feature-icon purple">🎯</div>
            <h4>Exact Product Names</h4>
            <p>Accurate extraction from tender PDFs</p>
          </div>
          <div class="tt-feature">
            <div class="tt-feature-icon green">🛡️</div>
            <h4>Your Data is Safe</h4>
            <p>Files are processed securely and not stored</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_footer() -> None:
    st.markdown(
        """
        <div class="tt-footer">
          <span>&copy; 2024 TenderTools. Simplify Your Work.</span>
          <div class="tt-footer-links">
            <a href="#">Privacy</a>
            <span>|</span>
            <a href="#">Terms</a>
            <span>|</span>
            <a href="#">Help</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _renumber_display(products: list) -> None:
    _renumber_sequential(products)


def _summary_line(result: TenderResult) -> str:
    info = result.tender_information
    parts: list[str] = []
    if info.tender_no:
        parts.append(info.tender_no)
    if info.division_name:
        parts.append(info.division_name)
    parts.append(f"{len(result.products)} products")
    return " · ".join(parts)


def _render_ai_agent(products: list, result_key: str) -> None:
    st.markdown("---")
    st.markdown("#### 🤖 AI Fix Agent")
    st.caption(
        "Instructions do aur Gemini agent product names fix karega. "
        "Example: *Remove supply/installation from all names* or *Delete junk clause rows*"
    )

    if settings.gemini_api_key:
        st.caption(f"Gemini · model `{settings.gemini_model}`")
    else:
        st.warning("Set `GEMINI_API_KEY` in `.env` file to use AI Fix.")

    instruction = st.text_area(
        "Your instructions",
        placeholder="Remove supply, installation, testing & commissioning from all product names. "
        "Keep only actual product. Fix wrong rows.",
        height=90,
        key=f"ai_instr_{result_key}",
    )

    if st.button("Run AI Fix", type="primary", key=f"ai_run_{result_key}"):
        if not instruction.strip():
            st.warning("Pehle instruction likho.")
            return
        with st.spinner("Gemini rechecking all rows…"):
            result = apply_ai_fix(products, instruction, settings)
        if result.error and not result.updated and not result.removed:
            st.error(result.summary)
        else:
            st.success(
                f"{result.summary} — {result.updated} updated, {result.removed} removed."
            )
            st.rerun()


def _products_preview(products: list, result_key: str = "default") -> None:
    _renumber_display(products)
    rows = [
        {
            "S.No.": str(i + 1),
            "Product Name": p.product_name,
            "Description": p.description,
            "Qty": p.item_qty,
            "Unit": p.qty_unit,
            "Amount": p.amount,
        }
        for i, p in enumerate(products)
    ]
    if not rows:
        st.info("No products found in this PDF.")
        return

    learning = get_name_learning()
    stats = learning.stats()
    if stats["exact"] or stats["prefixes"]:
        st.caption(
            f"Memory: {stats['exact']} exact names, {stats['prefixes']} learned patterns "
            f"(applies to all PDFs)"
        )

    height = min(max(len(rows) * 35 + 38, 120), 420)
    edited = st.data_editor(
        rows,
        width="stretch",
        hide_index=True,
        height=height,
        column_config={
            "Product Name": st.column_config.TextColumn("Product Name", required=True),
            "Description": st.column_config.TextColumn("Description", disabled=True),
            "S.No.": st.column_config.TextColumn("S.No.", disabled=True),
            "Qty": st.column_config.TextColumn("Qty", disabled=True),
            "Unit": st.column_config.TextColumn("Unit", disabled=True),
            "Amount": st.column_config.TextColumn("Amount", disabled=True),
        },
        key=f"product_editor_{result_key}",
    )

    if st.button("Save corrections & learn", key=f"learn_{result_key}"):
        learned = 0
        for idx, row in enumerate(edited):
            new_name = (row.get("Product Name") or "").strip()
            desc = products[idx].description or ""
            old_name = products[idx].product_name or ""
            if new_name and new_name != old_name:
                learning.learn(desc, new_name)
                products[idx].product_name = new_name
                learned += 1
        if learned:
            st.success(f"Saved {learned} correction(s). Future PDFs will use this automatically.")
            st.rerun()
        else:
            st.info("No changes to save.")

    _render_ai_agent(products, result_key)


def _update_activity(activity_box, completed: list[str], current: str | None) -> None:
    cur_idx = ORDER.index(current) if current in ORDER else len(ORDER)
    steps: list[tuple[str, str]] = []
    for i, stage in enumerate(ORDER):
        msg = STAGE_MESSAGES[stage]
        if stage in completed or i < cur_idx:
            steps.append(("done", msg))
        elif stage == current:
            steps.append(("active", msg))
    activity_box.markdown(
        render_activity_html(steps, active=bool(current)),
        unsafe_allow_html=True,
    )


def _match_stage(raw: str) -> str:
    for key in ORDER:
        if key.lower() in raw.lower():
            return key
    return raw


def _process_uploads(uploaded: list, activity_box) -> list[TenderResult]:
    pipeline = TenderPipeline(settings)
    progress = st.progress(0.0)
    completed: list[str] = []
    current: str | None = "Opening PDF"
    _update_activity(activity_box, completed, current)

    results: list[TenderResult] = []
    for file_idx, up in enumerate(uploaded):
        safe_name = Path(up.name).name
        dest = settings.upload_dir / safe_name
        if dest.exists():
            dest = settings.upload_dir / f"{file_idx}_{safe_name}"
        dest.write_bytes(up.getvalue())

        def on_progress(stage: str, frac: float, base=file_idx, total=len(uploaded)) -> None:
            nonlocal current, completed
            matched = _match_stage(stage)
            if current and current != matched and current not in completed:
                completed.append(current)
            if matched in ORDER:
                current = matched
            progress.progress(min((base + frac) / total, 1.0))
            _update_activity(activity_box, completed, current)
            time.sleep(0.06)

        results.append(
            pipeline.process(
                dest,
                progress_callback=on_progress,
            )
        )

    if current and current not in completed:
        completed.append(current)
    _update_activity(activity_box, completed, None)
    progress.progress(1.0)
    time.sleep(0.25)
    progress.empty()
    return results


def _check_errors(results: list[TenderResult]) -> bool:
    for result in results:
        if result.status == ExtractionStatus.OCR_FAILED:
            st.error("Scanned PDF — OCR failed. Install Tesseract and retry.")
            return True
        if result.status == ExtractionStatus.FAILED:
            st.error(result.meta.error if result.meta and result.meta.error else "Extraction failed.")
            return True
    return False


def _render_results(ok_results: list[TenderResult]) -> None:
    exporter = ExportService(settings)
    total = sum(len(r.products) for r in ok_results)
    st.markdown(
        f'<div class="tt-success">✓ Done — {len(ok_results)} file(s), {total} products extracted</div>',
        unsafe_allow_html=True,
    )

    if len(ok_results) == 1:
        result = ok_results[0]
        info = result.tender_information
        title = info.name_of_work or info.tender_no or "Tender"
        stem = Path(result.meta.filename if result.meta else "tender").stem
        st.markdown(
            f'<div class="tt-results-box"><h3>{title}</h3>'
            f'<p class="meta">{_summary_line(result)}</p></div>',
            unsafe_allow_html=True,
        )
        _products_preview(result.products, result_key=stem)
        st.download_button(
            "📊 Download Excel",
            data=exporter.to_excel_bytes(result),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            width="stretch",
        )
        return

    for idx, result in enumerate(ok_results):
        with st.expander(_summary_line(result), expanded=idx == 0):
            _products_preview(result.products, result_key=f"exp_{idx}")

    st.download_button(
        "📊 Download All (Excel)",
        data=exporter.to_combined_excel_bytes(ok_results),
        file_name="tenders_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )


def main() -> None:
    _init_state()
    st.set_page_config(
        page_title="TenderTools — PDF to Excel",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    _render_header()

    col_pdf, col_main, col_xls = st.columns([1, 2.2, 1], gap="small")

    with col_pdf:
        st.markdown('<div class="tt-col-marker tt-col-pdf"></div>', unsafe_allow_html=True)
        st.markdown(PDF_ICON, unsafe_allow_html=True)

    with col_main:
        st.markdown('<div class="tt-col-marker tt-col-main"></div>', unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "Choose PDF",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        st.markdown(
            '<p class="tt-file-note">Max file size: 200MB &bull; Supported format: PDF</p>',
            unsafe_allow_html=True,
        )
        activity_box = st.empty()
        convert = st.button(
            "📊 Convert to Excel →",
            type="primary",
            disabled=not uploaded,
            width="stretch",
        )

    with col_xls:
        st.markdown('<div class="tt-col-marker tt-col-xls"></div>', unsafe_allow_html=True)
        st.markdown(XLS_ICON, unsafe_allow_html=True)

    _render_features()
    _render_footer()

    if convert and uploaded:
        st.session_state.results = _process_uploads(uploaded, activity_box)

    results: list[TenderResult] = st.session_state.results
    if not results or _check_errors(results):
        return

    ok = [r for r in results if r.status == ExtractionStatus.COMPLETED]
    if ok:
        _render_results(ok)
        if st.button("Convert another PDF", type="secondary"):
            st.session_state.results = []
            st.rerun()


if __name__ == "__main__":
    main()
