from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher


class QueryPolicy(Protocol):
    """Validation hook before Cypher reaches the graph backend."""

    def validate_query(
        self,
        *,
        cypher: str,
        workspace_id: str,
        ontology_profile: str,
        database: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        ...


class NullQueryPolicy:
    """No-op default policy for local/internal call paths."""

    def validate_query(
        self,
        *,
        cypher: str,
        workspace_id: str,
        ontology_profile: str,
        database: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:  # noqa: ARG002
        return None


@dataclass(slots=True)
class QueryRequest:
    """Internal request object for graph-query orchestration."""

    cypher: str
    workspace_id: str
    database: str = "neo4j"
    ontology_profile: str = "default"
    params: Optional[Mapping[str, Any]] = None


class QueryProxy:
    """Read-only graph query proxy with policy and event hooks."""

    def __init__(
        self,
        graph_store: Any,
        *,
        publisher: Optional[EventPublisher] = None,
        policy: Optional[QueryPolicy] = None,
    ) -> None:
        self._graph_store = graph_store
        self._publisher = publisher or NullEventPublisher()
        self._policy = policy or NullQueryPolicy()

    def query(self, request: QueryRequest) -> list[Dict[str, Any]]:
        params = dict(request.params or {})
        self._policy.validate_query(
            cypher=request.cypher,
            workspace_id=request.workspace_id,
            ontology_profile=request.ontology_profile,
            database=request.database,
            params=params,
        )
        try:
            records = self._graph_store.query(
                request.cypher,
                params=params,
                database=request.database,
            )
        except Exception as exc:
            self._publisher.publish(
                DomainEvent(
                    kind="query.failed",
                    workspace_id=request.workspace_id,
                    payload={
                        "database": request.database,
                        "ontology_profile": request.ontology_profile,
                        "cypher": request.cypher,
                        "error": str(exc),
                    },
                )
            )
            raise

        self._publisher.publish(
            DomainEvent(
                kind="query.succeeded",
                workspace_id=request.workspace_id,
                payload={
                    "database": request.database,
                    "ontology_profile": request.ontology_profile,
                    "cypher": request.cypher,
                    "result_count": len(records),
                },
            )
        )
        return records
