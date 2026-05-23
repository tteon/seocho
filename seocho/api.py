from __future__ import annotations

from threading import RLock
from typing import Any, Dict, List, Optional, Sequence

from .client import ExecutionPlanBuilder, Seocho
from .semantic import ApprovedArtifacts
from .models import (
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
    SemanticRunRecord,
    SemanticRunResponse,
)
from .qualification import (
    CurationDecisionResult,
    CurationPreview,
    GraphProjectionResult,
    QualificationCase,
    QualificationRunResult,
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


def add_graph(graph_data: Dict[str, Any], **kwargs: Any) -> Memory:
    return get_client().add_graph(graph_data, **kwargs)


def qualify_graph(**kwargs: Any) -> QualificationRunResult:
    return get_client().qualify_graph(**kwargs)


def list_curation_cases(**kwargs: Any) -> List[QualificationCase]:
    return get_client().list_curation_cases(**kwargs)


def preview_curation_decision(case_id: str, **kwargs: Any) -> CurationPreview:
    return get_client().preview_curation_decision(case_id, **kwargs)


def apply_curation_decision(case_id: str, **kwargs: Any) -> CurationDecisionResult:
    return get_client().apply_curation_decision(case_id, **kwargs)


def project_canonical_graph(**kwargs: Any) -> GraphProjectionResult:
    return get_client().project_canonical_graph(**kwargs)


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


def advanced(query: str, **kwargs: Any) -> DebateRunResponse:
    return get_client().advanced(query, **kwargs)


def plan(query: str, **kwargs: Any) -> ExecutionPlanBuilder:
    return get_client().plan(query, **kwargs)


def execute(plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
    return get_client().execute(plan)


def semantic(query: str, **kwargs: Any) -> SemanticRunResponse:
    return get_client().semantic(query, **kwargs)


def semantic_runs(
    *,
    limit: int = 20,
    route: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> List[SemanticRunRecord]:
    return get_client().semantic_runs(limit=limit, route=route, intent_id=intent_id)


def semantic_run(run_id: str) -> SemanticRunRecord:
    return get_client().semantic_run(run_id)


def debate(query: str, **kwargs: Any) -> DebateRunResponse:
    return get_client().debate(query, **kwargs)


def platform_chat(message: str, **kwargs: Any) -> PlatformChatResponse:
    return get_client().platform_chat(message, **kwargs)


def session_history(session_id: str, **kwargs: Any) -> PlatformSessionResponse:
    return get_client().session_history(session_id, **kwargs)


def reset_session(session_id: str, **kwargs: Any) -> PlatformSessionResponse:
    return get_client().reset_session(session_id, **kwargs)


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
