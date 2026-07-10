"""Typed agent-to-agent exchange references for shared-memory workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Tuple

from ..tracing import log_span


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class AgentExchange:
    """An agent handoff containing references, never an implicit prompt dump."""

    exchange_id: str
    workspace_id: str
    run_id: str
    sender_agent_id: str
    recipient_agent_id: str
    message_type: str
    memory_refs: Tuple[str, ...] = ()
    evidence_refs: Tuple[str, ...] = ()
    causal_token: str = ""
    ontology_context_hash: str = ""
    trace_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.sender_agent_id.strip() or not self.recipient_agent_id.strip():
            raise ValueError("sender_agent_id and recipient_agent_id are required")
        if not self.message_type.strip():
            raise ValueError("message_type is required")
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())

    def telemetry_attributes(self) -> Mapping[str, Any]:
        return {
            "seocho.agent.exchange_id_hash": _hash(self.exchange_id),
            "seocho.workspace_hash": _hash(self.workspace_id),
            "seocho.agent.sender": self.sender_agent_id,
            "seocho.agent.recipient": self.recipient_agent_id,
            "seocho.agent.message_type": self.message_type,
            "seocho.agent.memory_ref_count": len(self.memory_refs),
            "seocho.agent.evidence_ref_count": len(self.evidence_refs),
            "seocho.agent.causal_token_hash": _hash(self.causal_token) if self.causal_token else "",
            "seocho.ontology_context_hash": self.ontology_context_hash,
            "seocho.agent.linked_trace_id": self.trace_id,
        }

    def emit_trace(self) -> None:
        log_span(
            "agent.exchange",
            metadata=dict(self.telemetry_attributes()),
            tags=["agent", "exchange"],
        )

