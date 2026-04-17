from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, List, Mapping, Protocol
import uuid


@dataclass(slots=True)
class DomainEvent:
    """Small event envelope for internal orchestration seams."""

    kind: str
    payload: Mapping[str, Any]
    workspace_id: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts_ms: int = field(default_factory=lambda: int(time() * 1000))


class EventPublisher(Protocol):
    """Protocol for thin event publication backends."""

    def publish(self, event: DomainEvent) -> None:
        ...


class NullEventPublisher:
    """Default publisher for paths that do not emit real traces yet."""

    def publish(self, event: DomainEvent) -> None:  # noqa: ARG002
        return None


class InMemoryEventPublisher:
    """Test helper that records published events in memory."""

    def __init__(self) -> None:
        self.events: List[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
