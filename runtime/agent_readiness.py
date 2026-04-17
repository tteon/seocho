"""Readiness state helpers for graph agent orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from runtime.agent_state import AgentStateMachine, AgentStatus


READINESS_READY = "ready"
READINESS_DEGRADED = "degraded"
READINESS_BLOCKED = "blocked"


def summarize_readiness(statuses: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_statuses = [_normalize_status(item.get("status")) for item in statuses]
    ready_count = sum(1 for status in normalized_statuses if status == AgentStatus.READY)
    total = len(statuses)
    degraded_count = sum(
        1
        for status in normalized_statuses
        if status in {AgentStatus.DEGRADED, AgentStatus.BLOCKED, AgentStatus.INITIALIZING}
    )

    state = AgentStateMachine()
    if ready_count == 0:
        state.mark_blocked("no ready graph agents available")
    elif degraded_count > 0:
        state.mark_degraded("one or more graph agents are not fully ready")
    else:
        state.mark_ready()


    return {
        "debate_state": state.status.value,
        "degraded": state.status != AgentStatus.READY,
        "ready_count": ready_count,
        "degraded_count": degraded_count,
        "total_count": total,
    }


def _normalize_status(value: Any) -> AgentStatus:
    raw = str(value or "").strip().lower()
    try:
        return AgentStatus(raw)
    except ValueError:
        return AgentStatus.DEGRADED
