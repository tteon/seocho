from __future__ import annotations

import json
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


class QueryExecutionError(RuntimeError):
    """Raised when a graph backend violates the typed query/result contract."""

    def __init__(
        self,
        *,
        database: str,
        cypher: str,
        source: str,
        detail: str,
    ) -> None:
        self.database = database
        self.cypher = cypher
        self.source = source
        self.detail = detail
        super().__init__(f"{source} query failed for '{database}': {detail}")


def coerce_query_records(
    raw: Any,
    *,
    database: str,
    cypher: str,
    source: str,
) -> list[Dict[str, Any]]:
    """Normalize connector/graph payloads onto the typed query-record contract."""

    if raw in (None, "", []):
        return []

    parsed: Any
    if isinstance(raw, list):
        parsed = raw
    elif isinstance(raw, str):
        if raw.startswith("Error"):
            raise QueryExecutionError(
                database=database,
                cypher=cypher,
                source=source,
                detail=raw,
            )
        try:
            parsed = json.loads(raw)
        except Exception as exc:  # pragma: no cover - exercised via semantic/runtime paths
            raise QueryExecutionError(
                database=database,
                cypher=cypher,
                source=source,
                detail=f"non-json payload: {str(raw)[:160]}",
            ) from exc
    else:
        raise QueryExecutionError(
            database=database,
            cypher=cypher,
            source=source,
            detail=f"unsupported payload type: {type(raw).__name__}",
        )

    if not isinstance(parsed, list):
        raise QueryExecutionError(
            database=database,
            cypher=cypher,
            source=source,
            detail=f"expected list payload, got {type(parsed).__name__}",
        )

    rows: list[Dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append({"value": item})
    return rows


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
            raw_records = self._graph_store.query(
                request.cypher,
                params=params,
                database=request.database,
            )
            records = coerce_query_records(
                raw_records,
                database=request.database,
                cypher=request.cypher,
                source=type(self._graph_store).__name__,
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
