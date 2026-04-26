from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher


class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


@dataclass(slots=True)
class AgentStateMachine:
    """Explicit runtime state transitions for agent availability policy.

    Phase 3 composes the state with the ontology context hash check so the
    degraded-on-skew rule lives in exactly one chokepoint. ``can_query_graph``
    requires both ``status == READY`` AND ``ontology_context_skew is None``.
    """

    workspace_id: str = "default"
    publisher: EventPublisher = field(default_factory=NullEventPublisher)
    status: AgentStatus = AgentStatus.INITIALIZING
    reason: str = ""
    ontology_context_skew: Optional[Dict[str, Any]] = None

    def mark_ready(self) -> None:
        self._transition(AgentStatus.READY, "")

    def mark_degraded(self, reason: str) -> None:
        self._transition(AgentStatus.DEGRADED, reason)

    def mark_blocked(self, reason: str) -> None:
        self._transition(AgentStatus.BLOCKED, reason)

    def set_ontology_context_skew(self, skew: Optional[Dict[str, Any]]) -> None:
        """Attach (or clear) ontology context hash drift evidence.

        When non-None, ``can_query_graph`` will refuse even if the state
        machine is READY. ``can_answer`` continues to allow synthesis from
        peer agents (debate orchestration can still produce a response from
        the unaffected graphs).
        """
        self.ontology_context_skew = skew

    def can_answer(self) -> bool:
        return self.status in {AgentStatus.READY, AgentStatus.DEGRADED}

    def can_query_graph(self) -> bool:
        if self.ontology_context_skew is not None:
            return False
        return self.status == AgentStatus.READY

    def _transition(self, next_status: AgentStatus, reason: str) -> None:
        previous = self.status
        self.status = next_status
        self.reason = reason
        self.publisher.publish(
            DomainEvent(
                kind="agent.state.changed",
                workspace_id=self.workspace_id,
                payload={
                    "from": previous.value,
                    "to": next_status.value,
                    "reason": reason,
                },
            )
        )
