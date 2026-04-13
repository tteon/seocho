from __future__ import annotations

import logging
import os
import pickle
from typing import Any, Dict, List, Optional

from seocho.store.llm import create_embedding_backend
from seocho.store.vector import FAISSVectorStore, _normalize_vectors

logger = logging.getLogger(__name__)


class VectorStore:
    """Backward-compatible extraction adapter over the canonical SEOCHO vector layer.

    This preserves the historical extraction-side API:

    - ``embed_text(text)``
    - ``add_document(doc_id, text)``
    - ``save_index(path)``
    - ``load_index(path)``
    - ``search(query, k=...)``

    while delegating embedding generation and FAISS index behavior to the
    canonical SDK implementation under ``seocho.store.vector``.
    """

    def __init__(
        self,
        api_key: str,
        dimension: int = 1536,
        *,
        provider: str = "openai",
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        embedding_backend = create_embedding_backend(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._store = FAISSVectorStore(
            embedding_backend=embedding_backend,
            embedding_provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            dimension=dimension,
        )
        self.dimension = dimension
        self.model = model
        self._sync_legacy_views()

    def _sync_legacy_views(self) -> None:
        self.index = self._store._index
        self.doc_map = {
            idx: str(doc["id"])
            for idx, doc in enumerate(self._store._docs)
            if doc.get("id")
        }
        self.documents = [
            {
                "id": str(doc["id"]),
                "text_preview": str(
                    doc.get("metadata", {}).get("text_preview")
                    or doc.get("text", "")
                )[:50],
            }
            for doc in self._store._docs
            if doc.get("id")
        ]

    def embed_text(self, text: str) -> List[float]:
        text_value = str(text or "").replace("\n", " ").strip()
        if not text_value:
            return [0.0] * self.dimension
        vectors = self._store._embedding_backend.embed(
            [text_value],
            model=self._store._model,
        )
        normalized = _normalize_vectors(vectors)
        first = normalized[0]
        return first.tolist() if hasattr(first, "tolist") else list(first)

    def add_document(self, doc_id: str, text: str) -> None:
        if not text or not text.strip():
            logger.warning("Skipping empty text for doc %s", doc_id)
            return
        self._store.add(
            str(doc_id),
            str(text),
            metadata={"text_preview": str(text)[:50]},
        )
        self._sync_legacy_views()

    def save_index(self, output_dir: str) -> None:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        index_path = os.path.join(output_dir, "vectors.index")
        meta_path = os.path.join(output_dir, "vectors_meta.pkl")

        self._store._faiss.write_index(self._store._index, index_path)
        with open(meta_path, "wb") as fh:
            pickle.dump(
                {
                    "docs": self._store._docs,
                    "id_to_idx": self._store._id_to_idx,
                    "dimension": self.dimension,
                    "model": self.model,
                },
                fh,
            )

        logger.info(
            "Saved vector index to %s with %d vectors.",
            index_path,
            self._store._index.ntotal,
        )

    def load_index(self, input_dir: str) -> None:
        index_path = os.path.join(input_dir, "vectors.index")
        meta_path = os.path.join(input_dir, "vectors_meta.pkl")

        if os.path.exists(index_path) and os.path.exists(meta_path):
            self._store._index = self._store._faiss.read_index(index_path)
            with open(meta_path, "rb") as fh:
                data = pickle.load(fh)
            self._store._docs = list(data.get("docs", []))
            self._store._id_to_idx = {
                str(key): int(value)
                for key, value in dict(data.get("id_to_idx", {})).items()
            }
            self.dimension = int(data.get("dimension", self.dimension))
            self.model = str(data.get("model", self.model))
            self._sync_legacy_views()
            logger.info(
                "Loaded vector index from %s with %d vectors.",
                input_dir,
                self._store._index.ntotal,
            )
        else:
            logger.warning("Index not found in %s, starting fresh.", input_dir)

    def search(self, query: str, k: int = 3) -> List[dict]:
        results = self._store.search(str(query), limit=k)
        legacy_results: List[dict] = []
        for result in results:
            text_preview = str(result.metadata.get("text_preview") or result.text)[:50]
            legacy_results.append({"id": result.id, "text": text_preview})
        return legacy_results
