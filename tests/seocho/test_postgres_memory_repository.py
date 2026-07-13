from typing import Any

import pytest

from seocho.memory import (
    CausalToken,
    PostgreSQLMemoryRepository,
    ProjectionFencingError,
    StaleAuthoritativeMemoryError,
)


class FakeCursor:
    def __init__(self, fetches: list[tuple[Any, ...] | None]) -> None:
        self.fetches = fetches
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.fetches.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self.fetches.pop(0)
        return [] if rows is None else list(rows)  # type: ignore[arg-type]

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.exit_exception: object = "not-exited"

    def cursor(self) -> FakeCursor:
        return self._cursor

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        self.exit_exception = exc_type


class RecordingObserver:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: list[tuple[str, float, str]] = []

    def record(self, phase: str, elapsed_ms: float, outcome: str) -> None:
        self.records.append((phase, elapsed_ms, outcome))
        if self.fail:
            raise RuntimeError("telemetry unavailable")


def test_commit_is_one_transaction_for_revision_outbox_and_idempotency() -> None:
    cursor = FakeCursor([None, (1,), None])
    connection = FakeConnection(cursor)
    repository = PostgreSQLMemoryRepository(lambda: connection)

    result = repository.commit_revision(
        workspace_id="ws-1",
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at="2026-07-11T00:00:00+00:00",
        provenance_id="source-1",
        payload={"state": "pending"},
        idempotency_key="delivery-1",
    )

    assert result.applied is True
    assert result.revision.revision == 1
    assert result.causal_token.sequence == 1
    statements = "\n".join(query for query, _ in cursor.executed)
    assert "pg_advisory_xact_lock" in statements
    assert "UPDATE agent_memory_heads" in statements
    assert "FOR UPDATE" not in statements
    assert "INSERT INTO agent_memory_revisions" in statements
    assert "INSERT INTO agent_memory_outbox" in statements
    assert "INSERT INTO agent_memory_idempotency" in statements
    assert connection.exit_exception is None


def test_first_writer_initializes_strict_sequence_without_select_for_update() -> None:
    cursor = FakeCursor([None, None, (1,), None])
    repository = PostgreSQLMemoryRepository(lambda: FakeConnection(cursor))

    result = repository.commit_revision(
        workspace_id="new-ws",
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at="2026-07-13T00:00:00+00:00",
        provenance_id="source-1",
        payload={"state": "pending"},
        idempotency_key="delivery-1",
    )

    statements = "\n".join(query for query, _ in cursor.executed)
    assert result.causal_token.sequence == 1
    assert "INSERT INTO agent_memory_heads" in statements
    assert "FOR UPDATE" not in statements


def test_sequence_initialization_race_retries_atomic_update() -> None:
    cursor = FakeCursor([None, None, None, (2,), None])
    repository = PostgreSQLMemoryRepository(lambda: FakeConnection(cursor))

    result = repository.commit_revision(
        workspace_id="raced-ws",
        memory_id="transaction-2",
        event_type="transaction.pending",
        occurred_at="2026-07-13T00:00:00+00:00",
        provenance_id="source-2",
        payload={"state": "pending"},
        idempotency_key="delivery-2",
    )

    head_updates = [
        query for query, _ in cursor.executed if "UPDATE agent_memory_heads" in query
    ]
    assert len(head_updates) == 2
    assert result.causal_token.sequence == 2


def test_commit_observer_records_bounded_phases() -> None:
    cursor = FakeCursor([None, (1,), None])
    observer = RecordingObserver()
    repository = PostgreSQLMemoryRepository(
        lambda: FakeConnection(cursor), phase_observer=observer
    )

    repository.commit_revision(
        workspace_id="ws-1",
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at="2026-07-13T00:00:00+00:00",
        provenance_id="source-1",
        payload={"state": "pending"},
        idempotency_key="delivery-1",
    )

    phases = {phase for phase, _, outcome in observer.records if outcome == "ok"}
    assert phases == {
        "connection_scope",
        "idempotency_lookup",
        "aggregate_lock",
        "sequence_allocate",
        "revision_lookup",
        "memory_writes",
    }
    assert all(elapsed_ms >= 0 for _, elapsed_ms, _ in observer.records)


def test_observer_failure_never_changes_authoritative_commit() -> None:
    cursor = FakeCursor([None, (1,), None])
    observer = RecordingObserver(fail=True)
    repository = PostgreSQLMemoryRepository(
        lambda: FakeConnection(cursor), phase_observer=observer
    )

    result = repository.commit_revision(
        workspace_id="ws-1",
        memory_id="transaction-1",
        event_type="transaction.pending",
        occurred_at="2026-07-13T00:00:00+00:00",
        provenance_id="source-1",
        payload={"state": "pending"},
        idempotency_key="delivery-1",
    )

    assert result.applied is True
    assert observer.records


