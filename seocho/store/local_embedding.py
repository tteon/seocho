"""Local (BGE / sentence-transformers) embedding backend — $0, no API.

Default embedder for benchmark vector lanes and any embedding need, per the
provider/cost policy: prefer local BGE/sentence embeddings over OpenAI; use
OpenAI `text-embedding-3-small` only when truly necessary.

Matches `seocho.store.llm.EmbeddingBackend.embed(texts) -> List[List[float]]`.
`sentence_transformers` is imported lazily so importing this module never fails
when the library is absent — only constructing the backend requires it.

NOTE: switching embedder changes the vector space; never mix BGE-embedded and
OpenAI-embedded vectors in the same comparison. Use one consistently per run.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

# bge-small-en-v1.5: 384-dim, CPU-friendly, cosine space (normalize embeddings).
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
# BGE retrieval convention: prepend this to QUERIES (not passages) for best recall.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class LocalBGEEmbeddingBackend:
    """sentence-transformers embedding backend (default BGE-small), normalized."""

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, *, device: Optional[str] = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "LocalBGEEmbeddingBackend requires 'sentence-transformers'. "
                "Install with: pip install --user sentence-transformers"
            ) from exc
        self._model_name = model
        self._model = SentenceTransformer(model, device=device)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: Sequence[str], *, model: Optional[str] = None) -> List[List[float]]:
        """Return L2-normalized embeddings (cosine space) for the texts."""
        if not texts:
            return []
        vecs = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vecs]

    def embed_queries(self, queries: Sequence[str]) -> List[List[float]]:
        """Embed queries with the BGE retrieval instruction prefix."""
        return self.embed([BGE_QUERY_INSTRUCTION + q for q in queries])
