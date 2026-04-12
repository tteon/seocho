"""
Vector backends for semantic similarity search in the public SEOCHO SDK.

The vector database and the embedding provider are intentionally decoupled:
FAISS/LanceDB handle storage and nearest-neighbor search, while embeddings are
generated through an embedding backend.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .llm import create_embedding_backend

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VectorSearchResult:
    """A single similarity search result."""

    id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    def add_batch(self, items: Sequence[Dict[str, Any]]) -> int:
        """Add multiple documents and return the count."""

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> List[VectorSearchResult]:
        """Find documents similar to query text."""

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """Remove a document from the index."""

    @abstractmethod
    def count(self) -> int:
        """Return the number of active documents in the index."""


def _normalize_vectors(vectors: Any) -> Any:
    import numpy as np

    matrix = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms


class FAISSVectorStore(VectorStore):
    """In-memory vector store using FAISS plus a pluggable embedding backend."""

    def __init__(
        self,
        *,
        embedding_backend: Optional[Any] = None,
        embedding_provider: str = "openai",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
    ) -> None:
        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "FAISSVectorStore requires 'faiss-cpu'. "
                "Install it with: pip install faiss-cpu"
            ) from exc

        self._faiss = faiss
        self._embedding_backend = embedding_backend or create_embedding_backend(
            provider=embedding_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._model = model
        self._dimension = dimension
        self._index = faiss.IndexFlatIP(dimension)
        self._docs: List[Dict[str, Any]] = []
        self._id_to_idx: Dict[str, int] = {}

    def _embed(self, texts: Sequence[str]) -> Any:
        return _normalize_vectors(self._embedding_backend.embed(texts, model=self._model))

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

    def add_batch(self, items: Sequence[Dict[str, Any]]) -> int:
        if not items:
            return 0

        for item in items:
            doc_id = str(item["id"])
            if doc_id in self._id_to_idx:
                self.delete(doc_id)

        texts = [str(item["text"]) for item in items]
        vectors = self._embed(texts)
        start_idx = len(self._docs)
        self._index.add(vectors)

        for offset, item in enumerate(items):
            doc_id = str(item["id"])
            self._docs.append(
                {
                    "id": doc_id,
                    "text": str(item["text"]),
                    "metadata": dict(item.get("metadata", {})),
                }
            )
            self._id_to_idx[doc_id] = start_idx + offset

        return len(items)

    def search(self, query: str, *, limit: int = 5) -> List[VectorSearchResult]:
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
            if doc.get("_deleted"):
                continue
            results.append(
                VectorSearchResult(
                    id=str(doc["id"]),
                    text=str(doc["text"]),
                    score=float(score),
                    metadata=dict(doc.get("metadata", {})),
                )
            )

        return results

    def delete(self, doc_id: str) -> bool:
        if doc_id not in self._id_to_idx:
            return False
        idx = self._id_to_idx.pop(doc_id)
        self._docs[idx] = {
            "id": "",
            "text": "",
            "metadata": {},
            "_deleted": True,
        }
        return True

    def count(self) -> int:
        return len(self._id_to_idx)

    def __repr__(self) -> str:
        return f"FAISSVectorStore(model={self._model!r}, count={self.count()})"


class LanceDBVectorStore(VectorStore):
    """Persistent vector store using LanceDB."""

    def __init__(
        self,
        *,
        uri: str = "./.lancedb",
        table_name: str = "seocho_vectors",
        embedding_backend: Optional[Any] = None,
        embedding_provider: str = "openai",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
        region: Optional[str] = None,
    ) -> None:
        try:
            import lancedb
        except ImportError as exc:
            raise ImportError(
                "LanceDBVectorStore requires 'lancedb'. "
                "Install it with: pip install lancedb"
            ) from exc

        connect_kwargs: Dict[str, Any] = {}
        if api_key:
            connect_kwargs["api_key"] = api_key
        if region:
            connect_kwargs["region"] = region

        self._lancedb = lancedb
        self._db = lancedb.connect(uri, **connect_kwargs)
        self._table_name = table_name
        self._embedding_backend = embedding_backend or create_embedding_backend(
            provider=embedding_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._model = model
        self._table = self._open_existing_table()

    def _open_existing_table(self) -> Any | None:
        try:
            return self._db.open_table(self._table_name)
        except Exception:
            return None

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        return self._embedding_backend.embed(texts, model=self._model)

    def _serialize_rows(self, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        vectors = self._embed([str(item["text"]) for item in items])
        rows: List[Dict[str, Any]] = []
        for item, vector in zip(items, vectors):
            rows.append(
                {
                    "id": str(item["id"]),
                    "text": str(item["text"]),
                    "vector": vector,
                    "metadata": json.dumps(item.get("metadata", {}), ensure_ascii=False),
                }
            )
        return rows

    def _ensure_table(self, rows: Sequence[Dict[str, Any]]) -> Any:
        if self._table is not None:
            return self._table, False
        self._table = self._db.create_table(self._table_name, data=list(rows))
        return self._table, True

    @staticmethod
    def _query_to_rows(query: Any) -> List[Dict[str, Any]]:
        if hasattr(query, "to_list"):
            return list(query.to_list())
        if hasattr(query, "to_arrow"):
            arrow_table = query.to_arrow()
            if hasattr(arrow_table, "to_pylist"):
                return list(arrow_table.to_pylist())
        if hasattr(query, "to_pandas"):
            frame = query.to_pandas()
            return frame.to_dict(orient="records")
        return []

    def add(
        self,
        doc_id: str,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.add_batch(
            [
                {
                    "id": doc_id,
                    "text": text,
                    "metadata": metadata or {},
                }
            ]
        )

    def add_batch(self, items: Sequence[Dict[str, Any]]) -> int:
        if not items:
            return 0

        if self._table is not None:
            for item in items:
                self.delete(str(item["id"]))

        rows = self._serialize_rows(items)
        table, created = self._ensure_table(rows)
        if not created:
            table.add(rows)
        return len(rows)

    def search(self, query: str, *, limit: int = 5) -> List[VectorSearchResult]:
        if self._table is None:
            return []

        query_vector = self._embed([query])[0]
        query_builder = self._table.search(query_vector).limit(limit)
        rows = self._query_to_rows(query_builder)

        results: List[VectorSearchResult] = []
        for row in rows:
            raw_metadata = row.get("metadata", {})
            if isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    metadata = {"raw_metadata": raw_metadata}
            elif isinstance(raw_metadata, dict):
                metadata = raw_metadata
            else:
                metadata = {}
            score = row.get("_distance", row.get("score", 0.0))
            results.append(
                VectorSearchResult(
                    id=str(row.get("id", "")),
                    text=str(row.get("text", "")),
                    score=float(score),
                    metadata=metadata,
                )
            )
        return results

    def delete(self, doc_id: str) -> bool:
        if self._table is None:
            return False
        safe_doc_id = str(doc_id).replace("'", "''")
        try:
            self._table.delete(f"id = '{safe_doc_id}'")
        except Exception:
            return False
        return True

    def count(self) -> int:
        if self._table is None:
            return 0
        if hasattr(self._table, "count_rows"):
            return int(self._table.count_rows())
        if hasattr(self._table, "to_arrow"):
            arrow_table = self._table.to_arrow()
            return int(getattr(arrow_table, "num_rows", 0))
        return 0

    def __repr__(self) -> str:
        return (
            f"LanceDBVectorStore(table_name={self._table_name!r}, "
            f"model={self._model!r}, count={self.count()})"
        )


def create_vector_store(
    *,
    kind: str = "faiss",
    embedding_backend: Optional[Any] = None,
    embedding_provider: str = "openai",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: str = "text-embedding-3-small",
    dimension: int = 1536,
    uri: str = "./.lancedb",
    table_name: str = "seocho_vectors",
    region: Optional[str] = None,
) -> VectorStore:
    """Create a vector store by kind."""

    kind_key = str(kind).strip().lower() or "faiss"
    if kind_key == "faiss":
        return FAISSVectorStore(
            embedding_backend=embedding_backend,
            embedding_provider=embedding_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            dimension=dimension,
        )
    if kind_key == "lancedb":
        return LanceDBVectorStore(
            uri=uri,
            table_name=table_name,
            embedding_backend=embedding_backend,
            embedding_provider=embedding_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            region=region,
        )
    raise ValueError(f"Unsupported vector store kind '{kind}'. Known kinds: faiss, lancedb")
