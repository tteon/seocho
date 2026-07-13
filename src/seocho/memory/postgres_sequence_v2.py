"""Opt-in PostgreSQL v2 sequence leasing and causal projection progress.

These tables are intentionally separate from the v1 total-order schema.  A
reserved but unused number is an observable lease gap, not evidence that an
event committed.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Callable

from .postgres_repository import Connection, ProjectionFencingError
from .sequence import CausalFrontier, CausalPosition, SequenceMode, SequencePolicy

POSTGRES_SEQUENCE_V2_SCHEMA_VERSION = "agent-memory-sequence-pg.v2alpha1"

POSTGRES_SEQUENCE_V2_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS agent_memory_sequence_heads_v2 (
    workspace_id text NOT NULL,
    domain text NOT NULL,
    shard integer NOT NULL CHECK (shard >= 0),
    next_sequence bigint NOT NULL CHECK (next_sequence > 0),
    next_fencing_token bigint NOT NULL CHECK (next_fencing_token > 0),
    PRIMARY KEY (workspace_id, domain, shard)
);

CREATE TABLE IF NOT EXISTS agent_memory_sequence_leases_v2 (
    lease_id uuid PRIMARY KEY,
    workspace_id text NOT NULL,
    domain text NOT NULL,
    shard integer NOT NULL CHECK (shard >= 0),
    owner_id text NOT NULL,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    range_start bigint NOT NULL CHECK (range_start > 0),
    range_end bigint NOT NULL CHECK (range_end >= range_start),
    acquired_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    UNIQUE (workspace_id, domain, shard, fencing_token)
);

CREATE INDEX IF NOT EXISTS agent_memory_sequence_leases_v2_lookup_idx
    ON agent_memory_sequence_leases_v2
       (workspace_id, domain, shard, acquired_at DESC);

CREATE TABLE IF NOT EXISTS agent_memory_outbox_v2 (
    workspace_id text NOT NULL,
    domain text NOT NULL,
    shard integer NOT NULL CHECK (shard >= 0),
    sequence bigint NOT NULL CHECK (sequence > 0),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    operation text NOT NULL CHECK (operation IN ('upsert', 'retract')),
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    payload jsonb NOT NULL,
    lease_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    claimed_by text,
    claimed_at timestamptz,
    projected_at timestamptz,
    PRIMARY KEY (workspace_id, domain, shard, sequence, ordinal)
);

CREATE INDEX IF NOT EXISTS agent_memory_outbox_v2_pending_idx
    ON agent_memory_outbox_v2 (workspace_id, domain, shard, sequence, ordinal)
    WHERE projected_at IS NULL;

CREATE TABLE IF NOT EXISTS agent_projection_watermarks_v2 (
    workspace_id text NOT NULL,
    projection text NOT NULL,
    domain text NOT NULL,
    shard integer NOT NULL CHECK (shard >= 0),
    applied_sequence bigint NOT NULL CHECK (applied_sequence >= 0),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, projection, domain, shard)
);
""".strip()


def postgres_sequence_v2_schema_statements() -> tuple[str, ...]:
    return tuple(
        statement.strip() + ";"
        for statement in POSTGRES_SEQUENCE_V2_SCHEMA_SQL.split(";")
        if statement.strip()
    )


@dataclass(frozen=True, slots=True)
class AllocatedPosition:
    position: CausalPosition
    lease_id: str
    fencing_token: int


@dataclass(slots=True)
class _LocalLease:
    lease_id: str
    fencing_token: int
    next_value: int
    range_end: int


@dataclass(frozen=True, slots=True)
class CausalOutboxEntry:
    workspace_id: str
    position: CausalPosition
    ordinal: int
    aggregate_id: str
    payload: object
    lease_id: str


