"""NOTOFIRE AI — Claude-style chat UI for PDF → Excel + AI fixes."""

from __future__ import annotations

import html
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.windows_asyncio import apply_windows_asyncio_fix

apply_windows_asyncio_fix()

import streamlit as st

from app.config import reload_settings
from app.models.schemas import ExtractionStatus, TenderResult
from app.services.ai_correction import ChatMessage, chat_ai, resolve_ai_provider
from app.services.chat_store import load_chats, save_chats
from app.services.export_service import ExportService
from app.services.pipeline import TenderPipeline
from app.services.product_sanitize import _renumber_sequential
from app.services.ui_prefs import load_prefs, save_prefs

settings = reload_settings()
CSS_PATH = Path(__file__).resolve().parent / "static" / "css" / "styles.css"

APP_NAME = "Notofire"

# Flame mark (dark UI)
_LOGO_SVG = """
<svg class="nf-logo" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect width="36" height="36" rx="10" fill="#3a3734"/>
  <path fill="#d97757" d="M18 6c1.2 4.2-1.6 6.2-1.6 9.4 0 2.4 1.6 3.8 3.4 3.8 2.6 0 4.6-2.4 4.6-5.6 3.2 3 5 6.2 5 9.8C29.4 29 24.4 33 18 33S6.6 29 6.6 23.6C6.6 16.8 13.2 13.2 18 6z"/>
  <circle cx="18" cy="24.5" r="3.2" fill="#fff5eb" opacity="0.9"/>
</svg>
"""

_STARBURST_SVG = """
<svg class="nf-starburst" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <g fill="#d97757">
    <circle cx="24" cy="24" r="5"/>
    <rect x="22.5" y="2" width="3" height="12" rx="1.5"/>
    <rect x="22.5" y="34" width="3" height="12" rx="1.5"/>
    <rect x="2" y="22.5" width="12" height="3" rx="1.5"/>
    <rect x="34" y="22.5" width="12" height="3" rx="1.5"/>
    <rect x="22.5" y="2" width="3" height="12" rx="1.5" transform="rotate(45 24 24)"/>
    <rect x="22.5" y="2" width="3" height="12" rx="1.5" transform="rotate(-45 24 24)"/>
    <rect x="22.5" y="34" width="3" height="12" rx="1.5" transform="rotate(45 24 24)"/>
    <rect x="22.5" y="34" width="3" height="12" rx="1.5" transform="rotate(-45 24 24)"/>
  </g>
</svg>
"""


def _html(markup: str) -> None:
    """Render real HTML (st.markdown often shows tags as plain text)."""
    st.html(markup)


def _theme() -> str:
    t = str(st.session_state.get("theme") or "dark").lower()
    return t if t in ("light", "dark") else "dark"


def _set_theme(mode: str) -> None:
    mode = "light" if mode == "light" else "dark"
    st.session_state.theme = mode
    save_prefs(settings.cache_dir, theme=mode)


def _toggle_theme() -> None:
    _set_theme("light" if _theme() == "dark" else "dark")
    st.rerun()


