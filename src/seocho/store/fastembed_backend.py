"""Local fastembed (bge) embedding backend (ADR-0103, slice S9).

The FAISS/LanceDB vector stores default `embedding_provider="openai"`, and
`create_embedding_backend` only builds OpenAI-compatible HTTP backends — the one
OpenAI seam in the few-shot / vector path. This adapter exposes a local
fastembed (BAAI/bge-small) backend behind the same `EmbeddingBackend.embed`
interface, so any vector store (and S10's embedding-based few-shot retrieval)
can run without ever calling OpenAI:

    store = FAISSVectorStore(embedding_backend=make_fastembed_backend(), ...)

Lazy + optional: `make_fastembed_backend()` returns None when fastembed (or the
model) is unavailable, so callers can fall back deliberately. See
[[feedback_mara_first_minimize_openai]].
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .llm import EmbeddingBackend

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class FastEmbedBackend(EmbeddingBackend):
    """EmbeddingBackend backed by a local fastembed (ONNX) model — no network."""

    def __init__(self, model: object, model_name: str = _DEFAULT_MODEL):
        self._model = model
        self.model_name = model_name

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,  # accepted for interface parity; ignored
    ) -> List[List[float]]:
        if not texts:
            return []
        return [list(map(float, v)) for v in self._model.embed(list(texts))]


def make_fastembed_backend(model_name: str = _DEFAULT_MODEL) -> Optional[FastEmbedBackend]:
    """Build a local bge embedding backend, or None if fastembed is unavailable.

    Lazy: imports fastembed and loads the model only here. Any failure (missing
    package, no model download) returns None so the caller falls back to its
    configured provider rather than crashing.
    """
    try:
        from fastembed import TextEmbedding
    except Exception:
        return None
    try:
        model = TextEmbedding(model_name=model_name)
    except Exception:
        return None
    return FastEmbedBackend(model, model_name=model_name)
