"""Embedding-backed grounding scorer (ADR-0101 upgrade) — CI-safe.

Tests the adapter logic (cosine + cache + injectable embedder) with a
fake deterministic embedder, so CI needs no fastembed/model. The real
fastembed path is exercised in the grounding A/B, not here.
"""

from __future__ import annotations

from typing import List, Sequence

from seocho.query.embedding_grounding import EmbeddingScorer, _cosine, make_fastembed_scorer
from seocho.query.ontology_grounding import ground


def _fake_embed(table):
    def embed(texts: Sequence[str]) -> List[List[float]]:
        return [table[t] for t in texts]
    return embed


def test_cosine_basic() -> None:
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([1, 0], [0, 0]) == 0.0


def test_embedding_scorer_ranks_by_cosine() -> None:
    # 'location' closer to HEADQUARTERED_IN than LED_BY (the lexical miss)
    table = {
        "location": [1.0, 0.0],
        "HEADQUARTERED_IN": [0.9, 0.1],
        "LED_BY": [0.2, 1.0],
    }
    scorer = EmbeddingScorer(_fake_embed(table))
    s_hq = scorer("location", "HEADQUARTERED_IN")
    s_led = scorer("location", "LED_BY")
    assert s_hq > s_led


def test_embedding_scorer_caches() -> None:
    calls = {"n": 0}
    table = {"a": [1.0, 0.0], "b": [0.0, 1.0]}

    def counting_embed(texts):
        calls["n"] += 1
        return [table[t] for t in texts]

    scorer = EmbeddingScorer(counting_embed)
    scorer("a", "b")
    scorer("a", "b")  # second call hits cache for both vectors
    # 2 distinct texts embedded once each = 2 embed calls, not 4
    assert calls["n"] == 2


def test_embedding_scorer_plugs_into_ground() -> None:
    table = {
        "location": [1.0, 0.0, 0.0],
        "HEADQUARTERED_IN": [0.95, 0.1, 0.0],
        "LED_BY": [0.1, 1.0, 0.0],
        "HAS_SUBSIDIARY": [0.0, 0.0, 1.0],
    }
    scorer = EmbeddingScorer(_fake_embed(table))
    ranked = ground(
        "location",
        ["HEADQUARTERED_IN", "LED_BY", "HAS_SUBSIDIARY"],
        top_k=3, threshold=0.5, scorer=scorer,
    )
    assert ranked[0][0] == "HEADQUARTERED_IN"


def test_make_fastembed_scorer_returns_none_or_callable() -> None:
    # Never raises: returns a callable when fastembed+model are present,
    # else None (caller falls back to lexical). Both are acceptable in CI.
    s = make_fastembed_scorer()
    assert s is None or callable(s)
