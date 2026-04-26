"""Phase 4b.2 — env-flagged agent-factory strategy in runtime/agent_server.py.

The strategy selector decides between the legacy direct factory call
and the Phase 4b.1 ``RuntimeBackedAgentFactory`` wrapper. Both paths
must produce structurally identical agent_statuses given the same
inputs — that is the load-bearing parity property the env flag relies
on for safe rollback.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional


def _import_agent_server():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import importlib

    if "runtime.agent_server" in sys.modules:
        return sys.modules["runtime.agent_server"]
    return importlib.import_module("runtime.agent_server")


# ---------------------------------------------------------------------------
# _agent_factory_strategy()
# ---------------------------------------------------------------------------


def test_strategy_default_is_direct(monkeypatch):
    monkeypatch.delenv("SEOCHO_AGENT_FACTORY", raising=False)
    srv = _import_agent_server()
    assert srv._agent_factory_strategy() == "direct"


def test_strategy_env_runtime_backed(monkeypatch):
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "runtime_backed")
    srv = _import_agent_server()
    assert srv._agent_factory_strategy() == "runtime_backed"


def test_strategy_handles_whitespace_and_case(monkeypatch):
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "  Runtime_Backed  ")
    srv = _import_agent_server()
    assert srv._agent_factory_strategy() == "runtime_backed"


def test_strategy_falls_back_to_direct_on_unknown_value(monkeypatch):
    """Kill-switch friendliness: any unrecognized value rolls back to direct."""
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "bogus_value")
    srv = _import_agent_server()
    assert srv._agent_factory_strategy() == "direct"


def test_strategy_treats_empty_string_as_default(monkeypatch):
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "")
    srv = _import_agent_server()
    assert srv._agent_factory_strategy() == "direct"


# ---------------------------------------------------------------------------
# _create_agents_for_graphs_with_strategy() — both paths produce equivalent
# agent_statuses for the same inputs (parity property).
# ---------------------------------------------------------------------------


class _RecordingFactory:
    """Stand-in for the module-level agent_factory. Records calls and emits
    deterministic status entries."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def create_agents_for_graphs(
        self,
        graph_ids: List[str],
        db_manager: Any,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        self.calls.append(
            {
                "graph_ids": list(graph_ids),
                "ontology_contexts": ontology_contexts,
                "workspace_id": workspace_id,
            }
        )
        return [
            {
                "graph": gid,
                "database": gid,
                "status": "ready",
                "reason": "stub",
                "ontology_contexts_seen": (
                    sorted(ontology_contexts.keys()) if ontology_contexts else []
                ),
            }
            for gid in graph_ids
        ]

    def get_agents_for_graphs(self, graph_ids):
        return {gid: object() for gid in graph_ids}

    def list_agents(self):
        return []


class _StubRegistry:
    def __init__(self, contexts: Dict[str, Dict[str, Any]]) -> None:
        self._contexts = contexts

    def ontology_contexts(self, *, workspace_id: str = "default"):
        return dict(self._contexts.get(workspace_id, {}))

    def active_context_hashes(self, *, workspace_id: str = "default"):
        return {gid: f"hash-{gid}" for gid in self._contexts.get(workspace_id, {})}


def test_direct_strategy_passes_registry_contexts_inline(monkeypatch):
    monkeypatch.delenv("SEOCHO_AGENT_FACTORY", raising=False)
    srv = _import_agent_server()
    factory = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", factory)

    registry = _StubRegistry(
        contexts={"acme": {"finance": object(), "legal": object()}}
    )

    statuses = srv._create_agents_for_graphs_with_strategy(
        ["finance", "legal"],
        db_manager=None,
        registry=registry,
        workspace_id="acme",
    )

    assert factory.calls[0]["ontology_contexts"] is not None
    assert sorted(factory.calls[0]["ontology_contexts"].keys()) == ["finance", "legal"]
    assert factory.calls[0]["workspace_id"] == "acme"
    assert {entry["graph"] for entry in statuses} == {"finance", "legal"}


def test_runtime_backed_strategy_routes_through_wrapper(monkeypatch):
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "runtime_backed")
    srv = _import_agent_server()
    factory = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", factory)

    registry = _StubRegistry(
        contexts={"acme": {"finance": object(), "legal": object()}}
    )

    statuses = srv._create_agents_for_graphs_with_strategy(
        ["finance", "legal"],
        db_manager=None,
        registry=registry,
        workspace_id="acme",
    )

    # Wrapper auto-filled ontology_contexts from registry → reached the delegate.
    assert factory.calls[0]["ontology_contexts"] is not None
    assert sorted(factory.calls[0]["ontology_contexts"].keys()) == ["finance", "legal"]
    assert factory.calls[0]["workspace_id"] == "acme"
    assert {entry["graph"] for entry in statuses} == {"finance", "legal"}


def test_both_strategies_produce_equivalent_statuses(monkeypatch):
    """Parity property: same inputs → equivalent agent_statuses.
    This is the safety guarantee for the env-flagged migration."""

    srv = _import_agent_server()
    registry = _StubRegistry(
        contexts={"acme": {"finance": "ctx_finance", "legal": "ctx_legal"}}
    )

    direct_factory = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", direct_factory)
    monkeypatch.delenv("SEOCHO_AGENT_FACTORY", raising=False)
    direct_statuses = srv._create_agents_for_graphs_with_strategy(
        ["finance", "legal"],
        db_manager=None,
        registry=registry,
        workspace_id="acme",
    )

    wrapped_factory = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", wrapped_factory)
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "runtime_backed")
    wrapped_statuses = srv._create_agents_for_graphs_with_strategy(
        ["finance", "legal"],
        db_manager=None,
        registry=registry,
        workspace_id="acme",
    )

    assert direct_statuses == wrapped_statuses
    # Both paths called the delegate exactly once with the same args.
    assert direct_factory.calls == wrapped_factory.calls


def test_empty_registry_keeps_ontology_contexts_unset(monkeypatch):
    """Backward compatibility: when the registry holds nothing, both paths
    forward None to the delegate so the legacy unset-default behavior runs."""

    srv = _import_agent_server()
    empty_registry = _StubRegistry(contexts={})

    factory_direct = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", factory_direct)
    monkeypatch.delenv("SEOCHO_AGENT_FACTORY", raising=False)
    srv._create_agents_for_graphs_with_strategy(
        ["finance"],
        db_manager=None,
        registry=empty_registry,
        workspace_id="acme",
    )
    assert factory_direct.calls[0]["ontology_contexts"] is None

    factory_wrapped = _RecordingFactory()
    monkeypatch.setattr(srv, "agent_factory", factory_wrapped)
    monkeypatch.setenv("SEOCHO_AGENT_FACTORY", "runtime_backed")
    srv._create_agents_for_graphs_with_strategy(
        ["finance"],
        db_manager=None,
        registry=empty_registry,
        workspace_id="acme",
    )
    assert factory_wrapped.calls[0]["ontology_contexts"] is None
