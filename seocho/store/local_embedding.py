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

import logging
import os
from typing import List, Optional, Sequence

# bge-small-en-v1.5: 384-dim, CPU-friendly, cosine space (normalize embeddings).
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
# BGE retrieval convention: prepend this to QUERIES (not passages) for best recall.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
LOCAL_BGE_DEVICE_ENV = "SEOCHO_BGE_DEVICE"
LOCAL_BGE_DEVICE_AUTO = "auto"
_SUPPORTED_DEVICES = {LOCAL_BGE_DEVICE_AUTO, "cpu", "cuda", "mps"}
logger = logging.getLogger(__name__)


def resolve_local_embedding_device(device: Optional[str] = None) -> str:
    """Resolve the local BGE device from an explicit value or environment.

    ``auto`` prefers CUDA, then Apple MPS, then CPU. Explicit devices are passed
    through so a run can fail loudly when the requested accelerator is missing.
    """
    requested = (device or os.getenv(LOCAL_BGE_DEVICE_ENV) or LOCAL_BGE_DEVICE_AUTO).strip().lower()
    if requested not in _SUPPORTED_DEVICES:
        supported = ", ".join(sorted(_SUPPORTED_DEVICES))
        raise ValueError(
            f"Unsupported BGE device {requested!r}; use one of: {supported}"
        )
    if requested != LOCAL_BGE_DEVICE_AUTO:
        return requested

    try:
        import torch
    except ImportError:
        return "cpu"

    cuda = getattr(torch, "cuda", None)
    if cuda is not None and callable(getattr(cuda, "is_available", None)):
        if cuda.is_available():
            return "cuda"

    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None) if backends is not None else None
    if mps is not None and callable(getattr(mps, "is_available", None)):
        if mps.is_available():
            return "mps"

    return "cpu"


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
        self.requested_device = device or os.getenv(LOCAL_BGE_DEVICE_ENV) or LOCAL_BGE_DEVICE_AUTO
        self.device = resolve_local_embedding_device(device)
        logger.info(
            "Loading local BGE embedding model",
            extra={"model": model, "requested_device": self.requested_device, "device": self.device},
        )
        self._model = SentenceTransformer(model, device=self.device)
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