class PostgreSQLCausalSequenceAllocator:
    """Reserve fenced ranges in PostgreSQL and allocate locally within them."""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        *,
        policy: SequencePolicy,
        owner_id: str,
    ) -> None:
        if policy.mode is SequenceMode.STRICT_WORKSPACE:
            raise ValueError("v2 allocator requires a leased or sharded policy")
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self._connection_factory = connection_factory
        self.policy = policy
        self.owner_id = owner_id
        self._leases: dict[tuple[str, str, int], _LocalLease] = {}
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str, int], threading.Lock] = {}

    @classmethod
    def connect_pool(
        cls,
        dsn: str,
        *,
        policy: SequencePolicy,
        owner_id: str,
        min_size: int = 1,
        max_size: int = 8,
    ) -> "PostgreSQLCausalSequenceAllocator":
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise ImportError("sequence pooling requires psycopg_pool") from exc
        pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        allocator = cls(pool.connection, policy=policy, owner_id=owner_id)
        allocator._pool = pool
        return allocator

    def close(self) -> None:
        pool = getattr(self, "_pool", None)
        if pool is not None:
            self._pool = None
            pool.close()

    def _lock_for(self, key: tuple[str, str, int]) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def allocate(
        self, *, workspace_id: str, domain: str, aggregate_id: str
    ) -> AllocatedPosition:
        if not workspace_id.strip() or not domain.strip():
            raise ValueError("workspace_id and domain are required")
        shard = self.policy.shard_for(aggregate_id)
        key = (workspace_id, domain, shard)
        with self._lock_for(key):
            lease = self._leases.get(key)
            if lease is None or lease.next_value > lease.range_end:
                lease = self._reserve(workspace_id, domain, shard)
                self._leases[key] = lease
            sequence = lease.next_value
            lease.next_value += 1
            return AllocatedPosition(
                position=CausalPosition(domain, shard, sequence),
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
            )

    def _reserve(self, workspace_id: str, domain: str, shard: int) -> _LocalLease:
        lease_id = str(uuid.uuid4())
        size = self.policy.lease_size
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO agent_memory_sequence_heads_v2
                       (workspace_id, domain, shard, next_sequence, next_fencing_token)
                       VALUES (%s, %s, %s, %s, 2)
                       ON CONFLICT (workspace_id, domain, shard) DO UPDATE
                       SET next_sequence = agent_memory_sequence_heads_v2.next_sequence
                                           + EXCLUDED.next_sequence - 1,
                           next_fencing_token =
                               agent_memory_sequence_heads_v2.next_fencing_token + 1
                       RETURNING next_sequence - %s, next_sequence - 1,
                                 next_fencing_token - 1""",
                    (workspace_id, domain, shard, size + 1, size),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("sequence lease reservation returned no range")
                range_start, range_end, fencing_token = map(int, row)
                cursor.execute(
                    """INSERT INTO agent_memory_sequence_leases_v2
                       (lease_id, workspace_id, domain, shard, owner_id,
                        fencing_token, range_start, range_end)
                       VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        lease_id,
                        workspace_id,
                        domain,
                        shard,
                        self.owner_id,
                        fencing_token,
                        range_start,
                        range_end,
                    ),
                )
        return _LocalLease(lease_id, fencing_token, range_start, range_end)

    def acknowledge(
        self,
        *,
        workspace_id: str,
        projection: str,
        position: CausalPosition,
        fencing_token: int,
    ) -> None:
        """Advance exactly one shard watermark using a monotonic worker fence."""

        if fencing_token < 0:
            raise ValueError("fencing_token cannot be negative")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO agent_projection_watermarks_v2
                       (workspace_id, projection, domain, shard,
                        applied_sequence, fencing_token)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (workspace_id, projection, domain, shard) DO UPDATE
                       SET applied_sequence = GREATEST(
                               agent_projection_watermarks_v2.applied_sequence,
                               EXCLUDED.applied_sequence),
                           fencing_token = EXCLUDED.fencing_token,
                           updated_at = now()
                       WHERE agent_projection_watermarks_v2.fencing_token
                             <= EXCLUDED.fencing_token
                       RETURNING fencing_token""",
                    (
                        workspace_id,
                        projection,
                        position.domain,
                        position.shard,
                        position.sequence,
                        fencing_token,
                    ),
                )
                if cursor.fetchone() is None:
                    raise ProjectionFencingError("stale shard projector was rejected")

    def read_frontier_status(
        self,
        *,
        workspace_id: str,
        projection: str,
        required: CausalFrontier,
    ) -> bool:
        required.assert_workspace(workspace_id)
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT domain, shard, applied_sequence
                       FROM agent_projection_watermarks_v2
                       WHERE workspace_id = %s AND projection = %s""",
                    (workspace_id, projection),
                )
                watermarks = {
                    (str(domain), int(shard)): int(sequence)
                    for domain, shard, sequence in cursor.fetchall()
                }
        return required.satisfied_by(watermarks)


