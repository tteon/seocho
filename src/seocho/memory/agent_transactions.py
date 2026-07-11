"""Atomic state-machine adapter for OKX-shaped agent transaction events."""

from __future__ import annotations

from typing import Any, Mapping

from .postgres_repository import MemoryCommitResult, PostgreSQLMemoryRepository


_PREVIOUS: dict[str, tuple[str, ...]] = {
    "propose_order": ("__initial__",),
    "approve_order": ("agent_transaction.propose_order",),
    "reject_order": ("agent_transaction.propose_order",),
    "place_order": ("agent_transaction.approve_order",),
    "ack_order": ("agent_transaction.place_order",),
    "partial_fill": ("agent_transaction.ack_order",),
    "fill_order": (
        "agent_transaction.ack_order",
        "agent_transaction.partial_fill",
    ),
    "settle_position": ("agent_transaction.fill_order",),
    "request_cancel": ("agent_transaction.ack_order",),
    "cancel_order": ("agent_transaction.request_cancel",),
    "ack_cancel": ("agent_transaction.cancel_order",),
    "record_rejection": ("agent_transaction.reject_order",),
    "publish_memory": (
        "agent_transaction.settle_position",
        "agent_transaction.ack_cancel",
    ),
}


class AgentTransactionMemory:
    def __init__(self, repository: PostgreSQLMemoryRepository) -> None:
        self._repository = repository

    def commit_event(self, event: Mapping[str, Any]) -> MemoryCommitResult:
        action = str(event.get("action", ""))
        if action not in _PREVIOUS:
            raise ValueError(f"unsupported agent transaction action: {action}")
        intent_id = str(event.get("transaction_intent_id", ""))
        event_id = str(event.get("event_id", ""))
        return self._repository.commit_revision(
            workspace_id=str(event.get("workspace_id", "")),
            memory_id=intent_id,
            event_type=f"agent_transaction.{action}",
            occurred_at=str(event.get("occurred_at", "")),
            provenance_id=str(event.get("provenance_id", "")),
            payload=dict(event),
            idempotency_key=event_id,
            schema_version=str(event.get("schema_version", "okx-agent-transaction.v1")),
            allowed_previous_event_types=_PREVIOUS[action],
        )


__all__ = ["AgentTransactionMemory"]
