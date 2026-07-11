"""Deterministic exchange-semantic agent transaction corpus."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator


DEFAULT_SCENARIO_WEIGHTS = {
    "full_fill": 2600,
    "partial_fill": 1900,
    "confirmed_cancel": 1400,
    "cancel_fill_race": 600,
    "amend": 600,
    "rejected": 600,
    "expired_or_ioc": 500,
    "stp_or_mmp": 300,
    "batch_partial": 300,
    "unknown_then_reconciled": 300,
    "duplicate_stream": 300,
    "out_of_order": 250,
    "reconnect_snapshot": 200,
    "fill_position_lag": 100,
    "policy_drift": 50,
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


def _venue_state(venue: str, step: str) -> str:
    states = {
        "okx": {"active": "live", "partial_fill": "partially_filled", "canceled": "canceled", "prevented": "mmp_canceled"},
        "binance": {"active": "NEW", "partial_fill": "PARTIALLY_FILLED", "canceled": "CANCELED", "prevented": "EXPIRED"},
        "coinbase": {"active": "OPEN", "partial_fill": "OPEN", "cancel_acknowledged": "CANCEL_QUEUED", "canceled": "CANCELLED", "prevented": "FAILED"},
    }
    if step == "filled":
        return "filled" if venue == "okx" else "FILLED"
    if step == "expired":
        return "canceled" if venue == "okx" else "EXPIRED"
    if step == "rejected":
        return "rejected" if venue == "okx" else "REJECTED" if venue == "binance" else "FAILED"
    return states[venue].get(step, step)


@dataclass(frozen=True, slots=True)
class ExchangeCalibratedEvent:
    sequence: int
    intent_id: str
    event_id: str
    causal_parent_id: str | None
    venue: str
    scenario: str
    step: str
    venue_state: str
    instrument_id: str
    event_time: str
    gateway_in_time: str
    gateway_out_time: str
    ingest_time: str
    evidence_class: str
    chain_anchor_ref: str
    duplicate: bool
    late: bool
    policy_version: str
    ontology_version: str
    schema_version: str = "exchange-calibrated-agent-memory.v1"

    def to_dict(self) -> dict:
        return asdict(self)


def generate_exchange_calibrated_events(
    *, intent_count: int, seed: int = 20260711
) -> Iterator[ExchangeCalibratedEvent]:
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
        previous_event: ExchangeCalibratedEvent | None = None
        for ordinal, step in enumerate(_STEPS[scenario]):
            if step == "duplicate_message":
                if previous_event is None:
                    raise RuntimeError("duplicate delivery requires a prior event")
                yield previous_event
                continue
            sequence += 1
            event_id = _ref("event", f"{seed}:{index}:{ordinal}")
            event_time = origin + timedelta(milliseconds=index * 500 + ordinal * 25)
            late = step == "late_active_message"
            event = ExchangeCalibratedEvent(
                sequence=sequence,
                intent_id=intent_id,
                event_id=event_id,
                causal_parent_id=parent,
                venue=venue,
                scenario=scenario,
                step=step,
                venue_state=_venue_state(venue, step),
                instrument_id={"okx": "BTC-USDT", "binance": "BTCUSDT", "coinbase": "BTC-USD"}[venue],
                event_time=(event_time + timedelta(milliseconds=150 if late else 0)).isoformat(),
                gateway_in_time=(event_time + timedelta(milliseconds=2)).isoformat(),
                gateway_out_time=(event_time + timedelta(milliseconds=5)).isoformat(),
                ingest_time=(event_time + timedelta(milliseconds=10 if not late else 200)).isoformat(),
                evidence_class="fault_injected" if step in {"late_active_message", "transport_timeout", "sequence_gap"} or scenario == "duplicate_stream" and step == "partial_fill" else "synthetic_calibrated",
                chain_anchor_ref=_ref("bitcoin-tx", f"public-anchor:{index % 102}"),
                duplicate=scenario == "duplicate_stream" and step == "partial_fill",
                late=late,
                policy_version="2.0.0" if step in {"policy_version_changed", "context_invalidated", "risk_rechecked"} else "1.0.0",
                ontology_version="exchange-memory-1",
            )
            yield event
            previous_event = event
            parent = event_id


__all__ = ["DEFAULT_SCENARIO_WEIGHTS", "ExchangeCalibratedEvent", "generate_exchange_calibrated_events"]
