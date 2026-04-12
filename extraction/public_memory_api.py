from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from config import graph_registry
from middleware import get_request_id
from policy import require_runtime_permission
from seocho.runtime_contract import (
    DATABASE_NAME_PATTERN,
    RuntimePath,
    SEMANTIC_ARTIFACT_POLICY_PATTERN,
    SOURCE_TYPE_PATTERN,
    WORKSPACE_ID_PATTERN,
)


class MemoryResource(BaseModel):
    memory_id: str
    workspace_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str
    updated_at: str
    database: Optional[str] = None
    content_preview: str = ""
    source_type: str = ""
    category: str = ""
    entities: List[Dict[str, Any]] = Field(default_factory=list)


class MemoryCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    memory_id: Optional[str] = None
    user_id: Optional[str] = Field(default=None, max_length=120)
    agent_id: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=120)
    content: str = Field(..., min_length=1, max_length=2_000_000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    database: Optional[str] = Field(default=None, pattern=DATABASE_NAME_PATTERN)
    category: str = Field(default="memory", max_length=100)
    source_type: str = Field(default="text", pattern=SOURCE_TYPE_PATTERN)
    semantic_artifact_policy: str = Field(default="auto", pattern=SEMANTIC_ARTIFACT_POLICY_PATTERN)
    approved_artifacts: Optional[Dict[str, Any]] = None
    approved_artifact_id: Optional[str] = None


class MemoryBatchItem(BaseModel):
    memory_id: Optional[str] = None
    content: str = Field(..., min_length=1, max_length=2_000_000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    category: str = Field(default="memory", max_length=100)
    source_type: str = Field(default="text", pattern=SOURCE_TYPE_PATTERN)


class MemoryBatchCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    user_id: Optional[str] = Field(default=None, max_length=120)
    agent_id: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=120)
    items: List[MemoryBatchItem] = Field(..., min_length=1, max_length=100)
    database: Optional[str] = Field(default=None, pattern=DATABASE_NAME_PATTERN)
    semantic_artifact_policy: str = Field(default="auto", pattern=SEMANTIC_ARTIFACT_POLICY_PATTERN)
    approved_artifacts: Optional[Dict[str, Any]] = None
    approved_artifact_id: Optional[str] = None


class MemorySearchRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    user_id: Optional[str] = Field(default=None, max_length=120)
    agent_id: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=120)
    graph_ids: Optional[List[str]] = None
    databases: Optional[List[str]] = None


class MemorySearchResult(BaseModel):
    memory_id: str
    content: str
    content_preview: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: float
    reasons: List[str] = Field(default_factory=list)
    matched_entities: List[str] = Field(default_factory=list)
    database: str
    status: str
    evidence_bundle: Dict[str, Any] = Field(default_factory=dict)


class MemoryCreateResponse(BaseModel):
    memory: MemoryResource
    ingest_summary: Dict[str, Any]
    trace_id: str


class MemoryBatchCreateResponse(BaseModel):
    memories: List[MemoryResource]
    ingest_summary: Dict[str, Any]
    trace_id: str


class MemoryGetResponse(BaseModel):
    memory: MemoryResource
    trace_id: str


class MemorySearchResponse(BaseModel):
    results: List[MemorySearchResult]
    semantic_context: Dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class MemoryArchiveResponse(BaseModel):
    memory_id: str
    workspace_id: str
    database: str
    status: str
    archived_at: str
    archived_nodes: int
    trace_id: str


class MemoryChatRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    message: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    user_id: Optional[str] = Field(default=None, max_length=120)
    agent_id: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=120)
    graph_ids: Optional[List[str]] = None
    databases: Optional[List[str]] = None


class MemoryChatResponse(BaseModel):
    assistant_message: str
    memory_hits: List[Dict[str, Any]] = Field(default_factory=list)
    search_results: List[MemorySearchResult] = Field(default_factory=list)
    semantic_context: Dict[str, Any] = Field(default_factory=dict)
    evidence_bundle: Dict[str, Any] = Field(default_factory=dict)
    trace_id: str


