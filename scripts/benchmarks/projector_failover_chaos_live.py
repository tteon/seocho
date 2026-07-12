#!/usr/bin/env python3
"""Live sustained-ingestion projector process-kill and fencing E2E."""

from __future__ import annotations

import argparse
import base64
import json
import multiprocessing as mp
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import psycopg
from neo4j import GraphDatabase

from seocho.memory import PostgreSQLMemoryRepository, ProjectionFencingError


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _acquire(base: str, key: str, worker: str, ttl: int) -> tuple[bool, int, int]:
    lease = _post(base, "/v3/lease/grant", {"TTL": ttl})
    lease_id = int(lease["ID"])
    transaction = _post(
        base,
        "/v3/kv/txn",
        {
            "compare": [
                {"key": _b64(key), "target": "VERSION", "result": "EQUAL", "version": "0"}
            ],
            "success": [
                {
                    "request_put": {
                        "key": _b64(key),
                        "value": _b64(json.dumps({"worker_id": worker}, sort_keys=True)),
                        "lease": str(lease_id),
                    }
                }
            ],
            "failure": [{"request_range": {"key": _b64(key)}}],
        },
    )
    revision = int(transaction["header"]["revision"])
    # etcd's protobuf JSON gateway omits scalar fields holding their default
    # value, so a failed comparison has no `succeeded` member at all.
    return bool(transaction.get("succeeded", False)), lease_id, revision


def _worker_a(base: str, key: str, ttl: int, ready: mp.Queue) -> None:
    ready.put(_acquire(base, key, "projector-a", ttl))
    time.sleep(60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--etcd", default="http://127.0.0.1:52379")
    parser.add_argument("--events", type=int, default=300)
    parser.add_argument("--lease-ttl", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    workspace = f"chaos-live-{uuid.uuid4().hex[:12]}"
    projection = "chaos-projector"
    owner_key = f"/seocho/experiments/{workspace}/owner"
    repository = PostgreSQLMemoryRepository.connect(args.dsn)
    ingest_errors: list[str] = []
    ingestion_finished: list[float] = []

    def ingest() -> None:
        for sequence in range(1, args.events + 1):
            try:
                repository.commit_revision(
                    workspace_id=workspace,
                    memory_id=f"transaction-{sequence:06d}",
                    event_type="exchange.transaction.observed",
                    occurred_at=f"2026-07-12T00:{sequence // 60:02d}:{sequence % 60:02d}+00:00",
                    provenance_id=f"blockchain:event-{sequence:06d}",
                    payload={"event_id": f"event-{sequence:06d}", "sequence": sequence},
                    idempotency_key=f"delivery-{sequence:06d}",
                )
            except Exception as exc:  # pragma: no cover - live diagnostic
                ingest_errors.append(type(exc).__name__)
        ingestion_finished.append(time.perf_counter())

    ingestion = threading.Thread(target=ingest, daemon=True)
    ingestion_started = time.perf_counter()
    ingestion.start()

    ready: mp.Queue = mp.Queue()
    worker_a = mp.Process(target=_worker_a, args=(args.etcd, owner_key, args.lease_ttl, ready))
    worker_a.start()
    acquired_a, _lease_a, token_a = ready.get(timeout=15)
    if not acquired_a:
        raise RuntimeError("projector A failed to acquire owner key")
    acquired_while_alive, _, _ = _acquire(args.etcd, owner_key, "projector-b", args.lease_ttl)
    worker_a.terminate()
    worker_a.join(timeout=5)
    killed_at = time.perf_counter()

    acquired_b = False
    token_b = 0
    deadline = time.monotonic() + args.lease_ttl + 10
    while time.monotonic() < deadline:
        acquired_b, _, token_b = _acquire(args.etcd, owner_key, "projector-b", 30)
        if acquired_b:
            break
        time.sleep(0.25)
    takeover_ms = (time.perf_counter() - killed_at) * 1000
    if not acquired_b:
        raise RuntimeError("projector B did not acquire after A lease expiry")

    ingestion.join(timeout=120)
    if ingestion.is_alive() or ingest_errors:
        raise RuntimeError(f"ingestion failed: {ingest_errors[:3]}")
    ingestion_ms = (ingestion_finished[0] - ingestion_started) * 1000

    driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.graph_password))
    projected = 0
    while True:
        entries = repository.read_outbox_batch(workspace_id=workspace, limit=100)
        if not entries:
            break
        repository.assert_projection_fence(
            workspace_id=workspace, projection=projection, fencing_token=token_b
        )
        rows = [{"id": f"{workspace}:{entry.sequence}", "sequence": entry.sequence} for entry in entries]
        with driver.session() as session:
            session.run(
                "UNWIND $rows AS row MERGE (n:ChaosProjectionEvent {id:row.id}) "
                "SET n.sequence=row.sequence, n.workspace=$workspace",
                rows=rows,
                workspace=workspace,
            ).consume()
        maximum = max(entry.sequence for entry in entries)
        repository.acknowledge_projection(
            workspace_id=workspace,
            projection=projection,
            applied_sequence=maximum,
            entries=entries,
            fencing_token=token_b,
        )
        projected += len(entries)

    stale_rejected = False
    try:
        repository.assert_projection_fence(
            workspace_id=workspace, projection=projection, fencing_token=token_a
        )
    except ProjectionFencingError:
        stale_rejected = True

    with psycopg.connect(args.dsn) as connection:
        revision_count, pending, watermark, durable_token = connection.execute(
            """SELECT
              (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s),
              (SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=%s AND projected_at IS NULL),
              applied_sequence, fencing_token FROM agent_projection_watermarks
              WHERE workspace_id=%s AND projection=%s""",
            (workspace, workspace, workspace, projection),
        ).fetchone()
    with driver.session() as session:
        graph_count = session.run(
            "MATCH (n:ChaosProjectionEvent {workspace:$workspace}) RETURN count(n) AS n",
            workspace=workspace,
        ).single()["n"]
    driver.close()
    _post(args.etcd, "/v3/kv/deleterange", {"key": _b64(owner_key)})

    passed = (
        not acquired_while_alive
        and token_b > token_a
        and stale_rejected
        and revision_count == projected == graph_count == args.events
        and pending == 0
        and watermark == args.events
        and durable_token == token_b
    )
    report = {
        "schema_version": "seocho.projector-failover-chaos-live.v1",
        "source": "live-postgresql-etcd-dozerdb",
        "events": args.events,
        "ingestion_ms": round(ingestion_ms, 3),
        "worker_a_process_exitcode": worker_a.exitcode,
        "worker_b_blocked_while_a_alive": not acquired_while_alive,
        "takeover_ms": round(takeover_ms, 3),
        "token_a": token_a,
        "token_b": token_b,
        "token_monotonic": token_b > token_a,
        "stale_a_rejected_before_graph_write": stale_rejected,
        "revision_count": int(revision_count),
        "projected_count": projected,
        "graph_count": int(graph_count),
        "pending_outbox": int(pending),
        "watermark": int(watermark),
        "durable_fencing_token": int(durable_token),
        "passed": passed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
