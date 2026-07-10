"""Typed contracts for blockchain long-term memory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Tuple


def workspace_token(workspace_id: str) -> str:
    value = workspace_id.strip()
    if not value:
        raise ValueError("workspace_id is required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def opaque_ref(value: str, *, namespace: str) -> str:
    """Return a stable opaque reference suitable for authoritative memory."""

    raw = value.strip()
    scope = namespace.strip()
    if not raw or not scope:
        raise ValueError("value and namespace are required")
    digest = hashlib.sha256(f"{scope}\0{raw}".encode("utf-8")).hexdigest()
    return f"{scope}:{digest}"


@dataclass(frozen=True, slots=True)
class CausalToken:
    workspace_token: str
    sequence: int

    def __post_init__(self) -> None:
        if not self.workspace_token.strip():
            raise ValueError("workspace_token is required")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")

    @classmethod
    def for_workspace(cls, workspace_id: str, sequence: int) -> "CausalToken":
        return cls(workspace_token(workspace_id), sequence)

    def assert_workspace(self, workspace_id: str) -> None:
        if self.workspace_token != workspace_token(workspace_id):
            raise ValueError("causal token belongs to another workspace")

    def serialize(self) -> str:
        return f"memory.v1:{self.workspace_token}:{self.sequence}"


@dataclass(frozen=True, slots=True)
class TransactionEvent:
    workspace_id: str
    chain_id: str
    block_height: int
    block_hash: str
    tx_hash: str
    event_index: int
    customer_ref: str
    counterparty_ref: str
    provenance_id: str
    occurred_at: str
    asset: str = ""
    amount: str = "0"
    risk_reason_codes: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        required = {
            "workspace_id": self.workspace_id,
            "chain_id": self.chain_id,
            "block_hash": self.block_hash,
            "tx_hash": self.tx_hash,
            "customer_ref": self.customer_ref,
            "counterparty_ref": self.counterparty_ref,
            "provenance_id": self.provenance_id,
            "occurred_at": self.occurred_at,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"required transaction fields missing: {', '.join(missing)}")
        if self.block_height < 0 or self.event_index < 0:
            raise ValueError("block_height and event_index must be non-negative")
        try:
            if Decimal(self.amount) < 0:
                raise ValueError("amount must be non-negative")
        except InvalidOperation as exc:
            raise ValueError("amount must be a decimal string") from exc
        if len(set(self.risk_reason_codes)) != len(self.risk_reason_codes):
            raise ValueError("risk_reason_codes must be unique")

    @property
    def event_id(self) -> str:
        payload = f"{self.chain_id}\0{self.tx_hash}\0{self.event_index}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def canonical_payload(self) -> Mapping[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "chain_id": self.chain_id,
            "block_height": self.block_height,
            "block_hash": self.block_hash,
            "tx_hash": self.tx_hash,
            "event_index": self.event_index,
            "customer_ref": self.customer_ref,
            "counterparty_ref": self.counterparty_ref,
            "provenance_id": self.provenance_id,
            "occurred_at": self.occurred_at,
            "asset": self.asset,
            "amount": self.amount,
            "risk_reason_codes": list(self.risk_reason_codes),
            "metadata": dict(self.metadata or {}),
        }

    @property
    def payload_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TransactionEventRevision:
    event: TransactionEvent
    revision: int
    status: str
    memory_sequence: int
    reorged_by_block_hash: str = ""

    def __post_init__(self) -> None:
        if self.revision < 1 or self.memory_sequence < 1:
            raise ValueError("revision and memory_sequence must be positive")
        if self.status not in {"canonical", "orphaned"}:
            raise ValueError("status must be canonical or orphaned")


@dataclass(frozen=True, slots=True)
class RiskAggregate:
    customer_ref: str
    counterparty_ref: str
    flagged_event_count: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class ProjectionOutboxEntry:
    workspace_token: str
    sequence: int
    ordinal: int
    operation: str
    event_id: str
    event_revision: int
    chain_id: str
    block_height: int
    block_hash: str

    def __post_init__(self) -> None:
        if self.operation not in {"upsert", "retract"}:
            raise ValueError("operation must be upsert or retract")


@dataclass(frozen=True, slots=True)
class BlockIngestResult:
    applied: bool
    causal_token: CausalToken
    canonical_event_count: int
    orphaned_event_count: int
    outbox_entry_count: int


@dataclass(frozen=True, slots=True)
class ProjectionStatus:
    projection: str
    applied_sequence: int
    required_sequence: int
    current: bool