class PostgreSQLCausalProjectionRepository:
    """Claim v2 outbox work concurrently and acknowledge shard-local progress."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def claim_batch(
        self,
        *,
        workspace_id: str,
        domain: str,
        shard: int,
        worker_id: str,
        limit: int = 100,
        reclaim_after_seconds: float = 30.0,
    ) -> tuple[CausalOutboxEntry, ...]:
        if not workspace_id.strip() or not domain.strip() or not worker_id.strip():
            raise ValueError("workspace_id, domain, and worker_id are required")
        if shard < 0 or limit < 1 or limit > 1000 or reclaim_after_seconds < 0:
            raise ValueError("invalid shard, limit, or reclaim interval")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """WITH candidates AS (
                           SELECT workspace_id, domain, shard, sequence, ordinal
                           FROM agent_memory_outbox_v2
                           WHERE workspace_id = %s AND domain = %s AND shard = %s
                             AND projected_at IS NULL
                             AND (claimed_at IS NULL OR claimed_at <
                                  now() - make_interval(secs => %s))
                           ORDER BY sequence, ordinal
                           FOR UPDATE SKIP LOCKED LIMIT %s
                       )
                       UPDATE agent_memory_outbox_v2 AS outbox
                       SET claimed_by = %s, claimed_at = now()
                       FROM candidates
                       WHERE outbox.workspace_id = candidates.workspace_id
                         AND outbox.domain = candidates.domain
                         AND outbox.shard = candidates.shard
                         AND outbox.sequence = candidates.sequence
                         AND outbox.ordinal = candidates.ordinal
                       RETURNING outbox.sequence, outbox.ordinal,
                                 outbox.aggregate_id, outbox.payload,
                                 outbox.lease_id::text""",
                    (
                        workspace_id,
                        domain,
                        shard,
                        reclaim_after_seconds,
                        limit,
                        worker_id,
                    ),
                )
                rows = cursor.fetchall()
        return tuple(
            CausalOutboxEntry(
                workspace_id=workspace_id,
                position=CausalPosition(domain, shard, int(sequence)),
                ordinal=int(ordinal),
                aggregate_id=str(aggregate_id),
                payload=payload,
                lease_id=str(lease_id),
            )
            for sequence, ordinal, aggregate_id, payload, lease_id in rows
        )

    def acknowledge_batch(
        self,
        *,
        projection: str,
        worker_id: str,
        fencing_token: int,
        entries: tuple[CausalOutboxEntry, ...],
    ) -> CausalPosition:
        if not projection.strip() or not worker_id.strip() or not entries:
            raise ValueError("projection, worker_id, and entries are required")
        first = entries[0]
        if any(
            entry.workspace_id != first.workspace_id
            or entry.position.domain != first.position.domain
            or entry.position.shard != first.position.shard
            for entry in entries
        ):
            raise ValueError("one acknowledgement batch must belong to one shard")
        high = max(entry.position.sequence for entry in entries)
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                for entry in entries:
                    cursor.execute(
                        """UPDATE agent_memory_outbox_v2 SET projected_at = now()
                           WHERE workspace_id = %s AND domain = %s AND shard = %s
                             AND sequence = %s AND ordinal = %s
                             AND projected_at IS NULL AND claimed_by = %s
                           RETURNING sequence""",
                        (
                            entry.workspace_id,
                            entry.position.domain,
                            entry.position.shard,
                            entry.position.sequence,
                            entry.ordinal,
                            worker_id,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise ProjectionFencingError(
                            "outbox claim was lost before acknowledgement"
                        )
                cursor.execute(
                    """INSERT INTO agent_projection_watermarks_v2
                       (workspace_id, projection, domain, shard,
                        applied_sequence, fencing_token)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (workspace_id, projection, domain, shard) DO UPDATE
                       SET applied_sequence = GREATEST(
                               agent_projection_watermarks_v2.applied_sequence,
                               EXCLUDED.applied_sequence),
                           fencing_token = EXCLUDED.fencing_token,
                           updated_at = now()
                       WHERE agent_projection_watermarks_v2.fencing_token
                             <= EXCLUDED.fencing_token
                       RETURNING applied_sequence""",
                    (
                        first.workspace_id,
                        projection,
                        first.position.domain,
                        first.position.shard,
                        high,
                        fencing_token,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ProjectionFencingError("stale shard projector was rejected")
                applied = int(row[0])
        return CausalPosition(first.position.domain, first.position.shard, applied)


__all__ = [
    "AllocatedPosition",
    "CausalOutboxEntry",
    "POSTGRES_SEQUENCE_V2_SCHEMA_SQL",
    "POSTGRES_SEQUENCE_V2_SCHEMA_VERSION",
    "PostgreSQLCausalSequenceAllocator",
    "PostgreSQLCausalProjectionRepository",
    "postgres_sequence_v2_schema_statements",
]
