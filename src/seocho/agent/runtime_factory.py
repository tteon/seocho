"""Phase 4b.1 — registry-aware wrapper that satisfies AgentFactoryProtocol.

Phase 4a defined ``AgentFactoryProtocol`` and pinned the gap: the SDK
package shipped no class satisfying it. This module is the first step
of closing that gap. ``RuntimeBackedAgentFactory`` wraps any delegate
already conforming to the protocol (today, the legacy
``extraction/agent_factory.AgentFactory``) and a Phase 1.5
``RuntimeOntologyRegistry``. When the caller does not pass
``ontology_contexts``, the wrapper auto-fills it from the registry —
hoisting the registry coupling that today lives inline in
``runtime/agent_server.py`` into the factory layer.

Why a wrapper and not a direct SDK promotion. The plan called Phase 4b
the "convergence" step where the SDK ontology-scoped factory becomes
canonical for single-graph paths. Doing that in one shot would require
(a) building a ``MultiGraphConnector`` → ``Neo4jGraphStore`` adapter so
SDK agents can run against the runtime's graph access surface, and (b)
swapping ``runtime/agent_server.py``'s in-flight call site to use the
new factory. That is the L-effort core. This module ships the *protocol
boundary* — the place where the SDK module gains a graph-set entrypoint
that already satisfies ``AgentFactoryProtocol`` — so the convergence
work is decoupled from the runtime's hot path. The actual factory
internals can swap to SDK-native construction in Phase 4b.2 (bd:
seocho-6vb6 follow-up) without changing the call site.

Usage::

    from seocho.agent.runtime_factory import RuntimeBackedAgentFactory
    from runtime.ontology_registry import get_runtime_ontology_registry

    factory = RuntimeBackedAgentFactory(
        delegate=legacy_runtime_agent_factory,
        registry=get_runtime_ontology_registry(),
    )
    statuses = factory.create_agents_for_graphs(
        ["finance", "legal"],
        db_manager,
        workspace_id="acme",
    )  # ontology_contexts auto-filled from registry
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contract import AgentFactoryProtocol


class RuntimeBackedAgentFactory:
    """Wraps a delegate factory and a runtime ontology registry.

    Conforms to ``AgentFactoryProtocol`` so the runtime ``run_debate``
    path can swap from the legacy direct-factory call to the wrapper
    without touching the call shape.
    """

    def __init__(
        self,
        delegate: AgentFactoryProtocol,
        *,
        registry: Optional[Any] = None,
    ) -> None:
        if not isinstance(delegate, AgentFactoryProtocol):
            raise TypeError(
                "RuntimeBackedAgentFactory delegate must satisfy AgentFactoryProtocol; "
                f"got {type(delegate).__name__}."
            )
        self._delegate = delegate
        self._registry = registry

    def create_agents_for_graphs(
        self,
        graph_ids: List[str],
        db_manager: Any,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Create agents for ``graph_ids`` and return their status entries.

        When ``ontology_contexts`` is not provided and the wrapper holds a
        registry reference, the registry's ``ontology_contexts(workspace_id)``
        is used to populate the parameter. This makes the registry the
        single producer of ontology-context-as-first-class data — Phase 1.5's
        promise — even when the agent-creation call site doesn't know
        about the registry.

        An explicit ``ontology_contexts`` argument always wins over the
        registry. Empty dict means "no ontology-context known", which is
        forwarded as-is so the delegate's backward-compatible
        no-skew-detection path runs.
        """

        resolved_contexts = ontology_contexts
        if resolved_contexts is None and self._registry is not None:
            registry_contexts = self._registry.ontology_contexts(
                workspace_id=workspace_id
            )
            resolved_contexts = registry_contexts or None

        return self._delegate.create_agents_for_graphs(
            graph_ids,
            db_manager,
            ontology_contexts=resolved_contexts,
            workspace_id=workspace_id,
        )

    def get_agents_for_graphs(self, graph_ids: List[str]) -> Dict[str, Any]:
        return self._delegate.get_agents_for_graphs(graph_ids)

    def list_agents(self) -> List[str]:
        return self._delegate.list_agents()


__all__ = ["RuntimeBackedAgentFactory"]
