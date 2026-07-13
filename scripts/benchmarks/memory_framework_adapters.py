"""Live adapters for the comparative blockchain memory benchmark."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Sequence

import psycopg

from seocho.eval.longitudinal_memory import LongitudinalEvent
from seocho.eval.memory_framework_benchmark import (
    CapabilityStatus,
    MemoryCapabilities,
    MemoryObservation,
)
from seocho.memory import POSTGRES_MEMORY_SCHEMA_SQL, PostgreSQLMemoryRepository


def _payload(event: LongitudinalEvent) -> dict[str, Any]:
    return {
        "state": event.state,
        "sequence": event.sequence,
        "event_kind": event.event_kind,
        "user_ref": event.user_ref,
        "transaction_ref": event.transaction_ref,
        "agent_ref": event.agent_ref,
        "counterparty_ref": event.counterparty_ref,
        "block_height": event.block_height,
        "block_hash_ref": event.block_hash_ref,
        "provenance_id": event.provenance_id,
    }


class SeochoPostgresAdapter:
    framework = "seocho-postgresql"
    capabilities = MemoryCapabilities(
        current_read=CapabilityStatus.NATIVE,
        point_in_time_read=CapabilityStatus.NATIVE,
        temporal_invalidation=CapabilityStatus.NATIVE,
        graph_relations=CapabilityStatus.ADAPTER,
        idempotent_write=CapabilityStatus.NATIVE,
        rollback_or_rebuild=CapabilityStatus.NATIVE,
        provenance=CapabilityStatus.NATIVE,
    )

    def __init__(self, dsn: str, workspace: str) -> None:
        self.dsn = dsn
        self.workspace = workspace
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(POSTGRES_MEMORY_SCHEMA_SQL)
        self.repository = PostgreSQLMemoryRepository.connect(dsn)

    def reset(self) -> None:
        with psycopg.connect(self.dsn) as connection, connection.transaction():
            for table in (
                "agent_memory_outbox",
                "agent_memory_idempotency",
                "agent_memory_revisions",
                "agent_projection_watermarks",
                "agent_memory_heads",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE workspace_id=%s", (self.workspace,)
                )

    def add(self, event: LongitudinalEvent) -> bool:
        result = self.repository.commit_revision(
            workspace_id=self.workspace,
            memory_id=event.transaction_ref,
            event_type=event.event_kind,
            occurred_at=event.occurred_at,
            provenance_id=event.provenance_id,
            payload=_payload(event),
            idempotency_key=event.idempotency_key,
        )
        return result.applied

    @staticmethod
    def _observation(revision: Any) -> MemoryObservation | None:
        if revision is None:
            return None
        return MemoryObservation(
            memory_id=revision.memory_id,
            state=str(revision.payload["state"]),
            sequence=revision.sequence,
            provenance_refs=(revision.provenance_id,),
            related_refs=tuple(
                str(revision.payload[key])
                for key in ("agent_ref", "counterparty_ref")
                if revision.payload.get(key)
            ),
            raw=revision.payload,
        )

    def get_current(self, memory_id: str) -> MemoryObservation | None:
        return self._observation(
            self.repository.read_revision(
                workspace_id=self.workspace, memory_id=memory_id
            )
        )

    def get_at_sequence(
        self, memory_id: str, sequence: int
    ) -> MemoryObservation | None:
        return self._observation(
            self.repository.read_revision(
                workspace_id=self.workspace,
                memory_id=memory_id,
                at_sequence=sequence,
            )
        )

    def search(self, query: str, *, limit: int) -> Sequence[MemoryObservation]:
        with psycopg.connect(self.dsn) as connection:
            rows = connection.execute(
                "SELECT DISTINCT ON(memory_id) memory_id,sequence,payload,provenance_id "
                "FROM agent_memory_revisions WHERE workspace_id=%s "
                "AND payload::text ILIKE %s ORDER BY memory_id,sequence DESC LIMIT %s",
                (self.workspace, f"%{query}%", limit),
            ).fetchall()
        return tuple(
            MemoryObservation(
                memory_id=str(memory_id),
                state=str(payload["state"]),
                sequence=int(sequence),
                provenance_refs=(str(provenance),),
                raw=payload,
            )
            for memory_id, sequence, payload, provenance in rows
        )


class LangGraphPostgresAdapter:
    framework = "langgraph-postgres-store"
    capabilities = MemoryCapabilities(
        current_read=CapabilityStatus.NATIVE,
        point_in_time_read=CapabilityStatus.UNSUPPORTED,
        temporal_invalidation=CapabilityStatus.UNSUPPORTED,
        graph_relations=CapabilityStatus.UNSUPPORTED,
        idempotent_write=CapabilityStatus.ADAPTER,
        rollback_or_rebuild=CapabilityStatus.UNSUPPORTED,
        provenance=CapabilityStatus.ADAPTER,
    )

    def __init__(self, dsn: str, workspace: str) -> None:
        from langgraph.store.postgres import PostgresStore

        self.workspace = workspace
        self.namespace = ("seocho-memory-bench", workspace, "current")
        self.receipt_namespace = ("seocho-memory-bench", workspace, "receipts")
        self._manager: AbstractContextManager[Any] = PostgresStore.from_conn_string(dsn)
        self.store = self._manager.__enter__()
        self.store.setup()

    def close(self) -> None:
        self._manager.__exit__(None, None, None)

    def reset(self) -> None:
        for namespace in (self.namespace, self.receipt_namespace):
            for item in self.store.search(namespace, limit=10000):
                self.store.delete(namespace, item.key)

    def add(self, event: LongitudinalEvent) -> bool:
        if self.store.get(self.receipt_namespace, event.idempotency_key) is not None:
            return False
        value = _payload(event)
        self.store.put(self.namespace, event.transaction_ref, value, index=False)
        self.store.put(
            self.receipt_namespace,
            event.idempotency_key,
            {"memory_id": event.transaction_ref, "sequence": event.sequence},
            index=False,
        )
        return True

    @staticmethod
    def _observation(item: Any) -> MemoryObservation | None:
        if item is None:
            return None
        value = item.value
        return MemoryObservation(
            memory_id=str(value["transaction_ref"]),
            state=str(value["state"]),
            sequence=int(value["sequence"]),
            provenance_refs=(str(value["provenance_id"]),),
            related_refs=(str(value["agent_ref"]), str(value["counterparty_ref"])),
            raw=value,
        )

    def get_current(self, memory_id: str) -> MemoryObservation | None:
        return self._observation(self.store.get(self.namespace, memory_id))

    def get_at_sequence(
        self, memory_id: str, sequence: int
    ) -> MemoryObservation | None:
        raise NotImplementedError("LangGraph Store does not retain value revisions")

    def search(self, query: str, *, limit: int) -> Sequence[MemoryObservation]:
        # No embedding index is configured in the structured-state baseline.
        return tuple(
            observation
            for item in self.store.search(self.namespace, limit=limit)
            if (observation := self._observation(item)) is not None
            and query.lower() in str(item.value).lower()
        )


__all__ = ["LangGraphPostgresAdapter", "SeochoPostgresAdapter"]
