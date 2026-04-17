from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher


class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


@dataclass(slots=True)
class AgentStateMachine:
    """Explicit runtime state transitions for agent availability policy."""

    workspace_id: str = "default"
    publisher: EventPublisher = field(default_factory=NullEventPublisher)
    status: AgentStatus = AgentStatus.INITIALIZING
    reason: str = ""

    def mark_ready(self) -> None:
        self._transition(AgentStatus.READY, "")

    def mark_degraded(self, reason: str) -> None:
        self._transition(AgentStatus.DEGRADED, reason)

    def mark_blocked(self, reason: str) -> None:
        self._transition(AgentStatus.BLOCKED, reason)

    def can_answer(self) -> bool:
        return self.status in {AgentStatus.READY, AgentStatus.DEGRADED}

    def can_query_graph(self) -> bool:
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
