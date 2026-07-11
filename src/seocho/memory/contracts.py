"""Provider-neutral contracts for auditable agent long-term memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Tuple

from .models import CausalToken


class TransactionState(str, Enum):
    INTENT_CREATED = "intent_created"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REPLACED = "replaced"
    REVERSED = "reversed"


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    workspace_id: str
    memory_id: str
    revision: int
    sequence: int
    event_type: str
    occurred_at: str
    ingested_at: str
    provenance_id: str
    payload: Mapping[str, Any]
    supersedes_revision: int | None = None
    canonical: bool = True
    schema_version: str = "agent-memory.v1"

    def __post_init__(self) -> None:
        required = (
            self.workspace_id,
            self.memory_id,
            self.event_type,
            self.occurred_at,
            self.ingested_at,
            self.provenance_id,
            self.schema_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("memory revision identifiers and timestamps are required")
        if self.revision < 1 or self.sequence < 1:
            raise ValueError("revision and sequence must be positive")
        if self.supersedes_revision is not None:
            if self.supersedes_revision < 1 or self.supersedes_revision >= self.revision:
                raise ValueError("supersedes_revision must precede revision")


@dataclass(frozen=True, slots=True)
class ContextEnvelope:
    workspace_id: str
    session_id: str
    intent_id: str
    required_causal_token: CausalToken
    memory_sequence_start: int
    memory_sequence_end: int
    graph_targets: Tuple[str, ...]
    projection_watermarks: Mapping[str, int]
    required_slots: Tuple[str, ...]
    ontology_version: str
    policy_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        required = (
            self.workspace_id,
            self.session_id,
            self.intent_id,
            self.ontology_version,
            self.policy_version,
            self.prompt_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("context envelope identifiers and versions are required")
        self.required_causal_token.assert_workspace(self.workspace_id)
        if self.memory_sequence_start < 0:
            raise ValueError("memory_sequence_start must be non-negative")
        if self.memory_sequence_end < self.memory_sequence_start:
            raise ValueError("memory sequence range is invalid")
        if self.required_causal_token.sequence > self.memory_sequence_end:
            raise ValueError("context does not satisfy required causal token")
        if len(set(self.graph_targets)) != len(self.graph_targets):
            raise ValueError("graph_targets must be unique")
        if len(set(self.required_slots)) != len(self.required_slots):
            raise ValueError("required_slots must be unique")

    @property
    def stale_targets(self) -> Tuple[str, ...]:
        required = self.required_causal_token.sequence
        return tuple(
            target
            for target in self.graph_targets
            if int(self.projection_watermarks.get(target, -1)) < required
        )


@dataclass(frozen=True, slots=True)
class MemoryUsageReceipt:
    workspace_id: str
    answer_id: str
    memory_revision_refs: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]
    provenance_refs: Tuple[str, ...]
    causal_token: CausalToken
    missing_slots: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.answer_id.strip():
            raise ValueError("workspace_id and answer_id are required")
        self.causal_token.assert_workspace(self.workspace_id)
        for values, name in (
            (self.memory_revision_refs, "memory_revision_refs"),
            (self.evidence_refs, "evidence_refs"),
            (self.provenance_refs, "provenance_refs"),
            (self.missing_slots, "missing_slots"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")


@dataclass(frozen=True, slots=True)
class AnswerReceipt:
    workspace_id: str
    answer_id: str
    session_id: str
    intent_id: str
    support_status: str
    usage: MemoryUsageReceipt
    ontology_version: str
    policy_version: str
    prompt_version: str
    model: str
    prompt_optimization: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.support_status not in {"supported", "partial", "unsupported", "stale"}:
            raise ValueError("invalid support_status")
        if self.usage.workspace_id != self.workspace_id:
            raise ValueError("usage receipt belongs to another workspace")
        if self.usage.answer_id != self.answer_id:
            raise ValueError("usage receipt belongs to another answer")
        required = (
            self.session_id,
            self.intent_id,
            self.ontology_version,
            self.policy_version,
            self.prompt_version,
            self.model,
        )
        if any(not value.strip() for value in required):
            raise ValueError("answer receipt identifiers and versions are required")


__all__ = [
    "AnswerReceipt",
    "ContextEnvelope",
    "MemoryRevision",
    "MemoryUsageReceipt",
    "TransactionState",
]
