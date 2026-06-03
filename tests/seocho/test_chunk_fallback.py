"""Graph-native chunk fallback (answerability fix) — CI-safe unit tests.

The answerability diagnosis showed structured Cypher misses ~70% of FinDER
cases while the facts sit in Chunk.text. This fallback queries the graph's
own Chunk nodes by question keywords when structured retrieval is empty.
Tested with a fake graph_store (no DB), bypassing __init__ via object.__new__.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from seocho.local_engine import _LocalEngine as LocalEngine


def _engine(fake_query):
    eng = object.__new__(LocalEngine)
    eng.workspace_id = "ws-cf"

    class _GS:
        def query(self, cypher, params=None, database="neo4j"):
            return fake_query(cypher, params or {}, database)

    eng.graph_store = _GS()
    return eng


def test_chunk_fallback_enabled_default_off(monkeypatch) -> None:
    monkeypatch.delenv("SEOCHO_CHUNK_FALLBACK", raising=False)
    assert LocalEngine._chunk_fallback_enabled() is False
    monkeypatch.setenv("SEOCHO_CHUNK_FALLBACK", "1")
    assert LocalEngine._chunk_fallback_enabled() is True


def test_fallback_passes_keywords_and_workspace_as_params() -> None:
    seen: Dict[str, Any] = {}

    def fq(cypher, params, database):
        seen["cypher"] = cypher
        seen["params"] = params
        return [{"text": "Apple Inc. is headquartered in Cupertino, California."}]

    eng = _engine(fq)
    ctx = eng._graph_chunk_fallback("Where is Apple headquartered?", "finderbaseline")
    # cypher-safety: static :Chunk label, params carry keywords + workspace_id
    assert "MATCH (c:Chunk)" in seen["cypher"]
    assert "$workspace_id" in seen["cypher"] and "$kws" in seen["cypher"]
    assert seen["params"]["workspace_id"] == "ws-cf"
    assert "apple" in seen["params"]["kws"]            # content keyword kept
    assert "where" not in seen["params"]["kws"]        # stopword dropped
    assert "Cupertino" in ctx and ctx.startswith("[Graph chunk]")


def test_fallback_empty_question_returns_blank() -> None:
    eng = _engine(lambda c, p, d: [{"text": "x"}])
    assert eng._graph_chunk_fallback("", "db") == ""
    # all-stopword question → no keywords → no query
    assert eng._graph_chunk_fallback("what is the", "db") == ""


def test_fallback_no_chunk_hit_returns_blank() -> None:
    eng = _engine(lambda c, p, d: [])
    assert eng._graph_chunk_fallback("Apple revenue 2023", "db") == ""


def test_fallback_swallows_query_error() -> None:
    def boom(c, p, d):
        raise RuntimeError("bolt down")

    eng = _engine(boom)
    assert eng._graph_chunk_fallback("Apple revenue", "db") == ""
