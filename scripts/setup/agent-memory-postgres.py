#!/usr/bin/env python3
"""Migrate, inspect, and smoke-test authoritative PostgreSQL agent memory."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone

import psycopg

from seocho.memory import (
    CausalFrontier,
    POSTGRES_MEMORY_SCHEMA_SQL,
    POSTGRES_MEMORY_SCHEMA_VERSION,
    POSTGRES_SEQUENCE_V2_SCHEMA_SQL,
    POSTGRES_SEQUENCE_V2_SCHEMA_VERSION,
    PostgreSQLCausalSequenceAllocator,
    PostgreSQLCausalProjectionRepository,
    PostgreSQLMemoryRepository,
    ProjectionFencingError,
    SequenceMode,
    SequencePolicy,
)


def _dsn(value: str | None) -> str:
    resolved = value or os.getenv("SEOCHO_POSTGRES_DSN", "")
    if not resolved.strip():
        raise SystemExit("--dsn or SEOCHO_POSTGRES_DSN is required")
    return resolved


def migrate(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection, connection.transaction():
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        connection.execute(POSTGRES_SEQUENCE_V2_SCHEMA_SQL)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS seocho_schema_versions("
            "component text PRIMARY KEY,version text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())"
        )
        connection.execute(
            "INSERT INTO seocho_schema_versions(component,version) VALUES(%s,%s) "
            "ON CONFLICT(component) DO UPDATE SET version=EXCLUDED.version,applied_at=now()",
            ("agent-memory", POSTGRES_MEMORY_SCHEMA_VERSION),
        )
        connection.execute(
            "INSERT INTO seocho_schema_versions(component,version) VALUES(%s,%s) "
            "ON CONFLICT(component) DO UPDATE SET version=EXCLUDED.version,applied_at=now()",
            ("agent-memory-sequence", POSTGRES_SEQUENCE_V2_SCHEMA_VERSION),
        )
    return {
        "operation": "migrate",
        "schema_version": POSTGRES_MEMORY_SCHEMA_VERSION,
        "sequence_schema_version": POSTGRES_SEQUENCE_V2_SCHEMA_VERSION,
        "passed": True,
    }


def status(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        version = connection.execute(
            "SELECT version FROM seocho_schema_versions WHERE component='agent-memory'"
        ).fetchone()
        sequence_version = connection.execute(
            "SELECT version FROM seocho_schema_versions "
            "WHERE component='agent-memory-sequence'"
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM agent_memory_revisions),"
            "(SELECT count(*) FROM agent_memory_idempotency),"
            "(SELECT count(*) FROM agent_memory_outbox),"
            "(SELECT count(*) FROM agent_memory_heads)"
        ).fetchone()
        sequence_counts = connection.execute(
            "SELECT (SELECT count(*) FROM agent_memory_sequence_heads_v2),"
            "(SELECT count(*) FROM agent_memory_sequence_leases_v2),"
            "(SELECT count(*) FROM agent_projection_watermarks_v2),"
            "(SELECT count(*) FROM agent_memory_outbox_v2)"
        ).fetchone()
    return {
        "operation": "status",
        "schema_version": version[0] if version else None,
        "sequence_schema_version": sequence_version[0] if sequence_version else None,
        "revisions": int(counts[0]),
        "idempotency": int(counts[1]),
        "outbox": int(counts[2]),
        "heads": int(counts[3]),
        "sequence_heads": int(sequence_counts[0]),
        "sequence_leases": int(sequence_counts[1]),
        "sequence_watermarks": int(sequence_counts[2]),
        "sequence_outbox": int(sequence_counts[3]),
        "passed": bool(
            version
            and version[0] == POSTGRES_MEMORY_SCHEMA_VERSION
            and sequence_version
            and sequence_version[0] == POSTGRES_SEQUENCE_V2_SCHEMA_VERSION
        ),
    }


def smoke(dsn: str) -> dict[str, object]:
    migrate(dsn)
    workspace = f"memory-smoke-{uuid.uuid4().hex[:12]}"
    repository = PostgreSQLMemoryRepository.connect(dsn)
    payload = {"state": "pending", "source": "seocho-memory-smoke"}
    result = repository.commit_revision(
        workspace_id=workspace,
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        provenance_id="memory-smoke",
        payload=payload,
        idempotency_key="memory-smoke-delivery-1",
    )
    replay = repository.commit_revision(
        workspace_id=workspace,
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at=result.revision.occurred_at,
        provenance_id="memory-smoke",
        payload=payload,
        idempotency_key="memory-smoke-delivery-1",
    )
    read = repository.read_revision(
        workspace_id=workspace,
        memory_id="transaction-1",
        at_sequence=result.revision.sequence,
    )
    allocator = PostgreSQLCausalSequenceAllocator.connect_pool(
        dsn,
        policy=SequencePolicy(mode=SequenceMode.SHARDED_DOMAIN, shards=4, lease_size=8),
        owner_id="memory-smoke",
        min_size=1,
        max_size=2,
    )
    first_position = allocator.allocate(
        workspace_id=workspace,
        domain="transaction",
        aggregate_id="transaction-1",
    )
    second_position = allocator.allocate(
        workspace_id=workspace,
        domain="transaction",
        aggregate_id="transaction-1",
    )
    with psycopg.connect(dsn) as connection:
        for ordinal, allocated in enumerate((first_position, second_position)):
            connection.execute(
                "INSERT INTO agent_memory_outbox_v2 "
                "(workspace_id,domain,shard,sequence,ordinal,operation,"
                " aggregate_type,aggregate_id,payload,lease_id) "
                "VALUES(%s,%s,%s,%s,%s,'upsert','memory_revision',%s,%s::jsonb,%s::uuid)",
                (
                    workspace,
                    allocated.position.domain,
                    allocated.position.shard,
                    allocated.position.sequence,
                    ordinal,
                    "transaction-1",
                    json.dumps({"state": "pending", "ordinal": ordinal}),
                    allocated.lease_id,
                ),
            )
    projector = PostgreSQLCausalProjectionRepository(lambda: psycopg.connect(dsn))
    abandoned = projector.claim_batch(
        workspace_id=workspace,
        domain=first_position.position.domain,
        shard=first_position.position.shard,
        worker_id="worker-abandoned",
        limit=2,
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE agent_memory_outbox_v2 SET claimed_at=now()-interval '60 seconds' "
            "WHERE workspace_id=%s",
            (workspace,),
        )
    reclaimed = projector.claim_batch(
        workspace_id=workspace,
        domain=first_position.position.domain,
        shard=first_position.position.shard,
        worker_id="worker-recovery",
        limit=2,
        reclaim_after_seconds=30,
    )
    stale_claim_rejected = False
    try:
        projector.acknowledge_batch(
            projection="smoke",
            worker_id="worker-abandoned",
            fencing_token=first_position.fencing_token,
            entries=abandoned,
        )
    except ProjectionFencingError:
        stale_claim_rejected = True
    projector.acknowledge_batch(
        projection="smoke",
        worker_id="worker-recovery",
        fencing_token=first_position.fencing_token,
        entries=reclaimed,
    )
    frontier_current = allocator.read_frontier_status(
        workspace_id=workspace,
        projection="smoke",
        required=CausalFrontier.for_workspace(workspace, second_position.position),
    )
    allocator.close()
    recovery_allocator = PostgreSQLCausalSequenceAllocator.connect_pool(
        dsn,
        policy=SequencePolicy(mode=SequenceMode.SHARDED_DOMAIN, shards=4, lease_size=8),
        owner_id="memory-smoke-recovery",
        min_size=1,
        max_size=2,
    )
    recovered_position = recovery_allocator.allocate(
        workspace_id=workspace,
        domain="transaction",
        aggregate_id="transaction-1",
    )
    recovery_allocator.close()
    observable_lease_gap = (
        recovered_position.position.sequence - second_position.position.sequence - 1
    )
    with psycopg.connect(dsn) as connection, connection.transaction():
        for table in (
            "agent_projection_watermarks_v2",
            "agent_memory_outbox_v2",
            "agent_memory_sequence_leases_v2",
            "agent_memory_sequence_heads_v2",
            "agent_memory_outbox",
            "agent_memory_idempotency",
            "agent_memory_revisions",
            "agent_memory_heads",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE workspace_id=%s", (workspace,)
            )
    passed = bool(
        result.applied
        and not replay.applied
        and read
        and read.payload.get("state") == "pending"
        and second_position.position.sequence == first_position.position.sequence + 1
        and frontier_current
        and len(abandoned) == 2
        and len(reclaimed) == 2
        and stale_claim_rejected
        and recovered_position.fencing_token > first_position.fencing_token
        and observable_lease_gap == 6
    )
    return {
        "operation": "smoke",
        "workspace": workspace,
        "atomic_commit": result.applied,
        "idempotent_replay": not replay.applied,
        "point_in_time_read": bool(read and read.sequence == result.revision.sequence),
        "leased_sequence": second_position.position.sequence,
        "causal_frontier_current": frontier_current,
        "stale_claim_rejected": stale_claim_rejected,
        "reclaimed_projection_entries": len(reclaimed),
        "recovered_lease_sequence": recovered_position.position.sequence,
        "observable_lease_gap": observable_lease_gap,
        "cleanup": True,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("migrate", "status", "smoke"))
    parser.add_argument("--dsn")
    args = parser.parse_args()
    dsn = _dsn(args.dsn)
    result = {"migrate": migrate, "status": status, "smoke": smoke}[args.operation](dsn)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
