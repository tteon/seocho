#!/usr/bin/env python3
"""Live PostgreSQL outbox to DozerDB projection and traversal benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from neo4j import GraphDatabase

from seocho.memory import AgentTransactionProjector, PostgreSQLMemoryRepository
from seocho.store.graph import Neo4jGraphStore


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * 0.95)] if ordered else 0.0


def run(
    *,
    postgres_dsn: str,
    bolt_uri: str,
    graph_user: str,
    graph_password: str,
    workspace_id: str,
    database: str,
    batch_size: int,
) -> dict:
    repository = PostgreSQLMemoryRepository.connect(postgres_dsn)
    graph_store = Neo4jGraphStore(bolt_uri, graph_user, graph_password)
    projector = AgentTransactionProjector(
        graph_store=graph_store, repository=repository
    )
    batches = 0
    projected_entries = 0
    projection_latencies = []
    started = time.perf_counter()
    while True:
        batch_started = time.perf_counter()
        result = projector.project_pending(
            workspace_id=workspace_id, database=database, limit=batch_size
        )
        if result.applied_entries == 0:
            break
        projection_latencies.append((time.perf_counter() - batch_started) * 1000)
        batches += 1
        projected_entries += result.applied_entries
    projection_elapsed = time.perf_counter() - started

    driver = GraphDatabase.driver(bolt_uri, auth=(graph_user, graph_password))
    with driver.session(database=database) as session:
        node_count = session.run(
            "MATCH (n) WHERE n._workspace_id = $workspace_id RETURN count(n) AS c",
            workspace_id=workspace_id,
        ).single()["c"]
        relationship_count = session.run(
            "MATCH ()-[r]->() WHERE r._workspace_id = $workspace_id RETURN count(r) AS c",
            workspace_id=workspace_id,
        ).single()["c"]
        traversal = []
        for hops in range(1, 5):
            latencies = []
            result_count = 0
            cypher = (
                "MATCH (s {id: 'strategy_agent'})"
                f"-[:HANDED_OFF_TO*{hops}]->(n) "
                "WHERE s._workspace_id = $workspace_id "
                "AND n._workspace_id = $workspace_id "
                "RETURN count(DISTINCT n) AS c"
            )
            for _ in range(11):
                query_started = time.perf_counter()
                result_count = session.run(
                    cypher, workspace_id=workspace_id
                ).single()["c"]
                latencies.append((time.perf_counter() - query_started) * 1000)
            measured = latencies[1:]
            traversal.append(
                {
                    "exact_hops": hops,
                    "distinct_results": result_count,
                    "latency_ms": {
                        "mean": round(statistics.fmean(measured), 3),
                        "p95": round(_p95(measured), 3),
                    },
                }
            )
    driver.close()
    graph_store.close()
    return {
        "schema_version": "okx-agent-projection-live.v1",
        "mode": "live-postgresql-dozerdb",
        "workspace_id": workspace_id,
        "projected_entries": projected_entries,
        "batches": batches,
        "batch_size": batch_size,
        "projection_seconds": round(projection_elapsed, 6),
        "projection_entries_per_second": round(
            projected_entries / projection_elapsed, 2
        ),
        "projection_batch_latency_ms": {
            "mean": round(statistics.fmean(projection_latencies), 3)
            if projection_latencies
            else 0.0,
            "p95": round(_p95(projection_latencies), 3),
        },
        "graph": {"nodes": node_count, "relationships": relationship_count},
        "exact_hop_traversal": traversal,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--bolt-uri", required=True)
    parser.add_argument("--graph-user", default="neo4j")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--workspace-id", default="okx-agent-exchange-eval")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(
        postgres_dsn=args.postgres_dsn,
        bolt_uri=args.bolt_uri,
        graph_user=args.graph_user,
        graph_password=args.graph_password,
        workspace_id=args.workspace_id,
        database=args.database,
        batch_size=args.batch_size,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
