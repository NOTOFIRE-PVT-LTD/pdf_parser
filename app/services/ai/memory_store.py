"""Chroma vector memory — store chats for RAG / learning."""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_COLLECTION = "notofire_memory"


class MemoryStore:
    """Persistent Chroma collection for chat / correction snippets."""

    def __init__(self, persist_dir: Path) -> None:
        self.persist_dir = persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._col = None

    def _ensure(self) -> Any:
        if self._col is not None:
            return self._col
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._col = client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
            embedding_function=DefaultEmbeddingFunction(),
        )
        return self._col

    def add_turn(
        self,
        *,
        chat_id: str,
        role: str,
        content: str,
        kind: str = "chat",
    ) -> None:
        text = (content or "").strip()
        if len(text) < 2:
            return
        col = self._ensure()
        doc_id = hashlib.sha1(f"{chat_id}:{role}:{text[:200]}".encode()).hexdigest()[:24]
        meta = {"chat_id": chat_id or "global", "role": role, "kind": kind}
        try:
            col.upsert(ids=[doc_id], documents=[text[:1500]], metadatas=[meta])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Memory upsert failed: %s", exc)

    def search(self, query: str, *, k: int = 4, chat_id: str | None = None) -> list[str]:
        q = (query or "").strip()
        if len(q) < 2:
            return []
        col = self._ensure()
        if col.count() == 0:
            return []
        n = min(k, max(1, col.count()))
        try:
            res = col.query(query_texts=[q[:800]], n_results=n)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Memory query failed: %s", exc)
            return []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        out: list[str] = []
        for doc, meta in zip(docs, metas or [{}] * len(docs)):
            if not doc:
                continue
            if chat_id and meta and meta.get("chat_id") not in {chat_id, "global"}:
                # Prefer same chat but still allow global — filter soft
                if meta.get("kind") != "correction":
                    continue
            out.append(doc)
        return out[:k] if out else [d for d in docs if d][:k]


@lru_cache
def get_memory_store() -> MemoryStore:
    settings = get_settings()
    return MemoryStore(settings.cache_dir / "chroma_memory")
