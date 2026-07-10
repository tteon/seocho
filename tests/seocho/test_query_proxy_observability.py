from __future__ import annotations

from typing import Any, Dict, List, Optional

from seocho.query.query_proxy import QueryProxy, QueryRequest
from seocho.tracing import TracingBackend, disable_tracing, enable_tracing


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
        self.spans.append(
            {
                "name": name,
                "input": input_data or {},
                "output": output_data or {},
                "metadata": metadata or {},
                "tags": tags or [],
            }
        )


class _Store:
    def query(self, cypher: str, *, params: dict, database: str) -> list[dict]:
        return [{"status": "pending"}]


def test_query_span_is_privacy_safe_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SEOCHO_TRACE_CAPTURE_CONTENT", raising=False)
    recorder = _Recorder()
    try:
        enable_tracing(backend=recorder)
        records = QueryProxy(_Store()).query(
            QueryRequest(
                cypher="MATCH (w:Withdrawal {wallet: '0xsecret'}) RETURN w",
                workspace_id="institution-secret",
                ontology_profile="okx-withdrawal-v1",
            )
        )
    finally:
        disable_tracing()

    assert records == [{"status": "pending"}]
    span = next(item for item in recorder.spans if item["name"] == "db.query")
    rendered = repr(span)
    assert span["output"]["db.rows_returned"] == 1
    assert span["metadata"]["db.system"] == "neo4j"
    assert len(span["metadata"]["seocho.workspace_hash"]) == 16
    assert "institution-secret" not in rendered
    assert "0xsecret" not in rendered


def test_query_content_capture_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_TRACE_CAPTURE_CONTENT", "1")
    recorder = _Recorder()
    try:
        enable_tracing(backend=recorder)
        QueryProxy(_Store()).query(
            QueryRequest(cypher="MATCH (n) RETURN n", workspace_id="ws")
        )
    finally:
        disable_tracing()

    span = next(item for item in recorder.spans if item["name"] == "db.query")
    assert span["input"]["db.statement"] == "MATCH (n) RETURN n"
