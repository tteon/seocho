"""Executable gold contracts for transaction-management memory questions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Tuple


@dataclass(frozen=True, slots=True)
class AgentMemoryQuery:
    query_id: str
    question: str
    scenario: str
    required_slots: Tuple[str, ...]
    expected_terminal_step: str | None
    max_hops: int
    denied_fields: Tuple[str, ...] = ("raw_account_id", "workspace_id")
    trigger_phrases: Tuple[str, ...] = ()


AGENT_MEMORY_QUERIES = (
    AgentMemoryQuery("current-state", "현재 canonical 주문 상태와 결정 event는?", "full_fill", ("state", "canonical_event", "provenance"), "memory_published", 1, trigger_phrases=("현재 주문 상태", "canonical 주문 상태", "latest order state", "canonical state", "주문은 지금")),
    AgentMemoryQuery("point-in-time", "이전 sequence 상태와 현재 상태가 달라진 이유는?", "amend", ("historical_state", "current_state", "superseding_events"), "memory_published", 2, trigger_phrases=("이전 sequence", "시점 상태", "point in time", "what changed since")),
    AgentMemoryQuery("cancel-fill-race", "취소 이후 fill이 발생한 주문의 최종 상태와 처리 순서는?", "cancel_fill_race", ("state", "ordered_events", "conflict_resolution"), "memory_published", 2, trigger_phrases=("취소 이후 fill", "취소 체결", "cancel fill race", "filled after cancel")),
    AgentMemoryQuery("agent-handoff", "전략 결정부터 memory publication까지의 agent 경로는?", "full_fill", ("relationship_path", "state_transitions", "provenance"), "memory_published", 4, trigger_phrases=("agent 경로", "에이전트 경로", "agent handoff", "which agents")),
    AgentMemoryQuery("projection-lag", "graph가 뒤처진 경우 방금 제출된 주문을 어떻게 답해야 하나?", "unknown_then_reconciled", ("authoritative_state", "projection_watermark", "support_status"), None, 1, trigger_phrases=("graph가 뒤처", "projection lag", "graph is stale", "아직 graph에")),
    AgentMemoryQuery("long-context", "전체 history에서 최신 상태 설명에 필요한 memory만 선택해줘.", "reconnect_snapshot", ("state", "selected_revisions", "provenance"), "memory_published", 2, trigger_phrases=("필요한 memory만", "필요한 메모리만", "long context", "select relevant memories")),
)


@dataclass(frozen=True, slots=True)
class AgentMemoryQueryPlan:
    query_id: str
    tier: str
    cypher: str
    params: Mapping[str, Any]
    required_slots: Tuple[str, ...]
    max_hops: int


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9가-힣]+", value.lower()))


def classify_agent_memory_query(question: str) -> AgentMemoryQuery | None:
    """Classify supported transaction-memory intent without exposing data to an LLM."""

    normalized = _normalized(question)
    exact = [
        query
        for query in AGENT_MEMORY_QUERIES
        if any(_normalized(phrase) in normalized for phrase in query.trigger_phrases)
    ]
    return exact[0] if len(exact) == 1 else None


def compile_agent_memory_query(
    query: AgentMemoryQuery, *, workspace_id: str, intent_id: str, limit: int = 50
) -> AgentMemoryQueryPlan:
    """Compile a supported intent to the audited event-chain retrieval recipe."""

    if not workspace_id or not intent_id:
        raise ValueError("workspace_id and intent_id are required")
    cypher = (
        "MATCH (:ExchangeIntent {id:$intent_id,workspace:$workspace_id})"
        "-[:HAS_EVENT]->(e:ExchangeMemoryEvent) "
        "RETURN e.step AS step,e.sequence AS sequence,e.actor AS actor,"
        "e.recipient AS recipient,e.provenance AS provenance "
        "ORDER BY e.sequence LIMIT $limit"
    )
    return AgentMemoryQueryPlan(
        query_id=query.query_id,
        tier="approved_recipe",
        cypher=cypher,
        params={"workspace_id": workspace_id, "intent_id": intent_id, "limit": min(max(limit, 1), 50)},
        required_slots=query.required_slots,
        max_hops=query.max_hops,
    )


def build_augmented_prompt(
    query: AgentMemoryQuery, *, evidence: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    """Build cache-stable policy prefix and request-specific evidence suffix."""

    system = (
        "SEOCHO agent-memory answer contract v1. Use audited evidence only. "
        "Return JSON keys state, explanation, evidence_count, support_status. "
        "Never reveal intent_ref, workspace_id, raw_account_id, or provenance. "
        "Never authorize, submit, cancel, or retry a transaction."
    )
    suffix = json.dumps(
        {"question": query.question, "required_slots": query.required_slots, "evidence": evidence},
        sort_keys=True,
        ensure_ascii=False,
    )
    metadata = {
        "query_id": query.query_id,
        "prompt_prefix_hash": hashlib.sha256(system.encode()).hexdigest()[:16],
        "required_slot_count": len(query.required_slots),
    }
    return system, suffix, metadata


__all__ = [
    "AGENT_MEMORY_QUERIES", "AgentMemoryQuery", "AgentMemoryQueryPlan",
    "build_augmented_prompt", "classify_agent_memory_query", "compile_agent_memory_query",
]