def _inject_css() -> None:
    css = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""
    theme = _theme()
    # Always kill Streamlit's built-in sidebar header / spacer so brand sits at true top
    chrome = """
    [data-testid="stSidebarHeader"],
    [data-testid="stLogoSpacer"],
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarCollapsedControl"],
    section[data-testid="stSidebar"] button[kind="headerNoPadding"],
    section[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"],
    section[data-testid="stSidebar"] [data-testid="stLogo"] {
      display: none !important;
      height: 0 !important;
      min-height: 0 !important;
      margin: 0 !important;
      padding: 0 !important;
      overflow: hidden !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
      padding-top: 0.45rem !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
      padding-top: 0 !important;
      margin-top: 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:first-child {
      margin-top: 0 !important;
      padding-top: 0 !important;
    }
    """
    if st.session_state.get("sidebar_collapsed"):
        force = chrome + """
        html { --nf-rail: 1; }
        section[data-testid="stSidebar"] {
          display: flex !important;
          visibility: visible !important;
          transform: none !important;
          min-width: 72px !important;
          max-width: 72px !important;
          width: 72px !important;
          opacity: 1 !important;
          overflow: hidden !important;
        }
        section[data-testid="stSidebar"] > div {
          padding: 0.5rem 0.3rem 0.75rem !important;
          display: flex !important;
          flex-direction: column !important;
          align-items: center !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
          width: 100% !important;
          align-items: center !important;
        }
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div {
          width: 100% !important;
          display: flex !important;
          justify-content: center !important;
          margin: 0.15rem 0 !important;
          padding: 0 !important;
        }
        section[data-testid="stSidebar"] .stButton {
          display: flex !important;
          justify-content: center !important;
          width: 100% !important;
        }
        section[data-testid="stSidebar"] .stButton > button,
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(2) .stButton > button {
          justify-content: center !important;
          text-align: center !important;
          align-items: center !important;
          width: 42px !important;
          min-width: 42px !important;
          max-width: 42px !important;
          height: 42px !important;
          min-height: 42px !important;
          padding: 0 !important;
          margin: 0 auto !important;
          border-radius: 12px !important;
          font-size: 1.2rem !important;
          line-height: 1 !important;
          background: transparent !important;
          border: 1px solid transparent !important;
        }
        section[data-testid="stSidebar"] .stButton > button:hover {
          background: var(--nf-hover) !important;
        }
        section[data-testid="stSidebar"] .nf-rail-logo {
          display: flex !important;
          justify-content: center !important;
          align-items: center !important;
          width: 100% !important;
          margin: 0.35rem 0 0.45rem !important;
          padding: 0 !important;
        }
        section[data-testid="stSidebar"] .nf-rail-logo .nf-logo {
          width: 30px !important;
          height: 30px !important;
          margin: 0 auto !important;
        }
        section[data-testid="stSidebar"] [data-testid="stHtml"],
        section[data-testid="stSidebar"] .stHtml {
          width: 100% !important;
          display: flex !important;
          justify-content: center !important;
        }
        """
    else:
        force = chrome + """
        section[data-testid="stSidebar"] {
          display: flex !important;
          visibility: visible !important;
          transform: none !important;
          min-width: 280px !important;
          width: 280px !important;
          opacity: 1 !important;
        }
        section[data-testid="stSidebar"][aria-expanded="false"] {
          display: flex !important;
          transform: none !important;
          min-width: 280px !important;
          width: 280px !important;
        }
        """
    # Apply theme even if iframe scripts are blocked (Streamlit often strips them)
    theme_vars = ""
    if theme == "light":
        theme_vars = """
        :root {
          --nf-bg: #faf9f5;
          --nf-sidebar: #f0eee6;
          --nf-hover: #e5e2d9;
          --nf-text: #1f1e1d;
          --nf-muted: #6b6560;
          --nf-border: #ddd8ce;
          --nf-accent: #c45d3e;
          --nf-user: #ebe8e0;
          --nf-input: #ffffff;
          --nf-send-bg: #1f1e1d;
          --nf-send-fg: #faf9f5;
          --nf-newchat-bg: #ffffff;
          --nf-newchat-border: #ddd8ce;
          --nf-newchat-hover: #f5f4ef;
          --nf-code-bg: #ebe8e0;
          --nf-chip-bg: #ebe8e0;
          --nf-sidebar-edge: #e5e2d9;
          --nf-shadow: 0 8px 28px rgba(31, 30, 29, 0.08);
        }
        /* Override Streamlit dark theme widgets that ignore CSS vars */
        .stApp, .stApp > header, .main, [data-testid="stAppViewContainer"],
        [data-testid="stMain"], [data-testid="stMainBlockContainer"] {
          background: #faf9f5 !important;
          background-color: #faf9f5 !important;
        }
        section[data-testid="stSidebar"],
        section[data-testid="stSidebar"] > div {
          background: #f0eee6 !important;
          background-color: #f0eee6 !important;
        }
        div[data-testid="stForm"],
        [data-testid="stChatInput"],
        [data-testid="stChatInput"] > div,
        [data-testid="stFileUploaderDropzone"],
        [data-testid="stFileUploader"] section,
        [data-testid="stFileUploaderDropzoneInstructions"],
        [data-baseweb="base-input"],
        [data-baseweb="textarea"],
        [data-testid="stTextArea"] > div,
        [data-testid="stChatInput"] [data-baseweb="base-input"] {
          background: #ffffff !important;
          background-color: #ffffff !important;
          color: #1f1e1d !important;
          border-color: #ddd8ce !important;
        }
        div[data-testid="stForm"] textarea,
        [data-testid="stTextArea"] textarea,
        [data-testid="stChatInput"] textarea,
        .main textarea {
          color: #1f1e1d !important;
          -webkit-text-fill-color: #1f1e1d !important;
          caret-color: #1f1e1d !important;
          background: transparent !important;
        }
        [data-testid="stFileUploaderDropzone"] *,
        [data-testid="stFileUploaderDropzoneInstructions"] * {
          color: #6b6560 !important;
        }
        [data-testid="stBottomBlockContainer"],
        [data-testid="stBottom"] {
          background: linear-gradient(180deg, transparent, #faf9f5 28%) !important;
        }
        """
    boot = f"""
    <script>
    (function() {{
      var t = {theme!r};
      document.documentElement.setAttribute('data-nf-theme', t);
      try {{
        var doc = window.parent && window.parent.document;
        if (doc && doc.documentElement) doc.documentElement.setAttribute('data-nf-theme', t);
      }} catch (e) {{}}
    }})();
    </script>
    """
    _html(f"<style>{css}{theme_vars}{force}</style>{boot}")



