"""Knowledge graph access: a graph database and three queries, nothing else.

Every query is logged verbatim with its parameters and row count, so a reader
can re-issue it by hand against the same database and get the same rows. That
property is the whole reason this module exists rather than a framework call.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Sequence

FACT_LABELS = ("MonetaryAmount", "CashFlow")

# Kept as named constants so a run's trace can be matched against the exact text
# that produced it. elementId is used rather than the deprecated id().
FACTS_FOR_CASE = """
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN $fact_labels)
  AND n.id IS NOT NULL AND n.workspace_id ENDS WITH $case
RETURN elementId(n) AS node_id, n.id AS slug,
       coalesce(n.amount, n.value, '') AS amount,
       coalesce(n.currency, '') AS unit,
       coalesce(n.period, '') AS period,
       n.workspace_id AS workspace, labels(n) AS labels
ORDER BY slug
"""

ENTITIES_FOR_CASE = """
MATCH (n)
WHERE n.name IS NOT NULL AND n.workspace_id ENDS WITH $case
  AND NOT any(l IN labels(n) WHERE l IN ['Chunk', 'Document', 'Section'])
RETURN elementId(n) AS node_id, n.name AS name, labels(n) AS labels
ORDER BY name
LIMIT $limit
"""

NEIGHBORHOOD = """
MATCH (n)-[r]-(m)
WHERE elementId(n) = $node_id AND m.name IS NOT NULL
RETURN type(r) AS rel, m.name AS neighbor, labels(m) AS labels
ORDER BY rel, neighbor
LIMIT $limit
"""


@dataclass(frozen=True)
class Fact:
    node_id: str
    slug: str
    raw: str
    unit: str
    period: str
    view: str
    labels: tuple[str, ...]

    @property
    def key(self) -> str:
        return re.sub(r"[^a-z0-9]+", "_", self.slug.strip().lower()).strip("_")


class KnowledgeGraph:
    """One graph database, one view per database name."""

    def __init__(self, uri: str, views: dict[str, str], run: Any) -> None:
        from neo4j import GraphDatabase

        password = os.environ.get("NEO4J_PASSWORD")
        if not password:
            raise SystemExit("NEO4J_PASSWORD is not set; refusing to guess credentials")
        self._driver = GraphDatabase.driver(
            uri, auth=(os.getenv("NEO4J_USER", "neo4j"), password))
        self.views = dict(views)
        self._run = run

    def close(self) -> None:
        self._driver.close()

    def _query(self, view: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
        database = self.views[view]
        with self._driver.session(database=database) as session:
            rows = [dict(r) for r in session.run(cypher, **params)]
        self._run.log(f"    cypher view={view} db={database} params={params} "
                      f"-> {len(rows)} rows")
        return rows

    def facts(self, view: str, case: str) -> list[Fact]:
        rows = self._query(view, FACTS_FOR_CASE, case=case, fact_labels=list(FACT_LABELS))
        return [Fact(node_id=r["node_id"], slug=str(r["slug"]),
                     raw=str(r["amount"]).strip(), unit=str(r["unit"]).strip().lower(),
                     period=str(r["period"]).strip(), view=view,
                     labels=tuple(r["labels"])) for r in rows]

    def entities(self, view: str, case: str, limit: int = 200) -> list[dict[str, Any]]:
        return self._query(view, ENTITIES_FOR_CASE, case=case, limit=limit)

    def neighborhood(self, view: str, node_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._query(view, NEIGHBORHOOD, node_id=node_id, limit=limit)

    def available_views(self) -> Sequence[str]:
        return tuple(self.views)
