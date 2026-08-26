"""
Tender Parser — Streamlit V1 UI.

Features:
- Multi-PDF drag & drop upload
- Progress / status
- Collapsible result sections
- Search (products, tender number, organization)
- Download JSON / Excel / CSV + copy JSON
- Dark mode via Streamlit theme sidebar toggle
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path when launched as `streamlit run app/streamlit_app.py`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app.config import get_settings
from app.models.schemas import ExtractionStatus, TenderResult
from app.services.ai.factory import get_ai_service
from app.services.export_service import ExportService
from app.services.pipeline import TenderPipeline
from app.services.search_service import filter_products, matches_query
from app.utils.env_settings import PROVIDER_ENV_VAR, has_saved_key
from app.utils.ui_settings import load_ui_settings, save_ui_settings

settings = get_settings()
CSS_PATH = Path(__file__).resolve().parent / "static" / "css" / "styles.css"


TENDER_INFO_LABELS = {
    "name_of_work": "Name of Work",
    "tender_no": "Tender No",
    "closing_date_time": "Closing Date/Time",
    "division_name": "Division Name",
    "zone": "Zone",
    "advertised_value": "Advertised Value",
    "earnest_money": "Earnest Money (Rs.)",
    "period_of_completion": "Period of Completion",
    "number_of_jv_member_allowed": "Number of JV Member Allowed",
}

PRODUCT_COLUMN_ORDER = [
    "schedule",
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


def _inject_css() -> None:
    if CSS_PATH.exists():
        st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _init_state() -> None:
    saved = load_ui_settings()
    # AI provider config comes entirely from .env (no in-app key entry) —
    # default the toggle/provider to whatever's actually configured there so
    # extraction just works without any setup in the UI itself.
    env_ai_active = settings.ai_enabled and (
        settings.ai_provider == "ollama" or has_saved_key(settings.ai_provider)
    )
    defaults = {
        "results": [],  # list[TenderResult]
        "last_stage": "",
        "dark_mode": saved.get("dark_mode", True),
        "pdf_password": "",
        "enrich_ai": env_ai_active or saved.get("enrich_ai", False),
        "ai_provider": settings.ai_provider if env_ai_active else saved.get("ai_provider", "gemini"),
        "schema_version": 11,
        "_settings_loaded": True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    # Drop stale results created before TenderInformation schema change
    if st.session_state.get("schema_version") != 11:
        st.session_state.results = []
        st.session_state.schema_version = 11
    else:
        cleaned = []
        for r in st.session_state.results:
            info = getattr(r, "tender_information", None)
            if info is not None and hasattr(info, "name_of_work"):
                cleaned.append(r)
        if len(cleaned) != len(st.session_state.results):
            st.session_state.results = cleaned


def _persist_sidebar_settings() -> None:
    """Persist only non-secret preferences; the PDF password stays in-memory only."""
    save_ui_settings(
        {
            "dark_mode": st.session_state.get("dark_mode", True),
            "enrich_ai": st.session_state.get("enrich_ai", False),
            "ai_provider": st.session_state.get("ai_provider", "gemini"),
        }
    )


def _status_badge(status: ExtractionStatus) -> str:
    mapping = {
        ExtractionStatus.COMPLETED: ("ok", "Completed"),
        ExtractionStatus.PASSWORD_REQUIRED: ("warn", "Password required"),
        ExtractionStatus.OCR_FAILED: ("err", "OCR failed"),
        ExtractionStatus.FAILED: ("err", "Failed"),
        ExtractionStatus.PROCESSING: ("warn", "Processing"),
        ExtractionStatus.PENDING: ("warn", "Pending"),
        ExtractionStatus.OCR_REQUIRED: ("warn", "OCR required"),
    }
    css, label = mapping.get(status, ("warn", status.value))
    return f'<span class="tp-badge {css}">{label}</span>'


def _kv_table(data: dict, label_map: dict[str, str] | None = None) -> None:
    rows = []
    for k, v in data.items():
        if label_map and k in label_map:
            label = label_map[k]
        else:
            label = k.replace("_", " ").title()
        display = "—" if v in (None, "", []) else v
        rows.append({"Field": label, "Value": display})
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _products_table(products: list) -> None:
    rows = []
    for p in products:
        raw = p.model_dump()
        ordered = {col: raw.get(col) for col in PRODUCT_COLUMN_ORDER if col in raw}
        rows.append(
            {
                "Schedule": ordered.get("schedule"),
                "S.No.": ordered.get("s_no"),
                "Item Code": ordered.get("item_code"),
                "Item Qty": ordered.get("item_qty"),
                "Qty Unit": ordered.get("qty_unit"),
                "Unit Rate": ordered.get("unit_rate"),
                "Basic Value": ordered.get("basic_value"),
                "Escl.(%)": ordered.get("escalation"),
                "Amount": ordered.get("amount"),
                "Bidding Unit": ordered.get("bidding_unit"),
                "Description": ordered.get("description"),
            }
        )
    # st.dataframe defaults to a short fixed-height viewport that only shows a
    # handful of rows before scrolling — with 50-100+ BOQ line items that reads
    # as "only N products found" even though the rest are just below the fold.
    # Size the viewport to the data (capped) so it's obvious there's more.
    row_px = 35
    height = min(max(len(rows) * row_px + row_px, 150), 640)
    st.dataframe(rows, use_container_width=True, hide_index=True, height=height)


def _render_result(result: TenderResult, idx: int) -> None:
    meta = result.meta
    info = result.tender_information
    # Safe access — old session results from before schema update may lack fields
    name_of_work = getattr(info, "name_of_work", None)
    tender_no = getattr(info, "tender_no", None)
    title = name_of_work or tender_no or (meta.filename if meta else f"Tender {idx + 1}")
    st.markdown(f"### {title}")
    cols = st.columns([2, 2, 2, 2])
    with cols[0]:
        st.markdown(_status_badge(result.status), unsafe_allow_html=True)
    if meta:
        with cols[1]:
            st.caption(f"Type: **{meta.pdf_type.value}**")
        with cols[2]:
            st.caption(f"Pages: **{meta.page_count}**")
        with cols[3]:
            st.caption(f"OCR: **{'Yes' if meta.ocr_used else 'No'}**")

    if result.status == ExtractionStatus.PASSWORD_REQUIRED:
        st.warning(meta.error if meta and meta.error else "This PDF is encrypted. Enter the password in the sidebar and re-upload.")
        return
    if result.status == ExtractionStatus.OCR_FAILED:
        st.error(meta.error if meta and meta.error else "OCR failed. Install Tesseract and retry.")
        return
    if result.status == ExtractionStatus.FAILED:
        st.error(meta.error if meta and meta.error else "Extraction failed.")
        return

    if meta and meta.warnings:
        for w in meta.warnings:
            st.warning(w)

    with st.expander("Tender Information", expanded=True):
        try:
            info_dict = result.tender_information.model_dump()
        except Exception:
            info_dict = {
                "name_of_work": getattr(result.tender_information, "name_of_work", None),
                "tender_no": getattr(result.tender_information, "tender_no", None),
                "closing_date_time": getattr(result.tender_information, "closing_date_time", None),
                "division_name": getattr(result.tender_information, "division_name", None),
                "zone": getattr(result.tender_information, "zone", None),
                "advertised_value": getattr(result.tender_information, "advertised_value", None),
                "earnest_money": getattr(result.tender_information, "earnest_money", None),
                "period_of_completion": getattr(result.tender_information, "period_of_completion", None),
                "number_of_jv_member_allowed": getattr(
                    result.tender_information, "number_of_jv_member_allowed", None
                ),
            }
        # Ensure all expected keys exist for display
        for key in TENDER_INFO_LABELS:
            info_dict.setdefault(key, None)
        _kv_table(info_dict, TENDER_INFO_LABELS)

    with st.expander(f"Products ({len(result.products)})", expanded=True):
        product_query = st.text_input(
            "Filter products",
            key=f"product_q_{idx}",
            placeholder="Search description, item code, schedule…",
        )
        products = filter_products(result, product_query)
        if products:
            if product_query:
                st.caption(f"Showing {len(products)} of {len(result.products)} products (filtered). Scroll the table for more.")
            elif len(products) > 10:
                st.caption(f"{len(products)} products — scroll the table below to see all of them.")
            _products_table(products)
        else:
            st.info("No products matched.")

    exporter = ExportService(settings)
    json_str = exporter.to_json_str(result)
    stem = Path(meta.filename if meta else f"tender_{idx}").stem

    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.download_button(
            "Download JSON",
            data=exporter.to_json_bytes(result),
            file_name=f"{stem}.json",
            mime="application/json",
            key=f"dl_json_{idx}",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Excel",
            data=exporter.to_excel_bytes(result),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_xlsx_{idx}",
            use_container_width=True,
        )
    with d3:
        st.download_button(
            "Download CSV",
            data=exporter.to_csv_bytes(result, which="products"),
            file_name=f"{stem}_products.csv",
            mime="text/csv",
            key=f"dl_csv_{idx}",
            use_container_width=True,
        )
    with d4:
        st.download_button(
            "Copy JSON (download)",
            data=json_str,
            file_name=f"{stem}_copy.json",
            mime="application/json",
            key=f"copy_json_{idx}",
            use_container_width=True,
            help="Browsers block clipboard writes without a secure context; download the JSON to copy locally.",
        )


def main() -> None:
    _init_state()

    st.set_page_config(
        page_title=settings.app_name,
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css()

    # Theme toggle (Streamlit native themes: user can also use Settings → Theme)
    with st.sidebar:
        st.markdown(f"## {settings.app_name}")
        mode_label = "Online AI" if st.session_state.enrich_ai else "Offline"
        st.caption(f"v{settings.app_version} · {mode_label} extraction")
        st.toggle(
            "Dark mode preference",
            key="dark_mode",
            help="Tip: also set Theme → Dark in the Streamlit menu (☰) for full dark UI.",
        )
        st.text_input(
            "PDF password (if encrypted)",
            type="password",
            key="pdf_password",
        )

        st.divider()
        st.markdown("### Extraction mode")
        st.toggle(
            "Online AI extraction",
            key="enrich_ai",
            help=(
                "Reads the ENTIRE document in page-group sections through Gemini / OpenAI / "
                "Claude / Ollama and merges every section's findings — not just a quick pass "
                "over the first few pages. Large PDFs mean more sections, so this can take "
                "noticeably longer than offline mode. Needs an API key (Ollama runs locally, "
                "no key needed)."
            ),
        )
        if st.session_state.enrich_ai:
            providers = ["gemini", "openai", "anthropic", "ollama"]
            current = st.session_state.ai_provider
            if current not in providers:
                st.session_state.ai_provider = "gemini"
            st.selectbox(
                "AI provider",
                options=providers,
                key="ai_provider",
            )
            provider = st.session_state.ai_provider
            # No API key entry in the UI at all — it's read straight from
            # .env at startup (app.config.get_settings()). To configure or
            # change it, edit .env and restart the app.
            if provider == "ollama":
                st.info("Ollama uses local models at localhost:11434 (no cloud key needed).")
            elif has_saved_key(provider):
                st.success(f"")
            else:
                env_var = PROVIDER_ENV_VAR[provider]
                st.warning(
                    f"No {provider} key configured. Add `{env_var}=...` and `AI_ENABLED=true` "
                    f"to your `.env` file, then restart the app."
                )
        else:
            st.caption("Offline mode uses regex rules only (no internet / no API).")

        # Persist after widgets update session_state
        _persist_sidebar_settings()

        st.divider()
        global_search = st.text_input(
            "Search results",
            placeholder="Tender No, Division, Name of Work, product…",
            key="global_search",
        )
        if st.button("Clear results", use_container_width=True):
            st.session_state.results = []
            st.rerun()

    online = st.session_state.enrich_ai
    st.markdown(
        f"""
        <div class="tp-hero">
          <h1>{settings.app_name}</h1>
          <p>Upload tender / NIT PDFs.
          Mode: <b>{"Online AI" if online else "Offline rules"}</b>
          — extract Name of Work, Tender No, schedule products, and more.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "Drag & drop PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        help="Supports text PDFs and scanned PDFs (Tesseract OCR).",
    )

    run_disabled = not uploaded
    if (
        online
        and st.session_state.ai_provider != "ollama"
        and not has_saved_key(st.session_state.ai_provider)
    ):
        run_disabled = True
    run = st.button("Extract", type="primary", disabled=run_disabled)

    if run and uploaded:
        ai_service = None
        if st.session_state.enrich_ai:
            ai_service = get_ai_service(
                settings, provider_override=st.session_state.ai_provider, force_enabled=True
            )
            if not ai_service.is_enabled():
                st.error(
                    "Online AI is not configured. Add the API key to your .env file "
                    "(or start Ollama if using ollama) and restart the app."
                )
                return

        pipeline = TenderPipeline(settings, ai_service=ai_service)
        progress = st.progress(0.0, text="Starting…")
        status_box = st.empty()
        new_results: list[TenderResult] = []

        for file_idx, up in enumerate(uploaded):
            safe_name = Path(up.name).name  # strip any path components from the filename
            dest = settings.upload_dir / safe_name
            if dest.exists():
                dest = settings.upload_dir / f"{file_idx}_{safe_name}"
            dest.write_bytes(up.getvalue())

            def on_progress(stage: str, frac: float, base=file_idx, total=len(uploaded)) -> None:
                overall = (base + frac) / total
                progress.progress(min(overall, 1.0), text=stage)
                status_box.info(f"Extraction status: {stage}")

            result = pipeline.process(
                dest,
                password=st.session_state.pdf_password or None,
                enrich_with_ai=st.session_state.enrich_ai,
                progress_callback=on_progress,
            )
            new_results.append(result)

        progress.progress(1.0, text="Complete")
        status_box.success(
            f"Processed {len(new_results)} file(s) "
            f"({'Online AI' if st.session_state.enrich_ai else 'Offline'})."
        )
        st.session_state.results = new_results + list(st.session_state.results)

    results: list[TenderResult] = list(st.session_state.results)
    if global_search:
        results = [r for r in results if matches_query(r, global_search)]

    if not results:
        st.info("Upload one or more tender PDFs and click **Extract** to begin.")
        return

    st.markdown(f"#### Results ({len(results)})")
    for idx, result in enumerate(results):
        with st.container(border=True):
            _render_result(result, idx)


if __name__ == "__main__":
    main()
