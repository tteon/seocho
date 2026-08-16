"""FAISSVectorStore delete behavior (requires faiss-cpu).

#122 — a deleted (tombstoned) vector must not consume a top-k slot: search
over-fetches by the tombstone count so it still returns up to `limit` live
results, and the deleted doc never appears.
"""

from __future__ import annotations

import pytest

pytest.importorskip("faiss")
pytest.importorskip("numpy")

from seocho.store.vector import FAISSVectorStore


class _AngleBackend:
    """Maps known texts to fixed 2-D vectors for deterministic ranking."""

    _VECS = {
        "q": [1.0, 0.0],
        "near": [0.99, 0.14],
        "mid": [0.70, 0.71],
        "far": [0.0, 1.0],
    }

    def embed(self, texts, *, model=None):
        return [self._VECS.get(str(t), [0.5, 0.5]) for t in texts]


def _store() -> FAISSVectorStore:
    return FAISSVectorStore(embedding_backend=_AngleBackend(), dimension=2)


def test_deleted_doc_not_in_results() -> None:
    store = _store()
    store.add("near", "near")
    store.add("far", "far")
    assert store.delete("near") is True
    ids = [r.id for r in store.search("q", limit=5)]
    assert "near" not in ids
    assert ids == ["far"]


def test_delete_still_returns_full_limit_of_live_results() -> None:
    # The deleted doc ranks at the very top; a limit=2 search with 3 live docs
    # remaining must return 2 live results, not 1 (the tombstone must not eat a
    # top-k slot). On main, search fetched only `limit` and returned 1.
    store = _store()
    store.add("dead", "near")  # top-ranked, then deleted
    store.add("near", "near")
    store.add("mid", "mid")
    store.add("far", "far")

    assert store.delete("dead") is True
    results = store.search("q", limit=2)
    ids = [r.id for r in results]

    assert "dead" not in ids
    assert len(ids) == 2          # full limit of live results
    assert ids[0] in {"near", "dead"} and ids[0] == "near"


def test_count_reflects_live_docs_after_delete() -> None:
    store = _store()
    store.add("a", "near")
    store.add("b", "far")
    store.delete("a")
    assert store.count() == 1
