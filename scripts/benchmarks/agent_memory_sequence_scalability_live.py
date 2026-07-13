#!/usr/bin/env python3
"""Live qualification of strict commits and experimental sequence policies.

The artifact keeps full-commit and allocator-only measurements separate.  A
sharded allocator result must never be presented as end-to-end memory write
throughput.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from seocho.memory import (
    POSTGRES_MEMORY_SCHEMA_SQL,
    POSTGRES_SEQUENCE_V2_SCHEMA_SQL,
    MemoryCommitMetricsObserver,
    PostgreSQLCausalSequenceAllocator,
    PostgreSQLMemoryRepository,
    SequenceMode,
    SequencePolicy,
)
from seocho.metrics import enable_metrics, shutdown_metrics


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * quantile), len(ordered) - 1)]


def latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


class PhaseObserver:
    def __init__(self, exporter: MemoryCommitMetricsObserver | None = None) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, int] = defaultdict(int)
        self.exporter = exporter

    def record(self, phase: str, elapsed_ms: float, outcome: str) -> None:
        with self._lock:
            self._values[phase].append(elapsed_ms)
            if outcome != "ok":
                self.errors[phase] += 1
        if self.exporter is not None:
            self.exporter.record(phase, elapsed_ms, outcome)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                phase: {**latency_summary(values), "errors": self.errors[phase]}
                for phase, values in sorted(self._values.items())
            }


def aggregate_for(
    distribution: str, ordinal: int, aggregate_count: int, seed: int
) -> str:
    if distribution == "hot-one":
        index = 0
    elif distribution == "uniform":
        index = random.Random(seed + ordinal * 104729).randrange(aggregate_count)
    elif distribution.startswith("zipf-"):
        alpha = float(distribution.split("-", 1)[1])
        rank = int(random.Random(seed + ordinal * 104729).paretovariate(alpha)) - 1
        index = min(rank, aggregate_count - 1)
    else:
        raise ValueError(f"unsupported distribution: {distribution}")
    return f"wallet-{index:08d}"


def strict_full_commit_run(
    *,
    dsn: str,
    events: int,
    concurrency: int,
    distribution: str,
    aggregate_count: int,
    seed: int,
    pooled: bool,
    metrics_observer: MemoryCommitMetricsObserver | None = None,
) -> dict[str, Any]:
    workspace = f"seq-live-strict-{uuid.uuid4().hex[:12]}"
    observer = PhaseObserver(metrics_observer)
    repository = (
        PostgreSQLMemoryRepository.connect_pool(
            dsn,
            min_size=min(4, concurrency),
            max_size=max(4, min(concurrency, 64)),
            phase_observer=observer,
        )
        if pooled
        else PostgreSQLMemoryRepository.connect(dsn, phase_observer=observer)
    )
    latencies: list[float] = []
    errors: list[str] = []

    def commit(ordinal: int) -> float:
        started = time.perf_counter()
        aggregate = aggregate_for(distribution, ordinal, aggregate_count, seed)
        repository.commit_revision(
            workspace_id=workspace,
            memory_id=aggregate,
            event_type="transaction.observed",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            provenance_id=f"sequence-live:{ordinal}",
            payload={"ordinal": ordinal, "state": "observed"},
            idempotency_key=f"sequence-live:{ordinal}",
        )
        return (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for future in [executor.submit(commit, ordinal) for ordinal in range(events)]:
            try:
                latencies.append(future.result())
            except Exception as exc:  # evidence artifact records every failure
                errors.append(f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    repository.close()
    with psycopg.connect(dsn) as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_idempotency WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=%s),"
            "(SELECT COALESCE(next_sequence - 1, 0) FROM agent_memory_heads "
            " WHERE workspace_id=%s)",
            (workspace, workspace, workspace, workspace),
        ).fetchone()
        for table in (
            "agent_memory_outbox",
            "agent_memory_idempotency",
            "agent_memory_revisions",
            "agent_memory_heads",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE workspace_id=%s", (workspace,)
            )
    cardinality = [int(value or 0) for value in counts]
    return {
        "scope": "full_memory_commit",
        "mode": "strict_workspace",
        "client": "python_psycopg_pool" if pooled else "python_psycopg_connect",
        "events": events,
        "concurrency": concurrency,
        "distribution": distribution,
        "aggregate_count": aggregate_count,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_events_s": round(len(latencies) / elapsed, 3),
        "latency": latency_summary(latencies),
        "phases": observer.summary(),
        "errors": errors[:20],
        "error_count": len(errors),
        "cardinality": {
            "revisions": cardinality[0],
            "idempotency": cardinality[1],
            "outbox": cardinality[2],
            "head_sequence": cardinality[3],
        },
        "correct": not errors and cardinality == [events, events, events, events],
    }


def allocator_run(
    *,
    dsn: str,
    events: int,
    concurrency: int,
    distribution: str,
    aggregate_count: int,
    seed: int,
    policy: SequencePolicy,
) -> dict[str, Any]:
    workspace = f"seq-live-v2-{uuid.uuid4().hex[:12]}"
    allocator = PostgreSQLCausalSequenceAllocator.connect_pool(
        dsn,
        policy=policy,
        owner_id=f"live-{uuid.uuid4().hex[:8]}",
        min_size=min(4, concurrency),
        max_size=max(4, min(concurrency, 64)),
    )
    latencies: list[float] = []
    positions = []
    errors: list[str] = []

    def allocate(ordinal: int):
        started = time.perf_counter()
        result = allocator.allocate(
            workspace_id=workspace,
            domain="transaction",
            aggregate_id=aggregate_for(distribution, ordinal, aggregate_count, seed),
        )
        return result.position, (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for future in [executor.submit(allocate, ordinal) for ordinal in range(events)]:
            try:
                position, latency = future.result()
                positions.append(position)
                latencies.append(latency)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    allocator.close()
    unique = {
        (position.domain, position.shard, position.sequence) for position in positions
    }
    with psycopg.connect(dsn) as connection:
        reserved, leases, heads = connection.execute(
            "SELECT COALESCE(sum(range_end-range_start+1),0),count(*),"
            "count(DISTINCT (domain,shard)) FROM agent_memory_sequence_leases_v2 "
            "WHERE workspace_id=%s",
            (workspace,),
        ).fetchone()
        connection.execute(
            "DELETE FROM agent_memory_sequence_leases_v2 WHERE workspace_id=%s",
            (workspace,),
        )
        connection.execute(
            "DELETE FROM agent_memory_sequence_heads_v2 WHERE workspace_id=%s",
            (workspace,),
        )
    reserved = int(reserved)
    return {
        "scope": "allocator_only",
        "mode": policy.mode.value,
        "shards": policy.shards,
        "lease_size": policy.lease_size,
        "events": events,
        "concurrency": concurrency,
        "distribution": distribution,
        "aggregate_count": aggregate_count,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_allocations_s": round(len(positions) / elapsed, 3),
        "latency": latency_summary(latencies),
        "error_count": len(errors),
        "errors": errors[:20],
        "unique_positions": len(unique),
        "reserved_positions": reserved,
        "observable_unused_lease_positions": reserved - len(positions),
        "lease_rows": int(leases),
        "active_shards": int(heads),
        "correct": not errors and len(unique) == events and reserved >= events,
    }


def parse_policy(value: str) -> SequencePolicy:
    if value.startswith("lease-"):
        return SequencePolicy(
            mode=SequenceMode.LEASED_DOMAIN, lease_size=int(value.split("-")[1])
        )
    if value.startswith("shard-"):
        parts = value.split("-")
        shards = int(parts[1])
        lease_size = int(parts[2]) if len(parts) == 3 else 128
        return SequencePolicy(
            mode=SequenceMode.SHARDED_DOMAIN,
            shards=shards,
            lease_size=lease_size,
        )
    raise ValueError(f"unsupported policy: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--aggregate-count", type=int, default=10_000)
    parser.add_argument("--distribution", default="uniform")
    parser.add_argument("--policies", default="lease-128,shard-16-128,shard-64-128")
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--skip-unpooled", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--otel-endpoint",
        help="Optional OTLP/gRPC endpoint for low-cardinality phase histograms",
    )
    args = parser.parse_args()
    if args.events < 1 or args.concurrency < 1 or args.aggregate_count < 1:
        raise SystemExit("events, concurrency, and aggregate-count must be positive")

    with psycopg.connect(args.dsn) as connection:
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        connection.execute(POSTGRES_SEQUENCE_V2_SCHEMA_SQL)
        server = connection.execute(
            "SELECT version(), current_setting('synchronous_commit'), "
            "current_setting('fsync'), current_setting('full_page_writes')"
        ).fetchone()

    metrics_observer = None
    if args.otel_endpoint:
        metrics_observer = MemoryCommitMetricsObserver(
            enable_metrics(backend="otlp", endpoint=args.otel_endpoint)
        )
    runs = []
    if not args.skip_unpooled:
        runs.append(
            strict_full_commit_run(
                dsn=args.dsn,
                events=args.events,
                concurrency=args.concurrency,
                distribution=args.distribution,
                aggregate_count=args.aggregate_count,
                seed=args.seed,
                pooled=False,
                metrics_observer=metrics_observer,
            )
        )
    runs.append(
        strict_full_commit_run(
            dsn=args.dsn,
            events=args.events,
            concurrency=args.concurrency,
            distribution=args.distribution,
            aggregate_count=args.aggregate_count,
            seed=args.seed,
            pooled=True,
            metrics_observer=metrics_observer,
        )
    )
    for value in args.policies.split(","):
        runs.append(
            allocator_run(
                dsn=args.dsn,
                events=args.events,
                concurrency=args.concurrency,
                distribution=args.distribution,
                aggregate_count=args.aggregate_count,
                seed=args.seed,
                policy=parse_policy(value.strip()),
            )
        )
    artifact = {
        "artifact_schema": "seocho.agent-memory-sequence-scalability.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "deterministic blockchain wallet workload",
        "data_mode": "live PostgreSQL writes; no mocked timings",
        "server": {
            "version": server[0],
            "synchronous_commit": server[1],
            "fsync": server[2],
            "full_page_writes": server[3],
        },
        "runs": runs,
        "all_correct": all(run["correct"] for run in runs),
        "interpretation_guardrail": (
            "allocator_only throughput excludes revision, idempotency, outbox, WAL, "
            "and graph projection and is not comparable to full_memory_commit throughput"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps(artifact, indent=2, sort_keys=True))
    shutdown_metrics()
    raise SystemExit(0 if artifact["all_correct"] else 1)


if __name__ == "__main__":
    main()
