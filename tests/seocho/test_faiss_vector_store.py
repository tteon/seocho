"""FAISSVectorStore behavior tests (require faiss-cpu).

#121 — embedding dimension is adopted from the first vector and a later
mismatch raises a clear Python error instead of an opaque native assertion.
"""

from __future__ import annotations

import pytest

pytest.importorskip("faiss")
pytest.importorskip("numpy")

from seocho.store.vector import FAISSVectorStore


class _FixedWidthBackend:
    """Embeds each text to a deterministic nonzero vector of a given width."""

    def __init__(self, width: int) -> None:
        self.width = width

    def embed(self, texts, *, model=None):
        out = []
        for text in texts:
            seed = float(abs(hash(str(text))) % 97) / 97.0
            vec = [seed] + [0.0] * (self.width - 1)
            vec[-1] = 1.0  # keep a nonzero norm
            out.append(vec)
        return out


def _store(width: int = 8, dimension: int = 1536) -> FAISSVectorStore:
    return FAISSVectorStore(embedding_backend=_FixedWidthBackend(width), dimension=dimension)


def test_first_embedding_dimension_is_adopted() -> None:
    # Default dimension is 1536 but the model emits width-8 vectors; the store
    # must adopt 8 on first add instead of raising a native faiss assertion.
    store = _store(width=8, dimension=1536)
    store.add("a", "alpha")
    assert store._dimension == 8
    assert store.count() == 1


def test_add_batch_adopts_first_dimension() -> None:
    store = _store(width=16, dimension=1536)
    n = store.add_batch([{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}])
    assert n == 2
    assert store._dimension == 16
    assert store.count() == 2


def test_dimension_mismatch_raises_clear_error() -> None:
    store = _store(width=8, dimension=1536)
    store.add("a", "alpha")  # locks the index to width 8
    store._embedding_backend = _FixedWidthBackend(16)  # different width
    with pytest.raises(ValueError, match="dimension mismatch"):
        store.add("b", "beta")
