"""Regression tests for seocho-lrvn — AgentConfig.on_agent_failure='raise'.

Default behaviour is the silent agent → pipeline fallback (back-compat
with seocho-1zck's current contract). Setting ``on_agent_failure='raise'``
on AgentConfig makes Session.add / Session.ask propagate the original
agent exception instead of degrading silently.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest


def _make_minimal_ontology():
    from seocho import NodeDef, Ontology, P
    return Ontology(
        name="lrvn_test",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
    )


def _build_session(monkeypatch, *, on_agent_failure: str = "fallback"):
    from seocho import agents_runtime as _agents_runtime_module
    from seocho.agent.context import SessionContext
    from seocho.agent_config import AgentConfig
    from seocho.session import Session

    sess = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
        agent_config=AgentConfig(execution_mode="agent", on_agent_failure=on_agent_failure),
        user_id="alice",
        workspace_id="acme",
    )
    sess.context = SessionContext()

    # Stub the agent factory so an agent object exists (any sentinel works —
    # the runtime never actually consumes it because we replace .run too).
    sess._get_indexing_agent = lambda: object()  # type: ignore[method-assign]
    sess._get_query_agent = lambda: object()  # type: ignore[method-assign]

    # Stub the agents runtime so .run() raises *inside* Session's try block,
    # exercising the fallback / re-raise path correctly.
    class _FailingRuntime:
        async def run(self, *, agent, input, context=None):
            raise RuntimeError("synthetic agent failure")

    monkeypatch.setattr(_agents_runtime_module, "get_agents_runtime",
                        lambda: _FailingRuntime())

    # Stub the pipeline fallback so it returns a clean shape (avoid touching
    # any real graph store).
    def _stub_pipeline_add(content, database, category, metadata):
        return {
            "extracted_nodes": [],
            "extracted_relationships": [],
            "nodes_created": 0,
            "relationships_created": 0,
            "mode": "pipeline",
        }

    def _stub_pipeline_ask(question, database, reasoning_mode):
        return {"answer": "from pipeline", "mode": "pipeline"}

    sess._add_via_pipeline = _stub_pipeline_add  # type: ignore[method-assign]
    sess._ask_via_pipeline = _stub_pipeline_ask  # type: ignore[method-assign]
    return sess


def test_default_fallback_silently_degrades(monkeypatch) -> None:
    """Back-compat: on_agent_failure='fallback' (default) keeps the silent fallback."""
    sess = _build_session(monkeypatch, on_agent_failure="fallback")
    result = sess.add("text")
    assert result.get("degraded") is True
    assert result.get("fallback_from") == "agent"
    assert "synthetic agent failure" in result.get("fallback_reason", "")


def test_raise_propagates_agent_exception_on_add(monkeypatch) -> None:
    """on_agent_failure='raise' propagates the exception instead of falling back."""
    sess = _build_session(monkeypatch, on_agent_failure="raise")
    with pytest.raises(RuntimeError, match="synthetic agent failure"):
        sess.add("text")


def test_raise_propagates_agent_exception_on_ask(monkeypatch) -> None:
    sess = _build_session(monkeypatch, on_agent_failure="raise")
    with pytest.raises(RuntimeError, match="synthetic agent failure"):
        sess.ask("who?")


def test_agent_config_dict_includes_on_agent_failure() -> None:
    """to_dict() exposes the new field for trace metadata + serialization."""
    from seocho.agent_config import AgentConfig
    cfg = AgentConfig(execution_mode="agent", on_agent_failure="raise")
    d = cfg.to_dict()
    assert d["on_agent_failure"] == "raise"
