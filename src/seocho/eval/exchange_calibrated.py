"""Deterministic exchange-shaped agent memory corpus for reliability evaluation."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator


DEFAULT_SCENARIO_WEIGHTS = {
    "full_fill": 2600, "partial_fill": 1900, "confirmed_cancel": 1400,
    "cancel_fill_race": 600, "amend": 600, "rejected": 600,
    "expired_or_ioc": 500, "stp_or_mmp": 300, "batch_partial": 300,
    "unknown_then_reconciled": 300, "duplicate_stream": 300,
    "out_of_order": 250, "reconnect_snapshot": 200,
    "fill_position_lag": 100, "policy_drift": 50,
}

_STEPS = {
    "full_fill": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "filled", "settled", "memory_published"),
    "partial_fill": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "partial_fill", "partial_fill", "filled", "settled", "memory_published"),
    "confirmed_cancel": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "cancel_requested", "cancel_acknowledged", "canceled", "memory_published"),
    "cancel_fill_race": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "cancel_requested", "partial_fill", "filled", "cancel_rejected_terminal", "memory_published"),
    "amend": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "amend_requested", "amend_acknowledged", "amended", "filled", "memory_published"),
    "rejected": ("intent", "risk_rejected", "rejected", "memory_published"),
    "expired_or_ioc": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "expired", "memory_published"),
    "stp_or_mmp": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "prevented", "memory_published"),
    "batch_partial": ("intent", "batch_sent", "batch_acknowledged", "batch_item_success", "batch_item_failed", "reconciled", "memory_published"),
    "unknown_then_reconciled": ("intent", "risk_approved", "request_sent", "transport_timeout", "execution_unknown", "stream_reconnected", "order_queried", "filled", "memory_published"),
    "duplicate_stream": ("intent", "risk_approved", "request_sent", "acknowledged", "active", "partial_fill", "duplicate_message", "filled", "memory_published"),
    "out_of_order": ("intent", "risk_approved", "request_sent", "acknowledged", "filled", "late_active_message", "reconciled", "memory_published"),
    "reconnect_snapshot": ("intent", "risk_approved", "request_sent", "acknowledged", "sequence_gap", "stream_reconnected", "snapshot", "patch", "reconciled", "memory_published"),
    "fill_position_lag": ("intent", "risk_approved", "request_sent", "acknowledged", "filled", "position_stale", "position_converged", "settled", "memory_published"),
    "policy_drift": ("intent", "risk_approved", "policy_version_changed", "context_invalidated", "risk_rechecked", "request_sent", "acknowledged", "filled", "memory_published"),
}


def _ref(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class ExchangeCalibratedEvent:
    workspace_id: str
    sequence: int
    intent_id: str
    event_id: str
    causal_parent_id: str | None
    actor_agent: str
    recipient_agent: str
    venue: str
    scenario: str
    step: str
    canonical_state: str
    instrument_id: str
    event_time: str
    ingest_time: str
    duplicate: bool
    late: bool
    policy_version: str
    provenance_id: str
    private_metadata: dict[str, str]
    schema_version: str = "exchange-calibrated-agent-memory.v2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_exchange_calibrated_events(
    *, intent_count: int, seed: int = 20260711, workspace_id: str = "okx-exchange-eval"
) -> Iterator[ExchangeCalibratedEvent]:
    """Generate reproducible venue-shaped events; scenario weights are hypotheses."""

    if intent_count < 1:
        raise ValueError("intent_count must be positive")
    rng = random.Random(seed)
    scenarios = tuple(DEFAULT_SCENARIO_WEIGHTS)
    weights = tuple(DEFAULT_SCENARIO_WEIGHTS.values())
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sequence = 0
    for index in range(intent_count):
        venue = ("okx", "binance", "coinbase")[index % 3]
        scenario = rng.choices(scenarios, weights=weights, k=1)[0]
        intent_id = _ref("intent", f"{seed}:{index}")
        parent = None
        previous: ExchangeCalibratedEvent | None = None
        for ordinal, step in enumerate(_STEPS[scenario]):
            if step == "duplicate_message":
                if previous is None:
                    raise RuntimeError("duplicate requires prior event")
                yield previous
                continue
            sequence += 1
            event_time = origin + timedelta(milliseconds=index * 500 + ordinal * 25)
            late = step == "late_active_message"
            event = ExchangeCalibratedEvent(
                workspace_id=workspace_id,
                sequence=sequence,
                intent_id=intent_id,
                event_id=_ref("event", f"{seed}:{index}:{ordinal}"),
                causal_parent_id=parent,
                actor_agent=(
                    "strategy_agent" if step == "intent"
                    else "risk_agent" if step.startswith("risk_")
                    else "exchange_agent" if step in {"acknowledged", "active", "partial_fill", "filled", "canceled", "expired", "prevented"}
                    else "memory_agent" if step == "memory_published"
                    else "execution_agent"
                ),
                recipient_agent=(
                    "risk_agent" if step == "intent"
                    else "execution_agent" if step.startswith("risk_")
                    else "memory_agent" if step == "memory_published"
                    else "settlement_agent" if step in {"filled", "canceled", "expired", "prevented"}
                    else "exchange_agent"
                ),
                venue=venue,
                scenario=scenario,
                step=step,
                canonical_state=step,
                instrument_id={"okx": "BTC-USDT", "binance": "BTCUSDT", "coinbase": "BTC-USD"}[venue],
                event_time=(event_time + timedelta(milliseconds=150 if late else 0)).isoformat(),
                ingest_time=(event_time + timedelta(milliseconds=200 if late else 10)).isoformat(),
                duplicate=scenario == "duplicate_stream" and step == "partial_fill",
                late=late,
                policy_version="2.0.0" if step in {"policy_version_changed", "context_invalidated", "risk_rechecked"} else "1.0.0",
                provenance_id=f"synthetic-calibrated:{seed}:{sequence}",
                private_metadata={"raw_account_id": f"never-export:{index:08d}"},
            )
            yield event
            previous = event
            parent = event.event_id


__all__ = ["DEFAULT_SCENARIO_WEIGHTS", "ExchangeCalibratedEvent", "generate_exchange_calibrated_events"]
