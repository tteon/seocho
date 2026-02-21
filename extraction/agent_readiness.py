"""Readiness state helpers for graph agent orchestration."""

from __future__ import annotations

from typing import Any, Dict, List


READINESS_READY = "ready"
READINESS_DEGRADED = "degraded"
READINESS_BLOCKED = "blocked"


def summarize_readiness(statuses: List[Dict[str, Any]]) -> Dict[str, Any]:
    ready_count = sum(1 for item in statuses if str(item.get("status")) == READINESS_READY)
    total = len(statuses)
    degraded_count = max(total - ready_count, 0)

    if ready_count == 0:
        state = READINESS_BLOCKED
    elif degraded_count > 0:
        state = READINESS_DEGRADED
    else:
        state = READINESS_READY

    return {
        "debate_state": state,
        "degraded": state != READINESS_READY,
        "ready_count": ready_count,
        "degraded_count": degraded_count,
        "total_count": total,
    }
