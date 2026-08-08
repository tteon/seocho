"""Deterministic pre-routing for bounded multi-graph debate fan-out."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Mapping


_TOKENS = re.compile(r"[A-Za-z0-9_]{3,}")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    selected_graph_ids: list[str]
    skipped_graphs: dict[str, str]
    scores: dict[str, float]


class GraphCircuitBreaker:
    """Small process-local breaker; readiness can surface its state to callers."""

    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 300.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def is_open(self, graph_id: str) -> bool:
        with self._lock:
            failures, opened_at = self._state.get(graph_id, (0, 0.0))
            return failures >= self.failure_threshold and time.monotonic() - opened_at < self.cooldown_seconds

    def record_success(self, graph_id: str) -> None:
        with self._lock:
            self._state.pop(graph_id, None)

    def record_failure(self, graph_id: str) -> None:
        with self._lock:
            failures, opened_at = self._state.get(graph_id, (0, 0.0))
            failures += 1
            self._state[graph_id] = (failures, time.monotonic() if failures >= self.failure_threshold else opened_at)


class FulltextRelevanceProbe:
    """Add existing Neo4j full-text evidence to the metadata relevance score.

    Routing must never make a graph unavailable merely because its optional
    full-text index has not been created yet.  Any query or driver failure
    therefore falls back to the deterministic metadata score.
    """

    def __init__(self, db_manager: Any, *, index_name: str = "entity_fulltext") -> None:
        self._db_manager = db_manager
        self._index_name = index_name

    def score(self, query: str, graph_id: str, agent: Any) -> float:
        fallback = GraphRouter._default_score(query, graph_id, agent)
        database = str(getattr(agent, "graph_database", "") or graph_id)
        try:
            with self._db_manager.driver.session(database=database) as session:
                record = session.run(
                    """
                    CALL db.index.fulltext.queryNodes($index_name, $fulltext_query)
                    YIELD node
                    RETURN count(node) AS hits
                    """,
                    index_name=self._index_name,
                    fulltext_query=query,
                ).single()
            hits = int(record["hits"]) if record is not None else 0
            return fallback + float(hits)
        except Exception:
            return fallback


class GraphRouter:
    """Route ready graphs by a no-LLM relevance score and deterministic caps."""

    def __init__(
        self,
        *,
        score_graph: Callable[[str, str, Any], float] | None = None,
        circuit_breaker: GraphCircuitBreaker | None = None,
    ) -> None:
        self._score_graph = score_graph or self._default_score
        self.circuit_breaker = circuit_breaker or GraphCircuitBreaker()

    def route(
        self,
        *,
        query: str,
        agents: Mapping[str, Any],
        readiness: list[Mapping[str, Any]] | None = None,
        top_k: int = 3,
        strict: bool = False,
    ) -> RouteDecision:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        readiness_by_graph = {str(item.get("graph", "")): str(item.get("status", "ready")) for item in readiness or []}
        candidates: list[tuple[str, float]] = []
        skipped: dict[str, str] = {}
        for graph_id, agent in agents.items():
            status = readiness_by_graph.get(graph_id, "ready")
            if status == "blocked":
                skipped[graph_id] = "readiness_blocked"
                continue
            if self.circuit_breaker.is_open(graph_id):
                skipped[graph_id] = "circuit_open"
                continue
            score = float(self._score_graph(query, graph_id, agent))
            if status == "degraded":
                score -= 0.25
            candidates.append((graph_id, score))
        candidates.sort(key=lambda item: (-item[1], item[0]))
        passing = [(graph_id, score) for graph_id, score in candidates if score > 0]
        if not passing and candidates and not strict:
            passing = candidates[:1]
            skipped[passing[0][0]] = "fallback_best_score"
        selected = [graph_id for graph_id, _ in passing[:top_k]]
        for graph_id, _ in candidates:
            if graph_id not in selected and graph_id not in skipped:
                skipped[graph_id] = "below_relevance_threshold" if graph_id not in [g for g, _ in passing] else "top_k_cap"
        return RouteDecision(selected, skipped, dict(candidates))

    @staticmethod
    def _default_score(query: str, graph_id: str, agent: Any) -> float:
        """Score ontology/graph metadata when an indexed probe is not configured."""

        question = set(token.lower() for token in _TOKENS.findall(query))
        metadata = " ".join(
            str(value)
            for value in (
                graph_id,
                getattr(agent, "graph_database", ""),
                getattr(agent, "ontology_id", ""),
                getattr(agent, "description", ""),
                getattr(agent, "ontology_hints", ""),
            )
        ).lower()
        return float(sum(token in metadata for token in question))


__all__ = ["FulltextRelevanceProbe", "GraphCircuitBreaker", "GraphRouter", "RouteDecision"]
