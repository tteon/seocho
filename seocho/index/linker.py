"""
Embedding-based entity relatedness for cross-record linking decisions.

This is the canonical SDK implementation shared by both local mode and
the extraction server.  It replaces the ad-hoc embedding logic that
previously lived only in ``extraction/runtime_ingest.py``.

Usage::

    from seocho.index.linker import EmbeddingLinker

    linker = EmbeddingLinker(embedding_backend)
    relatedness = linker.compute_relatedness(candidate_names, known_entities)
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Cosine similarity — prefer Rust native, fall back to Python
try:
    from seocho_core import cosine_similarity as _cosine_similarity
except ImportError:
    import math

    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = norm_a = norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return max(min(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)), 1.0), -1.0)


class EmbeddingLinker:
    """Decides whether extracted entities are related to known entities
    using lexical overlap + embedding cosine similarity.

    Args:
        embedding_backend: An :class:`~seocho.store.llm.EmbeddingBackend`
            instance.  If ``None``, only lexical matching is used.
        relatedness_threshold: Lexical overlap ratio to consider related.
        embedding_threshold: Cosine similarity to consider related.
        cache_max_size: Max entries in the embedding LRU cache.
    """

    def __init__(
        self,
        embedding_backend: Any = None,
        *,
        relatedness_threshold: float = 0.2,
        embedding_threshold: float = 0.72,
        cache_max_size: int = 4096,
    ) -> None:
        self._backend = embedding_backend
        self._relatedness_threshold = relatedness_threshold
        self._embedding_threshold = embedding_threshold
        self._cache: OrderedDict[str, List[float]] = OrderedDict()
        self._cache_max_size = cache_max_size
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_relatedness(
        self,
        candidate_names: Set[str],
        known_entities: Set[str],
    ) -> Dict[str, Any]:
        """Compute relatedness between candidate and known entity sets.

        Returns a dict with ``is_related``, ``score``, ``lexical_score``,
        ``embedding_score``, ``overlap_count``, and ``reason``.
        """
        if not candidate_names:
            return self._result(False, 0.0, 0.0, None, 0, "no_candidate_entities")
        if not known_entities:
            return self._result(True, 1.0, 1.0, None, 0, "bootstrap_record")

        overlap = candidate_names.intersection(known_entities)
        lexical_score = len(overlap) / max(len(candidate_names), 1)
        embedding_score = self._compute_embedding_score(candidate_names, known_entities)
        score = max(lexical_score, embedding_score or 0.0)

        if len(overlap) > 0:
            return self._result(True, score, lexical_score, embedding_score, len(overlap), "overlap_detected")
        if embedding_score is not None and embedding_score >= self._embedding_threshold:
            return self._result(True, score, lexical_score, embedding_score, 0, "embedding_match")
        if lexical_score >= self._relatedness_threshold:
            return self._result(True, score, lexical_score, embedding_score, 0, "lexical_threshold")
        return self._result(False, score, lexical_score, embedding_score, 0, "below_threshold")

    @staticmethod
    def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize relatedness results across a batch of records."""
        total = len(records)
        linked = sum(1 for r in records if r.get("is_related"))
        avg_score = sum(float(r.get("score", 0.0)) for r in records) / total if total else 0.0
        embed_available = sum(1 for r in records if r.get("embedding_score") is not None)
        return {
            "total_records": total,
            "related_records": linked,
            "unrelated_records": max(total - linked, 0),
            "average_score": round(avg_score, 3),
            "embedding_evaluated_records": embed_available,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_embedding_score(
        self,
        candidate_names: Set[str],
        known_entities: Set[str],
    ) -> Optional[float]:
        if self._backend is None:
            return None
        candidate_text = " | ".join(sorted(candidate_names)[:40]).strip()
        known_text = " | ".join(sorted(known_entities)[:120]).strip()
        if not candidate_text or not known_text:
            return None
        candidate_vec = self._embed(candidate_text)
        known_vec = self._embed(known_text)
        if candidate_vec is None or known_vec is None:
            return None
        return _cosine_similarity(candidate_vec, known_vec)

    def _embed(self, text: str) -> Optional[List[float]]:
        key = text.strip().lower()
        if not key:
            return None
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            vecs = self._backend.embed([text])
            if vecs and vecs[0]:
                self._cache_put(key, vecs[0])
                return vecs[0]
        except Exception as exc:
            logger.warning("Embedding failed: %s", exc)
        return None

    def _cache_get(self, key: str) -> Optional[List[float]]:
        with self._cache_lock:
            vec = self._cache.get(key)
            if vec is not None:
                self._cache.move_to_end(key)
            return vec

    def _cache_put(self, key: str, vec: List[float]) -> None:
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._cache_max_size:
                    self._cache.popitem(last=False)
            self._cache[key] = vec

    @staticmethod
    def _result(
        is_related: bool,
        score: float,
        lexical_score: float,
        embedding_score: Optional[float],
        overlap_count: int,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "is_related": is_related,
            "score": round(score, 3),
            "lexical_score": round(lexical_score, 3),
            "embedding_score": round(embedding_score, 3) if embedding_score is not None else None,
            "overlap_count": overlap_count,
            "reason": reason,
        }
