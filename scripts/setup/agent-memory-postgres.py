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
    POSTGRES_MEMORY_SCHEMA_SQL,
    POSTGRES_MEMORY_SCHEMA_VERSION,
    PostgreSQLMemoryRepository,
)


def _dsn(value: str | None) -> str:
    resolved = value or os.getenv("SEOCHO_POSTGRES_DSN", "")
    if not resolved.strip():
        raise SystemExit("--dsn or SEOCHO_POSTGRES_DSN is required")
    return resolved


def migrate(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection, connection.transaction():
        connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS seocho_schema_versions("
            "component text PRIMARY KEY,version text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())"
        )
        connection.execute(
            "INSERT INTO seocho_schema_versions(component,version) VALUES(%s,%s) "
            "ON CONFLICT(component) DO UPDATE SET version=EXCLUDED.version,applied_at=now()",
            ("agent-memory", POSTGRES_MEMORY_SCHEMA_VERSION),
        )
    return {
        "operation": "migrate",
        "schema_version": POSTGRES_MEMORY_SCHEMA_VERSION,
        "passed": True,
    }


def status(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        version = connection.execute(
            "SELECT version FROM seocho_schema_versions WHERE component='agent-memory'"
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM agent_memory_revisions),"
            "(SELECT count(*) FROM agent_memory_idempotency),"
            "(SELECT count(*) FROM agent_memory_outbox),"
            "(SELECT count(*) FROM agent_memory_heads)"
        ).fetchone()
    return {
        "operation": "status",
        "schema_version": version[0] if version else None,
        "revisions": int(counts[0]),
        "idempotency": int(counts[1]),
        "outbox": int(counts[2]),
        "heads": int(counts[3]),
        "passed": bool(version and version[0] == POSTGRES_MEMORY_SCHEMA_VERSION),
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
    with psycopg.connect(dsn) as connection, connection.transaction():
        for table in (
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
    )
    return {
        "operation": "smoke",
        "workspace": workspace,
        "atomic_commit": result.applied,
        "idempotent_replay": not replay.applied,
        "point_in_time_read": bool(read and read.sequence == result.revision.sequence),
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
