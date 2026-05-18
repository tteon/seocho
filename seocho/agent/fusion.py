"""Fusion strategies for parallel-backend retrieval results (ADR-0091).

A ``Fusion`` collapses ranked lists from multiple backends (Cypher, vector,
fulltext, GDS analytics) into a single ranked list. Default impl is
Reciprocal Rank Fusion (RRF) — deterministic, no LLM call, backend-weighted.

The protocol is pluggable so LLM-rerank or weighted-score-merge can replace
RRF without touching callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Protocol, Sequence


class Fusion(Protocol):
    """A fusion strategy collapses multiple ranked lists into one."""

    def fuse(
        self,
        ranked_lists: Mapping[str, Sequence[Any]],
        weights: Mapping[str, float],
    ) -> List[Dict[str, Any]]:
        ...


class ReciprocalRankFusion:
    """RRF: ``score(doc) = Σ_b weight_b / (k + rank_b(doc))``.

    Identity is taken from the ``id`` field on each item, falling back to
    ``str(item)`` when absent. The output preserves the first observed item
    payload for each id (later observations are ignored).
    """

    def __init__(self, *, k: int = 60, weight_floor: float = 0.10) -> None:
        if k <= 0:
            raise ValueError(f"RRF k must be positive, got {k}")
        self._k = int(k)
        self._weight_floor = float(weight_floor)

    def fuse(
        self,
        ranked_lists: Mapping[str, Sequence[Any]],
        weights: Mapping[str, float],
    ) -> List[Dict[str, Any]]:
        scores: Dict[str, float] = {}
        payloads: Dict[str, Any] = {}
        order: List[str] = []

        for backend, items in ranked_lists.items():
            w = float(weights.get(backend, 0.0))
            if w < self._weight_floor:
                continue
            for rank, item in enumerate(items, start=1):
                ident = _identity(item)
                if ident not in payloads:
                    payloads[ident] = item
                    order.append(ident)
                scores[ident] = scores.get(ident, 0.0) + (w / (self._k + rank))

        ranked = sorted(order, key=lambda i: scores[i], reverse=True)
        return [
            {"id": ident, "score": scores[ident], "item": payloads[ident]}
            for ident in ranked
        ]


def _identity(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("id", "elementId", "_id"):
            if key in item and item[key] is not None:
                return str(item[key])
    return str(item)
