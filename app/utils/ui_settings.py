"""
Persist non-sensitive Streamlit sidebar preferences so they survive reruns.

The PDF password is intentionally NEVER written to disk here — it lives only
in st.session_state for the current browser session. AI provider config
(API keys) isn't handled by this module at all: it comes entirely from
.env, read once at startup via app.config.get_settings() — there's no
in-app entry or persistence of it to worry about.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings

SETTINGS_PATH = get_settings().cache_dir / "ui_settings.json"

# Values safe to persist to disk.
DEFAULTS: dict[str, Any] = {
    "dark_mode": True,
    "enrich_ai": False,
    "ai_provider": "gemini",
}

# Secrets that must only ever live in-memory (st.session_state), never on disk.
SECRET_KEYS = ("pdf_password",)


def load_ui_settings() -> dict[str, Any]:
    data = dict(DEFAULTS)
    for k in SECRET_KEYS:
        data[k] = ""
    try:
        if SETTINGS_PATH.exists():
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw:
                        data[k] = raw[k]
    except Exception:  # noqa: BLE001
        pass
    return data


def save_ui_settings(values: dict[str, Any]) -> None:
    """Persist only non-secret preferences. Secrets are dropped, never written."""
    payload = {k: values.get(k, DEFAULTS[k]) for k in DEFAULTS}
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
