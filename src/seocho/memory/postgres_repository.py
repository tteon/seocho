"""Atomic PostgreSQL repository for authoritative agent-memory revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .contracts import MemoryRevision
from .models import CausalToken


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def __enter__(self) -> "Cursor": ...
    def __exit__(self, *args: object) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def __enter__(self) -> "Connection": ...
    def __exit__(self, *args: object) -> None: ...


@dataclass(frozen=True, slots=True)
class MemoryCommitResult:
    applied: bool
    revision: MemoryRevision
    causal_token: CausalToken


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class PostgreSQLMemoryRepository:
    """Commit revision, idempotency receipt, and projection outbox atomically."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def connect(cls, dsn: str) -> "PostgreSQLMemoryRepository":
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL memory requires the optional psycopg dependency"
            ) from exc
        return cls(lambda: psycopg.connect(dsn))

    def commit_revision(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        event_type: str,
        occurred_at: str,
        provenance_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        schema_version: str = "agent-memory.v1",
        canonical: bool = True,
    ) -> MemoryCommitResult:
        required = (
            workspace_id,
            memory_id,
            event_type,
            occurred_at,
            provenance_id,
            idempotency_key,
            schema_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("memory commit identifiers are required")
        payload_json = _canonical_json(payload)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        ingested_at = datetime.now(timezone.utc).isoformat()

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT memory_id, revision, sequence, payload_hash
                       FROM agent_memory_idempotency
                       WHERE workspace_id = %s AND idempotency_key = %s""",
                    (workspace_id, idempotency_key),
                )
                receipt = cursor.fetchone()
                if receipt is not None:
                    prior_memory_id, revision, sequence, prior_hash = receipt
                    if prior_memory_id != memory_id or prior_hash != payload_hash:
                        raise ValueError(
                            "idempotency key was reused with a different payload"
                        )
                    stored = self._read_revision(cursor, workspace_id, memory_id, int(revision))
                    return MemoryCommitResult(
                        applied=False,
                        revision=stored,
                        causal_token=CausalToken.for_workspace(workspace_id, int(sequence)),
                    )

                # Serialize writers for one logical memory while allowing
                # unrelated memories to progress concurrently.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{workspace_id}\0{memory_id}",),
                )
                cursor.execute(
                    """INSERT INTO agent_memory_heads (workspace_id, next_sequence)
                       VALUES (%s, 1) ON CONFLICT (workspace_id) DO NOTHING""",
                    (workspace_id,),
                )
                cursor.execute(
                    "SELECT next_sequence FROM agent_memory_heads WHERE workspace_id = %s FOR UPDATE",
                    (workspace_id,),
                )
                head = cursor.fetchone()
                if head is None:
                    raise RuntimeError("memory sequence head was not created")
                sequence = int(head[0])
                cursor.execute(
                    "UPDATE agent_memory_heads SET next_sequence = %s WHERE workspace_id = %s",
                    (sequence + 1, workspace_id),
                )
                cursor.execute(
                    """SELECT COALESCE(MAX(revision), 0)
                       FROM agent_memory_revisions
                       WHERE workspace_id = %s AND memory_id = %s""",
                    (workspace_id, memory_id),
                )
                row = cursor.fetchone()
                previous_revision = int(row[0] if row else 0)
                revision = previous_revision + 1
                supersedes = previous_revision or None
                if canonical and previous_revision:
                    cursor.execute(
                        """UPDATE agent_memory_revisions SET canonical = false
                           WHERE workspace_id = %s AND memory_id = %s AND canonical""",
                        (workspace_id, memory_id),
                    )
                cursor.execute(
                    """INSERT INTO agent_memory_revisions
                       (workspace_id, memory_id, revision, sequence, event_type,
                        occurred_at, ingested_at, provenance_id, payload, payload_hash,
                        supersedes_revision, canonical, schema_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)""",
                    (
                        workspace_id, memory_id, revision, sequence, event_type,
                        occurred_at, ingested_at, provenance_id, payload_json,
                        payload_hash, supersedes, canonical, schema_version,
                    ),
                )
                cursor.execute(
                    """INSERT INTO agent_memory_outbox
                       (workspace_id, sequence, ordinal, operation, aggregate_type,
                        aggregate_id, payload) VALUES (%s, %s, 0, 'upsert',
                        'memory_revision', %s, %s::jsonb)""",
                    (workspace_id, sequence, memory_id, payload_json),
                )
                cursor.execute(
                    """INSERT INTO agent_memory_idempotency
                       (workspace_id, idempotency_key, memory_id, revision, sequence,
                        payload_hash) VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        workspace_id, idempotency_key, memory_id, revision,
                        sequence, payload_hash,
                    ),
                )
                stored = MemoryRevision(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    revision=revision,
                    sequence=sequence,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    ingested_at=ingested_at,
                    provenance_id=provenance_id,
                    payload=dict(payload),
                    supersedes_revision=supersedes,
                    canonical=canonical,
                    schema_version=schema_version,
                )
                return MemoryCommitResult(
                    applied=True,
                    revision=stored,
                    causal_token=CausalToken.for_workspace(workspace_id, sequence),
                )

    @staticmethod
    def _read_revision(
        cursor: Cursor, workspace_id: str, memory_id: str, revision: int
    ) -> MemoryRevision:
        cursor.execute(
            """SELECT sequence, event_type, occurred_at, ingested_at, provenance_id,
                      payload, supersedes_revision, canonical, schema_version
               FROM agent_memory_revisions
               WHERE workspace_id = %s AND memory_id = %s AND revision = %s""",
            (workspace_id, memory_id, revision),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("idempotency receipt references a missing revision")
        payload = row[5]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return MemoryRevision(
            workspace_id=workspace_id,
            memory_id=memory_id,
            revision=revision,
            sequence=int(row[0]),
            event_type=str(row[1]),
            occurred_at=str(row[2]),
            ingested_at=str(row[3]),
            provenance_id=str(row[4]),
            payload=dict(payload),
            supersedes_revision=None if row[6] is None else int(row[6]),
            canonical=bool(row[7]),
            schema_version=str(row[8]),
        )


__all__ = ["MemoryCommitResult", "PostgreSQLMemoryRepository"]
