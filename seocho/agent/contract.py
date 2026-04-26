"""Cross-stack runtime agent protocols (Phase 4a — protocol bridge).

The plan splits Phase 4 into two steps. Phase 4a (this module) defines
the structural contracts both agent stacks must satisfy. Phase 4b — the
actual convergence — promotes the SDK ontology-scoped factory as
canonical for single-graph paths and rewires ``extraction/debate.py``
to consume protocol-produced agents through ``OrchestrationProtocol``.

Why ``runtime_checkable`` Protocols and not ABCs? Two stacks already
exist and ship working code:

- ``extraction/agent_factory.py::AgentFactory`` (graph-scoped runtime stack)
- ``seocho/agent/factory.py`` (ontology-scoped SDK stack — currently
  exposes ``create_indexing_agent``, ``create_query_agent``,
  ``create_supervisor_agent`` rather than ``create_agents_for_graphs``).

Forcing them to inherit a common base class would couple the migration
to a coordinated rewrite. Structural typing lets each side meet the
contract at its own pace, and lets test code use ``isinstance`` to
catch regressions without inheritance gymnastics.

Phase 4b's job is to make the SDK factory satisfy ``AgentFactoryProtocol``
(currently it doesn't), and to rewire the runtime debate path through
``OrchestrationProtocol`` so swapping the agent source does not require
touching the orchestrator.
"""

from __future__ import annotations

from typing import Any, Awaitable, Dict, List, Optional, Protocol, TypedDict, runtime_checkable


# ---------------------------------------------------------------------------
# Status / readiness data shapes (already produced by Phases 1-3)
# ---------------------------------------------------------------------------


class AgentStatusEntry(TypedDict, total=False):
    """The shape ``AgentFactory.create_agents_for_graphs`` emits per graph.

    Phase 2 added ``ontology_context_mismatch``; Phase 3 made
    ``summarize_readiness`` consume it. This TypedDict is the canonical
    schema — the runtime debate response, the readiness rollup, and any
    Phase 4b orchestrator agree on it.
    """

    graph: str
    graph_id: str
    database: str
    status: str
    reason: str
    ontology_context_mismatch: Dict[str, Any]


class ReadinessSummary(TypedDict):
    """The shape ``runtime/agent_readiness.summarize_readiness`` returns.

    Phase 1 introduced ``debate_state`` / ``ready_count`` / ``degraded_count``.
    Phase 3 added ``mismatch_count`` and ``mismatch_graph_ids`` so callers
    can route around hash-skewed agents specifically.
    """

    debate_state: str
    degraded: bool
    ready_count: int
    degraded_count: int
    total_count: int
    mismatch_count: int
    mismatch_graph_ids: List[str]


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentFactoryProtocol(Protocol):
    """The interface the runtime debate path needs from any agent factory.

    ``ontology_contexts`` is the Phase 2 hook through which compiled
    ontology contexts (one per graph) flow from
    ``runtime/ontology_registry.RuntimeOntologyRegistry.ontology_contexts``
    into the per-graph hash drift probe at agent creation time.

    Conformance: structural. ``isinstance(factory, AgentFactoryProtocol)``
    succeeds whenever the three callables exist with compatible names —
    Python's ``runtime_checkable`` does not verify signatures.
    """

    def create_agents_for_graphs(
        self,
        graph_ids: List[str],
        db_manager: Any,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Create or refresh agents bound to ``graph_ids``.

        Returns one ``AgentStatusEntry``-shaped dict per graph. Skewed
        graphs return ``status="degraded"`` with
        ``reason="ontology_context_mismatch"`` and an
        ``ontology_context_mismatch`` payload. The runtime never invokes
        a tool on a skewed agent — refusal lives in the agent's tool
        closure (Phase 2).
        """

    def get_agents_for_graphs(self, graph_ids: List[str]) -> Dict[str, Any]:
        """Return the previously-created agents for ``graph_ids``."""

    def list_agents(self) -> List[str]:
        """Return graph identifiers that currently have an agent."""


@runtime_checkable
class ReadinessSummarizer(Protocol):
    """Function-shape protocol for readiness rollup.

    Today only ``runtime/agent_readiness.summarize_readiness`` implements
    this; Phase 4b's protocol-based orchestrator will accept any
    callable matching this shape so alternative readiness policies (e.g.
    a per-workspace SLA-aware summarizer) can swap in.
    """

    def __call__(
        self,
        statuses: List[Dict[str, Any]],
    ) -> ReadinessSummary:
        ...


@runtime_checkable
class OrchestrationProtocol(Protocol):
    """The interface the runtime debate API needs from any orchestrator.

    ``run_debate`` consumes a query plus a server-side context object
    (today ``ServerContext`` from ``runtime/server_runtime.py``) and
    returns the dict that becomes the body of ``DebateResponse``. Any
    orchestrator satisfying this signature can replace the legacy
    ``DebateOrchestrator`` without changing the API surface — that is
    the load-bearing property Phase 4b relies on when we promote the
    SDK factory to feed the same orchestrator.
    """

    def run_debate(self, query: str, context: Any) -> Awaitable[Dict[str, Any]]:
        ...


__all__ = [
    "AgentFactoryProtocol",
    "AgentStatusEntry",
    "OrchestrationProtocol",
    "ReadinessSummarizer",
    "ReadinessSummary",
]
