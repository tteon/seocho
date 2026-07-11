"""Deterministic multi-agent transaction corpus shaped after OKX v5 orders."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping


def _ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()[:24]
    return f"{namespace}:{digest}"


@dataclass(frozen=True, slots=True)
class AgentTransactionEvent:
    workspace_id: str
    sequence: int
    conversation_id: str
    transaction_intent_id: str
    causal_parent_id: str | None
    event_id: str
    actor_agent: str
    recipient: str
    action: str
    decision: str
    occurred_at: str
    instrument_id: str
    instrument_type: str
    trade_mode: str
    side: str
    position_side: str
    order_type: str
    size: str
    price: str
    client_order_id: str
    exchange_order_ref: str
    exchange_state: str
    accumulated_fill_size: str
    average_fill_price: str
    simulation: bool
    provenance_id: str
    memory_sequence_required: int
    schema_version: str = "okx-agent-transaction.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LIFECYCLES: tuple[tuple[tuple[str, str, str, str], ...], ...] = (
    (
        ("strategy_agent", "risk_agent", "propose_order", "proposed"),
        ("risk_agent", "execution_agent", "approve_order", "approved"),
        ("execution_agent", "okx_demo", "place_order", "submitted"),
        ("okx_demo", "execution_agent", "ack_order", "live"),
        ("okx_demo", "settlement_agent", "partial_fill", "partially_filled"),
        ("okx_demo", "settlement_agent", "fill_order", "filled"),
        ("settlement_agent", "memory_agent", "settle_position", "settled"),
        ("memory_agent", "support_agent", "publish_memory", "committed"),
    ),
    (
        ("strategy_agent", "risk_agent", "propose_order", "proposed"),
        ("risk_agent", "execution_agent", "approve_order", "approved"),
        ("execution_agent", "okx_demo", "place_order", "submitted"),
        ("okx_demo", "execution_agent", "ack_order", "live"),
        ("strategy_agent", "execution_agent", "request_cancel", "cancel_requested"),
        ("execution_agent", "okx_demo", "cancel_order", "cancel_submitted"),
        ("okx_demo", "memory_agent", "ack_cancel", "canceled"),
        ("memory_agent", "support_agent", "publish_memory", "committed"),
    ),
    (
        ("strategy_agent", "risk_agent", "propose_order", "proposed"),
        ("risk_agent", "strategy_agent", "reject_order", "rejected"),
        ("strategy_agent", "memory_agent", "record_rejection", "committed"),
    ),
)


def generate_agent_transaction_events(
    *,
    transaction_count: int,
    workspace_id: str = "okx-agent-exchange-eval",
    start_at: datetime | None = None,
) -> Iterator[AgentTransactionEvent]:
    """Generate typed agent handoffs; no live or demo API call is performed."""

    if transaction_count < 1:
        raise ValueError("transaction_count must be positive")
    origin = start_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    if origin.tzinfo is None:
        raise ValueError("start_at must be timezone-aware")
    sequence = 0
    for transaction_index in range(transaction_count):
        lifecycle = _LIFECYCLES[transaction_index % len(_LIFECYCLES)]
        conversation_id = _ref("conversation", str(transaction_index))
        intent_id = _ref("intent", str(transaction_index))
        client_order_id = f"seocho{transaction_index:026d}"[-32:]
        exchange_order_ref = _ref("okx-order", client_order_id)
        parent: str | None = None
        size = str(2 + transaction_index % 10)
        price = str(60_000 + (transaction_index % 500) * 10)
        for step, (actor, recipient, action, state) in enumerate(lifecycle):
            sequence += 1
            event_id = _ref("agent-event", f"{transaction_index}:{step}")
            is_partial = state == "partially_filled"
            is_filled = (
                state in {"filled", "settled", "committed"} and len(lifecycle) == 8
            )
            yield AgentTransactionEvent(
                workspace_id=workspace_id,
                sequence=sequence,
                conversation_id=conversation_id,
                transaction_intent_id=intent_id,
                causal_parent_id=parent,
                event_id=event_id,
                actor_agent=actor,
                recipient=recipient,
                action=action,
                decision=state,
                occurred_at=(
                    origin + timedelta(milliseconds=sequence * 25)
                ).isoformat(),
                instrument_id="BTC-USDT-SWAP",
                instrument_type="SWAP",
                trade_mode="cross",
                side="buy" if transaction_index % 2 == 0 else "sell",
                position_side="net",
                order_type="limit",
                size=size,
                price=price,
                client_order_id=client_order_id,
                exchange_order_ref=exchange_order_ref,
                exchange_state=state,
                accumulated_fill_size=(
                    str(max(1, int(size) // 2))
                    if is_partial
                    else size if is_filled else "0"
                ),
                average_fill_price=price if is_partial or is_filled else "0",
                simulation=True,
                provenance_id=f"okx-demo-replay:{transaction_index}:{step}",
                memory_sequence_required=max(sequence - 1, 0),
            )
            parent = event_id


def normalize_okx_order_row(
    row: Mapping[str, Any], *, workspace_id: str, sequence: int
) -> Mapping[str, Any]:
    """Normalize an OKX v5 order/fill row without retaining credentials."""

    return {
        "workspace_id": workspace_id,
        "sequence": sequence,
        "instrument_id": str(row.get("instId", "")),
        "instrument_type": str(row.get("instType", "")),
        "client_order_id": str(row.get("clOrdId", "")),
        "exchange_order_ref": _ref("okx-order", str(row.get("ordId", "unknown"))),
        "exchange_state": str(row.get("state", "")),
        "side": str(row.get("side", "")),
        "position_side": str(row.get("posSide", "")),
        "order_type": str(row.get("ordType", "")),
        "size": str(row.get("sz", "0")),
        "price": str(row.get("px", "0")),
        "accumulated_fill_size": str(row.get("accFillSz", "0")),
        "average_fill_price": str(row.get("avgPx", "0")),
        "exchange_updated_at": str(row.get("uTime", "")),
        "schema_version": "okx-v5-order-normalized.v1",
    }


__all__ = [
    "AgentTransactionEvent",
    "generate_agent_transaction_events",
    "normalize_okx_order_row",
]