def test_pool_configuration_is_validated_before_optional_import() -> None:
    with pytest.raises(ValueError, match="pool sizes"):
        PostgreSQLMemoryRepository.connect_pool(
            "postgresql://example", min_size=5, max_size=4
        )


def test_idempotency_key_reuse_with_different_payload_rolls_back() -> None:
    cursor = FakeCursor([("transaction-1", 1, 1, "different-hash")])
    connection = FakeConnection(cursor)
    repository = PostgreSQLMemoryRepository(lambda: connection)

    with pytest.raises(ValueError, match="different payload"):
        repository.commit_revision(
            workspace_id="ws-1",
            memory_id="transaction-1",
            event_type="transaction.pending",
            occurred_at="2026-07-11T00:00:00+00:00",
            provenance_id="source-1",
            payload={"state": "confirmed"},
            idempotency_key="delivery-1",
        )

    assert connection.exit_exception is ValueError


def test_point_in_time_read_returns_revision_at_bounded_sequence() -> None:
    row = (
        2,
        7,
        "transaction.pending",
        "2026-07-11T00:00:00+00:00",
        "2026-07-11T00:00:01+00:00",
        "source-2",
        '{"state":"pending"}',
        1,
        False,
        "agent-memory.v1",
    )
    cursor = FakeCursor([row])
    repository = PostgreSQLMemoryRepository(lambda: FakeConnection(cursor))

    revision = repository.read_revision(
        workspace_id="ws-1", memory_id="transaction-1", at_sequence=7
    )

    assert revision is not None
    assert revision.revision == 2
    assert revision.sequence == 7
    assert revision.payload["state"] == "pending"
    assert cursor.executed[-1][1] == ("ws-1", "transaction-1", 7)


def test_causal_read_rejects_uncommitted_required_sequence() -> None:
    cursor = FakeCursor([(8,)])
    repository = PostgreSQLMemoryRepository(lambda: FakeConnection(cursor))

    with pytest.raises(StaleAuthoritativeMemoryError, match="behind required"):
        repository.read_revision(
            workspace_id="ws-1",
            memory_id="transaction-1",
            required_causal_token=CausalToken.for_workspace("ws-1", 9),
        )


def test_outbox_batch_is_ordered_and_normalized() -> None:
    rows = [
        (3, 0, "intent-1", '{"event_id":"event-1"}'),
        (4, 0, "intent-1", {"event_id": "event-2"}),
    ]
    cursor = FakeCursor([rows])  # type: ignore[list-item]
    repository = PostgreSQLMemoryRepository(lambda: FakeConnection(cursor))

    entries = repository.read_outbox_batch(workspace_id="ws-1", limit=10)

    assert [entry.sequence for entry in entries] == [3, 4]
    assert entries[0].payload["event_id"] == "event-1"
    assert cursor.executed[0][1] == ("ws-1", 10)


def test_projection_acknowledgement_persists_fencing_token() -> None:
    entry = type("Entry", (), {"sequence": 7, "ordinal": 0})()
    cursor = FakeCursor([(9,)])
    connection = FakeConnection(cursor)
    repository = PostgreSQLMemoryRepository(lambda: connection)

    repository.acknowledge_projection(
        workspace_id="ws-1",
        projection="neo4j",
        applied_sequence=7,
        entries=(entry,),
        fencing_token=9,
    )

    assert cursor.executed[-1][1] == ("ws-1", "neo4j", 7, 9)
    assert "RETURNING fencing_token" in cursor.executed[-1][0]
    assert connection.exit_exception is None


def test_stale_projection_acknowledgement_rolls_back_outbox_updates() -> None:
    entry = type("Entry", (), {"sequence": 7, "ordinal": 0})()
    cursor = FakeCursor([None])
    connection = FakeConnection(cursor)
    repository = PostgreSQLMemoryRepository(lambda: connection)

    with pytest.raises(ProjectionFencingError, match="stale projector"):
        repository.acknowledge_projection(
            workspace_id="ws-1",
            projection="neo4j",
            applied_sequence=7,
            entries=(entry,),
            fencing_token=8,
        )

    assert connection.exit_exception is ProjectionFencingError


def test_preflight_fence_rejects_before_graph_write() -> None:
    cursor = FakeCursor([(10,)])
    connection = FakeConnection(cursor)
    repository = PostgreSQLMemoryRepository(lambda: connection)

    with pytest.raises(ProjectionFencingError, match="before graph write"):
        repository.assert_projection_fence(
            workspace_id="ws-1", projection="neo4j", fencing_token=9
        )

    assert cursor.executed[-1][1] == ("ws-1", "neo4j")
