"""Embedding-backed scorer for ontology grounding (ADR-0101 upgrade).

The lexical scorer in ``ontology_grounding`` misses non-lexical synonyms
(`location` ↔ `HEADQUARTERED_IN` share no token). An embedding scorer
closes that: cosine over sentence embeddings ranks HEADQUARTERED_IN above
LED_BY for "location" (measured 0.632 vs 0.574 with BAAI/bge-small).

This is an OPTIONAL, LAZY upgrade — fastembed (ONNX, no torch) is not a
hard dependency, and the model is fetched once on first use. The scorer
is injectable (``embed_fn=``) so the adapter logic (cosine + cache) is
unit-testable without the model, and production can swap any embedder.
``make_fastembed_scorer`` returns None when fastembed/model is
unavailable, so callers fall back to the lexical scorer.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence

# embed_fn: a batch embedder — list[str] -> list[list[float]].
EmbedFn = Callable[[Sequence[str]], List[List[float]]]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingScorer:
    """Cosine-similarity scorer over a (lazy, cached) embedder.

    Caches per-text embeddings so the same ontology type names aren't
    re-embedded across calls. Returns a ``(intent, candidate) -> float``
    callable compatible with ``ontology_grounding.ground(scorer=...)``.
    """

    def __init__(self, embed_fn: EmbedFn) -> None:
        self._embed_fn = embed_fn
        self._cache: Dict[str, List[float]] = {}

    def _vec(self, text: str) -> List[float]:
        if text not in self._cache:
            self._cache[text] = list(self._embed_fn([text])[0])
        return self._cache[text]

    def __call__(self, intent: str, candidate: str) -> float:
        if not intent or not candidate:
            return 0.0
        return round(_cosine(self._vec(intent), self._vec(candidate)), 4)


def make_fastembed_scorer(
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> Optional[Callable[[str, str], float]]:
    """Build an EmbeddingScorer backed by fastembed, or None if unavailable.

    Lazy: imports fastembed and loads the model only when called. Any
    failure (missing package, no model download) returns None so the
    caller keeps the lexical scorer — embedding grounding is never a hard
    requirement.
    """
    try:
        from fastembed import TextEmbedding
    except Exception:
        return None

    try:
        model = TextEmbedding(model_name=model_name)
    except Exception:
        return None

    def _embed(texts: Sequence[str]) -> List[List[float]]:
        return [list(v) for v in model.embed(list(texts))]

    return EmbeddingScorer(_embed)
