"""Load a minimal FIBO-lite corpus for GOPTS F4 live PROFILE oracle runs.

ADR-0097 F4 (seocho-75jf): the Layer-1 ranking-quality harness ships
``make_profile_oracle_fn`` but needs a populated DozerDB to produce real
oracle data. This script writes a small workspace-scoped corpus that
exercises every cypher_shape registered in seocho/query/pattern_catalog.py
so the harness has something meaningful to PROFILE.

The corpus is idempotent via MERGE — running the script twice is a
no-op. All nodes and relationships are stamped with
``_workspace_id = "fixture-gopts"`` to match the YAML fixtures under
``seocho/tests/fixtures/gopts/``.

Usage::

    NEO4J_PASSWORD=... python -m scripts.eval.load_gopts_fibo_corpus
    # or, from a session that already loaded .env:
    python scripts/eval/load_gopts_fibo_corpus.py

The script connects to whichever DozerDB the standard SEOCHO env vars
point at (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Iterable, List, Sequence

WORKSPACE_ID = "fixture-gopts"
DATABASE = os.environ.get("SEOCHO_GOPTS_DATABASE", "neo4j")


# ---------------------------------------------------------------------------
# Static corpus — every node carries _workspace_id; relationships too.
# ---------------------------------------------------------------------------


_COMPANIES: Sequence[Dict[str, Any]] = [
    {"name": "Apple"},
    {"name": "Microsoft"},
    {"name": "Tesla"},
    {"name": "TSMC"},
    {"name": "Foxconn"},
]

# NOTE on FinancialMetric: the pre-existing DozerDB schema in this
# repo's compose stack has uniqueness constraints on FinancialMetric.name
# (and on Revenue/NetIncome/etc subclasses) which prevent multiple metric
# nodes from sharing a metric name like "revenue". F4 sidesteps that by
# not loading metric data — fixtures 05/06 (finance_metric_lookup,
# finance_metric_delta) get filtered from the live Layer-1 run via the
# integration test's fixture filter. They stay in the YAML suite so
# Layer-1 in mock-oracle mode (test_gopts_ranking) still exercises them.
_FINANCIAL_METRICS: Sequence[Dict[str, Any]] = ()

_ENTITIES: Sequence[Dict[str, Any]] = [
    {"name": "Apple"},
    {"name": "TSMC"},
    {"name": "Foxconn"},
]

_PEOPLE: Sequence[Dict[str, Any]] = [
    {"name": "Tim Cook"},
]

# (source_label, source_name, rel_type, target_label, target_name)
_RELATIONSHIPS: Sequence[Dict[str, Any]] = [
    {
        "source_label": "Person",
        "source_name": "Tim Cook",
        "rel_type": "MANAGES",
        "target_label": "Entity",
        "target_name": "Apple",
    },
    {
        "source_label": "Entity",
        "source_name": "Apple",
        "rel_type": "RELATES_TO",
        "target_label": "Entity",
        "target_name": "TSMC",
    },
    {
        "source_label": "Entity",
        "source_name": "TSMC",
        "rel_type": "RELATES_TO",
        "target_label": "Entity",
        "target_name": "Foxconn",
    },
]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load(driver: Any, *, database: str = DATABASE, workspace_id: str = WORKSPACE_ID) -> Dict[str, int]:
    """Write the static corpus idempotently. Returns counts per category."""
    counts: Dict[str, int] = {
        "companies": 0,
        "financial_metrics": 0,
        "entities": 0,
        "people": 0,
        "relationships": 0,
        "indexes": 0,
    }
    with driver.session(database=database) as session:
        # Indexes — cost ranker reads these via SHOW INDEXES.
        for stmt in _INDEX_STATEMENTS:
            session.run(stmt)
            counts["indexes"] += 1

        for company in _COMPANIES:
            session.run(
                """
                MERGE (c:Company {name: $name})
                ON CREATE SET c._workspace_id = $workspace_id, c._created = timestamp()
                ON MATCH  SET c._workspace_id = $workspace_id
                """,
                name=company["name"],
                workspace_id=workspace_id,
            )
            counts["companies"] += 1

        for metric in _FINANCIAL_METRICS:
            session.run(
                """
                MATCH (c:Company {name: $company})
                MERGE (m:FinancialMetric {name: $name, year: $year, _company: $company})
                ON CREATE SET
                    m._workspace_id = $workspace_id,
                    m.value = $value,
                    m._created = timestamp()
                ON MATCH SET
                    m._workspace_id = $workspace_id,
                    m.value = $value
                MERGE (c)-[r:REPORTED]->(m)
                ON CREATE SET r._workspace_id = $workspace_id
                ON MATCH  SET r._workspace_id = $workspace_id
                """,
                company=metric["company"],
                name=metric["name"],
                year=metric["year"],
                value=metric["value"],
                workspace_id=workspace_id,
            )
            counts["financial_metrics"] += 1

        for entity in _ENTITIES:
            session.run(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET e._workspace_id = $workspace_id, e._created = timestamp()
                ON MATCH  SET e._workspace_id = $workspace_id
                """,
                name=entity["name"],
                workspace_id=workspace_id,
            )
            counts["entities"] += 1

        for person in _PEOPLE:
            session.run(
                """
                MERGE (p:Person {name: $name})
                ON CREATE SET p._workspace_id = $workspace_id, p._created = timestamp()
                ON MATCH  SET p._workspace_id = $workspace_id
                """,
                name=person["name"],
                workspace_id=workspace_id,
            )
            counts["people"] += 1

        for rel in _RELATIONSHIPS:
            cypher = (
                f"MATCH (s:{rel['source_label']} {{name: $source_name}}) "
                f"MATCH (t:{rel['target_label']} {{name: $target_name}}) "
                f"MERGE (s)-[r:{rel['rel_type']}]->(t) "
                "ON CREATE SET r._workspace_id = $workspace_id "
                "ON MATCH  SET r._workspace_id = $workspace_id"
            )
            session.run(
                cypher,
                source_name=rel["source_name"],
                target_name=rel["target_name"],
                workspace_id=workspace_id,
            )
            counts["relationships"] += 1

    return counts