def _new_chat() -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "title": "New chat",
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
        "results": [],
    }


def _persist() -> None:
    """Save chat history so browser refresh keeps conversations."""
    save_chats(
        settings.cache_dir,
        st.session_state.chats,
        st.session_state.get("active_chat_id"),
    )


def _init_state() -> None:
    if "chats" not in st.session_state:
        saved, active = load_chats(settings.cache_dir)
        if saved:
            st.session_state.chats = saved
            st.session_state.active_chat_id = active or next(iter(saved))
        else:
            chat = _new_chat()
            st.session_state.chats = {chat["id"]: chat}
            st.session_state.active_chat_id = chat["id"]
            _persist()
    if "active_chat_id" not in st.session_state:
        st.session_state.active_chat_id = next(iter(st.session_state.chats))
    if "sidebar_collapsed" not in st.session_state:
        st.session_state.sidebar_collapsed = False
    if "theme" not in st.session_state:
        st.session_state.theme = load_prefs(settings.cache_dir).get("theme", "dark")


def _active_chat() -> dict:
    cid = st.session_state.active_chat_id
    chats = st.session_state.chats
    if cid not in chats:
        chat = _new_chat()
        chats[chat["id"]] = chat
        st.session_state.active_chat_id = chat["id"]
        return chat
    return chats[cid]


def _set_title_from_files(chat: dict, names: list[str]) -> None:
    if chat.get("title") not in (None, "", "New chat"):
        return
    if not names:
        return
    label = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"
    chat["title"] = label[:48]


def _set_title_from_text(chat: dict, text: str) -> None:
    if chat.get("title") not in (None, "", "New chat"):
        return
    text = (text or "").strip()
    if text:
        chat["title"] = text[:48]


def _summary_line(result: TenderResult) -> str:
    info = result.tender_information
    parts: list[str] = []
    if info.tender_no:
        parts.append(info.tender_no)
    if info.division_name:
        parts.append(info.division_name)
    parts.append(f"{len(result.products)} products")
    return " · ".join(parts)


def _process_pdfs(files: list) -> list[TenderResult]:
    pipeline = TenderPipeline(settings)
    results: list[TenderResult] = []
    for file_idx, up in enumerate(files):
        safe_name = Path(up.name).name
        dest = settings.upload_dir / safe_name
        if dest.exists():
            dest = settings.upload_dir / f"{file_idx}_{safe_name}"
        dest.write_bytes(up.getvalue())
        results.append(pipeline.process(dest))
    return results


def _process_pdf_paths(paths: list[Path]) -> list[TenderResult]:
    pipeline = TenderPipeline(settings)
    return [pipeline.process(path) for path in paths]


def _snapshot_results(chat: dict) -> list[dict]:
    out: list[dict] = []
    for r in chat.get("results") or []:
        if isinstance(r, TenderResult):
            out.append(r.model_dump(mode="json"))
        elif isinstance(r, dict):
            out.append(r)
    return out


