"""ADR-0144 / seocho-d6x.5: db.query + db.execute_write spans.

Covers the Cypher execution-boundary instrumentation: the server-vs-hydration
timing split (the ADR-0111 rust-ext slice), the active PackStream codec, row
counts, write counters, workspace_id, and content gating — all without a live
DozerDB (a fake driver stands in for the neo4j session).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from seocho.store.graph import Neo4jGraphStore, packstream_codec
from seocho.tracing import (
    TracingBackend,
    disable_tracing,
    enable_tracing,
)


class _Recorder(TracingBackend):
    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append({"name": name, "metadata": dict(metadata or {})})


# --- fake neo4j driver -----------------------------------------------------

class _FakeRecord:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def data(self) -> Dict[str, Any]:
        return self._data


class _FakeSummary:
    result_available_after = 5
    result_consumed_after = 7


class _FakeCounters:
    nodes_created = 3
    nodes_deleted = 1
    relationships_created = 2
    relationships_deleted = 0
    properties_set = 9


class _FakeResult:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = [_FakeRecord(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)

    def consume(self) -> Any:
        s = _FakeSummary()
        s.counters = _FakeCounters()  # type: ignore[attr-defined]
        return s


class _FakeSession:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, cypher: str, parameters: Optional[Dict[str, Any]] = None) -> _FakeResult:
        return _FakeResult(self._rows)


class _FakeDriver:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def session(self, database: Optional[str] = None) -> _FakeSession:
        return _FakeSession(self._rows)


def _store(rows: List[Dict[str, Any]]) -> Neo4jGraphStore:
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)
    store._driver = _FakeDriver(rows)  # type: ignore[attr-defined]
    return store


# --- tests -----------------------------------------------------------------

def test_packstream_codec_is_known() -> None:
    assert packstream_codec() in {"rust-ext", "pure-python", "unknown"}


def test_query_emits_no_span_when_disabled() -> None:
    disable_tracing()
    rec = _Recorder()
    store = _store([{"x": 1}])
    # backend not enabled -> hot path untouched, rows still returned
    assert store.query("RETURN 1", database="neo4j") == [{"x": 1}]
    assert rec.spans == []


def test_query_span_carries_db_attributes_and_timing_split() -> None:
    rec = _Recorder()
    store = _store([{"x": 1}, {"x": 2}])
    try:
        enable_tracing(backend=rec)
        rows = store.query("MATCH (n) RETURN n", database="neo4j", workspace_id="ws9")
    finally:
        disable_tracing()

    assert rows == [{"x": 1}, {"x": 2}]
    md = next(s["metadata"] for s in rec.spans if s["name"] == "db.query")
    assert md["db.system"] == "neo4j"
    assert md["db.name"] == "neo4j"
    assert md["db.rows_returned"] == 2
    assert md["workspace_id"] == "ws9"
    assert md["db.client.codec"] in {"rust-ext", "pure-python", "unknown"}
    # server time = result_available_after + result_consumed_after = 12.0
    assert md["db.duration_server_ms"] == 12.0
    assert "db.duration_hydrate_ms" in md


def test_query_statement_is_content_gated(monkeypatch) -> None:
    rec = _Recorder()
    store = _store([{"x": 1}])
    monkeypatch.delenv("SEOCHO_TRACE_CAPTURE_CONTENT", raising=False)
    try:
        enable_tracing(backend=rec)
        store.query("MATCH (secret) RETURN secret", database="neo4j")
    finally:
        disable_tracing()
    md = next(s["metadata"] for s in rec.spans if s["name"] == "db.query")
    assert "db.statement" not in md

    rec2 = _Recorder()
    monkeypatch.setenv("SEOCHO_TRACE_CAPTURE_CONTENT", "1")
    try:
        enable_tracing(backend=rec2)
        store.query("MATCH (secret) RETURN secret", database="neo4j")
    finally:
        disable_tracing()
    md2 = next(s["metadata"] for s in rec2.spans if s["name"] == "db.query")
    assert "MATCH (secret)" in md2["db.statement"]


def test_execute_write_span_carries_counters() -> None:
    rec = _Recorder()
    store = _store([])
    try:
        enable_tracing(backend=rec)
        out = store.execute_write("CREATE (n)", database="neo4j", workspace_id="ws1")
    finally:
        disable_tracing()

    assert out == {
        "nodes_affected": 4,  # created 3 + deleted 1
        "relationships_affected": 2,
        "properties_set": 9,
    }
    md = next(s["metadata"] for s in rec.spans if s["name"] == "db.execute_write")
    assert md["db.nodes_created"] == 3
    assert md["db.relationships_created"] == 2
    assert md["db.properties_set"] == 9
    assert md["workspace_id"] == "ws1"
    assert md["db.client.codec"] in {"rust-ext", "pure-python", "unknown"}