def teardown(driver: Any, *, database: str = DATABASE, workspace_id: str = WORKSPACE_ID) -> None:
    """Best-effort cleanup of every node/rel stamped with ``workspace_id``."""
    with driver.session(database=database) as session:
        # Relationships first (they reference nodes).
        session.run(
            "MATCH ()-[r]->() WHERE r._workspace_id = $workspace_id "
            "WITH r LIMIT 10000 DELETE r",
            workspace_id=workspace_id,
        )
        session.run(
            "MATCH (n) WHERE n._workspace_id = $workspace_id "
            "WITH n LIMIT 10000 DETACH DELETE n",
            workspace_id=workspace_id,
        )


# Workspace-aware indexes. The cost ranker reads SHOW INDEXES and prefers
# patterns whose required_labels have a matching index — without these the
# Layer-1 PROFILE oracle would always pick full-scan plans.
_INDEX_STATEMENTS: List[str] = [
    "CREATE INDEX gopts_company_name IF NOT EXISTS FOR (c:Company) ON (c.name)",
    "CREATE INDEX gopts_entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE INDEX gopts_person_name IF NOT EXISTS FOR (p:Person) ON (p.name)",
]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _open_driver_from_env() -> Any:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - exercised in operator env
        raise SystemExit(
            "load_gopts_fibo_corpus requires the 'neo4j' package."
        ) from exc

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit("NEO4J_PASSWORD env var is required.")
    return GraphDatabase.driver(uri, auth=(user, password))


def main(argv: Iterable[str] = ()) -> int:
    args = list(argv)
    teardown_only = "--teardown" in args

    driver = _open_driver_from_env()
    try:
        if teardown_only:
            teardown(driver)
            print(f"[gopts] cleared workspace_id={WORKSPACE_ID!r} from db={DATABASE!r}")
            return 0
        counts = load(driver)
        print(f"[gopts] loaded workspace_id={WORKSPACE_ID!r} into db={DATABASE!r}")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
