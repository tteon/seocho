from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher


@dataclass(slots=True)
class IngestRequest:
    """Stable orchestration request for local/runtime indexing entrypoints."""

    content: str
    workspace_id: str
    database: str = "neo4j"
    category: str = "memory"
    metadata: Optional[Dict[str, Any]] = None
    strict_validation: bool = False
    source_id: Optional[str] = None


class IngestionFacade:
    """Thin facade over ``IndexingPipeline`` with event publication hooks."""

    def __init__(
        self,
        indexing_pipeline: Any,
        *,
        publisher: Optional[EventPublisher] = None,
    ) -> None:
        self._indexing_pipeline = indexing_pipeline
        self._publisher = publisher or NullEventPublisher()

    def ingest(self, request: IngestRequest) -> Any:
        self._publisher.publish(
            DomainEvent(
                kind="ingest.started",
                workspace_id=request.workspace_id,
                payload={
                    "database": request.database,
                    "category": request.category,
                    "source_id": request.source_id,
                },
            )
        )

        original_strict = getattr(self._indexing_pipeline, "strict_validation", None)
        if original_strict is not None:
            self._indexing_pipeline.strict_validation = bool(request.strict_validation)

        result = self._indexing_pipeline.index(
            request.content,
            database=request.database,
            category=request.category,
            metadata=request.metadata,
        )

        if original_strict is not None:
            self._indexing_pipeline.strict_validation = original_strict

        payload = result.to_dict() if hasattr(result, "to_dict") else {"result": result}
        self._publisher.publish(
            DomainEvent(
                kind="ingest.finished",
                workspace_id=request.workspace_id,
                payload=payload,
            )
        )
        return result
