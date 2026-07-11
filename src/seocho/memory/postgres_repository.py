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
    def fetchall(self) -> list[tuple[Any, ...]]: ...
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


class StaleAuthoritativeMemoryError(RuntimeError):
    """Raised when a caller requires a sequence not committed in PostgreSQL."""


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
        allowed_previous_event_types: tuple[str, ...] | None = None,
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
                    stored = self._read_revision(
                        cursor, workspace_id, memory_id, int(revision)
                    )
                    return MemoryCommitResult(
                        applied=False,
                        revision=stored,
                        causal_token=CausalToken.for_workspace(
                            workspace_id, int(sequence)
                        ),
                    )

                # Serialize writers for one logical memory while allowing
                # unrelated memories to progress concurrently.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{len(workspace_id)}:{workspace_id}{memory_id}",),
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
                    """SELECT revision, event_type FROM agent_memory_revisions
                       WHERE workspace_id = %s AND memory_id = %s
                       ORDER BY revision DESC LIMIT 1""",
                    (workspace_id, memory_id),
                )
                row = cursor.fetchone()
                previous_revision = int(row[0] if row else 0)
                previous_event_type = str(row[1]) if row and len(row) > 1 else ""
                if allowed_previous_event_types is not None:
                    allowed = set(allowed_previous_event_types)
                    valid = (
                        previous_revision == 0 and "__initial__" in allowed
                    ) or (
                        previous_revision > 0 and previous_event_type in allowed
                    )
                    if not valid:
                        actual = previous_event_type or "__initial__"
                        raise ValueError(
                            f"invalid memory transition from {actual} to {event_type}"
                        )
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
                        workspace_id,
                        memory_id,
                        revision,
                        sequence,
                        event_type,
                        occurred_at,
                        ingested_at,
                        provenance_id,
                        payload_json,
                        payload_hash,
                        supersedes,
                        canonical,
                        schema_version,
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
                        workspace_id,
                        idempotency_key,
                        memory_id,
                        revision,
                        sequence,
                        payload_hash,
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

    def read_revision(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        at_sequence: int | None = None,
        required_causal_token: CausalToken | None = None,
    ) -> MemoryRevision | None:
        """Read the latest logical revision at or before a memory sequence."""

        if not workspace_id.strip() or not memory_id.strip():
            raise ValueError("workspace_id and memory_id are required")
        if at_sequence is not None and at_sequence < 1:
            raise ValueError("at_sequence must be positive")
        if required_causal_token is not None:
            required_causal_token.assert_workspace(workspace_id)

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                if required_causal_token is not None:
                    cursor.execute(
                        """SELECT COALESCE(next_sequence - 1, 0)
                           FROM agent_memory_heads WHERE workspace_id = %s""",
                        (workspace_id,),
                    )
                    head = cursor.fetchone()
                    committed = int(head[0] if head else 0)
                    if committed < required_causal_token.sequence:
                        raise StaleAuthoritativeMemoryError(
                            f"committed sequence {committed} is behind required "
                            f"sequence {required_causal_token.sequence}"
                        )
                query = """SELECT revision, sequence, event_type, occurred_at,
                                  ingested_at, provenance_id, payload,
                                  supersedes_revision, canonical, schema_version
                           FROM agent_memory_revisions
                           WHERE workspace_id = %s AND memory_id = %s"""
                params: tuple[Any, ...] = (workspace_id, memory_id)
                if at_sequence is not None:
                    query += " AND sequence <= %s"
                    params += (at_sequence,)
                query += " ORDER BY sequence DESC LIMIT 1"
                cursor.execute(query, params)
                row = cursor.fetchone()
                return (
                    None
                    if row is None
                    else self._revision_from_read_row(workspace_id, memory_id, row)
                )

    def read_history(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        through_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[MemoryRevision, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                query = """SELECT revision, sequence, event_type, occurred_at,
                                  ingested_at, provenance_id, payload,
                                  supersedes_revision, canonical, schema_version
                           FROM agent_memory_revisions
                           WHERE workspace_id = %s AND memory_id = %s"""
                params: tuple[Any, ...] = (workspace_id, memory_id)
                if through_sequence is not None:
                    if through_sequence < 1:
                        raise ValueError("through_sequence must be positive")
                    query += " AND sequence <= %s"
                    params += (through_sequence,)
                query += " ORDER BY sequence DESC LIMIT %s"
                cursor.execute(query, params + (limit,))
                return tuple(
                    self._revision_from_read_row(workspace_id, memory_id, row)
                    for row in cursor.fetchall()
                )

    def read_outbox_batch(
        self, *, workspace_id: str, limit: int = 100
    ) -> tuple[Any, ...]:
        """Read an ordered, bounded pending projection batch."""

        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        from .agent_projection import AgentProjectionEntry

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT sequence, ordinal, aggregate_id, payload
                       FROM agent_memory_outbox
                       WHERE workspace_id = %s AND projected_at IS NULL
                       ORDER BY sequence, ordinal LIMIT %s""",
                    (workspace_id, limit),
                )
                entries = []
                for sequence, ordinal, aggregate_id, payload in cursor.fetchall():
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    entries.append(
                        AgentProjectionEntry(
                            workspace_id=workspace_id,
                            sequence=int(sequence),
                            ordinal=int(ordinal),
                            aggregate_id=str(aggregate_id),
                            payload=dict(payload),
                        )
                    )
                return tuple(entries)

    def acknowledge_projection(
        self,
        *,
        workspace_id: str,
        projection: str,
        applied_sequence: int,
        entries: tuple[Any, ...],
    ) -> None:
        """Atomically mark exactly one batch and advance a monotonic watermark."""

        if applied_sequence < 1 or not entries:
            raise ValueError("a non-empty applied projection batch is required")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                for entry in entries:
                    cursor.execute(
                        """UPDATE agent_memory_outbox SET projected_at = now()
                           WHERE workspace_id = %s AND sequence = %s AND ordinal = %s
                             AND projected_at IS NULL""",
                        (workspace_id, entry.sequence, entry.ordinal),
                    )
                cursor.execute(
                    """INSERT INTO agent_projection_watermarks
                       (workspace_id, projection, applied_sequence, fencing_token)
                       VALUES (%s, %s, %s, 0)
                       ON CONFLICT (workspace_id, projection) DO UPDATE
                       SET applied_sequence = GREATEST(
                               agent_projection_watermarks.applied_sequence,
                               EXCLUDED.applied_sequence),
                           updated_at = now()""",
                    (workspace_id, projection, applied_sequence),
                )

    @staticmethod
    def _revision_from_read_row(
        workspace_id: str, memory_id: str, row: tuple[Any, ...]
    ) -> MemoryRevision:
        payload = row[6]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return MemoryRevision(
            workspace_id=workspace_id,
            memory_id=memory_id,
            revision=int(row[0]),
            sequence=int(row[1]),
            event_type=str(row[2]),
            occurred_at=str(row[3]),
            ingested_at=str(row[4]),
            provenance_id=str(row[5]),
            payload=dict(payload),
            supersedes_revision=None if row[7] is None else int(row[7]),
            canonical=bool(row[8]),
            schema_version=str(row[9]),
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


__all__ = [
    "MemoryCommitResult",
    "PostgreSQLMemoryRepository",
    "StaleAuthoritativeMemoryError",
]
