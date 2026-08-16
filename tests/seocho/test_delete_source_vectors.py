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


def _code_without_docstring(func) -> str:
    """Source with the docstring removed — the docstrings here *discuss* LIKE."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    return ast.unparse(node)


# ---------------------------------------------------------------------------
# Regression: the LanceDB predicate must not match a neighbouring source.
# ---------------------------------------------------------------------------

def test_lancedb_predicate_is_a_range_not_a_like():
    """`id LIKE 'src_chunk_%'` deletes other documents' vectors.

    In SQL LIKE, `_` matches any single character, so that pattern reads as
    `src` + any + `chunk` + any + anything. A source `report_chunked_v2`, whose
    ids look like `report_chunked_v2_chunk_0000`, matches `report_chunk_%`:

        report   -> literal
        _        -> the real '_'
        chunk    -> literal
        _        -> the 'e' of "chunked"      <-- the bug
        %        -> the rest

    reindex() routes through delete_by_source, so re-indexing one document
    silently destroyed another's vectors, and the loss showed up later as a
    retrieval or generation failure rather than as a delete bug.
    """
    from seocho.store.vector import LanceDBVectorStore

    source = _code_without_docstring(LanceDBVectorStore.delete_by_source)
    assert "LIKE" not in source.upper(), (
        "LIKE treats '_' as a wildcard; use a half-open range on the id prefix"
    )

    # And the range itself must be exact.
    lo, hi = "report_chunk_", "report_chunk`"
    assert lo <= "report_chunk_0000" < hi
    assert not (lo <= "report_chunked_v2_chunk_0000" < hi), (
        "a neighbouring source is still inside the range"
    )
    assert not (lo <= "reportX_chunk_0000" < hi)


def test_backends_agree_on_delete_semantics():
    """FAISS matched by prefix while LanceDB matched by LIKE — same call, two
    meanings. A caller cannot reason about delete_source if the answer depends
    on which vector backend is configured."""
    from seocho.store.vector import FAISSVectorStore, LanceDBVectorStore

    faiss_src = _code_without_docstring(FAISSVectorStore.delete_by_source)
    lance_src = _code_without_docstring(LanceDBVectorStore.delete_by_source)
    assert "LIKE" not in (faiss_src + lance_src).upper()
    assert "_chunk_" in faiss_src and "_chunk_" in lance_src, (
        "both backends must key on the same chunk-id scheme"
    )
