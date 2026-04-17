from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher


@dataclass(slots=True)
class AgentConfig:
    """Small internal config object for query-agent construction."""

    mode: str
    workspace_id: str
    database: str
    ontology_profile: str = "default"
    graph_targets: Optional[Sequence[Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


AgentBuilder = Callable[[AgentConfig], Any]


class AgentFactory:
    """Registry-backed factory for query/runtime agent objects."""

    def __init__(
        self,
        *,
        publisher: Optional[EventPublisher] = None,
    ) -> None:
        self._publisher = publisher or NullEventPublisher()
        self._builders: Dict[str, AgentBuilder] = {}

    def register(self, mode: str, builder: AgentBuilder) -> None:
        key = str(mode).strip().lower()
        if not key:
            raise ValueError("Agent mode must be non-empty.")
        self._builders[key] = builder

    def create(self, config: AgentConfig) -> Any:
        key = str(config.mode).strip().lower()
        builder = self._builders.get(key)
        if builder is None:
            known = ", ".join(sorted(self._builders)) or "none"
            raise ValueError(f"Unsupported mode '{config.mode}'. Registered modes: {known}")

        agent = builder(config)
        self._publisher.publish(
            DomainEvent(
                kind="agent.created",
                workspace_id=config.workspace_id,
                payload={
                    "mode": key,
                    "database": config.database,
                    "ontology_profile": config.ontology_profile,
                },
            )
        )
        return agent

    @classmethod
    def with_semantic_flow(
        cls,
        connector: Any,
        *,
        graph_targets: Optional[Sequence[Any]] = None,
        publisher: Optional[EventPublisher] = None,
    ) -> "AgentFactory":
        """Create a factory preloaded with the canonical semantic-flow builder."""

        factory = cls(publisher=publisher)

        def _build_semantic(config: AgentConfig) -> Any:
            from .semantic_flow import SemanticAgentFlow

            active_targets = config.graph_targets if config.graph_targets is not None else graph_targets
            return SemanticAgentFlow(connector, graph_targets=active_targets)

        factory.register("semantic", _build_semantic)
        return factory
