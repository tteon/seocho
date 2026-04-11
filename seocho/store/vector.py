"""
Vector store — embedding-based similarity search for the SDK.

Provides a pluggable :class:`VectorStore` abstraction with an
in-memory FAISS implementation. This enables semantic search
alongside graph-structured queries.

Usage::

    from seocho.vector_store import FAISSVectorStore

    vs = FAISSVectorStore(llm=llm_backend)  # reuses OpenAIBackend for embeddings
    vs.add("doc-1", "Samsung is a Korean tech company.")
    vs.add("doc-2", "Apple is based in Cupertino.")

    results = vs.search("Korean electronics company", limit=3)
    # [{"id": "doc-1", "score": 0.92, "text": "Samsung is..."}]
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VectorSearchResult:
    """A single similarity search result."""

    id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class VectorStore(ABC):
    """Abstract interface for vector similarity search."""

    @abstractmethod
    def add(
        self,
        doc_id: str,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a document to the vector index."""

    @abstractmethod
    def add_batch(
        self,
        items: Sequence[Dict[str, Any]],
    ) -> int:
        """Add multiple documents. Each item should have ``id``, ``text``,
        and optional ``metadata``. Returns number added."""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> List[VectorSearchResult]:
        """Find documents similar to query text."""

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """Remove a document from the index. Returns True if found."""

    @abstractmethod
    def count(self) -> int:
        """Number of documents in the index."""


# ---------------------------------------------------------------------------
# FAISS implementation (in-memory)
# ---------------------------------------------------------------------------


class FAISSVectorStore(VectorStore):
    """In-memory vector store using FAISS and OpenAI embeddings.

    Requires ``faiss-cpu`` and an LLM backend that supports embeddings
    (or a separate OpenAI client for embeddings).

    Parameters
    ----------
    api_key:
        OpenAI API key for embeddings. Falls back to env var.
    model:
        Embedding model name.
    dimension:
        Embedding vector dimension (1536 for text-embedding-3-small).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
    ) -> None:
        try:
            import faiss
            import numpy as np
        except ImportError as exc:
            raise ImportError(
                "FAISSVectorStore requires 'faiss-cpu' and 'numpy'. "
                "Install with: pip install faiss-cpu numpy"
            ) from exc

        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "FAISSVectorStore requires 'openai' for embeddings. "
                "Install with: pip install openai"
            ) from exc

        kwargs: Dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        self._openai = openai.OpenAI(**kwargs)
        self._model = model
        self._dimension = dimension

        self._index = faiss.IndexFlatIP(dimension)  # inner product (cosine on normalized)
        self._docs: List[Dict[str, Any]] = []  # parallel to index vectors
        self._id_to_idx: Dict[str, int] = {}

    def _embed(self, texts: List[str]) -> Any:
        """Get embeddings from OpenAI."""
        import numpy as np

        response = self._openai.embeddings.create(
            model=self._model,
            input=texts,
        )
        vectors = np.array(
            [item.embedding for item in response.data],
            dtype=np.float32,
        )
        # Normalize for cosine similarity
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        vectors = vectors / norms
        return vectors

    def add(
        self,
        doc_id: str,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if doc_id in self._id_to_idx:
            self.delete(doc_id)

        vectors = self._embed([text])
        idx = len(self._docs)
        self._index.add(vectors)
        self._docs.append({"id": doc_id, "text": text, "metadata": metadata or {}})
        self._id_to_idx[doc_id] = idx

    def add_batch(
        self,
        items: Sequence[Dict[str, Any]],
    ) -> int:
        if not items:
            return 0

        texts = [item["text"] for item in items]
        vectors = self._embed(texts)

        start_idx = len(self._docs)
        self._index.add(vectors)

        for i, item in enumerate(items):
            doc_id = item["id"]
            self._docs.append({
                "id": doc_id,
                "text": item["text"],
                "metadata": item.get("metadata", {}),
            })
            self._id_to_idx[doc_id] = start_idx + i

        return len(items)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> List[VectorSearchResult]:
        if self._index.ntotal == 0:
            return []

        query_vec = self._embed([query])
        k = min(limit, self._index.ntotal)
        scores, indices = self._index.search(query_vec, k)

        results: List[VectorSearchResult] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._docs):
                continue
            doc = self._docs[idx]
            results.append(VectorSearchResult(
                id=doc["id"],
                text=doc["text"],
                score=float(score),
                metadata=doc["metadata"],
            ))

        return results

    def delete(self, doc_id: str) -> bool:
        # FAISS IndexFlatIP doesn't support removal.
        # Mark as deleted and filter in search.
        if doc_id in self._id_to_idx:
            idx = self._id_to_idx[doc_id]
            self._docs[idx] = {"id": "", "text": "", "metadata": {}, "_deleted": True}
            del self._id_to_idx[doc_id]
            return True
        return False

    def count(self) -> int:
        return len(self._id_to_idx)

    def __repr__(self) -> str:
        return f"FAISSVectorStore(model={self._model!r}, count={self.count()})"
