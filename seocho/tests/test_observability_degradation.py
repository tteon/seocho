"""Regression tests for seocho-qr74 — degraded_observability stamping.

When a tracing backend silently drops spans (the canonical case is
``OpikBackend`` whose ``__init__`` catches client-init failures and
sets ``_client = None``), ``Session`` results should carry
``degraded_observability=True`` so callers can fail fast when
observability is required.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest


def _make_minimal_ontology():
    from seocho import NodeDef, Ontology, P
    return Ontology(
        name="qr74_test",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
    )


def test_tracing_degraded_reasons_returns_empty_when_no_backends() -> None:
    from seocho.tracing import disable_tracing, tracing_degraded_reasons
    disable_tracing()
    assert tracing_degraded_reasons() == []


def test_tracing_degraded_reasons_reports_failed_opik_backend(monkeypatch) -> None:
    """A backend that ships an _init_error attribute is reported as degraded."""
    from seocho import tracing as t

    class _FakeBrokenBackend:
        _init_error = "ConnectionRefused: opik unreachable"

        def log_span(self, *a, **kw): pass
        def close(self): pass

    monkeypatch.setattr(t, "_BACKENDS", [_FakeBrokenBackend()])
    monkeypatch.setattr(t, "_BACKEND_NAMES", ["opik"])
    reasons = t.tracing_degraded_reasons()
    assert len(reasons) == 1
    assert "opik" in reasons[0]
    assert "ConnectionRefused" in reasons[0]
    assert t.is_observability_degraded() is True


def test_tracing_healthy_when_no_init_error(monkeypatch) -> None:
    from seocho import tracing as t

    class _FakeHealthyBackend:
        def log_span(self, *a, **kw): pass
        def close(self): pass

    monkeypatch.setattr(t, "_BACKENDS", [_FakeHealthyBackend()])
    monkeypatch.setattr(t, "_BACKEND_NAMES", ["jsonl"])
    assert t.tracing_degraded_reasons() == []
    assert t.is_observability_degraded() is False


def test_session_stamps_degraded_observability_on_add(monkeypatch) -> None:
    """Session.add result carries degraded_observability=True when traces drop."""
    from seocho import tracing as t
    from seocho.agent.context import SessionContext
    from seocho.session import Session

    class _BrokenOpik:
        _init_error = "auth failed"

        def log_span(self, *a, **kw): pass
        def close(self): pass

    monkeypatch.setattr(t, "_BACKENDS", [_BrokenOpik()])
    monkeypatch.setattr(t, "_BACKEND_NAMES", ["opik"])

    sess = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
        user_id="alice",
        workspace_id="acme",
    )
    sess.context = SessionContext()

    def _stub_pipeline(content, database, category, metadata):
        return {
            "extracted_nodes": [],
            "extracted_relationships": [],
            "nodes_created": 0,
            "relationships_created": 0,
            "mode": "pipeline",
        }

    sess._add_via_pipeline = _stub_pipeline  # type: ignore[method-assign]

    result = sess.add("Tim Cook leads Apple.")
    assert result.get("degraded_observability") is True
    reasons = result.get("observability_degradation_reasons", [])
    assert any("opik" in r for r in reasons)


def test_session_does_not_stamp_when_tracing_healthy(monkeypatch) -> None:
    """When all backends are healthy, the field is absent (or False)."""
    from seocho import tracing as t
    from seocho.agent.context import SessionContext
    from seocho.session import Session

    monkeypatch.setattr(t, "_BACKENDS", [])
    monkeypatch.setattr(t, "_BACKEND_NAMES", [])

    sess = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
        user_id="bob",
        workspace_id="acme",
    )
    sess.context = SessionContext()

    def _stub_pipeline(content, database, category, metadata):
        return {
            "extracted_nodes": [],
            "extracted_relationships": [],
            "nodes_created": 0,
            "relationships_created": 0,
            "mode": "pipeline",
        }

    sess._add_via_pipeline = _stub_pipeline  # type: ignore[method-assign]

    result = sess.add("hello")
    assert not result.get("degraded_observability", False)
