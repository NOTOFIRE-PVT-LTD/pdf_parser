"""Tests for AI correction response parsing."""

from __future__ import annotations

import json

from app.services.ai_correction import _parse_response, _validate_name


def test_parse_json_response():
    raw = '{"changes":[{"index":0,"product_name":"Fault isolator","remove":false}],"summary":"Fixed"}'
    data = _parse_response(raw)
    assert len(data["changes"]) == 1


def test_parse_markdown_wrapped():
    raw = '```json\n{"changes":[],"summary":"ok"}\n```'
    data = _parse_response(raw)
    assert data["summary"] == "ok"


def test_validate_name_substring():
    desc = "Supply of Heat and smoke multi sensor as per RDSO"
    assert _validate_name(desc, "Heat and smoke multi sensor")
