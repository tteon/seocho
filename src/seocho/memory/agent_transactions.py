"""Atomic state-machine adapter for OKX-shaped agent transaction events."""

from __future__ import annotations

import time
from typing import Any, Mapping

from ..metrics import get_metrics
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
        started = time.perf_counter()
        action = str(event.get("action", ""))
        if action not in _PREVIOUS:
            raise ValueError(f"unsupported agent transaction action: {action}")
        intent_id = str(event.get("transaction_intent_id", ""))
        event_id = str(event.get("event_id", ""))
        metrics = get_metrics()
        try:
            result = self._repository.commit_revision(
                workspace_id=str(event.get("workspace_id", "")),
                memory_id=intent_id,
                event_type=f"agent_transaction.{action}",
                occurred_at=str(event.get("occurred_at", "")),
                provenance_id=str(event.get("provenance_id", "")),
                payload=dict(event),
                idempotency_key=event_id,
                schema_version=str(
                    event.get("schema_version", "okx-agent-transaction.v1")
                ),
                allowed_previous_event_types=_PREVIOUS[action],
            )
        except ValueError:
            metrics.add(
                "seocho.memory.transition_conflict.count",
                attributes={"event.type": action},
            )
            metrics.add(
                "seocho.memory.commit.count", attributes={"outcome": "conflict"}
            )
            metrics.record(
                "seocho.memory.commit.duration",
                time.perf_counter() - started,
                {"outcome": "conflict", "error.type": "ValueError"},
            )
            raise
        except Exception as exc:
            metrics.add(
                "seocho.memory.commit.count", attributes={"outcome": "error"}
            )
            metrics.record(
                "seocho.memory.commit.duration",
                time.perf_counter() - started,
                {"outcome": "error", "error.type": type(exc).__name__},
            )
            raise
        applied = bool(getattr(result, "applied", True))
        outcome = "applied" if applied else "replayed"
        metrics.add("seocho.memory.commit.count", attributes={"outcome": outcome})
        metrics.record(
            "seocho.memory.commit.duration",
            time.perf_counter() - started,
            {"outcome": outcome},
        )
        causal_token = getattr(result, "causal_token", None)
        if causal_token is not None:
            metrics.set("seocho.memory.sequence", causal_token.sequence)
        if not applied:
            metrics.add(
                "seocho.memory.idempotency_replay.count",
                attributes={"outcome": "matched"},
            )
        return result


__all__ = ["AgentTransactionMemory"]
