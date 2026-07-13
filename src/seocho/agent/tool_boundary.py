"""Deterministic guardrails at agent tool input and output boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .identity import AgentPrincipal, AuthorizationDecision
from ..query.sdcr import Evidence, filter_evidence


@dataclass(frozen=True, slots=True)
class ToolBoundaryReceipt:
    phase: str
    tool_id: str
    allowed: bool
    reason: str
    policy_version: str
    protected_items_removed: int = 0


class ToolBoundaryGuard:
    """Authorize calls and remove protected evidence before synthesis."""

    def __init__(self, *, max_input_bytes: int = 64 * 1024) -> None:
        if max_input_bytes < 1:
            raise ValueError("max_input_bytes must be positive")
        self._max_input_bytes = max_input_bytes

    def authorize_input(
        self,
        *,
        principal: AgentPrincipal,
        workspace_id: str,
        tool_id: str,
        arguments: Mapping[str, Any],
    ) -> tuple[AuthorizationDecision, ToolBoundaryReceipt]:
        decision = principal.authorize(
            action="tool.invoke", resource=tool_id, workspace_id=workspace_id
        )
        size = len(
            json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
        )
        allowed, reason = decision.allowed, decision.reason
        if allowed and size > self._max_input_bytes:
            allowed, reason = False, "input_too_large"
        return decision, ToolBoundaryReceipt(
            phase="input",
            tool_id=tool_id,
            allowed=allowed,
            reason=reason,
            policy_version=principal.policy_version,
        )

    def filter_output(
        self,
        *,
        principal: AgentPrincipal,
        tool_id: str,
        evidence: Sequence[Evidence],
    ) -> tuple[tuple[Evidence, ...], ToolBoundaryReceipt]:
        safe = tuple(filter_evidence(evidence))
        removed = len(evidence) - len(safe)
        return safe, ToolBoundaryReceipt(
            phase="output",
            tool_id=tool_id,
            allowed=True,
            reason="protected_evidence_filtered" if removed else "allowed",
            policy_version=principal.policy_version,
            protected_items_removed=removed,
        )