def build_public_memory_router(
    *,
    memory_service: Any,
    approved_artifact_resolver: Any,
) -> APIRouter:
    router = APIRouter(tags=["public-memory"])

    def _trace_id() -> str:
        return get_request_id() or "trace_unavailable"

    def _resolve_approved_artifacts(
        *,
        workspace_id: str,
        approved_artifacts: Optional[Dict[str, Any]],
        approved_artifact_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if approved_artifacts is not None:
            return approved_artifacts
        if approved_artifact_id:
            return approved_artifact_resolver(workspace_id=workspace_id, artifact_id=approved_artifact_id)
        return None

    def _resolve_target_databases(
        *,
        databases: Optional[List[str]],
        graph_ids: Optional[List[str]],
    ) -> Optional[List[str]]:
        resolved: List[str] = []
        for database in databases or []:
            if database not in resolved:
                resolved.append(database)
        for graph_id in graph_ids or []:
            target = graph_registry.get_graph(graph_id)
            if target is None:
                raise ValueError(
                    f"Invalid graph '{graph_id}'. Valid options: {graph_registry.list_graph_ids()}"
                )
            if target.database not in resolved:
                resolved.append(target.database)
        return resolved or None

    @router.post(RuntimePath.API_MEMORIES, response_model=MemoryCreateResponse)
    async def create_memory(request: MemoryCreateRequest) -> MemoryCreateResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=request.workspace_id)
            payload = memory_service.create_memory(
                workspace_id=request.workspace_id,
                content=request.content,
                metadata=request.metadata,
                memory_id=request.memory_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                database=request.database,
                category=request.category,
                source_type=request.source_type,
                semantic_artifact_policy=request.semantic_artifact_policy,
                approved_artifacts=_resolve_approved_artifacts(
                    workspace_id=request.workspace_id,
                    approved_artifacts=request.approved_artifacts,
                    approved_artifact_id=request.approved_artifact_id,
                ),
            )
            return MemoryCreateResponse(
                memory=MemoryResource(**payload["memory"]),
                ingest_summary=payload["ingest_summary"],
                trace_id=_trace_id(),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(RuntimePath.API_MEMORIES_BATCH, response_model=MemoryBatchCreateResponse)
    async def create_memory_batch(request: MemoryBatchCreateRequest) -> MemoryBatchCreateResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=request.workspace_id)
            payload = memory_service.create_memories(
                workspace_id=request.workspace_id,
                items=[item.model_dump() for item in request.items],
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                database=request.database,
                semantic_artifact_policy=request.semantic_artifact_policy,
                approved_artifacts=_resolve_approved_artifacts(
                    workspace_id=request.workspace_id,
                    approved_artifacts=request.approved_artifacts,
                    approved_artifact_id=request.approved_artifact_id,
                ),
            )
            return MemoryBatchCreateResponse(
                memories=[MemoryResource(**item) for item in payload["memories"]],
                ingest_summary=payload["ingest_summary"],
                trace_id=_trace_id(),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(RuntimePath.API_MEMORY, response_model=MemoryGetResponse)
    async def get_memory(
        memory_id: str,
        workspace_id: str = Query(..., pattern=WORKSPACE_ID_PATTERN),
        database: Optional[str] = Query(default=None, pattern=DATABASE_NAME_PATTERN),
    ) -> MemoryGetResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=workspace_id)
            payload = memory_service.get_memory(memory_id=memory_id, workspace_id=workspace_id, database=database)
            if payload is None:
                raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
            return MemoryGetResponse(memory=MemoryResource(**payload), trace_id=_trace_id())
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post(RuntimePath.API_MEMORIES_SEARCH, response_model=MemorySearchResponse)
    async def search_memories(request: MemorySearchRequest) -> MemorySearchResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=request.workspace_id)
            payload = memory_service.search_memories(
                workspace_id=request.workspace_id,
                query=request.query,
                limit=request.limit,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                databases=_resolve_target_databases(
                    databases=request.databases,
                    graph_ids=request.graph_ids,
                ),
            )
            return MemorySearchResponse(
                results=[MemorySearchResult(**item) for item in payload["results"]],
                semantic_context=payload.get("semantic_context", {}),
                trace_id=_trace_id(),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete(RuntimePath.API_MEMORY, response_model=MemoryArchiveResponse)
    async def archive_memory(
        memory_id: str,
        workspace_id: str = Query(..., pattern=WORKSPACE_ID_PATTERN),
        database: Optional[str] = Query(default=None, pattern=DATABASE_NAME_PATTERN),
    ) -> MemoryArchiveResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=workspace_id)
            payload = memory_service.archive_memory(
                memory_id=memory_id,
                workspace_id=workspace_id,
                database=database,
            )
            return MemoryArchiveResponse(trace_id=_trace_id(), **payload)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post(RuntimePath.API_CHAT, response_model=MemoryChatResponse)
    async def chat_from_memories(request: MemoryChatRequest) -> MemoryChatResponse:
        try:
            require_runtime_permission(role="user", action="manage_memories", workspace_id=request.workspace_id)
            payload = memory_service.chat_from_memories(
                workspace_id=request.workspace_id,
                message=request.message,
                limit=request.limit,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                databases=_resolve_target_databases(
                    databases=request.databases,
                    graph_ids=request.graph_ids,
                ),
            )
            return MemoryChatResponse(
                assistant_message=payload["assistant_message"],
                memory_hits=payload["memory_hits"],
                search_results=[MemorySearchResult(**item) for item in payload["search_results"]],
                semantic_context=payload.get("semantic_context", {}),
                evidence_bundle=payload.get("evidence_bundle", {}),
                trace_id=_trace_id(),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
