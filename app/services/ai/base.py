"""
AI extraction interface.

Plug in Claude, GPT, Gemini, or Ollama by subclassing AIExtractionService and
registering it in the factory — callers and the rest of the pipeline stay
unchanged. Every backend implements a single `_chat` hook (send one prompt,
get back text); this base class owns the shared logic of splitting a whole
document into page-group chunks, sending each chunk through `_chat`, and
merging every chunk's findings into one TenderResult. That way a 300-page
tender gets genuinely read end to end by whichever model is configured,
instead of only its first ~28k characters, and all four providers behave
identically instead of each reimplementing the same loop.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable

from app.models.schemas import TenderResult
from app.services.ai.merge import (
    CHUNK_SYSTEM_PROMPT,
    build_chunk_prompt,
    chunk_text_by_pages,
    merge_chunk_into_result,
    parse_json_content,
    sanitize_products,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float], None]


class AIExtractionService(ABC):
    """Contract for optional AI-assisted enrichment / extraction."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True when the backend is configured and reachable."""

    @abstractmethod
    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send one request to the backend and return its raw text response.

        Implementations should let exceptions propagate (network errors,
        HTTP errors, timeouts) — `enrich` catches per-chunk so one bad
        request doesn't abort extraction for the rest of the document.
        """

    def enrich(
        self,
        result: TenderResult,
        text: str,
        progress_callback: ProgressCallback | None = None,
    ) -> TenderResult:
        """
        Read the WHOLE document, not just a truncated prefix.

        Splits `text` into page-group chunks, sends each one through the
        model with an extraction (not "correct this") prompt, and merges
        every chunk's findings into `result`. Products/documents/clauses are
        unioned across every chunk; scalar header fields (tender_information,
        financial, dates, eligibility, contact_details) are filled the first
        time a chunk reports them and left alone after that. The starting
        `result` — normally the rule-based extraction — is never discarded:
        a chunk that fails or a model that returns nothing still leaves the
        offline data intact.
        """
        if not self.is_enabled():
            return result

        settings = getattr(self, "settings", None)
        max_pages = getattr(settings, "ai_chunk_max_pages", 6)
        max_chars = getattr(settings, "ai_chunk_max_chars", 12000)
        chunks = chunk_text_by_pages(text, max_pages=max_pages, max_chars=max_chars)
        if not chunks:
            return result

        merged = result
        successes = 0
        failures = 0
        for idx, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback(
                    f"AI extraction — section {idx}/{len(chunks)}", idx / len(chunks)
                )
            try:
                content = self._chat(CHUNK_SYSTEM_PROMPT, build_chunk_prompt(chunk, idx, len(chunks)))
                data = parse_json_content(content)
                if not data:
                    raise ValueError("model returned no parseable JSON")
                merged = merge_chunk_into_result(merged, data)
                successes += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning("AI chunk %d/%d failed: %s", idx, len(chunks), exc)

        merged.products = sanitize_products(merged.products)
        if merged.meta is not None:
            if successes:
                merged.meta.warnings.append(
                    f"AI extraction applied ({successes}/{len(chunks)} sections)."
                )
            if failures:
                merged.meta.warnings.append(
                    f"AI extraction skipped {failures}/{len(chunks)} section(s) after errors "
                    "(offline results for those sections are unaffected)."
                )
        return merged

    def extract_from_text(self, text: str) -> TenderResult | None:
        """Full AI extraction with no rule-based starting point."""
        if not self.is_enabled():
            return None
        return self.enrich(TenderResult(raw_text=text), text)


class NoOpAIExtractionService(AIExtractionService):
    """Default offline stub — never calls external APIs."""

    def is_enabled(self) -> bool:
        return False

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError("NoOpAIExtractionService never calls a backend")