def _restore_results(raw: list | None) -> list[TenderResult]:
    results: list[TenderResult] = []
    for item in raw or []:
        try:
            results.append(TenderResult.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return results


def _revert_assistant(chat: dict, idx: int) -> None:
    """Restore chat to the state before this assistant reply (Cursor-like Revert)."""
    messages = chat.get("messages") or []
    if idx < 0 or idx >= len(messages):
        return
    msg = messages[idx]
    if msg.get("role") != "assistant":
        return
    if "checkpoint_results" in msg:
        chat["results"] = _restore_results(msg.get("checkpoint_results"))
    chat["messages"] = messages[:idx]
    # Drop any in-flight turn tied to this chat
    pending = st.session_state.get("_pending_turn")
    if pending and pending.get("chat_id") == chat.get("id"):
        st.session_state.pop("_pending_turn", None)
    _persist()
    st.rerun()


def _all_products(chat: dict) -> list:
    products: list = []
    for result in chat.get("results") or []:
        if result.status == ExtractionStatus.COMPLETED:
            products.extend(result.products)
    return products


def _product_rows(products: list) -> list[dict]:
    _renumber_sequential(products)
    return [
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


def _friendly_ai_error(summary: str, error: str | None) -> str:
    blob = f"{summary} {error or ''}".lower()
    if "429" in blob or "rate" in blob:
        return (
            "Rate limit exceeded. Wait a minute, or put a fresh "
            "`MISTRAL_API_KEY` in `.env` and refresh."
        )
    if "no_key" in (error or "") or "api_key" in blob or "set mistral" in blob:
        return "AI key missing. Add `MISTRAL_API_KEY` in `.env`, then refresh."
    return summary or "Something went wrong. Try again."


def _history_chats() -> list[dict]:
    chats = sorted(
        st.session_state.chats.values(),
        key=lambda c: c.get("created", ""),
        reverse=True,
    )
    return [c for c in chats if c.get("messages")]


def _md_to_html(text: str) -> str:
    """Minimal safe markdown → HTML for bubbles."""
    import re

    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"^[-•]\s+(.+)$", r"<li>\1</li>", escaped, flags=re.M)
    if "<li>" in escaped:
        escaped = re.sub(r"(?:<li>.*?</li>\s*)+", lambda m: f"<ul>{m.group(0)}</ul>", escaped)
    return escaped.replace("\n", "<br>")


def _thinking_html(label: str) -> str:
    return f"""
    <div class="nf-row nf-row-assistant">
      <div class="nf-avatar">{_LOGO_SVG}</div>
      <div class="nf-bubble nf-bubble-assistant nf-thinking">
        <div class="nf-think-label">{html.escape(label)}</div>
        <div class="nf-dots"><span></span><span></span><span></span></div>
      </div>
    </div>
    """


def _start_new_chat() -> None:
    empty = next(
        (c for c in st.session_state.chats.values() if not c.get("messages")),
        None,
    )
    if empty:
        st.session_state.active_chat_id = empty["id"]
    else:
        chat = _new_chat()
        st.session_state.chats[chat["id"]] = chat
        st.session_state.active_chat_id = chat["id"]
    _persist()
    st.rerun()


def _render_sidebar() -> None:
    collapsed = bool(st.session_state.sidebar_collapsed)

    with st.sidebar:
        if collapsed:
            # Thin icon rail: menu → logo → new (all centered)
            if st.button("☰", key="expand_sidebar", help="Expand sidebar"):
                st.session_state.sidebar_collapsed = False
                st.rerun()
            _html(f'<div class="nf-rail-logo">{_LOGO_SVG}</div>')
            if st.button("＋", key="new_chat_rail", help="New chat"):
                _start_new_chat()
            return

        # Top row flush to top: Notofire + hamburger
        top_l, top_r = st.columns([5, 1], gap="small", vertical_alignment="center")
        with top_l:
            _html(
                f"""
                <div class="nf-side-brand">
                  {_LOGO_SVG}
                  <div class="nf-side-wordmark">{html.escape(APP_NAME)}</div>
                </div>
                """
            )
        with top_r:
            if st.button("☰", key="collapse_sidebar", help="Collapse sidebar"):
                st.session_state.sidebar_collapsed = True
                st.rerun()

        if st.button("＋  New", key="new_chat_btn", width="stretch"):
            _start_new_chat()

        _html('<div class="nf-side-label">Chats</div>')
        history = _history_chats()
        if not history:
            _html('<div class="nf-side-empty">No chats yet</div>')

        for chat in history:
            active = chat["id"] == st.session_state.active_chat_id
            label = (chat.get("title") or "Untitled").strip() or "Untitled"
            if len(label) > 36:
                label = label[:34] + "…"
            if active:
                label = f"●  {label}"
            cols = st.columns([5, 1])
            with cols[0]:
                if st.button(label, key=f"open_{chat['id']}", width="stretch"):
                    st.session_state.active_chat_id = chat["id"]
                    _persist()
                    st.rerun()
            with cols[1]:
                if st.button("🗑", key=f"del_{chat['id']}", help="Delete"):
                    del st.session_state.chats[chat["id"]]
                    if not st.session_state.chats:
                        fresh = _new_chat()
                        st.session_state.chats[fresh["id"]] = fresh
                        st.session_state.active_chat_id = fresh["id"]
                    elif st.session_state.active_chat_id == chat["id"]:
                        st.session_state.active_chat_id = next(iter(st.session_state.chats))
                    _persist()
                    st.rerun()


def _render_theme_top() -> None:
    """Sun/moon toggle pinned to the main area top-right."""
    theme = _theme()
    icon = "☀" if theme == "dark" else "☾"
    help_txt = "Light mode" if theme == "dark" else "Dark mode"
    _, right = st.columns([20, 1], gap="small")
    with right:
        if st.button(icon, key="theme_top_right", help=help_txt):
            _toggle_theme()


def _render_empty_state() -> tuple[str, list] | None:
    """Claude-style home: greeting + centered composer (no starter cards)."""
    _html(
        f"""
        <div class="nf-home">
          <div class="nf-home-greeting">
            {_STARBURST_SVG}
            <h1>How can I help you today?</h1>
          </div>
        </div>
        """
    )
    with st.container():
        with st.form("home_composer", clear_on_submit=True, border=False):
            text = st.text_area(
                "Message",
                placeholder="Message Notofire…",
                height=110,
                label_visibility="collapsed",
                key="home_text",
            )
            up_col, send_col = st.columns([4, 1])
            with up_col:
                files = st.file_uploader(
                    "Attach PDFs",
                    type=["pdf"],
                    accept_multiple_files=True,
                    label_visibility="collapsed",
                    key="home_files",
                )
            with send_col:
                sent = st.form_submit_button("Send", type="primary", width="stretch")
    if sent and ((text or "").strip() or files):
        return (text or "").strip(), list(files or [])
    return None


def _copy_to_clipboard(text: str) -> None:
    """Copy text via browser clipboard (works inside Streamlit iframe)."""
    import json

    payload = json.dumps(text or "")
    st.html(
        f"""
        <script>
        (function() {{
          var t = {payload};
          function go(w) {{
            try {{
              if (w && w.navigator && w.navigator.clipboard) {{
                w.navigator.clipboard.writeText(t);
                return true;
              }}
            }} catch (e) {{}}
            return false;
          }}
          if (!go(window)) go(window.parent);
        }})();
        </script>
        """
    )


def _find_prev_user(messages: list, idx: int) -> int | None:
    i = idx - 1
    while i >= 0:
        if (messages[i] or {}).get("role") == "user":
            return i
        i -= 1
    return None


def _start_edit_user(chat: dict, idx: int) -> None:
    """Cursor-like Edit: drop this user turn + later replies, open edit draft."""
    messages = chat.get("messages") or []
    if idx < 0 or idx >= len(messages):
        return
    msg = messages[idx]
    if msg.get("role") != "user":
        return
    # Restore products to state before the reply that followed this user turn
    if idx + 1 < len(messages) and messages[idx + 1].get("role") == "assistant":
        cp = messages[idx + 1].get("checkpoint_results")
        if cp is not None:
            chat["results"] = _restore_results(cp)
    chat["messages"] = messages[:idx]
    st.session_state["_edit_draft"] = {
        "chat_id": chat["id"],
        "text": msg.get("prompt_text") or msg.get("content") or "",
        "file_names": list(msg.get("files") or []),
        "file_paths": list(msg.get("file_paths") or []),
    }
    pending = st.session_state.get("_pending_turn")
    if pending and pending.get("chat_id") == chat.get("id"):
        st.session_state.pop("_pending_turn", None)
    _persist()
    st.rerun()


def _refresh_assistant(chat: dict, idx: int) -> None:
    """Regenerate this AI reply from the preceding user message."""
    messages = chat.get("messages") or []
    if idx < 0 or idx >= len(messages):
        return
    msg = messages[idx]
    if msg.get("role") != "assistant":
        return
    user_idx = _find_prev_user(messages, idx)
    if user_idx is None:
        return
    user = messages[user_idx]
    if "checkpoint_results" in msg:
        chat["results"] = _restore_results(msg.get("checkpoint_results"))
    chat["messages"] = messages[: user_idx + 1]
    text = user.get("prompt_text")
    if not text:
        content = user.get("content") or ""
        if not (user.get("file_paths") and content.startswith("Uploaded ")):
            text = content
    st.session_state.pop("_edit_draft", None)
    st.session_state["_pending_turn"] = {
        "chat_id": chat["id"],
        "text": text or "",
        "paths": list(user.get("file_paths") or []),
        "checkpoint_results": _snapshot_results(chat),
    }
    _persist()
    st.rerun()


def _render_hover_actions(chat: dict, idx: int, role: str, content: str) -> None:
    """Icon toolbar — CSS hides until the message row is hovered."""
    cid = chat["id"]
    if role == "user":
        sp, a1, a2 = st.columns([14, 1, 1], gap="small")
        with a1:
            _html('<span class="nf-action-hit nf-action-hit-user"></span>')
            if st.button("✏️", key=f"edit_{cid}_{idx}", help="Edit"):
                _start_edit_user(chat, idx)
        with a2:
            if st.button("📋", key=f"copy_u_{cid}_{idx}", help="Copy"):
                _copy_to_clipboard(content)
        return

    a1, a2, a3, sp = st.columns([1, 1, 1, 14], gap="small")
    with a1:
        _html('<span class="nf-action-hit nf-action-hit-ai"></span>')
        if st.button("🔄", key=f"ref_{cid}_{idx}", help="Refresh / regenerate"):
            _refresh_assistant(chat, idx)
    with a2:
        if st.button("📋", key=f"copy_a_{cid}_{idx}", help="Copy"):
            _copy_to_clipboard(content)
    with a3:
        if st.button("↩", key=f"rev_{cid}_{idx}", help="Revert to before this reply"):
            _revert_assistant(chat, idx)


def _render_user_bubble(content: str, files: list[str] | None = None) -> None:
    chips = ""
    if files:
        chips = (
            '<div class="nf-file-row">'
            + "".join(
                f'<span class="nf-file-chip">📄 {html.escape(n)}</span>' for n in files
            )
            + "</div>"
        )
    _html(
        f"""
        <div class="nf-row nf-row-user">
          <div class="nf-bubble nf-bubble-user">
            <div class="nf-bubble-text">{_md_to_html(content)}</div>
            {chips}
          </div>
        </div>
        """
    )


def _render_message(msg: dict, chat: dict, idx: int) -> None:
    role = msg.get("role", "assistant")
    content = msg.get("content") or ""
    files = msg.get("files") or []

    if role == "user":
        _render_user_bubble(content, files)
        _render_hover_actions(chat, idx, "user", content)
        return

    _html(
        f"""
        <div class="nf-row nf-row-assistant">
          <div class="nf-avatar">{_LOGO_SVG}</div>
          <div class="nf-bubble nf-bubble-assistant">
            <div class="nf-bubble-text">{_md_to_html(content)}</div>
          </div>
        </div>
        """
    )
    # Actions right under bubble so hover CSS can target the next row
    _render_hover_actions(chat, idx, "assistant", content)

    if msg.get("show_table"):
        products = _all_products(chat)
        if products:
            rows = _product_rows(products)
            st.dataframe(
                rows,
                width="stretch",
                hide_index=True,
                height=min(360, 42 + 35 * len(rows)),
            )

    if msg.get("excel"):
        exporter = ExportService(settings)
        results = [
            r for r in chat.get("results") or []
            if r.status == ExtractionStatus.COMPLETED
        ]
        if len(results) == 1:
            stem = Path(results[0].meta.filename if results[0].meta else "tender").stem
            st.download_button(
                "Download Excel",
                data=exporter.to_excel_bytes(results[0]),
                file_name=f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{chat['id']}_{idx}",
            )
        elif results:
            st.download_button(
                "Download all Excel",
                data=exporter.to_combined_excel_bytes(results),
                file_name="tenders_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{chat['id']}_{idx}",
            )


def _build_pdf_reply(chat: dict, results: list[TenderResult]) -> dict:
    chat["results"] = results
    ok = [r for r in results if r.status == ExtractionStatus.COMPLETED]
    failed = [r for r in results if r.status != ExtractionStatus.COMPLETED]

    if not ok:
        err_lines = []
        for r in failed:
            name = r.meta.filename if r.meta else "PDF"
            if r.status == ExtractionStatus.OCR_FAILED:
                err_lines.append(
                    f"**{name}** — scanned PDF; OCR failed. Install Tesseract and retry."
                )
            else:
                detail = r.meta.error if r.meta and r.meta.error else "extraction failed"
                err_lines.append(f"**{name}** — {detail}")
        return {
            "role": "assistant",
            "content": "I couldn’t extract products from the upload.\n\n" + "\n".join(err_lines),
        }

    total = sum(len(r.products) for r in ok)
    lines = [
        f"Extracted **{total} products** from **{len(ok)}** file(s).",
        "",
    ]
    for r in ok:
        lines.append(f"- {_summary_line(r)}")
    if failed:
        lines.append("")
        lines.append(f"{len(failed)} file(s) had issues and were skipped.")
    lines.append("")
    lines.append("Tell me what to fix, or download Excel below.")
    return {
        "role": "assistant",
        "content": "\n".join(lines),
        "show_table": True,
        "excel": True,
    }


def _build_text_reply(chat: dict, text: str, status) -> dict:
    if not resolve_ai_provider(settings):
        return {
            "role": "assistant",
            "content": _friendly_ai_error("Set MISTRAL_API_KEY in .env", "no_key"),
        }

    products = _all_products(chat)
    history = [
        ChatMessage(role=m["role"], content=m.get("content") or "")
        for m in chat["messages"]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ][:-1]  # exclude the just-appended user turn (passed as instruction)
    history = history[-16:]

    status.html(_thinking_html("Thinking…"))
    status.html(_thinking_html("Generating response…"))
    result = chat_ai(
        text,
        products=products,
        history=history,
        settings=settings,
        chat_id=str(chat.get("id") or "global"),
    )

    if result.error and not result.updated and not result.removed and not result.reply:
        return {
            "role": "assistant",
            "content": _friendly_ai_error(result.summary, result.error),
        }

    bits = [result.reply or result.summary]
    if result.updated or result.removed:
        bits.append("")
        bits.append(
            f"_Applied: {result.updated} updated, {result.removed} removed · "
            "saved to learning memory._"
        )
    return {
        "role": "assistant",
        "content": "\n".join(bits),
        "show_table": bool(result.updated or result.removed),
        "excel": bool(products),
    }


def _enqueue_turn(
    chat: dict,
    text: str,
    files: list | None = None,
    *,
    paths: list[str] | None = None,
    file_names: list[str] | None = None,
) -> None:
    """Save user message + files, then rerun so UI switches to bottom composer."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = list(paths or [])
    names: list[str] = list(file_names or [])

    for up in files or []:
        name = Path(getattr(up, "name", "upload.pdf")).name
        dest = settings.upload_dir / f"{chat['id']}_{uuid.uuid4().hex[:8]}_{name}"
        dest.write_bytes(up.getvalue())
        saved.append(str(dest))
        names.append(name)

    prompt_text = (text or "").strip()
    has_files = bool(saved)
    if has_files:
        _set_title_from_files(chat, names)
        user_content = prompt_text or f"Uploaded {len(names)} PDF(s)"
    else:
        _set_title_from_text(chat, prompt_text)
        user_content = prompt_text

    chat["messages"].append({
        "role": "user",
        "content": user_content,
        "prompt_text": prompt_text,
        "files": names or None,
        "file_paths": saved or None,
    })
    st.session_state.pop("_edit_draft", None)
    st.session_state["_pending_turn"] = {
        "chat_id": chat["id"],
        "text": prompt_text,
        "paths": saved,
        "checkpoint_results": _snapshot_results(chat),
    }
    _persist()
    st.rerun()


def _finish_pending_turn(chat: dict, pending: dict) -> None:
    """Run extraction / AI after UI already shows chat + bottom composer."""
    st.session_state.pop("_pending_turn", None)

    # Bottom composer stays available (Claude-style) while we stream status
    st.chat_input(
        "Message Notofire…",
        accept_file="multiple",
        file_type=["pdf"],
        key=f"composer_busy_{chat['id']}",
        disabled=True,
    )

    for idx, msg in enumerate(chat.get("messages") or []):
        _render_message(msg, chat, idx)

    status = st.empty()
    text = pending.get("text") or ""
    paths = [Path(p) for p in (pending.get("paths") or []) if p]
    checkpoint = pending.get("checkpoint_results") or []

    if paths:
        status.html(_thinking_html(f"Reading {len(paths)} PDF(s)…"))
        try:
            status.html(_thinking_html("Extracting products…"))
            results = _process_pdf_paths(paths)
            reply = _build_pdf_reply(chat, results)
        except Exception as exc:  # noqa: BLE001
            reply = {"role": "assistant", "content": f"Extraction failed: {exc}"}
    else:
        status.html(_thinking_html("Thinking…"))
        reply = _build_text_reply(chat, text, status)

    status.empty()
    reply["checkpoint_results"] = checkpoint
    chat["messages"].append(reply)
    _persist()
    st.rerun()


def _run_turn(chat: dict, text: str, files: list) -> None:
    """Backward-compatible entry — prefer enqueue so composer jumps to bottom."""
    _enqueue_turn(chat, text, files)


def main() -> None:
    _init_state()
    st.set_page_config(
        page_title="Notofire",
        page_icon="🔥",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css()
    _render_sidebar()
    _render_theme_top()

    chat = _active_chat()

    # Finish in-flight turn with chat layout (composer at bottom)
    pending = st.session_state.get("_pending_turn")
    if pending and pending.get("chat_id") == chat.get("id"):
        _finish_pending_turn(chat, pending)
        return

    messages = chat.get("messages") or []

    # Home: greeting + centered composer (only before first message)
    if not messages:
        draft = st.session_state.get("_edit_draft")
        if draft and draft.get("chat_id") == chat.get("id"):
            # Editing the only message left the chat empty — show edit form
            _html('<div class="nf-edit-banner">Editing message</div>')
            with st.form("edit_composer_home", clear_on_submit=False, border=False):
                text = st.text_area(
                    "Edit message",
                    value=draft.get("text") or "",
                    height=110,
                    label_visibility="collapsed",
                    key="edit_text_home",
                )
                c1, c2 = st.columns([1, 1])
                with c1:
                    cancel = st.form_submit_button("Cancel", width="stretch")
                with c2:
                    save = st.form_submit_button("Save & Send", type="primary", width="stretch")
            if cancel:
                st.session_state.pop("_edit_draft", None)
                st.rerun()
            if save and ((text or "").strip() or draft.get("file_paths")):
                _enqueue_turn(
                    chat,
                    text,
                    paths=list(draft.get("file_paths") or []),
                    file_names=list(draft.get("file_names") or []),
                )
            return

        home = _render_empty_state()
        if home is not None:
            text, files = home
            if (text or "").strip() or files:
                _enqueue_turn(chat, text, list(files or []))
        return

    # Active chat: bottom composer (Claude-style) + history
    draft = st.session_state.get("_edit_draft")
    editing = bool(draft and draft.get("chat_id") == chat.get("id"))

    if editing:
        _html('<div class="nf-edit-banner">Editing message — save to resend</div>')
        with st.form("edit_composer", clear_on_submit=False, border=False):
            text = st.text_area(
                "Edit message",
                value=draft.get("text") or "",
                height=110,
                label_visibility="collapsed",
                key=f"edit_text_{chat['id']}",
            )
            if draft.get("file_names"):
                st.caption("Attached: " + ", ".join(draft["file_names"]))
            c1, c2 = st.columns([1, 1])
            with c1:
                cancel = st.form_submit_button("Cancel", width="stretch")
            with c2:
                save = st.form_submit_button("Save & Send", type="primary", width="stretch")
        if cancel:
            st.session_state.pop("_edit_draft", None)
            st.rerun()
        if save and ((text or "").strip() or draft.get("file_paths")):
            _enqueue_turn(
                chat,
                text,
                paths=list(draft.get("file_paths") or []),
                file_names=list(draft.get("file_names") or []),
            )
            return
    else:
        prompt = st.chat_input(
            "Message Notofire…",
            accept_file="multiple",
            file_type=["pdf"],
            key=f"composer_{chat['id']}",
        )

    for idx, msg in enumerate(messages):
        _render_message(msg, chat, idx)

    if not editing and prompt is not None:
        if isinstance(prompt, str):
            text, files = prompt.strip(), []
        else:
            text = (getattr(prompt, "text", None) or "").strip()
            files = list(getattr(prompt, "files", None) or [])
        if text or files:
            _enqueue_turn(chat, text, files)


if __name__ == "__main__":
    main()
