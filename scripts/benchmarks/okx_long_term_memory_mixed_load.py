#!/usr/bin/env python3
"""Mixed live workload over a populated PostgreSQL agent-memory workspace.

This is deliberately not a synthetic SELECT-only benchmark.  It mixes point-in-
time and current reads with the production repository's atomic single-event
commit path, idempotent replay, transition rejection, context compaction and an
incremental projection consumer.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from seocho.memory import PostgreSQLMemoryRepository

MIX = {
    "current_read": 35,
    "historical_read": 20,
    "projection_read": 15,
    "context_compaction": 10,
    "live_write": 8,
    "duplicate_replay": 5,
    "invalid_transition": 3,
    "reorg_compensation": 2,
    "projection_refresh": 2,
}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * p), len(ordered) - 1)]


def prepare(dsn: str, workspace: str) -> tuple[int, list[tuple[str, int]]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        count, maximum = conn.execute(
            "SELECT count(*),max(sequence) FROM agent_memory_revisions WHERE workspace_id=%s",
            (workspace,),
        ).fetchone()
        if not count:
            raise RuntimeError(f"workspace {workspace!r} has no revisions")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_memory_projection_mixed_shadow("
            "workspace_id text,memory_id text,sequence bigint,state text,"
            "PRIMARY KEY(workspace_id,memory_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_memory_projection_mixed_watermark("
            "workspace_id text PRIMARY KEY,sequence bigint NOT NULL DEFAULT 0,"
            "updated_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.execute(
            "INSERT INTO agent_memory_projection_mixed_watermark(workspace_id,sequence) VALUES(%s,0) "
            "ON CONFLICT(workspace_id) DO NOTHING",
            (workspace,),
        )
        conn.execute(
            "DELETE FROM agent_memory_projection_mixed_shadow WHERE workspace_id=%s",
            (workspace,),
        )
        conn.execute(
            "INSERT INTO agent_memory_projection_mixed_shadow(workspace_id,memory_id,sequence,state) "
            "SELECT DISTINCT ON(memory_id) workspace_id,memory_id,sequence,payload->>'state' "
            "FROM agent_memory_revisions WHERE workspace_id=%s ORDER BY memory_id,sequence DESC",
            (workspace,),
        )
        conn.execute(
            "UPDATE agent_memory_projection_mixed_watermark SET sequence=%s,updated_at=now() WHERE workspace_id=%s",
            (maximum, workspace),
        )
        samples = conn.execute(
            "SELECT memory_id,max(sequence) FROM agent_memory_revisions "
            "WHERE workspace_id=%s GROUP BY memory_id ORDER BY max(sequence) DESC LIMIT 10000",
            (workspace,),
        ).fetchall()
    return int(maximum), [(str(row[0]), int(row[1])) for row in samples]


class Runner:
    def __init__(
        self, dsn: str, workspace: str, samples: list[tuple[str, int]], seed: int
    ):
        self.dsn = dsn
        self.workspace = workspace
        self.samples = samples
        self.seed = seed
        self.run_token = uuid.uuid4().hex[:12]
        self.repository = PostgreSQLMemoryRepository.connect(dsn)
        self.lock = threading.Lock()
        self.replayable: list[dict] = []
        self.ordinal = 0

    def choose(self, rng: random.Random) -> str:
        return rng.choices(tuple(MIX), weights=tuple(MIX.values()), k=1)[0]

    def _new_write(self, kind: str = "transaction.pending") -> dict:
        with self.lock:
            self.ordinal += 1
            ordinal = self.ordinal
        memory_id = f"mixed-{self.run_token}-{ordinal}"
        return {
            "workspace_id": self.workspace,
            "memory_id": memory_id,
            "event_type": kind,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provenance_id": f"mixed-live:{ordinal}",
            "payload": {
                "state": kind.rsplit(".", 1)[-1],
                "ordinal": ordinal,
                "source": "live-mixed-load",
            },
            "idempotency_key": f"mixed-live-{self.run_token}-{ordinal}",
        }

    def execute(self, operation: str, task_id: int) -> tuple[str, float, bool, str]:
        started = time.perf_counter()
        expected = False
        detail = "ok"
        try:
            rng = random.Random(self.seed + task_id * 104729)
            memory_id, latest = self.samples[rng.randrange(len(self.samples))]
            if operation == "current_read":
                value = self.repository.read_revision(
                    workspace_id=self.workspace, memory_id=memory_id
                )
                if value is None or value.sequence != latest:
                    raise AssertionError("current revision mismatch")
            elif operation == "historical_read":
                at = max(1, latest - rng.randint(1, 2))
                value = self.repository.read_revision(
                    workspace_id=self.workspace, memory_id=memory_id, at_sequence=at
                )
                if value is not None and value.sequence > at:
                    raise AssertionError("point-in-time revision mismatch")
            elif operation == "projection_read":
                with psycopg.connect(self.dsn) as conn:
                    projected = conn.execute(
                        "SELECT sequence,state FROM agent_memory_projection_mixed_shadow "
                        "WHERE workspace_id=%s AND memory_id=%s",
                        (self.workspace, memory_id),
                    ).fetchone()
                    if projected is None or int(projected[0]) != latest:
                        raise AssertionError("projection revision mismatch")
            elif operation == "context_compaction":
                with psycopg.connect(self.dsn) as conn:
                    rows = conn.execute(
                        "SELECT DISTINCT ON(memory_id) memory_id,sequence,payload->>'state' "
                        "FROM agent_memory_revisions WHERE workspace_id=%s AND sequence<=%s "
                        "ORDER BY memory_id,sequence DESC LIMIT 100",
                        (self.workspace, latest),
                    ).fetchall()
                    if not rows:
                        raise AssertionError("empty context")
            elif operation == "live_write":
                payload = self._new_write()
                result = self.repository.commit_revision(**payload)
                if not result.applied:
                    raise AssertionError("new write was not applied")
                with self.lock:
                    self.replayable.append(payload)
            elif operation == "duplicate_replay":
                with self.lock:
                    payload = (
                        self.replayable[rng.randrange(len(self.replayable))]
                        if self.replayable
                        else None
                    )
                if payload is None:
                    payload = self._new_write()
                    first = self.repository.commit_revision(**payload)
                    if not first.applied:
                        raise AssertionError("replay seed was not applied")
                    with self.lock:
                        self.replayable.append(payload)
                result = self.repository.commit_revision(**payload)
                if result.applied:
                    raise AssertionError("duplicate replay was applied twice")
                expected = True
                detail = "idempotent_replay"
            elif operation == "invalid_transition":
                payload = self._new_write("transaction.confirmed")
                try:
                    self.repository.commit_revision(
                        **payload, allowed_previous_event_types=("transaction.pending",)
                    )
                except ValueError as exc:
                    expected = True
                    detail = "transition_rejected"
                    if "invalid memory transition" not in str(exc):
                        raise
                else:
                    raise AssertionError("invalid transition was accepted")
            elif operation == "reorg_compensation":
                payload = self._new_write("transaction.reversed")
                payload["payload"]["reorged_by_block_hash"] = f"block-{task_id:x}"
                result = self.repository.commit_revision(**payload, canonical=False)
                if not result.applied:
                    raise AssertionError("compensation was not appended")
            elif operation == "projection_refresh":
                with psycopg.connect(self.dsn) as conn:
                    with conn.transaction():
                        watermark = int(
                            conn.execute(
                                "SELECT sequence FROM agent_memory_projection_mixed_watermark "
                                "WHERE workspace_id=%s FOR UPDATE",
                                (self.workspace,),
                            ).fetchone()[0]
                        )
                        rows = conn.execute(
                            "SELECT memory_id,sequence,payload->>'state' FROM agent_memory_revisions "
                            "WHERE workspace_id=%s AND sequence>%s ORDER BY sequence LIMIT 500",
                            (self.workspace, watermark),
                        ).fetchall()
                        for mid, sequence, state in rows:
                            conn.execute(
                                "INSERT INTO agent_memory_projection_mixed_shadow VALUES(%s,%s,%s,%s) "
                                "ON CONFLICT(workspace_id,memory_id) DO UPDATE SET "
                                "sequence=EXCLUDED.sequence,state=EXCLUDED.state "
                                "WHERE agent_memory_projection_mixed_shadow.sequence<EXCLUDED.sequence",
                                (self.workspace, mid, sequence, state),
                            )
                        if rows:
                            high = max(int(row[1]) for row in rows)
                            conn.execute(
                                "UPDATE agent_memory_projection_mixed_watermark SET sequence=%s,updated_at=now() "
                                "WHERE workspace_id=%s",
                                (high, self.workspace),
                            )
            else:
                raise AssertionError(f"unknown operation: {operation}")
            return operation, (time.perf_counter() - started) * 1000, expected, detail
        except Exception as exc:
            return (
                operation,
                (time.perf_counter() - started) * 1000,
                False,
                f"{type(exc).__name__}: {exc}",
            )


def run_phase(
    runner: Runner, name: str, operations: int, concurrency: int, offset: int
) -> dict:
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for index in range(operations):
            task_id = offset + index
            rng = random.Random(runner.seed + task_id * 1009)
            futures.append(pool.submit(runner.execute, runner.choose(rng), task_id))
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started
    grouped: dict[str, list[tuple[float, bool, str]]] = defaultdict(list)
    for operation, latency, expected, detail in results:
        grouped[operation].append((latency, expected, detail))
    operations_report = {}
    unexpected = 0
    for operation in MIX:
        rows = grouped[operation]
        latencies = [row[0] for row in rows]
        failures = [
            row[2]
            for row in rows
            if row[2] not in {"ok", "idempotent_replay", "transition_rejected"}
        ]
        unexpected += len(failures)
        operations_report[operation] = {
            "count": len(rows),
            "expected_control_outcomes": sum(row[1] for row in rows),
            "unexpected_errors": len(failures),
            "error_samples": failures[:3],
            "latency_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "max": max(latencies, default=0),
            },
        }
    return {
        "name": name,
        "concurrency": concurrency,
        "operations": operations,
        "elapsed_seconds": elapsed,
        "operations_per_second": operations / elapsed,
        "unexpected_errors": unexpected,
        "by_operation": operations_report,
    }


def snapshot(dsn: str, workspace: str) -> dict:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_idempotency WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=%s),"
            "(SELECT max(sequence) FROM agent_memory_revisions WHERE workspace_id=%s),"
            "(SELECT next_sequence-1 FROM agent_memory_heads WHERE workspace_id=%s),"
            "(SELECT sequence FROM agent_memory_projection_mixed_watermark WHERE workspace_id=%s)",
            (workspace,) * 6,
        ).fetchone()
        locks = conn.execute(
            "SELECT count(*) FILTER (WHERE NOT granted),count(*) FROM pg_locks WHERE database=pg_backend_pid()"
        ).fetchone()
    return {
        "revisions": int(row[0]),
        "idempotency": int(row[1]),
        "outbox": int(row[2]),
        "max_sequence": int(row[3]),
        "head_sequence": int(row[4]),
        "projection_watermark": int(row[5]),
        "projection_lag_events": int(row[4]) - int(row[5]),
        "waiting_locks": int(locks[0]),
        "observed_locks": int(locks[1]),
        "authoritative_integrity": int(row[0]) == int(row[1]) == int(row[2]),
        "sequence_integrity": int(row[3]) == int(row[4]),
    }


def drain_projection(dsn: str, workspace: str) -> None:
    with psycopg.connect(dsn) as conn, conn.transaction():
        watermark = int(
            conn.execute(
                "SELECT sequence FROM agent_memory_projection_mixed_watermark "
                "WHERE workspace_id=%s FOR UPDATE",
                (workspace,),
            ).fetchone()[0]
        )
        rows = conn.execute(
            "SELECT memory_id,sequence,payload->>'state' FROM agent_memory_revisions "
            "WHERE workspace_id=%s AND sequence>%s ORDER BY sequence LIMIT 500",
            (workspace, watermark),
        ).fetchall()
        for memory_id, sequence, state in rows:
            conn.execute(
                "INSERT INTO agent_memory_projection_mixed_shadow VALUES(%s,%s,%s,%s) "
                "ON CONFLICT(workspace_id,memory_id) DO UPDATE SET sequence=EXCLUDED.sequence,state=EXCLUDED.state "
                "WHERE agent_memory_projection_mixed_shadow.sequence<EXCLUDED.sequence",
                (workspace, memory_id, sequence, state),
            )
        if rows:
            conn.execute(
                "UPDATE agent_memory_projection_mixed_watermark SET sequence=%s,updated_at=now() WHERE workspace_id=%s",
                (max(int(row[1]) for row in rows), workspace),
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--steady-operations", type=int, default=2000)
    parser.add_argument("--spike-operations", type=int, default=4000)
    parser.add_argument("--steady-concurrency", type=int, default=16)
    parser.add_argument("--spike-concurrency", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    initial_sequence, samples = prepare(args.dsn, args.workspace)
    runner = Runner(args.dsn, args.workspace, samples, args.seed)
    before = snapshot(args.dsn, args.workspace)
    steady = run_phase(
        runner, "steady", args.steady_operations, args.steady_concurrency, 0
    )
    spike = run_phase(
        runner,
        "spike",
        args.spike_operations,
        args.spike_concurrency,
        args.steady_operations,
    )
    # Recovery phase deliberately lowers concurrency while the incremental consumer catches up.
    recovery = run_phase(
        runner,
        "recovery",
        1000,
        args.steady_concurrency,
        args.steady_operations + args.spike_operations,
    )
    pre_drain = snapshot(args.dsn, args.workspace)
    initial_projection_lag = pre_drain["projection_lag_events"]
    drain_started = time.perf_counter()
    drain_batches = 0
    while pre_drain["projection_lag_events"] > 0 and drain_batches < 100:
        drain_projection(args.dsn, args.workspace)
        drain_batches += 1
        pre_drain = snapshot(args.dsn, args.workspace)
    projection_recovery = {
        "initial_lag_events": initial_projection_lag,
        "drain_batches": drain_batches,
        "rto_seconds": time.perf_counter() - drain_started,
        "final_lag_events": pre_drain["projection_lag_events"],
    }
    after = pre_drain
    report = {
        "schema_version": "seocho.long-term-memory-mixed-live.v1",
        "run_id": str(uuid.uuid4()),
        "source": "live-postgresql-production-repository-path",
        "workspace": args.workspace,
        "initial_sequence": initial_sequence,
        "workload_mix_percent": MIX,
        "phases": [steady, spike, recovery],
        "before": before,
        "after": after,
        "projection_recovery": projection_recovery,
        "limitations": [
            "Projection operations use the PostgreSQL shadow consumer; DozerDB traversal is measured separately.",
            "The phase runner submits a fixed operation count rather than an open-loop arrival process.",
        ],
    }
    report["passed"] = bool(
        all(not phase["unexpected_errors"] for phase in report["phases"])
        and after["authoritative_integrity"]
        and after["sequence_integrity"]
        and after["projection_lag_events"] == 0
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
