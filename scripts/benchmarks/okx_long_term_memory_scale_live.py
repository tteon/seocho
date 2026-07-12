#!/usr/bin/env python3
"""Live 100K→1M PostgreSQL long-term-memory scale and recovery benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg

from seocho.eval.longitudinal_memory import generate_longitudinal_events
from seocho.memory import POSTGRES_MEMORY_SCHEMA_SQL


def _percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * value), len(ordered) - 1)]


def _sizes(connection) -> dict[str, int]:
    names = (
        "agent_memory_revisions",
        "agent_memory_idempotency",
        "agent_memory_outbox",
    )
    return {
        name: int(
            connection.execute(
                "SELECT pg_total_relation_size(%s::regclass)", (name,)
            ).fetchone()[0]
        )
        for name in names
    }


def _payload(event) -> tuple[str, str]:
    value = {
        "user_ref": event.user_ref,
        "chain_id": event.chain_id,
        "block_height": event.block_height,
        "block_hash_ref": event.block_hash_ref,
        "transaction_ref": event.transaction_ref,
        "agent_ref": event.agent_ref,
        "counterparty_ref": event.counterparty_ref,
        "state": event.state,
        "event_kind": event.event_kind,
        "direction": event.direction,
        "amount_sats": event.amount_sats,
        "confirmation_count": event.confirmation_count,
        "session_ref": event.session_ref,
        "private_metadata": event.private_metadata,
    }
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return rendered, hashlib.sha256(rendered.encode()).hexdigest()


def _ingest(
    connection,
    *,
    events: int,
    batch_size: int,
    workspace: str,
    seed: int,
) -> dict:
    existing = int(
        connection.execute(
            "SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s",
            (workspace,),
        ).fetchone()[0]
    )
    started = time.perf_counter()
    batch_latencies = []
    applied = existing
    source = generate_longitudinal_events(
        event_count=events, seed=seed, workspace_id=workspace
    )
    batch = []
    for event in source:
        if event.sequence <= existing:
            continue
        batch.append(event)
        if len(batch) == batch_size:
            batch_latencies.append(_write_batch(connection, batch, workspace))
            applied += len(batch)
            batch = []
    if batch:
        batch_latencies.append(_write_batch(connection, batch, workspace))
        applied += len(batch)
    elapsed = time.perf_counter() - started
    return {
        "existing_events": existing,
        "applied_events": applied - existing,
        "total_events": applied,
        "elapsed_seconds": elapsed,
        "events_per_second": (
            (applied - existing) / elapsed if applied > existing else 0
        ),
        "batch_latency_ms": {
            "mean": statistics.fmean(batch_latencies) if batch_latencies else 0,
            "p95": _percentile(batch_latencies, 0.95) if batch_latencies else 0,
            "max": max(batch_latencies, default=0),
        },
    }


def _write_batch(connection, batch, workspace: str) -> float:
    started = time.perf_counter()
    with connection.transaction():
        with connection.cursor() as cursor:
            with cursor.copy(
                "COPY agent_memory_revisions "
                "(workspace_id,memory_id,revision,sequence,event_type,occurred_at,"
                "provenance_id,payload,payload_hash,supersedes_revision,canonical,schema_version) "
                "FROM STDIN"
            ) as copy:
                for event in batch:
                    revision = (event.sequence - 1) % 3 + 1
                    payload, payload_hash = _payload(event)
                    copy.write_row(
                        (
                            workspace,
                            event.transaction_ref,
                            revision,
                            event.sequence,
                            event.event_kind,
                            event.occurred_at,
                            event.provenance_id,
                            payload,
                            payload_hash,
                            revision - 1 or None,
                            revision == 3,
                            event.schema_version,
                        )
                    )
            with cursor.copy(
                "COPY agent_memory_idempotency "
                "(workspace_id,idempotency_key,memory_id,revision,sequence,payload_hash) FROM STDIN"
            ) as copy:
                for event in batch:
                    revision = (event.sequence - 1) % 3 + 1
                    _, payload_hash = _payload(event)
                    copy.write_row(
                        (
                            workspace,
                            event.idempotency_key,
                            event.transaction_ref,
                            revision,
                            event.sequence,
                            payload_hash,
                        )
                    )
            with cursor.copy(
                "COPY agent_memory_outbox "
                "(workspace_id,sequence,ordinal,operation,aggregate_type,aggregate_id,payload) FROM STDIN"
            ) as copy:
                for event in batch:
                    payload, _ = _payload(event)
                    copy.write_row(
                        (
                            workspace,
                            event.sequence,
                            0,
                            "upsert",
                            "memory_revision",
                            event.transaction_ref,
                            payload,
                        )
                    )
            cursor.execute(
                "INSERT INTO agent_memory_heads(workspace_id,next_sequence) VALUES(%s,%s) "
                "ON CONFLICT(workspace_id) DO UPDATE SET next_sequence=GREATEST(agent_memory_heads.next_sequence,EXCLUDED.next_sequence)",
                (workspace, batch[-1].sequence + 1),
            )
    return (time.perf_counter() - started) * 1000


def _read_benchmark(
    dsn: str, workspace: str, events: int, reads: int, concurrency: int
) -> dict:
    rng = random.Random(20260712)
    final_sequences = [3 * rng.randint(1, events // 3) for _ in range(reads)]

    def one(sequence: int) -> tuple[float, float, bool]:
        with psycopg.connect(dsn) as connection:
            memory_id = connection.execute(
                "SELECT memory_id FROM agent_memory_revisions WHERE workspace_id=%s AND sequence=%s",
                (workspace, sequence),
            ).fetchone()[0]
            at_sequence = sequence - 1

            def current_read():
                started = time.perf_counter()
                value = connection.execute(
                    "SELECT sequence,payload->>'state' FROM agent_memory_revisions "
                    "WHERE workspace_id=%s AND memory_id=%s ORDER BY sequence DESC LIMIT 1",
                    (workspace, memory_id),
                ).fetchone()
                return value, (time.perf_counter() - started) * 1000

            def historical_read():
                started = time.perf_counter()
                value = connection.execute(
                    "SELECT sequence,payload->>'state' FROM agent_memory_revisions "
                    "WHERE workspace_id=%s AND memory_id=%s AND sequence<=%s "
                    "ORDER BY sequence DESC LIMIT 1",
                    (workspace, memory_id, at_sequence),
                ).fetchone()
                return value, (time.perf_counter() - started) * 1000

            if sequence % 2:
                current, current_ms = current_read()
                historical, historical_ms = historical_read()
            else:
                historical, historical_ms = historical_read()
                current, current_ms = current_read()
            return (
                current_ms,
                historical_ms,
                bool(
                    current
                    and historical
                    and current[0] == sequence
                    and historical[0] <= at_sequence
                ),
            )

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(one, final_sequences))
    elapsed = time.perf_counter() - started
    current = [row[0] for row in results]
    historical = [row[1] for row in results]
    return {
        "reads": reads,
        "concurrency": concurrency,
        "reads_per_second": reads * 2 / elapsed,
        "point_in_time_correct": sum(row[2] for row in results),
        "current_latency_ms": {
            "p50": _percentile(current, 0.5),
            "p95": _percentile(current, 0.95),
            "p99": _percentile(current, 0.99),
        },
        "historical_latency_ms": {
            "p50": _percentile(historical, 0.5),
            "p95": _percentile(historical, 0.95),
            "p99": _percentile(historical, 0.99),
        },
    }


def _context_and_rebuild(connection, workspace: str) -> dict:
    raw = connection.execute(
        "WITH ids AS (SELECT memory_id,max(sequence) AS latest FROM agent_memory_revisions "
        "WHERE workspace_id=%s GROUP BY memory_id ORDER BY latest DESC LIMIT 100) "
        "SELECT r.memory_id,r.sequence,r.payload FROM agent_memory_revisions r "
        "JOIN ids USING(memory_id) WHERE r.workspace_id=%s ORDER BY r.memory_id,r.sequence",
        (workspace, workspace),
    ).fetchall()
    compact = connection.execute(
        "WITH ids AS (SELECT memory_id,max(sequence) AS latest FROM agent_memory_revisions "
        "WHERE workspace_id=%s GROUP BY memory_id ORDER BY latest DESC LIMIT 100) "
        "SELECT r.memory_id,r.sequence,r.payload FROM agent_memory_revisions r "
        "JOIN ids ON ids.memory_id=r.memory_id AND ids.latest=r.sequence "
        "WHERE r.workspace_id=%s ORDER BY r.memory_id",
        (workspace, workspace),
    ).fetchall()
    raw_latest = {
        memory_id: (sequence, payload.get("state"))
        for memory_id, sequence, payload in raw
    }
    compact_latest = {
        memory_id: (sequence, payload.get("state"))
        for memory_id, sequence, payload in compact
    }
    raw_bytes = len(json.dumps(raw, default=str))
    compact_bytes = len(json.dumps(compact, default=str))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS agent_memory_projection_scale_shadow("
        "workspace_id text,memory_id text,sequence bigint,state text,PRIMARY KEY(workspace_id,memory_id))"
    )
    connection.execute(
        "DELETE FROM agent_memory_projection_scale_shadow WHERE workspace_id=%s",
        (workspace,),
    )
    started = time.perf_counter()
    connection.execute(
        "INSERT INTO agent_memory_projection_scale_shadow(workspace_id,memory_id,sequence,state) "
        "SELECT DISTINCT ON(memory_id) workspace_id,memory_id,sequence,payload->>'state' "
        "FROM agent_memory_revisions WHERE workspace_id=%s ORDER BY memory_id,sequence DESC",
        (workspace,),
    )
    connection.commit()
    rebuild_seconds = time.perf_counter() - started
    revisions, projection = connection.execute(
        "SELECT (SELECT count(DISTINCT memory_id) FROM agent_memory_revisions WHERE workspace_id=%s),"
        "(SELECT count(*) FROM agent_memory_projection_scale_shadow WHERE workspace_id=%s)",
        (workspace, workspace),
    ).fetchone()
    return {
        "raw_context_bytes": raw_bytes,
        "compact_context_bytes": compact_bytes,
        "context_reduction": 1 - compact_bytes / raw_bytes,
        "context_answer_parity": raw_latest == compact_latest,
        "raw_revisions": len(raw),
        "selected_memories": len(compact),
        "projection_rebuild_seconds": rebuild_seconds,
        "expected_memories": revisions,
        "rebuilt_memories": projection,
        "projection_parity": revisions == projection,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--events", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--reads", type=int, default=1_000)
    parser.add_argument("--read-concurrency", type=int, default=16)
    parser.add_argument("--workspace")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.events % 3:
        raise SystemExit("--events must be divisible by three")
    workspace = args.workspace or f"ltm-scale-{args.events}-{args.seed}"
    with psycopg.connect(args.dsn, autocommit=True) as connection:
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        before = _sizes(connection)
        ingestion = _ingest(
            connection,
            events=args.events,
            batch_size=args.batch_size,
            workspace=workspace,
            seed=args.seed,
        )
        after = _sizes(connection)
        integrity = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_idempotency WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=%s),"
            "(SELECT max(sequence) FROM agent_memory_revisions WHERE workspace_id=%s)",
            (workspace, workspace, workspace, workspace),
        ).fetchone()
        context = _context_and_rebuild(connection, workspace)
    reads = _read_benchmark(
        args.dsn, workspace, args.events, args.reads, args.read_concurrency
    )
    report = {
        "schema_version": "seocho.long-term-memory-scale-live.v1",
        "mode": "transactional-batch-replay-not-single-event-commit",
        "workspace": workspace,
        "events": args.events,
        "batch_size": args.batch_size,
        "ingestion": ingestion,
        "integrity": {
            "revisions": integrity[0],
            "idempotency": integrity[1],
            "outbox": integrity[2],
            "max_sequence": integrity[3],
            "passed": tuple(integrity)
            == (args.events, args.events, args.events, args.events),
        },
        "storage_delta_bytes": {name: after[name] - before[name] for name in before},
        "bytes_per_event": sum(after[name] - before[name] for name in before)
        / args.events,
        "reads": reads,
        "context_and_rebuild": context,
    }
    report["passed"] = bool(
        report["integrity"]["passed"]
        and reads["point_in_time_correct"] == args.reads
        and context["projection_parity"]
        and context["context_answer_parity"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
