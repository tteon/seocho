"""Deterministic single-user corpus for long-term agent-memory evaluation."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Tuple

from seocho.memory.contracts import TransactionState


@dataclass(frozen=True, slots=True)
class LongitudinalEvent:
    workspace_id: str
    user_ref: str
    sequence: int
    transaction_ref: str
    agent_ref: str
    counterparty_ref: str
    state: str
    occurred_at: str
    provenance_id: str
    idempotency_key: str
    session_ref: str
    private_metadata: Mapping[str, Any]
    schema_version: str = "longitudinal-memory.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GoldMemoryQuery:
    query_id: str
    family: str
    question: str
    required_slots: Tuple[str, ...]
    expected_support_status: str
    expected_sequence: int
    denied_fields: Tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required_slots"] = list(self.required_slots)
        payload["denied_fields"] = list(self.denied_fields)
        return payload


def _ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()[:24]
    return f"{namespace}:{digest}"


def generate_longitudinal_events(
    *,
    event_count: int,
    seed: int = 42,
    workspace_id: str = "okx-memory-eval",
    user_ref: str = "user:synthetic-001",
    start_at: datetime | None = None,
) -> Iterator[LongitudinalEvent]:
    """Yield reproducible events without asserting ownership of real wallets."""

    if event_count < 1:
        raise ValueError("event_count must be positive")
    if not workspace_id.strip() or not user_ref.strip():
        raise ValueError("workspace_id and user_ref are required")
    rng = random.Random(seed)
    origin = start_at or datetime(2025, 1, 1, tzinfo=timezone.utc)
    if origin.tzinfo is None:
        raise ValueError("start_at must be timezone-aware")
    state_cycle = (
        TransactionState.INTENT_CREATED.value,
        TransactionState.PENDING.value,
        TransactionState.CONFIRMED.value,
        TransactionState.FAILED.value,
        TransactionState.REPLACED.value,
        TransactionState.REVERSED.value,
    )

    for index in range(event_count):
        sequence = index + 1
        transaction_number = index // 3
        transaction_ref = _ref("tx", f"{seed}:{transaction_number}")
        state = state_cycle[index % len(state_cycle)]
        agent_ref = f"agent:{1 + transaction_number % 8:02d}"
        counterparty_ref = _ref("counterparty", str(transaction_number % 97))
        occurred_at = origin + timedelta(seconds=index * 11 + rng.randint(0, 3))
        yield LongitudinalEvent(
            workspace_id=workspace_id,
            user_ref=user_ref,
            sequence=sequence,
            transaction_ref=transaction_ref,
            agent_ref=agent_ref,
            counterparty_ref=counterparty_ref,
            state=state,
            occurred_at=occurred_at.isoformat(),
            provenance_id=f"synthetic:{seed}:{sequence}",
            idempotency_key=_ref("idem", f"{seed}:{sequence}"),
            session_ref=f"session:{1 + index // 250:06d}",
            private_metadata={
                "raw_wallet_address": f"never-export:{transaction_number:08d}",
                "internal_note": "disclosure-test-only",
            },
        )


def build_gold_queries(*, final_sequence: int) -> tuple[GoldMemoryQuery, ...]:
    if final_sequence < 1:
        raise ValueError("final_sequence must be positive")
    common_denied = ("raw_wallet_address", "internal_note")
    return (
        GoldMemoryQuery(
            query_id="q1-cross-session",
            family="cross_session_memory.v1",
            question="Recall the latest three agent transactions from prior sessions.",
            required_slots=("transaction", "agent", "state", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q3-agent-path",
            family="agent_transaction_path.v1",
            question="Show the bounded agent path and each state transition for this transfer.",
            required_slots=("agent_path", "state_transitions", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q4-federated-retrieval",
            family="federated_transaction_history.v1",
            question="Merge unfinished transactions with related historical interactions.",
            required_slots=("active_transactions", "historical_interactions", "target_status"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q2-point-in-time",
            family="point_in_time_explanation.v1",
            question="Explain the recorded state at an earlier sequence and compare it with now.",
            required_slots=("historical_state", "current_state", "revision", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q6-concurrent-exchange",
            family="concurrent_agent_exchange.v1",
            question="Return the latest serialized transaction state after concurrent agents update it.",
            required_slots=("state", "revision", "conflict_status", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q7-ingest-ordering",
            family="duplicate_out_of_order_ingest.v1",
            question="Explain which delivered event became canonical and which deliveries were ignored.",
            required_slots=("canonical_event", "duplicate_count", "ordering_basis"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q8-reorg-rollback",
            family="reorg_rollback_explanation.v1",
            question="Explain how the replacement block changed the answer without deleting history.",
            required_slots=("orphaned_revision", "replacement_revision", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q9-disclosure",
            family="ontology_disclosure.v1",
            question="Return the transaction explanation allowed for the current subject and role.",
            required_slots=("allowed_fields", "denied_fields", "policy_version"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q10-text2cypher",
            family="bounded_text2cypher.v1",
            question="Answer an unknown read query using a validated bounded graph plan.",
            required_slots=("query_plan", "workspace_scope", "evidence"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q5-causal-read",
            family="causal_transaction_status.v1",
            question="Include the transaction intent that was just committed.",
            required_slots=("state", "causal_sequence", "projection_status"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q11-context-budget",
            family="long_context_optimization.v1",
            question="Select only the causal memories needed to explain the latest state.",
            required_slots=("state", "selected_revisions", "provenance"),
            expected_support_status="supported",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
        GoldMemoryQuery(
            query_id="q12-degradation",
            family="service_degradation_recovery.v1",
            question="Report a bounded partial or stale answer when a dependency is unavailable.",
            required_slots=("dependency_status", "support_status", "recovery_action"),
            expected_support_status="partial",
            expected_sequence=final_sequence,
            denied_fields=common_denied,
        ),
    )


__all__ = [
    "GoldMemoryQuery",
    "LongitudinalEvent",
    "build_gold_queries",
    "generate_longitudinal_events",
]
