"""Regression for #123 — delete_source/reindex must also drop the source's
vectors. Previously they removed graph nodes only, leaving orphan vectors that
kept surfacing as top-k hits pointing at deleted/stale graph nodes.
"""

from __future__ import annotations

import pytest

from seocho.index.chunk import build_chunk_id
from seocho.index.pipeline import IndexingPipeline
from seocho.ontology import NodeDef, Ontology, P


class _FakeGraphStore:
    def delete_by_source(self, source_id, *, database="neo4j"):
        return {"nodes_deleted": 1, "relationships_deleted": 0}


class _SpyVectorStore:
    def __init__(self):
        self.deleted = []

    def delete_by_source(self, source_id):
        self.deleted.append(source_id)
        return 3


def _pipeline(vector_store):
    onto = Ontology(name="t", nodes={"Doc": NodeDef(properties={"name": P(str)})})
    return IndexingPipeline(
        ontology=onto, graph_store=_FakeGraphStore(), llm=object(),
        vector_store=vector_store,
    )


def test_delete_source_also_deletes_vectors():
    spy = _SpyVectorStore()
    summary = _pipeline(spy).delete_source("src-1")
    assert spy.deleted == ["src-1"]
    assert summary["vectors_deleted"] == 3
    assert summary["nodes_deleted"] == 1


def test_delete_source_without_vector_store_is_unaffected():
    summary = _pipeline(None).delete_source("src-1")
    assert "vectors_deleted" not in summary
    assert summary["nodes_deleted"] == 1


# --- FAISS backend end-to-end (requires faiss-cpu) ---

faiss = pytest.importorskip("faiss")
pytest.importorskip("numpy")

from seocho.store.vector import FAISSVectorStore


class _Embed:
    def embed(self, texts, *, model=None):
        return [[float(abs(hash(str(t))) % 97) / 97.0, 1.0] for t in texts]


def test_faiss_delete_by_source_removes_only_that_source():
    store = FAISSVectorStore(embedding_backend=_Embed(), dimension=2)
    for src in ("srcA", "srcB"):
        for ordinal in range(3):
            cid = build_chunk_id(src, ordinal)
            store.add(cid, f"text {cid}", metadata={"source_id": src})
    assert store.count() == 6

    removed = store.delete_by_source("srcA")
    assert removed == 3
    assert store.count() == 3
    # only srcB vectors remain
    remaining = {r.metadata["source_id"] for r in store.search("text", limit=10)}
    assert remaining == {"srcB"}
