"""
Unit tests for chunked AI extraction (chunking, cross-chunk merge, and the
generic chunk loop in AIExtractionService). No real network calls — the loop
is exercised with a fake provider that returns canned JSON per chunk.

Run: pytest -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.models.schemas import TenderResult
from app.services.ai.base import AIExtractionService
from app.services.ai.merge import chunk_text_by_pages, merge_chunk_into_result


def _paged_text(num_pages: int) -> str:
    pages = [f"[[PAGE:{i}]]\nContent for page {i}." for i in range(1, num_pages + 1)]
    return "\n\n".join(pages)


def test_chunk_text_by_pages_respects_page_cap():
    text = _paged_text(6)
    chunks = chunk_text_by_pages(text, max_pages=2, max_chars=100_000)
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.count("[[PAGE:") == 2


def test_chunk_text_by_pages_respects_char_cap():
    # Each page body is long enough that 2 pages already exceed a small cap.
    pages = [f"[[PAGE:{i}]]\n{'x' * 50}" for i in range(1, 5)]
    text = "\n\n".join(pages)
    chunks = chunk_text_by_pages(text, max_pages=10, max_chars=70)
    # With max_chars=70 and each page ~60+ chars, pages must split roughly 1 per chunk.
    assert len(chunks) >= 3
    joined = "".join(chunks)
    for i in range(1, 5):
        assert f"[[PAGE:{i}]]" in joined


def test_chunk_text_by_pages_no_markers_falls_back_to_char_split():
    text = "a" * 250
    chunks = chunk_text_by_pages(text, max_pages=6, max_chars=100)
    assert chunks == ["a" * 100, "a" * 100, "a" * 50]


def test_chunk_text_by_pages_empty_text():
    assert chunk_text_by_pages("", max_pages=6, max_chars=100) == []


def test_merge_chunk_into_result_fills_null_fields_only_once():
    result = TenderResult()
    result = merge_chunk_into_result(
        result, {"tender_information": {"tender_no": "T-1", "name_of_work": "Work A"}}
    )
    # A later chunk must not clobber a field already found.
    result = merge_chunk_into_result(
        result, {"tender_information": {"tender_no": "SHOULD-NOT-WIN", "zone": "WESTERN"}}
    )
    assert result.tender_information.tender_no == "T-1"
    assert result.tender_information.name_of_work == "Work A"
    assert result.tender_information.zone == "WESTERN"


def test_merge_chunk_into_result_unions_products_across_chunks():
    result = TenderResult()
    result = merge_chunk_into_result(
        result,
        {"products": [{"s_no": "1", "item_qty": "5.00", "unit_rate": "10.00", "description": "Item A"}]},
    )
    result = merge_chunk_into_result(
        result,
        {"products": [{"s_no": "2", "item_qty": "3.00", "unit_rate": "20.00", "description": "Item B"}]},
    )
    assert len(result.products) == 2
    descriptions = {p.description for p in result.products}
    assert descriptions == {"Item A", "Item B"}


def test_merge_chunk_into_result_keeps_longer_clause_content():
    result = TenderResult()
    result = merge_chunk_into_result(
        result,
        {"important_clauses": [{"clause_type": "Payment Terms", "content": "Short version."}]},
    )
    result = merge_chunk_into_result(
        result,
        {
            "important_clauses": [
                {"clause_type": "Payment Terms", "content": "A much longer and more complete version of the clause."}
            ]
        },
    )
    assert len(result.important_clauses) == 1
    assert "much longer" in (result.important_clauses[0].content or "")


class _FakeAIService(AIExtractionService):
    """Test double: returns canned JSON per call instead of hitting a network."""

    def __init__(self, responses: list[dict | None], max_pages: int = 2, max_chars: int = 100_000):
        self.responses = responses
        self.calls: list[str] = []
        self.settings = SimpleNamespace(ai_chunk_max_pages=max_pages, ai_chunk_max_chars=max_chars)

    def is_enabled(self) -> bool:
        return True

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append(user_prompt)
        idx = len(self.calls) - 1
        response = self.responses[idx] if idx < len(self.responses) else {}
        if response is None:
            raise RuntimeError("simulated backend failure")
        return json.dumps(response)


def test_ai_service_enrich_chunks_whole_document_and_survives_a_failed_chunk():
    text = _paged_text(6)  # -> 3 chunks at max_pages=2
    responses = [
        {
            "tender_information": {"name_of_work": "Test Work", "tender_no": "T-100"},
            "products": [{"s_no": "1", "item_qty": "5.00", "unit_rate": "100.00", "description": "Widget A"}],
        },
        {"products": [{"s_no": "2", "item_qty": "3.00", "unit_rate": "50.00", "description": "Widget B"}]},
        None,  # simulated network failure on the last chunk
    ]
    fake = _FakeAIService(responses, max_pages=2, max_chars=100_000)

    progress_events: list[tuple[str, float]] = []
    result = fake.enrich(
        TenderResult(raw_text=text),
        text,
        progress_callback=lambda stage, frac: progress_events.append((stage, frac)),
    )

    assert len(fake.calls) == 3
    assert result.tender_information.tender_no == "T-100"
    assert result.tender_information.name_of_work == "Test Work"
    assert len(result.products) == 2
    assert progress_events[-1] == (progress_events[-1][0], 1.0)


def test_ai_service_enrich_noop_when_disabled():
    class _Disabled(_FakeAIService):
        def is_enabled(self) -> bool:
            return False

    fake = _Disabled([{"products": []}])
    result = TenderResult()
    out = fake.enrich(result, "[[PAGE:1]]\nsome text")
    assert out is result
    assert fake.calls == []
