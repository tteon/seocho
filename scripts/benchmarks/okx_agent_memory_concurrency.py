#!/usr/bin/env python3
"""Live PostgreSQL concurrency benchmark for agent transaction memory."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

from seocho.eval.agent_transaction_dataset import generate_agent_transaction_events
from seocho.memory import (
    AgentTransactionMemory,
    POSTGRES_MEMORY_SCHEMA_SQL,
    PostgreSQLMemoryRepository,
)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * percentile), len(ordered) - 1)
    return ordered[index]


def run(*, dsn: str, transactions: int, concurrency: int) -> dict:
    events = list(generate_agent_transaction_events(transaction_count=transactions))
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(event.transaction_intent_id, []).append(event.to_dict())

    with psycopg.connect(dsn) as connection:
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)

    repository = PostgreSQLMemoryRepository.connect(dsn)
    memory = AgentTransactionMemory(repository)

    def commit_lifecycle(lifecycle: list[dict]) -> tuple[int, list[float]]:
        applied = 0
        latencies = []
        for event in lifecycle:
            started = time.perf_counter()
            result = memory.commit_event(event)
            latencies.append((time.perf_counter() - started) * 1000)
            applied += int(result.applied)
        return applied, latencies

    started = time.perf_counter()
    applied = 0
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(commit_lifecycle, lifecycle) for lifecycle in grouped.values()]
        for future in as_completed(futures):
            lifecycle_applied, lifecycle_latencies = future.result()
            applied += lifecycle_applied
            latencies.extend(lifecycle_latencies)
    elapsed = time.perf_counter() - started

    replay_applied = sum(
        int(memory.commit_event(event.to_dict()).applied) for event in events
    )
    with psycopg.connect(dsn) as connection:
        revision_count = connection.execute(
            "SELECT count(*) FROM agent_memory_revisions WHERE workspace_id = %s",
            (events[0].workspace_id,),
        ).fetchone()[0]
        outbox_count = connection.execute(
            "SELECT count(*) FROM agent_memory_outbox WHERE workspace_id = %s",
            (events[0].workspace_id,),
        ).fetchone()[0]

    return {
        "schema_version": "okx-agent-memory-concurrency.v1",
        "mode": "live-postgresql",
        "transactions": transactions,
        "events": len(events),
        "concurrency": concurrency,
        "applied_events": applied,
        "replay_applied_events": replay_applied,
        "revision_count": revision_count,
        "outbox_count": outbox_count,
        "lost_commits": len(events) - applied,
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
    parser.add_argument("--transactions", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(
        dsn=args.dsn,
        transactions=args.transactions,
        concurrency=args.concurrency,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
