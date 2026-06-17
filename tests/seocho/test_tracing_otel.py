"""ADR-0144: OTLP backend, content-capture policy, and nested start_span().

These cover the tracing primitives added for the local OpenTelemetry path and
the structured span tree, independent of any live Collector.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from seocho.tracing import (
    TracingBackend,
    _flatten_attributes,
    capture_text,
    configure_tracing_from_env,
    content_capture_enabled,
    current_backend_names,
    disable_tracing,
    enable_tracing,
    start_span,
)


class _Recorder(TracingBackend):
    """Flat backend (no open_span) that records every emitted span."""

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


# ---------------------------------------------------------------------------
# content-capture policy
# ---------------------------------------------------------------------------

def test_content_capture_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SEOCHO_TRACE_CAPTURE_CONTENT", raising=False)
    assert content_capture_enabled() is False
    assert capture_text("secret prompt") is None


def test_content_capture_enabled_truncates(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_TRACE_CAPTURE_CONTENT", "1")
    assert content_capture_enabled() is True
    out = capture_text("x" * 10, max_chars=4)
    assert out is not None and out.startswith("xxxx") and "+6 chars" in out
    assert capture_text(None) is None


def test_flatten_attributes_otel_safe() -> None:
    flat = _flatten_attributes(
        {"a": 1, "b": {"c": "d"}, "e": [1, 2], "f": [{"x": 1}], "g": None}
    )
    assert flat == {"a": 1, "b.c": "d", "e": [1, 2], "f": '[{"x": 1}]'}


# ---------------------------------------------------------------------------
# start_span nesting
# ---------------------------------------------------------------------------

def test_start_span_noop_when_disabled() -> None:
    disable_tracing()
    with start_span("rag.ask") as handle:
        handle.set_output(ok=True)  # must not raise on the null handle


def test_start_span_builds_nested_tree() -> None:
    rec = _Recorder()
    try:
        enable_tracing(backend=rec)
        with start_span("rag.ask", metadata={"workspace_id": "ws1"}):
            with start_span("rag.execute") as child:
                child.set_output(rows=3)
            with start_span("rag.synthesize"):
                pass
    finally:
        disable_tracing()

    by_name = {s["name"]: s for s in rec.spans}
    assert set(by_name) == {"rag.ask", "rag.execute", "rag.synthesize"}

    root = by_name["rag.ask"]["metadata"]
    execute = by_name["rag.execute"]["metadata"]
    synth = by_name["rag.synthesize"]["metadata"]

    # root carries ids + duration + workspace, and has no parent
    assert {"trace_id", "span_id", "duration_ms"} <= set(root)
    assert "parent_span_id" not in root
    assert root["workspace_id"] == "ws1"

    # children share the root trace and point at the root span
    assert execute["trace_id"] == root["trace_id"] == synth["trace_id"]
    assert execute["parent_span_id"] == root["span_id"]
    assert synth["parent_span_id"] == root["span_id"]

    # in-flight output is emitted on close
    assert by_name["rag.execute"]["output"].get("rows") == 3


def test_start_span_records_error_and_reraises() -> None:
    rec = _Recorder()
    try:
        enable_tracing(backend=rec)
        with pytest.raises(ValueError):
            with start_span("rag.ask"):
                raise ValueError("boom")
    finally:
        disable_tracing()

    span = rec.spans[-1]
    assert span["name"] == "rag.ask"
    assert "error" in span["metadata"]
    assert "error" in span["tags"]


# ---------------------------------------------------------------------------
# otlp backend registration
# ---------------------------------------------------------------------------

def test_otlp_is_a_valid_backend_name(monkeypatch) -> None:
    # The env contract must accept 'otlp' (not reject it as unsupported),
    # regardless of whether the exporter is installed.
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "otlp")
    try:
        configure_tracing_from_env()  # returns False without exporter; must not raise
    finally:
        disable_tracing()


def test_otlp_backend_real_init_when_available() -> None:
    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    from seocho.tracing import OTLPBackend

    backend = OTLPBackend(endpoint="http://localhost:4317", service_name="seocho-test")
    assert backend._init_error is None
    try:
        enable_tracing(backend=backend)
        assert "otlp" not in current_backend_names()  # custom instance, not named
        # open/close/log must not raise even with no live Collector
        with start_span("rag.ask"):
            with start_span("rag.execute") as child:
                child.set_output(rows=1)
        backend.log_span("leaf", output_data={"k": "v"})
    finally:
        disable_tracing()
