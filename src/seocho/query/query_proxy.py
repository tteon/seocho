from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol

from seocho.events import DomainEvent, EventPublisher, NullEventPublisher
from seocho.metrics import get_metrics
from seocho.tracing import capture_text, start_span


def _env_enforce_workspace_filter() -> bool:
    """Default for tenant-isolation enforcement at the query boundary.

    Off by default (preserves behaviour); multi-tenant deployments set
    ``SEOCHO_ENFORCE_WORKSPACE_FILTER=1`` to make the runtime refuse any query
    whose Cypher does not scope to ``$workspace_id``.
    """
    return os.getenv("SEOCHO_ENFORCE_WORKSPACE_FILTER", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _env_non_negative_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _env_non_negative_float(name: str, default: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


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


class QueryAdmissionRejected(RuntimeError):
    """Raised when a bounded query executor has no capacity before deadline."""


class QueryAdmissionController:
    """Process-local concurrency gate for graph queries.

    Deployments normally share one controller across every ``QueryProxy`` in a
    worker. A zero limit disables admission control for backward compatibility.
    Cross-instance capacity remains an infrastructure concern (pool sizing and
    autoscaling), while this gate prevents one worker from flooding the graph.
    """

    def __init__(self, max_inflight: int = 0, wait_seconds: float = 0.0) -> None:
        if max_inflight < 0:
            raise ValueError("max_inflight must be non-negative")
        if wait_seconds < 0:
            raise ValueError("wait_seconds must be non-negative")
        self.max_inflight = max_inflight
        self.wait_seconds = wait_seconds
        self._semaphore = (
            threading.BoundedSemaphore(max_inflight) if max_inflight else None
        )

    def acquire(self) -> bool:
        if self._semaphore is None:
            return True
        return self._semaphore.acquire(timeout=self.wait_seconds)

    def release(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()


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
        enforce_workspace_filter: Optional[bool] = None,
        admission_controller: Optional[QueryAdmissionController] = None,
    ) -> None:
        self._graph_store = graph_store
        self._publisher = publisher or NullEventPublisher()
        self._policy = policy or NullQueryPolicy()
        # None -> take the deployment default from the environment.
        self._enforce_workspace_filter = (
            _env_enforce_workspace_filter()
            if enforce_workspace_filter is None
            else enforce_workspace_filter
        )
        self._admission = admission_controller or QueryAdmissionController(
            max_inflight=_env_non_negative_int("SEOCHO_GRAPH_QUERY_MAX_INFLIGHT"),
            wait_seconds=_env_non_negative_float(
                "SEOCHO_GRAPH_QUERY_ADMISSION_WAIT_SECONDS"
            ),
        )

    def query(self, request: QueryRequest) -> list[Dict[str, Any]]:
        metric_started = time.perf_counter()
        metrics = get_metrics()
        params = dict(request.params or {})
        self._policy.validate_query(
            cypher=request.cypher,
            workspace_id=request.workspace_id,
            ontology_profile=request.ontology_profile,
            database=request.database,
            params=params,
        )
        statement = capture_text(request.cypher)
        input_data = {"db.statement": statement} if statement is not None else None
        metadata = {
            "db.system": "neo4j",
            "db.name": request.database,
            "db.operation.name": "query",
            "seocho.workspace_hash": hashlib.sha256(
                request.workspace_id.encode("utf-8")
            ).hexdigest()[:16],
            "seocho.ontology_profile": request.ontology_profile,
            "seocho.query.template_hash": hashlib.sha256(
                request.cypher.encode("utf-8")
            ).hexdigest()[:16],
            "seocho.query.workspace_filter_enforced": self._enforce_workspace_filter,
            "seocho.query.max_inflight": self._admission.max_inflight,
        }
        admitted = self._admission.acquire()
        if not admitted:
            metrics.add(
                "seocho.retrieval.admission_rejection.count",
                attributes={"source": "neo4j", "reason": "capacity"},
            )
            self._publisher.publish(
                DomainEvent(
                    kind="query.rejected",
                    workspace_id=request.workspace_id,
                    payload={
                        "database": request.database,
                        "ontology_profile": request.ontology_profile,
                        "reason": "capacity",
                    },
                )
            )
            raise QueryAdmissionRejected("graph query capacity exhausted")
        metrics.add(
            "seocho.retrieval.inflight",
            attributes={"source": "neo4j"},
        )
        try:
            with start_span(
                "db.query",
                input_data=input_data,
                metadata=metadata,
                tags=["query", "graph"],
            ) as span:
                if self._enforce_workspace_filter:
                # Tenant-isolation enforced: thread workspace_id (auto-merged
                # into params by the store) and refuse Cypher that doesn't scope
                # to $workspace_id. Only passed when enabled so the default path
                # keeps the exact call shape backends/test-doubles already accept.
                    raw_records = self._graph_store.query(
                        request.cypher,
                        params=params,
                        database=request.database,
                        workspace_id=request.workspace_id,
                        enforce_workspace_filter=True,
                    )
                else:
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
                span.set_output(**{"db.rows_returned": len(records)})
        except Exception as exc:
            metrics.record(
                "seocho.retrieval.duration",
                time.perf_counter() - metric_started,
                {"source": "neo4j", "outcome": "error"},
            )
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
        finally:
            metrics.add(
                "seocho.retrieval.inflight",
                -1,
                {"source": "neo4j"},
            )
            self._admission.release()

        metrics.record(
            "seocho.retrieval.duration",
            time.perf_counter() - metric_started,
            {"source": "neo4j", "outcome": "success"},
        )
        metrics.record(
            "seocho.retrieval.candidate_count",
            len(records),
            {"source": "neo4j"},
        )
        metrics.record(
            "seocho.retrieval.selected_count",
            len(records),
            {"source": "neo4j"},
        )

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
