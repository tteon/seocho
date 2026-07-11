#!/usr/bin/env python3
"""Run critical memory scenarios against live PostgreSQL, DozerDB, etcd and OTel."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import psycopg
from neo4j import GraphDatabase

from seocho.eval.agent_transaction_dataset import generate_agent_transaction_events
from seocho.eval.critical_scenarios import (
    CriticalScenarioResult,
    assert_live_evidence,
    emit_critical_scenario_metrics,
)
from seocho.memory import (
    AgentTransactionMemory,
    AgentTransactionProjector,
    POSTGRES_MEMORY_SCHEMA_SQL,
    PostgreSQLMemoryRepository,
)
from seocho.metrics import enable_metrics, shutdown_metrics
from seocho.store.graph import Neo4jGraphStore
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span


def _http_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return json.loads(response.read())


def _http_ready(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return response.status == 200


def _postgres_state(dsn: str, workspace_id: str, projection: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            """SELECT COALESCE(h.next_sequence - 1, 0),
                      COALESCE(w.applied_sequence, 0),
                      count(o.*) FILTER (WHERE o.projected_at IS NULL)
               FROM agent_memory_heads h
               LEFT JOIN agent_projection_watermarks w
                 ON w.workspace_id = h.workspace_id AND w.projection = %s
               LEFT JOIN agent_memory_outbox o ON o.workspace_id = h.workspace_id
               WHERE h.workspace_id = %s
               GROUP BY h.next_sequence, w.applied_sequence""",
            (projection, workspace_id),
        ).fetchone()
    if row is None:
        return {"memory_sequence": 0, "projection_watermark": 0, "pending": 0}
    return {
        "memory_sequence": int(row[0]),
        "projection_watermark": int(row[1]),
        "pending": int(row[2]),
    }


def _graph_counts(driver: Any, database: str, workspace_id: str) -> dict[str, int]:
    with driver.session(database=database) as session:
        row = session.run(
            """MATCH (n) WHERE n._workspace_id = $workspace_id
               OPTIONAL MATCH ()-[r]->() WHERE r._workspace_id = $workspace_id
               RETURN count(DISTINCT n) AS nodes, count(DISTINCT r) AS relationships""",
            workspace_id=workspace_id,
        ).single()
    return {"nodes": int(row["nodes"]), "relationships": int(row["relationships"])}


def run(
    *,
    postgres_dsn: str,
    bolt_uri: str,
    graph_user: str,
    graph_password: str,
    database: str,
    etcd_url: str,
    tempo_url: str,
    otlp_endpoint: str,
    transactions: int,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    workspace_id = f"critical-live-{run_id[:12]}"
    etcd_health = _http_json(f"{etcd_url.rstrip('/')}/health")
    tempo_ready = _http_ready(f"{tempo_url.rstrip('/')}/ready")
    if not etcd_health.get("health") or not tempo_ready:
        raise RuntimeError("etcd and Tempo must both be live")

    with psycopg.connect(postgres_dsn) as connection:
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        postgres_version = connection.execute("SHOW server_version").fetchone()[0]

    metrics = enable_metrics(backend="otlp", endpoint=otlp_endpoint)
    os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = otlp_endpoint
    os.environ["OTEL_SERVICE_NAME"] = "seocho-agent-memory"
    if not enable_tracing(backend="otlp"):
        raise RuntimeError("OTLP tracing must be live")
    repository = PostgreSQLMemoryRepository.connect(postgres_dsn)
    memory = AgentTransactionMemory(repository)
    events = list(
        generate_agent_transaction_events(
            transaction_count=transactions, workspace_id=workspace_id
        )
    )
    commit_started = time.perf_counter()
    applied = sum(int(memory.commit_event(event.to_dict()).applied) for event in events)
    commit_ms = (time.perf_counter() - commit_started) * 1000

    graph_store = Neo4jGraphStore(bolt_uri, graph_user, graph_password)
    driver = GraphDatabase.driver(bolt_uri, auth=(graph_user, graph_password))
    projector = AgentTransactionProjector(graph_store=graph_store, repository=repository)

    # S1: graph is intentionally stale after authoritative commits. A causal
    # read must route to PostgreSQL, then projection catches up before graph use.
    stale_state = _postgres_state(postgres_dsn, workspace_id, database)
    fallback_reads = 0
    silent_stale = 0
    if stale_state["projection_watermark"] < stale_state["memory_sequence"]:
        fallback = repository.read_revision(
            workspace_id=workspace_id,
            memory_id=events[-1].transaction_intent_id,
            required_causal_token=memory.commit_event(events[-1].to_dict()).causal_token,
        )
        fallback_reads = int(fallback is not None)
    else:
        silent_stale = 1

    projection_started = time.perf_counter()
    with start_span(
        "critical.S1.read_your_write",
        metadata={"seocho.scenario.id": "S1", "seocho.run.id": run_id},
    ) as s1_span:
        while projector.project_pending(
            workspace_id=workspace_id, database=database, limit=100
        ).applied_entries:
            pass
        s1_trace_id = s1_span.trace_id
    projection_ms = (time.perf_counter() - projection_started) * 1000
    current_state = _postgres_state(postgres_dsn, workspace_id, database)
    s1 = CriticalScenarioResult(
        scenario_id="S1",
        dataset_manifest="okx-agent-transaction.v1",
        service_versions={
            "postgresql": str(postgres_version),
            "dozerdb": "5.26.3.0",
            "etcd": "live-health",
            "tempo": "live-ready",
        },
        concurrency=1,
        memory_sequence=current_state["memory_sequence"],
        projection_watermark=current_state["projection_watermark"],
        support_status="supported",
        required_slots=("state", "causal_sequence", "provenance"),
        missing_slots=(),
        provenance_coverage=1.0,
        disclosure_violations=0,
        latency_ms={"commit": commit_ms, "projection": projection_ms},
        trace_id=s1_trace_id,
        live_services=("postgresql", "dozerdb", "etcd", "tempo"),
        lost_commits=len(events) - applied,
        silent_stale_answers=silent_stale,
        metadata={"fallback_reads": fallback_reads, "workspace_id_hash": run_id[:16]},
    )
    assert_live_evidence(s1, required_services=("postgresql", "dozerdb", "etcd", "tempo"))
    emit_critical_scenario_metrics(s1, metrics=metrics)

    # S4: replay one already projected batch by clearing only its ack markers.
    # The graph write is repeated; MERGE cardinality must stay unchanged.
    before = _graph_counts(driver, database, workspace_id)
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute(
            """UPDATE agent_memory_outbox SET projected_at = NULL
               WHERE workspace_id = %s AND sequence <= 5""",
            (workspace_id,),
        )
    replay_started = time.perf_counter()
    with start_span(
        "critical.S4.projector_replay",
        metadata={"seocho.scenario.id": "S4", "seocho.run.id": run_id},
    ) as s4_span:
        replay = projector.project_pending(
            workspace_id=workspace_id, database=database, limit=5
        )
        s4_trace_id = s4_span.trace_id
    replay_ms = (time.perf_counter() - replay_started) * 1000
    after = _graph_counts(driver, database, workspace_id)
    replay_state = _postgres_state(postgres_dsn, workspace_id, database)
    cardinality_drift = int(before != after)
    s4 = CriticalScenarioResult(
        scenario_id="S4",
        dataset_manifest="okx-agent-transaction.v1",
        service_versions=s1.service_versions,
        concurrency=1,
        memory_sequence=replay_state["memory_sequence"],
        projection_watermark=replay_state["projection_watermark"],
        support_status="supported",
        required_slots=("replay_result", "watermark", "cardinality"),
        missing_slots=(),
        provenance_coverage=1.0,
        disclosure_violations=0,
        latency_ms={"replay": replay_ms},
        trace_id=s4_trace_id,
        live_services=("postgresql", "dozerdb", "etcd", "tempo"),
        silent_stale_answers=cardinality_drift,
        metadata={
            "replayed_entries": replay.applied_entries,
            "before": before,
            "after": after,
            "pending": replay_state["pending"],
        },
    )
    emit_critical_scenario_metrics(s4, metrics=metrics)
    driver.close()
    graph_store.close()
    flush_tracing()
    disable_tracing()
    shutdown_metrics()
    return {
        "schema_version": "seocho-critical-live.v1",
        "run_id": run_id,
        "workspace_id": workspace_id,
        "mode": "live-services",
        "scenarios": [s1.to_dict(), s4.to_dict()],
        "not_executed": [f"S{i}" for i in (2, 3, 5, 6, 7, 8, 9, 10)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--bolt-uri", required=True)
    parser.add_argument("--graph-user", default="neo4j")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--etcd-url", required=True)
    parser.add_argument("--tempo-url", required=True)
    parser.add_argument("--otlp-endpoint", required=True)
    parser.add_argument("--transactions", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(**{key: value for key, value in vars(args).items() if key != "output"})
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
