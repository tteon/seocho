"""Equivalence tests for the Arrow-native RRF rewrite.

Pinned: the new rrf_arrow must produce the same id ranking as the original
Python-dict rrf for any input. Run with::

    python3 -m pytest experiments/retrieval_comparison/test_fusion.py -v
"""

from __future__ import annotations

import math

import pytest

from experiments.retrieval_comparison.run import (
    items_to_arrow,
    rrf,
    rrf_arrow,
)


def _to_items(ranked_ids):
    return [{"id": i, "score": 0.0, "text_preview": ""} for i in ranked_ids]


@pytest.mark.parametrize(
    "ranked_lists",
    [
        # Identical lists across backends.
        {"a": ["x", "y", "z"], "b": ["x", "y", "z"]},
        # Partially overlapping lists.
        {"a": ["x", "y", "z"], "b": ["y", "w", "x"]},
        # Disjoint lists.
        {"a": ["a1", "a2"], "b": ["b1", "b2"]},
        # Single-backend (no fusion needed but must still work).
        {"a": ["x", "y"]},
        # Three backends.
        {"a": ["p", "q", "r"], "b": ["q", "r", "s"], "c": ["s", "p"]},
    ],
)
def test_rrf_arrow_matches_python_ranking(ranked_lists):
    """The Arrow and Python paths must agree on the ranking, up to ties.

    Ties on RRF score have no canonical order — both paths may break them
    differently. We assert that, when ids are grouped by their RRF score,
    the *grouping* is identical.
    """
    py_pairs = rrf(ranked_lists, k=60)
    py_buckets: list[set] = []
    last_score: float | None = None
    for ident, score in py_pairs:
        if last_score is None or score != last_score:
            py_buckets.append({ident})
            last_score = score
        else:
            py_buckets[-1].add(ident)

    arrow_tables = {
        backend: items_to_arrow(_to_items(ids), backend=backend)
        for backend, ids in ranked_lists.items()
    }
    arrow_rows = rrf_arrow(arrow_tables, k=60, top=100).to_pylist()
    arrow_buckets: list[set] = []
    last_score = None
    for row in arrow_rows:
        if last_score is None or row["rrf_score"] != last_score:
            arrow_buckets.append({row["id"]})
            last_score = row["rrf_score"]
        else:
            arrow_buckets[-1].add(row["id"])

    assert arrow_buckets == py_buckets


def test_rrf_arrow_scores_match():
    """Per-id RRF scores must agree to within float epsilon."""
    ranked_lists = {"a": ["x", "y", "z"], "b": ["y", "z", "x"]}

    py_scores = dict(rrf(ranked_lists, k=60))
    arrow_tables = {
        backend: items_to_arrow(_to_items(ids), backend=backend)
        for backend, ids in ranked_lists.items()
    }
    arrow_fused = rrf_arrow(arrow_tables, k=60, top=100).to_pylist()
    arrow_scores = {row["id"]: row["rrf_score"] for row in arrow_fused}

    assert set(arrow_scores.keys()) == set(py_scores.keys())
    for ident in py_scores:
        assert math.isclose(arrow_scores[ident], py_scores[ident], rel_tol=1e-9), (
            f"mismatch on {ident}: arrow={arrow_scores[ident]} py={py_scores[ident]}"
        )


def test_rrf_arrow_records_provenance():
    """Each fused row must carry the set of backends that contributed it."""
    arrow_tables = {
        "vec": items_to_arrow(_to_items(["x", "y"]), backend="vec"),
        "graph": items_to_arrow(_to_items(["y", "z"]), backend="graph"),
    }
    fused = rrf_arrow(arrow_tables, k=60, top=10).to_pylist()
    by_id = {row["id"]: row for row in fused}
    assert sorted(by_id["x"]["backends"]) == ["vec"]
    assert sorted(by_id["y"]["backends"]) == ["graph", "vec"]
    assert sorted(by_id["z"]["backends"]) == ["graph"]
    assert by_id["y"]["n_backends"] == 2
    assert by_id["x"]["n_backends"] == 1


def test_rrf_arrow_empty_input():
    """Zero backends must not crash and must return an empty Arrow table."""
    fused = rrf_arrow({}, k=60, top=10)
    assert fused.num_rows == 0
    assert "id" in fused.column_names
    assert "rrf_score" in fused.column_names
