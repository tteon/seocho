#!/usr/bin/env python3
"""Live PostgreSQL overload-protection evidence for the agent-memory path."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg_pool import ConnectionPool

from seocho.memory import (
    AdmissionRejected,
    PostgresReadRouter,
    PostgresTarget,
    SingleFlightCache,
    WorkloadAdmissionController,
    WorkloadTier,
)
from seocho.metrics import enable_metrics, shutdown_metrics
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * quantile), len(ordered) - 1)]


def timed(operation: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    return operation(), (time.perf_counter() - started) * 1000


def dataset_snapshot(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        version = connection.execute("SELECT version()").fetchone()[0]
        recovery = bool(connection.execute("SELECT pg_is_in_recovery()").fetchone()[0])
        rows = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT workspace_id) FROM agent_memory_revisions"
        ).fetchone()
    return {
        "postgres_version": version,
        "server_in_recovery": recovery,
        "memory_revisions": int(rows[0]),
        "workspaces": int(rows[1]),
    }


def cache_stampede(pool: ConnectionPool, concurrency: int) -> dict[str, Any]:
    cache: SingleFlightCache[int] = SingleFlightCache()
    loader_calls = 0
    lock = threading.Lock()

    def loader() -> int:
        nonlocal loader_calls
        with lock:
            loader_calls += 1
        with pool.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_memory_revisions, LATERAL pg_sleep(0.05)"
                ).fetchone()[0]
            )

    def request(_: int) -> tuple[str, float, int]:
        (value, outcome), latency = timed(
            lambda: cache.get_or_load(
                "revision-count", loader, ttl_seconds=30, wait_seconds=5
            )
        )
        return outcome, latency, value

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(request, range(concurrency)))
    outcomes: dict[str, int] = {}
    for outcome, _, _ in results:
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "requests": concurrency,
        "database_loader_calls": loader_calls,
        "coalescing_ratio": 1 - loader_calls / concurrency,
        "outcomes": outcomes,
        "latency_ms": {
            "p50": percentile([item[1] for item in results], 0.50),
            "p95": percentile([item[1] for item in results], 0.95),
            "max": max(item[1] for item in results),
        },
        "consistent_value": len({item[2] for item in results}) == 1,
    }


def _critical_read(pool: ConnectionPool) -> None:
    with pool.connection() as connection:
        connection.execute("SELECT 1").fetchone()


def _background_hold(pool: ConnectionPool) -> None:
    with pool.connection() as connection:
        connection.execute("SELECT pg_sleep(0.05)").fetchone()


def workload_isolation(dsn: str, concurrency: int) -> dict[str, Any]:
    shared = ConnectionPool(dsn, min_size=1, max_size=4, timeout=5, open=True)
    critical = ConnectionPool(dsn, min_size=1, max_size=4, timeout=5, open=True)
    background = ConnectionPool(dsn, min_size=1, max_size=2, timeout=5, open=True)
    admission = WorkloadAdmissionController(
        {WorkloadTier.CRITICAL: 32, WorkloadTier.BACKGROUND: 2}
    )

    def phase(*, isolated: bool) -> dict[str, Any]:
        critical_latencies: list[float] = []
        background_admitted = 0
        background_rejected = 0
        lock = threading.Lock()

        def background_task() -> None:
            nonlocal background_admitted, background_rejected
            try:
                if isolated:
                    admission.run(
                        WorkloadTier.BACKGROUND,
                        lambda: _background_hold(background),
                        wait_seconds=0.002,
                    )
                else:
                    _background_hold(shared)
                with lock:
                    background_admitted += 1
            except AdmissionRejected:
                with lock:
                    background_rejected += 1

        def critical_task() -> None:
            _, latency = timed(
                lambda: admission.run(
                    WorkloadTier.CRITICAL,
                    lambda: _critical_read(critical if isolated else shared),
                    wait_seconds=10,
                )
            )
            with lock:
                critical_latencies.append(latency)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            background_futures = [executor.submit(background_task) for _ in range(32)]
            time.sleep(0.01)
            critical_futures = [executor.submit(critical_task) for _ in range(32)]
            for future in as_completed(background_futures + critical_futures):
                future.result()
        return {
            "critical_reads": len(critical_latencies),
            "critical_latency_ms": {
                "p50": percentile(critical_latencies, 0.50),
                "p95": percentile(critical_latencies, 0.95),
                "max": max(critical_latencies),
                "mean": statistics.fmean(critical_latencies),
            },
            "background_admitted": background_admitted,
            "background_rejected": background_rejected,
        }

    try:
        baseline = phase(isolated=False)
        isolated = phase(isolated=True)
        baseline_p95 = baseline["critical_latency_ms"]["p95"]
        isolated_p95 = isolated["critical_latency_ms"]["p95"]
        return {
            "shared_pool_baseline": baseline,
            "tier_isolated": isolated,
            "critical_p95_improvement_ratio": (
                baseline_p95 / isolated_p95 if isolated_p95 else 0.0
            ),
        }
    finally:
        shared.close()
        critical.close()
        background.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    os.environ["OTEL_SERVICE_NAME"] = "seocho-postgres-resilience-live"
    os.environ["OTEL_SERVICE_INSTANCE_ID"] = uuid.uuid4().hex[:12]
    enable_tracing(backend="otlp", endpoint=args.otlp_grpc)
    enable_metrics(backend="otlp", endpoint=args.otlp_grpc)
    pool = ConnectionPool(
        args.dsn, min_size=1, max_size=min(args.concurrency, 16), timeout=10, open=True
    )
    try:
        with start_span(
            "okx.postgres_resilience.run",
            metadata={
                "traffic.type": "evaluation",
                "seocho.benchmark.concurrency": args.concurrency,
            },
            tags=["okx", "postgresql", "resilience", "agent-memory"],
        ) as root_span:
            trace_id = str(getattr(root_span, "trace_id", ""))
            snapshot = dataset_snapshot(args.dsn)
            stampede = cache_stampede(pool, args.concurrency)
            isolation = workload_isolation(args.dsn, args.concurrency)
            logical_route = PostgresReadRouter().choose(
                [
                    PostgresTarget("primary", "primary", "kr", priority=10),
                    PostgresTarget(
                        "replica-stale", "replica", "kr", replay_lag_seconds=5
                    ),
                    PostgresTarget(
                        "replica-fresh", "replica", "us", replay_lag_seconds=0.1
                    ),
                ],
                client_region="kr",
                max_replay_lag_seconds=1,
            )
            root_span.set_output(
                {
                    "database_loader_calls": stampede["database_loader_calls"],
                    "critical_p95_improvement_ratio": isolation[
                        "critical_p95_improvement_ratio"
                    ],
                    "physical_replica_qualified": snapshot["server_in_recovery"],
                }
            )
        passed = bool(
            stampede["database_loader_calls"] == 1
            and stampede["consistent_value"]
            and isolation["tier_isolated"]["critical_reads"] == 32
            and isolation["tier_isolated"]["background_rejected"] > 0
        )
        report = {
            "schema_version": "seocho.okx-postgres-resilience-live.v1",
            "run_id": os.environ["OTEL_SERVICE_INSTANCE_ID"],
            "trace_id": trace_id,
            "source": "live-postgresql-agent-memory",
            "dataset": snapshot,
            "cache_stampede": stampede,
            "workload_isolation": isolation,
            "replica_routing_contract": {
                "selected_target": logical_route.target.target_id,
                "reason": logical_route.reason,
                "physical_replication_status": (
                    "qualified" if snapshot["server_in_recovery"] else "not-qualified"
                ),
            },
            "limitations": [
                "Physical streaming-replica failover and cascading replication are not qualified by a single-primary container.",
                "The workload protects the existing PostgreSQL authority path; it does not claim OpenAI-scale QPS.",
                "Connection pooling is psycopg_pool in this run; PgBouncer requires a separate deployment profile and failure test.",
            ],
            "passed": passed,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if passed else 1)
    finally:
        pool.close()
        flush_tracing()
        disable_tracing()
        shutdown_metrics()


if __name__ == "__main__":
    main()
