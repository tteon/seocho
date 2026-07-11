#!/usr/bin/env python3
"""Load exchange-calibrated lifecycles into live authoritative PostgreSQL."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

from seocho.eval.exchange_calibrated import generate_exchange_calibrated_events
from seocho.memory import POSTGRES_MEMORY_SCHEMA_SQL, PostgreSQLMemoryRepository


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percentile)] if ordered else 0.0


def run(*, dsn: str, intents: int, concurrency: int, seed: int) -> dict:
    workspace_id = f"exchange-calibrated-{intents}-{uuid.uuid4().hex[:8]}"
    events = list(generate_exchange_calibrated_events(intent_count=intents, seed=seed))
    lifecycles: dict[str, list] = defaultdict(list)
    for event in events:
        lifecycles[event.intent_id].append(event)
    with psycopg.connect(dsn) as connection:
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        postgres_version = connection.execute("SHOW server_version").fetchone()[0]
    repository = PostgreSQLMemoryRepository.connect(dsn)

    def commit_lifecycle(lifecycle: list) -> tuple[int, int, list[float]]:
        applied = 0
        replayed = 0
        latencies = []
        for event in lifecycle:
            started = time.perf_counter()
            result = repository.commit_revision(
                workspace_id=workspace_id,
                memory_id=event.intent_id,
                event_type=f"exchange.{event.venue}.{event.step}",
                occurred_at=event.event_time,
                provenance_id=f"{event.evidence_class}:{event.event_id}",
                payload=event.to_dict(),
                idempotency_key=event.event_id,
                schema_version=event.schema_version,
            )
            latencies.append((time.perf_counter() - started) * 1000)
            applied += int(result.applied)
            replayed += int(not result.applied)
        return applied, replayed, latencies

    started = time.perf_counter()
    applied = 0
    replayed = 0
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(commit_lifecycle, lifecycle) for lifecycle in lifecycles.values()]
        for future in as_completed(futures):
            lifecycle_applied, lifecycle_replayed, lifecycle_latencies = future.result()
            applied += lifecycle_applied
            replayed += lifecycle_replayed
            latencies.extend(lifecycle_latencies)
    elapsed = time.perf_counter() - started
    with psycopg.connect(dsn) as connection:
        revision_count, outbox_count = connection.execute(
            """SELECT
                 (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id = %s),
                 (SELECT count(*) FROM agent_memory_outbox WHERE workspace_id = %s)""",
            (workspace_id, workspace_id),
        ).fetchone()
    unique_events = len({event.event_id for event in events})
    return {
        "schema_version": "exchange-calibrated-postgres-live.v1",
        "mode": "live-postgresql",
        "postgresql_version": str(postgres_version),
        "workspace_id": workspace_id,
        "seed": seed,
        "intents": intents,
        "delivered_events": len(events),
        "unique_events": unique_events,
        "duplicate_deliveries": len(events) - unique_events,
        "concurrency": concurrency,
        "applied_events": applied,
        "idempotent_replays": replayed,
        "revision_count": int(revision_count),
        "outbox_count": int(outbox_count),
        "lost_commits": unique_events - applied,
        "parity": applied == unique_events == revision_count == outbox_count,
        "elapsed_seconds": round(elapsed, 6),
        "events_per_second": round(len(events) / elapsed, 2),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "p99": round(_percentile(latencies, 0.99), 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--intents", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(dsn=args.dsn, intents=args.intents, concurrency=args.concurrency, seed=args.seed)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return int(not report["parity"])


if __name__ == "__main__":
    raise SystemExit(main())
