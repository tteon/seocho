from typing import Any

import pytest

from seocho.memory import (
    CausalFrontier,
    CausalPosition,
    CausalOutboxEntry,
    PostgreSQLCausalProjectionRepository,
    PostgreSQLCausalSequenceAllocator,
    ProjectionFencingError,
    SequenceMode,
    SequencePolicy,
)


class Cursor:
    def __init__(self, fetches: list[Any]) -> None:
        self.fetches = fetches
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self) -> Any:
        return self.fetches.pop(0)

    def fetchall(self) -> list[Any]:
        return self.fetches.pop(0)

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> Cursor:
        return self._cursor

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def allocator(cursor: Cursor, *, lease_size: int = 4):
    policy = SequencePolicy(
        mode=SequenceMode.SHARDED_DOMAIN, shards=8, lease_size=lease_size
    )
    return PostgreSQLCausalSequenceAllocator(
        lambda: Connection(cursor), policy=policy, owner_id="worker-a"
    )


def test_allocator_reserves_once_then_consumes_local_range() -> None:
    cursor = Cursor([(9, 12, 3)])
    subject = allocator(cursor)

    first = subject.allocate(
        workspace_id="ws-1", domain="transaction", aggregate_id="wallet-a"
    )
    second = subject.allocate(
        workspace_id="ws-1", domain="transaction", aggregate_id="wallet-a"
    )

    assert (first.position.sequence, second.position.sequence) == (9, 10)
    assert first.position.shard == second.position.shard
    assert first.lease_id == second.lease_id
    assert first.fencing_token == 3
    assert len([q for q, _ in cursor.executed if "sequence_heads_v2" in q]) == 1
    assert len([q for q, _ in cursor.executed if "sequence_leases_v2" in q]) == 1


def test_allocator_reserves_next_range_after_local_exhaustion() -> None:
    cursor = Cursor([(1, 2, 1), (3, 4, 2)])
    subject = allocator(cursor, lease_size=2)

    allocated = [
        subject.allocate(
            workspace_id="ws-1", domain="transaction", aggregate_id="wallet-a"
        )
        for _ in range(3)
    ]

    assert [item.position.sequence for item in allocated] == [1, 2, 3]
    assert allocated[-1].fencing_token == 2


def test_shard_acknowledgement_rejects_stale_fence() -> None:
    cursor = Cursor([None])
    subject = allocator(cursor)

    with pytest.raises(ProjectionFencingError, match="stale shard"):
        subject.acknowledge(
            workspace_id="ws-1",
            projection="dozerdb",
            position=CausalPosition("transaction", 2, 10),
            fencing_token=4,
        )


def test_frontier_status_uses_every_physical_shard() -> None:
    cursor = Cursor([[("policy", 0, 7), ("transaction", 2, 14)]])
    subject = allocator(cursor)
    required = CausalFrontier.for_workspace(
        "ws-1",
        CausalPosition("policy", 0, 7),
        CausalPosition("transaction", 2, 14),
    )

    assert subject.read_frontier_status(
        workspace_id="ws-1", projection="dozerdb", required=required
    )


def test_projection_claim_uses_skip_locked_and_returns_typed_positions() -> None:
    cursor = Cursor([[(9, 0, "wallet-a", {"state": "confirmed"}, "lease-1")]])
    repository = PostgreSQLCausalProjectionRepository(lambda: Connection(cursor))

    entries = repository.claim_batch(
        workspace_id="ws-1",
        domain="transaction",
        shard=3,
        worker_id="projector-a",
        limit=10,
    )

    assert entries[0].position == CausalPosition("transaction", 3, 9)
    assert "FOR UPDATE SKIP LOCKED" in cursor.executed[0][0]
    assert cursor.executed[0][1][-1] == "projector-a"


def test_projection_acknowledgement_is_one_shard_and_owner_fenced() -> None:
    entries = (
        CausalOutboxEntry(
            "ws-1", CausalPosition("transaction", 3, 9), 0, "wallet-a", {}, "lease-1"
        ),
        CausalOutboxEntry(
            "ws-1", CausalPosition("transaction", 3, 10), 0, "wallet-b", {}, "lease-1"
        ),
    )
    cursor = Cursor([(9,), (10,), (10,)])
    repository = PostgreSQLCausalProjectionRepository(lambda: Connection(cursor))

    applied = repository.acknowledge_batch(
        projection="dozerdb",
        worker_id="projector-a",
        fencing_token=7,
        entries=entries,
    )

    assert applied == CausalPosition("transaction", 3, 10)
    assert all("claimed_by = %s" in query for query, _ in cursor.executed[:2])
    assert "agent_projection_watermarks_v2" in cursor.executed[-1][0]


def test_projection_acknowledgement_rejects_lost_claim() -> None:
    entry = CausalOutboxEntry(
        "ws-1", CausalPosition("transaction", 3, 9), 0, "wallet-a", {}, "lease-1"
    )
    repository = PostgreSQLCausalProjectionRepository(
        lambda: Connection(Cursor([None]))
    )

    with pytest.raises(ProjectionFencingError, match="claim was lost"):
        repository.acknowledge_batch(
            projection="dozerdb",
            worker_id="stale-projector",
            fencing_token=6,
            entries=(entry,),
        )
