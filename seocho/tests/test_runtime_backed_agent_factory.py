"""Phase 4b.1 — RuntimeBackedAgentFactory + DebateOrchestrator conformance.

Phase 4a defined the protocols; Phase 4b.1 ships the wrapper that closes
the SDK-side gap and pins ``DebateOrchestrator`` against
``OrchestrationProtocol`` so future orchestrator changes cannot quietly
drop the contract.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import nullcontext

import pytest

from seocho.agent.contract import AgentFactoryProtocol, OrchestrationProtocol
from seocho.agent.runtime_factory import RuntimeBackedAgentFactory


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubDelegate:
    """Minimal AgentFactoryProtocol-conformant stub recording invocations."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_agents_for_graphs(
        self,
        graph_ids,
        db_manager,
        *,
        ontology_contexts=None,
        workspace_id: str = "default",
    ):
        self.calls.append(
            {
                "graph_ids": list(graph_ids),
                "db_manager": db_manager,
                "ontology_contexts": ontology_contexts,
                "workspace_id": workspace_id,
            }
        )
        return [
            {"graph": gid, "database": gid, "status": "ready", "reason": "stub"}
            for gid in graph_ids
        ]

    def get_agents_for_graphs(self, graph_ids):
        return {gid: object() for gid in graph_ids}

    def list_agents(self):
        return ["stub-agent"]


class _StubRegistry:
    """Mimics RuntimeOntologyRegistry.ontology_contexts surface."""

    def __init__(self, contexts: dict[str, dict[str, object]]) -> None:
        self._contexts = contexts

    def ontology_contexts(self, *, workspace_id: str = "default"):
        return dict(self._contexts.get(workspace_id, {}))


# ---------------------------------------------------------------------------
# RuntimeBackedAgentFactory — protocol conformance + delegation behavior
# ---------------------------------------------------------------------------


def test_wrapper_satisfies_agent_factory_protocol():
    """Phase 4a's gap was: the SDK module ships no class satisfying
    AgentFactoryProtocol. RuntimeBackedAgentFactory closes that gap."""

    wrapper = RuntimeBackedAgentFactory(delegate=_StubDelegate())
    assert isinstance(wrapper, AgentFactoryProtocol)


def test_wrapper_rejects_non_conformant_delegate():
    """Constructor must refuse a delegate that doesn't satisfy the protocol —
    this prevents silent failures at the call site."""

    class _NotAFactory:
        def list_agents(self):
            return []

    with pytest.raises(TypeError, match="AgentFactoryProtocol"):
        RuntimeBackedAgentFactory(delegate=_NotAFactory())  # type: ignore[arg-type]


def test_wrapper_passes_args_through_to_delegate():
    delegate = _StubDelegate()
    wrapper = RuntimeBackedAgentFactory(delegate=delegate)
    db_manager = object()

    statuses = wrapper.create_agents_for_graphs(
        ["finance", "legal"],
        db_manager,
        ontology_contexts={"finance": "explicit_ctx"},
        workspace_id="acme",
    )

    assert delegate.calls == [
        {
            "graph_ids": ["finance", "legal"],
            "db_manager": db_manager,
            "ontology_contexts": {"finance": "explicit_ctx"},
            "workspace_id": "acme",
        }
    ]
    assert statuses[0]["graph"] == "finance"


def test_wrapper_auto_fills_ontology_contexts_from_registry():
    """The structural promise: when caller doesn't pass ontology_contexts
    and the wrapper holds a registry, the registry's contexts are forwarded
    to the delegate. This hoists the registry coupling from agent_server.py
    inline call site into the factory layer."""

    delegate = _StubDelegate()
    registry = _StubRegistry(
        contexts={"acme": {"finance": "registry_ctx_finance"}}
    )
    wrapper = RuntimeBackedAgentFactory(delegate=delegate, registry=registry)

    wrapper.create_agents_for_graphs(["finance"], None, workspace_id="acme")

    assert delegate.calls[0]["ontology_contexts"] == {"finance": "registry_ctx_finance"}


def test_wrapper_explicit_arg_overrides_registry():
    """An explicit ontology_contexts argument always wins over the registry —
    callers retain full control when they need it."""

    delegate = _StubDelegate()
    registry = _StubRegistry(contexts={"default": {"finance": "registry_ctx"}})
    wrapper = RuntimeBackedAgentFactory(delegate=delegate, registry=registry)

    wrapper.create_agents_for_graphs(
        ["finance"],
        None,
        ontology_contexts={"finance": "explicit_ctx"},
    )

    assert delegate.calls[0]["ontology_contexts"] == {"finance": "explicit_ctx"}


def test_wrapper_empty_registry_forwards_none_to_delegate():
    """When registry returns empty mapping, the wrapper forwards None
    (not {}), so the delegate's backward-compatible no-skew-detection path
    runs unchanged."""

    delegate = _StubDelegate()
    registry = _StubRegistry(contexts={})
    wrapper = RuntimeBackedAgentFactory(delegate=delegate, registry=registry)

    wrapper.create_agents_for_graphs(["finance"], None, workspace_id="acme")

    assert delegate.calls[0]["ontology_contexts"] is None


def test_wrapper_proxies_get_agents_and_list_agents():
    delegate = _StubDelegate()
    wrapper = RuntimeBackedAgentFactory(delegate=delegate)

    assert wrapper.list_agents() == ["stub-agent"]
    assert set(wrapper.get_agents_for_graphs(["finance", "legal"])) == {"finance", "legal"}


# ---------------------------------------------------------------------------
# DebateOrchestrator — OrchestrationProtocol conformance
# ---------------------------------------------------------------------------


def _stub_extraction_imports(monkeypatch) -> None:
    """Make extraction.debate importable in the test environment without
    a real OpenAI Agents SDK or DB connector."""

    fake_agents = types.SimpleNamespace(
        Agent=type("_StubAgent", (), {}),
        function_tool=lambda fn: fn,
        RunContextWrapper=type("_StubCtx", (), {}),
        Runner=type("_StubRunner", (), {}),
        trace=lambda *_a, **_k: nullcontext(),
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extraction_path = os.path.join(repo_root, "extraction")
    if extraction_path not in sys.path:
        sys.path.insert(0, extraction_path)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def test_debate_orchestrator_satisfies_orchestration_protocol(monkeypatch):
    """The legacy orchestrator must keep matching OrchestrationProtocol so
    Phase 4b.2 can swap the agent source under a stable orchestration
    surface."""

    _stub_extraction_imports(monkeypatch)

    import importlib

    if "debate" in sys.modules:
        del sys.modules["debate"]
    debate_module = importlib.import_module("debate")

    orchestrator = debate_module.DebateOrchestrator(
        agents={},
        supervisor=object(),
        shared_memory=object(),
    )
    assert isinstance(orchestrator, OrchestrationProtocol)
