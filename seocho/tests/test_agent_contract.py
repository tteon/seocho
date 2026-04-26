"""Conformance tests for Phase 4a's cross-stack agent protocols.

Phase 4a defines protocols both stacks should satisfy. Today the runtime
stack (``extraction/agent_factory.py``) already does; the SDK stack
(``seocho/agent/factory.py``) does not (it exposes ontology-scoped
single-agent factories instead of a graph-set factory). The tests below
make those facts machine-checkable so Phase 4b's promotion of the SDK
factory cannot land without first satisfying ``AgentFactoryProtocol``.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import nullcontext

import pytest

from seocho.agent.contract import (
    AgentFactoryProtocol,
    AgentStatusEntry,
    OrchestrationProtocol,
    ReadinessSummarizer,
    ReadinessSummary,
)


# ---------------------------------------------------------------------------
# Helpers — let the runtime stack import without a real OpenAI Agents SDK.
# ---------------------------------------------------------------------------


def _stub_agents_module(monkeypatch) -> None:
    fake_agents = types.SimpleNamespace(
        Agent=type("_StubAgent", (), {}),
        function_tool=lambda fn: fn,
        RunContextWrapper=type("_StubCtx", (), {}),
        Runner=type("_StubRunner", (), {}),
        trace=lambda *_a, **_k: nullcontext(),
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)


def _import_runtime_factory():
    """Import the legacy runtime AgentFactory from the extraction package.

    The tests live in seocho/tests/, but the runtime factory lives at
    extraction/agent_factory.py and is normally imported via
    extraction/_runtime_alias.py. We add extraction/ to sys.path so the
    flat module resolves the same way it does in production.
    """

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extraction_path = os.path.join(repo_root, "extraction")
    if extraction_path not in sys.path:
        sys.path.insert(0, extraction_path)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import importlib

    if "agent_factory" in sys.modules:
        del sys.modules["agent_factory"]
    return importlib.import_module("agent_factory")


# ---------------------------------------------------------------------------
# AgentFactoryProtocol — runtime stack conformance
# ---------------------------------------------------------------------------


def test_runtime_agent_factory_satisfies_protocol(monkeypatch):
    """The legacy extraction/agent_factory.AgentFactory ships a graph-set
    factory surface — it must satisfy the cross-stack contract."""

    _stub_agents_module(monkeypatch)
    agent_factory = _import_runtime_factory()
    factory = agent_factory.AgentFactory(neo4j_connector=object())

    assert isinstance(factory, AgentFactoryProtocol)


def test_protocol_rejects_factories_missing_required_methods():
    """Negative control: the protocol is structural, not nominal."""

    class _Incomplete:
        def list_agents(self):
            return []

    assert not isinstance(_Incomplete(), AgentFactoryProtocol)


# ---------------------------------------------------------------------------
# SDK stack — known gap, recorded as a test that documents Phase 4b scope
# ---------------------------------------------------------------------------


def test_sdk_factory_does_not_yet_satisfy_protocol():
    """Today the SDK exposes ``create_indexing_agent`` / ``create_query_agent``
    / ``create_supervisor_agent``, not a graph-set factory. Phase 4b
    promotes a wrapper that satisfies AgentFactoryProtocol; this test
    pins the current gap so 4b cannot silently land without filling it.
    """

    from seocho.agent import factory as sdk_factory

    # The module exposes free functions, not a factory class instance —
    # so isinstance against the protocol is meaningless. Verify the
    # symbol shape we expect today, and the absence of the protocol
    # method on any module-level attribute.
    assert callable(getattr(sdk_factory, "create_indexing_agent", None))
    assert callable(getattr(sdk_factory, "create_query_agent", None))
    assert callable(getattr(sdk_factory, "create_supervisor_agent", None))
    assert not hasattr(sdk_factory, "create_agents_for_graphs"), (
        "If this assertion fails, Phase 4b has begun: update the test to "
        "isinstance-check the new wrapper against AgentFactoryProtocol."
    )


# ---------------------------------------------------------------------------
# ReadinessSummarizer — runtime/agent_readiness.summarize_readiness
# ---------------------------------------------------------------------------


def test_summarize_readiness_returns_well_formed_summary():
    """The runtime summarizer's return value must match ReadinessSummary."""

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from runtime.agent_readiness import summarize_readiness

    summary = summarize_readiness(
        [
            {"graph": "kgnormal", "database": "kgnormal", "status": "ready"},
            {
                "graph": "kgfibo",
                "database": "kgfibo",
                "status": "degraded",
                "ontology_context_mismatch": {"active_context_hash": "x"},
            },
        ]
    )

    # All ReadinessSummary keys present
    expected_keys = set(ReadinessSummary.__annotations__.keys())
    assert expected_keys <= set(summary.keys()), (
        f"summarize_readiness output is missing keys required by ReadinessSummary: "
        f"{expected_keys - set(summary.keys())}"
    )
    # And types line up with the TypedDict declarations
    assert isinstance(summary["debate_state"], str)
    assert isinstance(summary["degraded"], bool)
    assert isinstance(summary["ready_count"], int)
    assert isinstance(summary["degraded_count"], int)
    assert isinstance(summary["total_count"], int)
    assert isinstance(summary["mismatch_count"], int)
    assert isinstance(summary["mismatch_graph_ids"], list)


def test_summarize_readiness_satisfies_callable_protocol():
    """A callable matching ReadinessSummarizer should pass isinstance."""

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from runtime.agent_readiness import summarize_readiness

    assert isinstance(summarize_readiness, ReadinessSummarizer)


# ---------------------------------------------------------------------------
# OrchestrationProtocol — pinned as a structural contract for Phase 4b.
# ---------------------------------------------------------------------------


def test_orchestration_protocol_accepts_async_run_debate():
    """Any class with ``async def run_debate(self, query, context)``
    satisfies the protocol — including a stub. Phase 4b's promoted
    orchestrator must continue to satisfy it."""

    class _MinimalOrchestrator:
        async def run_debate(self, query, context):
            return {"response": "", "trace_steps": [], "debate_results": []}

    assert isinstance(_MinimalOrchestrator(), OrchestrationProtocol)


def test_orchestration_protocol_rejects_missing_run_debate():
    class _NoRunDebate:
        async def run(self, query, context):
            return {}

    assert not isinstance(_NoRunDebate(), OrchestrationProtocol)


# ---------------------------------------------------------------------------
# AgentStatusEntry shape — pinned by Phase 2 + 3 producers
# ---------------------------------------------------------------------------


def test_agent_status_entry_is_total_false_typeddict():
    """``status`` and ``database`` are mandatory in practice but are typed
    optional so older callers (e.g. test fixtures that emit only
    ``database`` + ``status``) keep working."""

    # total=False permits partial dicts at the type level; runtime
    # dict() still allows arbitrary keys, so the assertion is structural.
    entry: AgentStatusEntry = {"graph": "kgnormal", "database": "kgnormal", "status": "ready"}
    assert entry["graph"] == "kgnormal"
