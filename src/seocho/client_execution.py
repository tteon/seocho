"""Chainable execution-plan construction behind the public SDK facade."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from .runtime_contract import normalize_query_mode
from .models import (
    EntityOverride,
    ExecutionPlan,
    ExecutionResult,
    GraphRef,
    GraphTarget,
    ReasoningPolicy,
)

if TYPE_CHECKING:
    from .client import Seocho


class ExecutionPlanBuilder:
    """Chainable graph-centric execution plan builder for SDK callers."""

    def __init__(
        self,
        client: Seocho,
        query: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Initialize the builder with a client, query, and optional scope."""
        self._client = client
        self._query = query
        self._targets: List[GraphRef] = []
        self._reasoning = ReasoningPolicy(
            reasoning_cycle=self._client._default_reasoning_cycle(),
        )
        self._entity_overrides: List[EntityOverride] = []
        self._ontology_ids: List[str] = []
        self._vocabulary_profiles: List[str] = []
        self._user_id = user_id
        self._session_id = session_id

    def on_graph(
        self, graph: GraphRef | GraphTarget | Dict[str, Any] | str
    ) -> "ExecutionPlanBuilder":
        """Target a single graph for execution."""
        return self.on_graphs(graph)

    def on_graphs(
        self, *graphs: GraphRef | GraphTarget | Dict[str, Any] | str
    ) -> "ExecutionPlanBuilder":
        """Target one or more graphs for execution."""
        for graph in graphs:
            if isinstance(graph, (list, tuple)):
                for item in graph:
                    self._targets.append(self._client._coerce_graph_ref(item))
                continue
            self._targets.append(self._client._coerce_graph_ref(graph))
        return self

    def with_ontology(self, *ontology_ids: str) -> "ExecutionPlanBuilder":
        """Filter target graphs by ontology ID."""
        self._ontology_ids = [
            str(item).strip() for item in ontology_ids if str(item).strip()
        ]
        return self

    def with_vocabulary(self, *vocabulary_profiles: str) -> "ExecutionPlanBuilder":
        """Filter target graphs by vocabulary profile."""
        self._vocabulary_profiles = [
            str(item).strip() for item in vocabulary_profiles if str(item).strip()
        ]
        return self

    def with_reasoning_cycle(
        self,
        reasoning_cycle: Dict[str, Any],
    ) -> "ExecutionPlanBuilder":
        """Attach an anomaly-driven inquiry contract to semantic/debate execution."""
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=self._reasoning.repair_budget,
            query_mode=self._reasoning.query_mode,
            fallback_style=self._reasoning.fallback_style,
            reasoning_cycle=dict(reasoning_cycle or {}),
        )
        return self

    def with_reasoning(
        self,
        *,
        style: str,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        repair_budget: Optional[int] = None,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Set the reasoning policy for query execution.

        Args:
            style: Reasoning style (``"direct"``, ``"react"``, or ``"debate"``).
            max_steps: Maximum reasoning steps for react/debate.
            tool_budget: Maximum tool calls allowed.
            require_grounded_evidence: Require graph-grounded evidence in the answer.
            repair_budget: Maximum query repair attempts on empty results.
            fallback_style: Style to fall back to if the primary style fails.
        """
        self._reasoning = ReasoningPolicy(
            style=str(style).strip().lower() or "direct",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            repair_budget=(
                self._reasoning.repair_budget
                if repair_budget is None
                else max(0, int(repair_budget))
            ),
            query_mode=self._reasoning.query_mode,
            fallback_style=(str(fallback_style).strip().lower() or None)
            if fallback_style
            else None,
            reasoning_cycle=dict(self._reasoning.reasoning_cycle),
        )
        self._reasoning.normalized_style()
        return self

    def direct(self) -> "ExecutionPlanBuilder":
        """Use direct (single-pass) reasoning style."""
        return self.with_reasoning(style="direct")

    def react(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Use the runtime's react-style reasoning policy."""
        return self.with_reasoning(
            style="react",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def debate(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Use multi-agent debate reasoning style."""
        return self.with_reasoning(
            style="debate",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def advanced(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Alias for :meth:`debate` reasoning style."""
        return self.debate(
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def with_repair_budget(self, repair_budget: int) -> "ExecutionPlanBuilder":
        """Set the maximum number of query repair attempts on empty results."""
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=max(0, int(repair_budget)),
            query_mode=self._reasoning.query_mode,
            fallback_style=self._reasoning.fallback_style,
            reasoning_cycle=dict(self._reasoning.reasoning_cycle),
        )
        return self

    def with_query_mode(self, query_mode: str) -> "ExecutionPlanBuilder":
        """Set the semantic direct-query execution sub-mode."""
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=self._reasoning.repair_budget,
            query_mode=normalize_query_mode(query_mode),
            fallback_style=self._reasoning.fallback_style,
            reasoning_cycle=dict(self._reasoning.reasoning_cycle),
        )
        return self

    def graph_cot(self) -> "ExecutionPlanBuilder":
        """Run the direct semantic path in Graph-CoT mode."""
        return self.with_query_mode("graph_cot")

    def with_entity_overrides(
        self,
        *entity_overrides: EntityOverride | Dict[str, Any],
    ) -> "ExecutionPlanBuilder":
        """Provide entity disambiguation overrides for the query."""
        flattened: List[EntityOverride | Dict[str, Any]] = []
        for item in entity_overrides:
            if isinstance(item, (list, tuple)):
                flattened.extend(item)
            else:
                flattened.append(item)
        self._entity_overrides = [
            self._client._coerce_entity_override(item) for item in flattened
        ]
        return self

    def for_user(self, user_id: str) -> "ExecutionPlanBuilder":
        """Set the user ID for this execution plan."""
        self._user_id = user_id
        return self

    def in_session(self, session_id: str) -> "ExecutionPlanBuilder":
        """Set the session ID for this execution plan."""
        self._session_id = session_id
        return self

    def build(self) -> ExecutionPlan:
        """Build the execution plan without running it.

        Returns:
            An :class:`ExecutionPlan` ready for :meth:`Seocho.execute`.
        """
        return ExecutionPlan(
            query=self._query,
            targets=list(self._targets),
            reasoning=self._reasoning,
            entity_overrides=list(self._entity_overrides),
            user_id=self._user_id,
            session_id=self._session_id,
            workspace_id=self._client.workspace_id,
            ontology_ids=list(self._ontology_ids),
            vocabulary_profiles=list(self._vocabulary_profiles),
        )

    def run(self) -> ExecutionResult:
        """Build and execute the plan in one step.

        Returns:
            An :class:`ExecutionResult` with the answer and execution metadata.
        """
        return self._client.execute(self.build())
