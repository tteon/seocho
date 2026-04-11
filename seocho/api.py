from __future__ import annotations

from threading import RLock
from typing import Any, Dict, List, Optional, Sequence

from .client import ExecutionPlanBuilder, Seocho
from .semantic import ApprovedArtifacts
from .types import (
    AgentRunResponse,
    ArchiveResult,
    ChatResponse,
    DebateRunResponse,
    EntityOverride,
    ExecutionPlan,
    ExecutionResult,
    FulltextIndexResponse,
    GraphRef,
    GraphTarget,
    Memory,
    MemoryCreateResult,
    PlatformChatResponse,
    PlatformSessionResponse,
    RawIngestResult,
    ReasoningPolicy,
    SearchResult,
    SemanticRunResponse,
)

_client_lock = RLock()
_default_client: Optional[Seocho] = None


def connect(**kwargs: Any) -> Seocho:
    return Seocho(**kwargs)


def configure(**kwargs: Any) -> Seocho:
    global _default_client
    with _client_lock:
        if _default_client is not None:
            _default_client.close()
        _default_client = Seocho(**kwargs)
        return _default_client


def get_client() -> Seocho:
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = Seocho()
        return _default_client


def close() -> None:
    global _default_client
    with _client_lock:
        if _default_client is not None:
            _default_client.close()
            _default_client = None


def add(content: str, **kwargs: Any) -> Memory:
    return get_client().add(content, **kwargs)


def add_with_details(content: str, **kwargs: Any) -> MemoryCreateResult:
    return get_client().add_with_details(content, **kwargs)


def apply_artifact(artifact_id: str, content: str, **kwargs: Any) -> MemoryCreateResult:
    return get_client().apply_artifact(artifact_id, content, **kwargs)


def get(memory_id: str, **kwargs: Any) -> Memory:
    return get_client().get(memory_id, **kwargs)


def search(query: str, **kwargs: Any) -> List[SearchResult]:
    return get_client().search(query, **kwargs)


def ask(message: str, **kwargs: Any) -> str:
    return get_client().ask(message, **kwargs)


def chat(message: str, **kwargs: Any) -> ChatResponse:
    return get_client().chat(message, **kwargs)


def delete(memory_id: str, **kwargs: Any) -> ArchiveResult:
    return get_client().delete(memory_id, **kwargs)


def router(query: str, **kwargs: Any) -> AgentRunResponse:
    return get_client().router(query, **kwargs)


def react(query: str, **kwargs: Any) -> AgentRunResponse:
    return get_client().react(query, **kwargs)


def plan(query: str, **kwargs: Any) -> ExecutionPlanBuilder:
    return get_client().plan(query, **kwargs)


def execute(plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
    return get_client().execute(plan)


def semantic(query: str, **kwargs: Any) -> SemanticRunResponse:
    return get_client().semantic(query, **kwargs)


def debate(query: str, **kwargs: Any) -> DebateRunResponse:
    return get_client().debate(query, **kwargs)


def platform_chat(message: str, **kwargs: Any) -> PlatformChatResponse:
    return get_client().platform_chat(message, **kwargs)


def session_history(session_id: str) -> PlatformSessionResponse:
    return get_client().session_history(session_id)


def reset_session(session_id: str) -> PlatformSessionResponse:
    return get_client().reset_session(session_id)


def raw_ingest(
    records: Sequence[Dict[str, Any]],
    *,
    target_database: str,
    enable_rule_constraints: bool = True,
    create_database_if_missing: bool = True,
    semantic_artifact_policy: str = "auto",
    approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
    approved_artifact_id: Optional[str] = None,
) -> RawIngestResult:
    return get_client().raw_ingest(
        records,
        target_database=target_database,
        enable_rule_constraints=enable_rule_constraints,
        create_database_if_missing=create_database_if_missing,
        semantic_artifact_policy=semantic_artifact_policy,
        approved_artifacts=approved_artifacts,
        approved_artifact_id=approved_artifact_id,
    )


def graphs() -> List[GraphTarget]:
    return get_client().graphs()


def databases() -> List[str]:
    return get_client().databases()


def agents() -> List[str]:
    return get_client().agents()


def health(*, scope: str = "runtime") -> Dict[str, Any]:
    return get_client().health(scope=scope)


def ensure_fulltext_indexes(**kwargs: Any) -> FulltextIndexResponse:
    return get_client().ensure_fulltext_indexes(**kwargs)
