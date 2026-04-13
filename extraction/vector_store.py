"""
Compatibility adapter — delegates to ``seocho.store.vector`` (canonical).

Maintains the extraction-layer API (``embed_text``, ``add_document``,
``save_index``, ``load_index``, ``search``) while the real implementations
live in the SDK package.  This eliminates ~150 LOC of duplicated vector
store code.
"""

import logging
import os
import pickle
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base — kept for type-hint compatibility with deduplicator.py
# ---------------------------------------------------------------------------


class VectorStoreBase(ABC):
    """Common interface for extraction-layer vector store usage."""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """Generate embedding for text."""

    @abstractmethod
    def add_document(self, doc_id: str, text: str) -> None:
        """Embed and store a document."""

    @abstractmethod
    def search(self, query: str, k: int = 3) -> List[dict]:
        """Search for similar documents."""

    @abstractmethod
    def save_index(self, output_dir: str) -> None:
        """Persist the index to disk."""

    @abstractmethod
    def load_index(self, input_dir: str) -> None:
        """Load the index from disk."""


# ---------------------------------------------------------------------------
# Adapter wrapping seocho.store.vector.FAISSVectorStore
# ---------------------------------------------------------------------------


class FaissVectorStore(VectorStoreBase):
    """FAISS backend — delegates to ``seocho.store.vector.FAISSVectorStore``."""

    def __init__(self, api_key: str, dimension: int = 1536):
        from seocho.store.vector import FAISSVectorStore as _SDK

        self._store = _SDK(
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            dimension=dimension,
        )
        self._dimension = dimension
        # Keep local metadata for save/load compat
        self.doc_map: Dict[int, str] = {}
        self.documents: List[Dict[str, Any]] = []

    def embed_text(self, text: str) -> List[float]:
        text = text.replace("\n", " ")
        vecs = self._store._embed([text])
        return list(vecs[0])

    def add_document(self, doc_id: str, text: str) -> None:
        if not text or not text.strip():
            logger.warning("Skipping empty text for doc %s", doc_id)
            return
        self._store.add(doc_id, text)
        internal_id = len(self.documents)
        self.doc_map[internal_id] = doc_id
        self.documents.append({"id": doc_id, "text_preview": text[:50]})

    def search(self, query: str, k: int = 3) -> List[dict]:
        results = self._store.search(query, limit=k)
        return [{"id": r.doc_id, "text": r.text[:50] if r.text else ""} for r in results]

    def save_index(self, output_dir: str) -> None:
        import faiss

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        index_path = os.path.join(output_dir, "vectors.index")
        faiss.write_index(self._store._index, index_path)

        meta_path = os.path.join(output_dir, "vectors_meta.pkl")
        with open(meta_path, "wb") as f:
            pickle.dump({"doc_map": self.doc_map, "documents": self.documents}, f)
        logger.info("Saved FAISS index to %s.", index_path)

    def load_index(self, input_dir: str) -> None:
        import faiss

        index_path = os.path.join(input_dir, "vectors.index")
        meta_path = os.path.join(input_dir, "vectors_meta.pkl")
        if os.path.exists(index_path) and os.path.exists(meta_path):
            self._store._index = faiss.read_index(index_path)
            with open(meta_path, "rb") as f:
                data = pickle.load(f)
                self.doc_map = data["doc_map"]
                self.documents = data["documents"]
            logger.info("Loaded FAISS index from %s.", input_dir)
        else:
            logger.warning("FAISS index not found in %s, starting fresh.", input_dir)


# ---------------------------------------------------------------------------
# Adapter wrapping seocho.store.vector.LanceDBVectorStore
# ---------------------------------------------------------------------------


class LanceDBVectorStore(VectorStoreBase):
    """LanceDB backend — delegates to ``seocho.store.vector.LanceDBVectorStore``."""

    def __init__(self, api_key: str, dimension: int = 1536, db_path: str = ""):
        from seocho.store.vector import LanceDBVectorStore as _SDK

        self._store = _SDK(
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            uri=db_path or os.getenv("SEOCHO_LANCEDB_PATH", ".lancedb"),
        )
        self._api_key = api_key
        self._dimension = dimension

    def embed_text(self, text: str) -> List[float]:
        text = text.replace("\n", " ")
        vecs = self._store._embed([text])
        return list(vecs[0])

    def add_document(self, doc_id: str, text: str) -> None:
        if not text or not text.strip():
            logger.warning("Skipping empty text for doc %s", doc_id)
            return
        self._store.add(doc_id, text)

    def search(self, query: str, k: int = 3) -> List[dict]:
        results = self._store.search(query, limit=k)
        return [{"id": r.doc_id, "text": r.text[:50] if r.text else ""} for r in results]

    def save_index(self, output_dir: str) -> None:
        logger.info("LanceDB auto-persists; save_index is a no-op.")

    def load_index(self, input_dir: str) -> None:
        logger.info("LanceDB loads on connect; load_index is a no-op.")


# ---------------------------------------------------------------------------
# Factory — backward-compatible ``VectorStore`` name
# ---------------------------------------------------------------------------

_BACKEND = os.getenv("SEOCHO_VECTOR_BACKEND", "faiss").strip().lower()


def VectorStore(api_key: str, dimension: int = 1536, **kwargs: Any) -> VectorStoreBase:
    """Create a vector store based on ``SEOCHO_VECTOR_BACKEND``.

    Supported values: ``faiss`` (default), ``lancedb``.
    """
    if _BACKEND == "lancedb":
        return LanceDBVectorStore(api_key=api_key, dimension=dimension, **kwargs)
    return FaissVectorStore(api_key=api_key, dimension=dimension)
