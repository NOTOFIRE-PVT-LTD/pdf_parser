"""ChatGPT-style activity messages for the parsing progress UI."""

from __future__ import annotations

STAGE_MESSAGES: dict[str, str] = {
    "Opening PDF": "Reading tender PDF and detecting document type…",
    "Running OCR": "Running OCR on scanned pages — extracting text layer…",
    "Extracting tables": "Locating BOQ / schedule tables across pages…",
    "Extracting information": "Parsing tender header, products & exact product names…",
    "Done": "Formatting rows for Excel export…",
}

ORDER = [
    "Opening PDF",
    "Running OCR",
    "Extracting tables",
    "Extracting information",
    "Done",
]


def stage_message(stage: str) -> str:
    for key, msg in STAGE_MESSAGES.items():
        if key.lower() in stage.lower():
            return msg
    return stage


def render_activity_html(steps: list[tuple[str, str]], active: bool = False) -> str:
    """
    steps: list of (status, message) where status is 'done' | 'active' | 'pending'
    """
    rows = []
    for status, message in steps:
        icon = "✓" if status == "done" else ("●" if status == "active" else "○")
        cls = f"tt-step tt-step-{status}"
        rows.append(f'<div class="{cls}"><span class="tt-step-icon">{icon}</span><span>{message}</span></div>')
    pulse = '<div class="tt-typing"><span></span><span></span><span></span></div>' if active else ""
    return f'<div class="tt-activity">{"".join(rows)}{pulse}</div>'
