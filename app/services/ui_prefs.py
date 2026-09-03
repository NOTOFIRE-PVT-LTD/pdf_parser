"""Persist simple UI prefs (theme) across refreshes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FILE = "ui_prefs.json"
_DEFAULTS: dict[str, Any] = {"theme": "dark"}


def load_prefs(cache_dir: Path) -> dict[str, Any]:
    path = Path(cache_dir) / _FILE
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        out = dict(_DEFAULTS)
        theme = str(data.get("theme") or "dark").lower()
        out["theme"] = theme if theme in ("light", "dark") else "dark"
        return out
    except Exception:
        return dict(_DEFAULTS)


def save_prefs(cache_dir: Path, **updates: Any) -> None:
    path = Path(cache_dir) / _FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_prefs(cache_dir)
    data.update(updates)
    theme = str(data.get("theme") or "dark").lower()
    data["theme"] = theme if theme in ("light", "dark") else "dark"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
