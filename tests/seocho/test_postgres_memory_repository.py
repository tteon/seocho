from typing import Any

import pytest

from seocho.memory import PostgreSQLMemoryRepository


class FakeCursor:
    def __init__(self, fetches: list[tuple[Any, ...] | None]) -> None:
        self.fetches = fetches
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.fetches.pop(0)

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


def test_commit_is_one_transaction_for_revision_outbox_and_idempotency() -> None:
    cursor = FakeCursor([None, (1,), (0,)])
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
    assert "INSERT INTO agent_memory_revisions" in statements
    assert "INSERT INTO agent_memory_outbox" in statements
    assert "INSERT INTO agent_memory_idempotency" in statements
    assert connection.exit_exception is None


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
