from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

WORKSPACE_ID_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$"
# Keep API validation aligned with runtime/config and DozerDB provisioning rules.
DATABASE_NAME_PATTERN = r"^[a-z][a-z0-9]{2,62}$"
INDEX_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
SOURCE_TYPE_PATTERN = r"^(text|csv|pdf)$"
SEMANTIC_ARTIFACT_POLICY_PATTERN = r"^(auto|draft_only|approved_only)$"


class RuntimePath:
    API_MEMORIES = "/api/memories"
    API_MEMORIES_BATCH = "/api/memories/batch"
    API_MEMORY = "/api/memories/{memory_id}"
    API_MEMORIES_SEARCH = "/api/memories/search"
    API_CHAT = "/api/chat"
    RUN_AGENT = "/run_agent"
    RUN_AGENT_SEMANTIC = "/run_agent_semantic"
    RUN_DEBATE = "/run_debate"
    PLATFORM_CHAT_SEND = "/platform/chat/send"
    PLATFORM_CHAT_SESSION = "/platform/chat/session/{session_id}"
    PLATFORM_INGEST_RAW = "/platform/ingest/raw"
    SEMANTIC_RUNS = "/semantic/runs"
    SEMANTIC_RUN = "/semantic/runs/{run_id}"
    INDEXES_FULLTEXT_ENSURE = "/indexes/fulltext/ensure"
    GRAPHS = "/graphs"
    DATABASES = "/databases"
    AGENTS = "/agents"
    HEALTH_RUNTIME = "/health/runtime"
    HEALTH_BATCH = "/health/batch"


def memory_path(memory_id: str) -> str:
    return RuntimePath.API_MEMORY.format(memory_id=memory_id)


def platform_chat_session_path(session_id: str) -> str:
    return RuntimePath.PLATFORM_CHAT_SESSION.format(session_id=session_id)


def semantic_run_path(run_id: str) -> str:
    return RuntimePath.SEMANTIC_RUN.format(run_id=run_id)


def build_scope_payload(
    *,
    default_user_id: Optional[str] = None,
    default_agent_id: Optional[str] = None,
    default_session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    resolved_user_id = user_id if user_id is not None else default_user_id
    resolved_agent_id = agent_id if agent_id is not None else default_agent_id
    resolved_session_id = session_id if session_id is not None else default_session_id
    if resolved_user_id:
        payload["user_id"] = resolved_user_id
    if resolved_agent_id:
        payload["agent_id"] = resolved_agent_id
    if resolved_session_id:
        payload["session_id"] = resolved_session_id
    return payload


def build_query_payload(
    *,
    query: str,
    workspace_id: str,
    default_user_id: Optional[str] = None,
    user_id: Optional[str] = None,
    graph_ids: Optional[Sequence[str]] = None,
    reasoning_cycle: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "query": query,
        "workspace_id": workspace_id,
        "user_id": user_id if user_id is not None else default_user_id or "user_default",
    }
    if graph_ids:
        payload["graph_ids"] = list(graph_ids)
    if reasoning_cycle:
        payload["reasoning_cycle"] = dict(reasoning_cycle)
    return payload


def serialize_entity_overrides(
    entity_overrides: Sequence[Any],
) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for item in entity_overrides:
        if isinstance(item, dict):
            serialized.append(dict(item))
            continue
        if hasattr(item, "to_dict"):
            serialized.append(item.to_dict())
            continue
        raise TypeError("entity_overrides must contain dict objects or values with to_dict()")
    return serialized
