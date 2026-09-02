"""Tests for cross-PDF product name learning."""

from __future__ import annotations

from pathlib import Path

from app.utils.product_name import ProductNameLearning, extract_product_name, infer_prefix


def test_generic_scope_not_pdf_specific():
    raw = "Supply, Installation, Testing & Commissioning of Fire alarm panel"
    assert extract_product_name(raw) == "Fire alarm panel"


def test_learn_exact_reuse(tmp_path: Path):
    store = ProductNameLearning(tmp_path / "learn.json")
    desc = "Some weird tender line XYZ-9000 pump unit"
    store.learn(desc, "XYZ-9000 pump unit")
    assert store.resolve(desc) == "XYZ-9000 pump unit"


def test_learn_prefix_applies_to_similar(tmp_path: Path):
    store = ProductNameLearning(tmp_path / "learn.json")
    full = "Supply, Installation, Testing & Commissioning of CPVC pipe fittings"
    store.learn(full, "CPVC pipe fittings")
    similar = "Supply, Installation, Testing & Commissioning of HDPE pipe roll"
    assert store.resolve(similar) == "HDPE pipe roll"


def test_infer_prefix():
    full = "Supply of Micro Processor based RTU with extras"
    assert infer_prefix(full, "Micro Processor based RTU with extras") == "Supply of"
