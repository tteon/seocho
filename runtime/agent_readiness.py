"""Readiness state helpers for graph agent orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from runtime.agent_state import AgentStateMachine, AgentStatus


READINESS_READY = "ready"
READINESS_DEGRADED = "degraded"
READINESS_BLOCKED = "blocked"


def summarize_readiness(statuses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll up per-graph status entries into one debate readiness verdict.

    Phase 3 surfaces ontology context hash drift as first-class rollup data.
    Status entries that carry an ``ontology_context_mismatch`` field (populated
    by ``AgentFactory.create_agents_for_graphs`` when Phase 2's tool-level
    skew probe fires) are counted in ``mismatch_count`` and listed in
    ``mismatch_graph_ids``. The router/supervisor can then route around
    skewed agents the same way it routes around DEGRADED ones.
    """

    normalized_statuses = [_normalize_status(item.get("status")) for item in statuses]
    ready_count = sum(1 for status in normalized_statuses if status == AgentStatus.READY)
    total = len(statuses)
    degraded_count = sum(
        1
        for status in normalized_statuses
        if status in {AgentStatus.DEGRADED, AgentStatus.BLOCKED, AgentStatus.INITIALIZING}
    )

    skewed_entries = [
        item
        for item in statuses
        if isinstance(item, dict) and item.get("ontology_context_mismatch")
    ]
    mismatch_graph_ids: List[str] = []
    for entry in skewed_entries:
        graph_id = str(entry.get("graph") or entry.get("graph_id") or entry.get("database") or "").strip()
        if graph_id and graph_id not in mismatch_graph_ids:
            mismatch_graph_ids.append(graph_id)
    mismatch_count = len(mismatch_graph_ids)

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
        "mismatch_count": mismatch_count,
        "mismatch_graph_ids": mismatch_graph_ids,
    }


def _normalize_status(value: Any) -> AgentStatus:
    raw = str(value or "").strip().lower()
    try:
        return AgentStatus(raw)
    except ValueError:
        return AgentStatus.DEGRADED
