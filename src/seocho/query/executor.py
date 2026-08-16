from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .contracts import QueryExecution, QueryPlan

logger = logging.getLogger(__name__)


class GraphQueryExecutor:
    """Canonical graph query executor for local SDK and adapter runtimes."""

    def __init__(self, *, graph_store: Any, database: str) -> None:
        self.graph_store = graph_store
        self.database = database

    def explain(self, plan: QueryPlan) -> Optional[Dict[str, Any]]:
        """Return the query's plan tree without executing it.

        Advisory: a backend that cannot EXPLAIN returns None, and the caller
        treats that as "no signal" rather than as a bad plan.
        """
        explainer = getattr(self.graph_store, "explain_plan", None)
        if explainer is None:
            return None
        try:
            return explainer(plan.cypher, params=plan.params, database=self.database)
        except Exception:  # noqa: BLE001 — never let a planning probe fail a query
            return None

    def execute(self, plan: QueryPlan) -> QueryExecution:
        try:
            records = self.graph_store.query(
                plan.cypher,
                params=plan.params,
                database=self.database,
            )
            return QueryExecution(
                cypher=plan.cypher,
                params=dict(plan.params),
                records=list(records),
            )
        except Exception as exc:
            logger.error("Cypher execution failed: %s — query: %s", exc, plan.cypher)
            return QueryExecution(
                cypher=plan.cypher,
                params=dict(plan.params),
                records=[],
                error=f"The query could not be executed: {exc}",
            )

