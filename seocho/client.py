from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urljoin

import requests

from .exceptions import SeochoConnectionError, SeochoHTTPError
from .governance import ArtifactDiff, ArtifactValidationResult, diff_artifact_payloads, validate_artifact_payload
from .semantic import (
    ApprovedArtifacts,
    SemanticArtifact,
    SemanticArtifactDraftInput,
    SemanticArtifactSummary,
    SemanticPromptContext,
    serialize_optional_mapping,
)
from .types import (
    AgentRunResponse,
    ArchiveResult,
    ChatResponse,
    DebateRunResponse,
    EntityOverride,
    FulltextIndexResponse,
    GraphTarget,
    Memory,
    MemoryCreateResult,
    PlatformChatResponse,
    PlatformSessionResponse,
    RawIngestResult,
    SearchResponse,
    SearchResult,
    SemanticRunResponse,
)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Seocho:
    """Thin client over SEOCHO's public memory-first HTTP API."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or _env_str("SEOCHO_BASE_URL", "http://localhost:8001")).rstrip("/") + "/"
        self.workspace_id = workspace_id or _env_str("SEOCHO_WORKSPACE_ID", "default")
        self.user_id = user_id or os.getenv("SEOCHO_USER_ID")
        self.agent_id = agent_id or os.getenv("SEOCHO_AGENT_ID")
        self.session_id = session_id or os.getenv("SEOCHO_SESSION_ID")
        self.timeout = timeout if timeout is not None else _env_float("SEOCHO_TIMEOUT", 30.0)
        self._session = session or requests.Session()

    def add(
        self,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> Memory:
        payload = self.add_with_details(
            content,
            metadata=metadata,
            prompt_context=prompt_context,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            database=database,
            category=category,
            source_type=source_type,
            semantic_artifact_policy=semantic_artifact_policy,
            approved_artifacts=approved_artifacts,
            approved_artifact_id=approved_artifact_id,
        )
        return payload.memory

    def add_with_details(
        self,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> MemoryCreateResult:
        resolved_metadata = dict(metadata or {})
        serialized_prompt_context = serialize_optional_mapping(
            prompt_context,
            field_name="prompt_context",
        )
        if serialized_prompt_context:
            resolved_metadata["semantic_prompt_context"] = serialized_prompt_context
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "content": content,
            "metadata": resolved_metadata,
            "category": category,
            "source_type": source_type,
            "semantic_artifact_policy": semantic_artifact_policy,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if database:
            body["database"] = database
        serialized_approved_artifacts = serialize_optional_mapping(
            approved_artifacts,
            field_name="approved_artifacts",
        )
        if serialized_approved_artifacts:
            body["approved_artifacts"] = serialized_approved_artifacts
        if approved_artifact_id:
            body["approved_artifact_id"] = approved_artifact_id
        payload = self._request_json("POST", "/api/memories", json_body=body)
        return MemoryCreateResult.from_dict(payload)

    def apply_artifact(
        self,
        artifact_id: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
    ) -> MemoryCreateResult:
        return self.add_with_details(
            content,
            metadata=metadata,
            prompt_context=prompt_context,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            database=database,
            category=category,
            source_type=source_type,
            semantic_artifact_policy="approved_only",
            approved_artifact_id=artifact_id,
        )

    def get(self, memory_id: str, *, database: Optional[str] = None) -> Memory:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("GET", f"/api/memories/{memory_id}", params=params)
        return Memory.from_dict(payload["memory"])

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> List[SearchResult]:
        return self.search_with_context(
            query,
            limit=limit,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            graph_ids=graph_ids,
            databases=databases,
        ).results

    def search_with_context(
        self,
        query: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> SearchResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "query": query,
            "limit": limit,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", "/api/memories/search", json_body=body)
        return SearchResponse.from_dict(payload)

    def ask(
        self,
        message: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> str:
        return self.chat(
            message,
            limit=limit,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            graph_ids=graph_ids,
            databases=databases,
        ).assistant_message

    def chat(
        self,
        message: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> ChatResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "message": message,
            "limit": limit,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", "/api/chat", json_body=body)
        return ChatResponse.from_dict(payload)

    def delete(self, memory_id: str, *, database: Optional[str] = None) -> ArchiveResult:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("DELETE", f"/api/memories/{memory_id}", params=params)
        return ArchiveResult.from_dict(payload)

    def router(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> AgentRunResponse:
        body = self._query_payload(query=query, user_id=user_id, graph_ids=graph_ids)
        payload = self._request_json("POST", "/run_agent", json_body=body)
        return AgentRunResponse.from_dict(payload)

    def semantic(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        databases: Optional[Sequence[str]] = None,
        entity_overrides: Optional[Sequence[EntityOverride | Dict[str, Any]]] = None,
    ) -> SemanticRunResponse:
        body = self._query_payload(query=query, user_id=user_id)
        if databases:
            body["databases"] = list(databases)
        if entity_overrides:
            body["entity_overrides"] = self._serialize_entity_overrides(entity_overrides)
        payload = self._request_json("POST", "/run_agent_semantic", json_body=body)
        return SemanticRunResponse.from_dict(payload)

    def debate(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> DebateRunResponse:
        body = self._query_payload(query=query, user_id=user_id, graph_ids=graph_ids)
        payload = self._request_json("POST", "/run_debate", json_body=body)
        return DebateRunResponse.from_dict(payload)

    def platform_chat(
        self,
        message: str,
        *,
        mode: str = "semantic",
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
        entity_overrides: Optional[Sequence[EntityOverride | Dict[str, Any]]] = None,
    ) -> PlatformChatResponse:
        body: Dict[str, Any] = {
            "message": message,
            "mode": mode,
            "workspace_id": self.workspace_id,
            "user_id": user_id if user_id is not None else self.user_id or "user_default",
        }
        if session_id is not None:
            body["session_id"] = session_id
        elif self.session_id:
            body["session_id"] = self.session_id
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        if entity_overrides:
            body["entity_overrides"] = self._serialize_entity_overrides(entity_overrides)
        payload = self._request_json("POST", "/platform/chat/send", json_body=body)
        return PlatformChatResponse.from_dict(payload)

    def session_history(self, session_id: str) -> PlatformSessionResponse:
        payload = self._request_json("GET", f"/platform/chat/session/{session_id}")
        return PlatformSessionResponse.from_dict(payload)

    def reset_session(self, session_id: str) -> PlatformSessionResponse:
        payload = self._request_json("DELETE", f"/platform/chat/session/{session_id}")
        return PlatformSessionResponse.from_dict(payload)

    def raw_ingest(
        self,
        records: Sequence[Dict[str, Any]],
        *,
        target_database: str,
        enable_rule_constraints: bool = True,
        create_database_if_missing: bool = True,
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> RawIngestResult:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "target_database": target_database,
            "records": [dict(item) for item in records],
            "enable_rule_constraints": enable_rule_constraints,
            "create_database_if_missing": create_database_if_missing,
            "semantic_artifact_policy": semantic_artifact_policy,
        }
        serialized_approved_artifacts = serialize_optional_mapping(
            approved_artifacts,
            field_name="approved_artifacts",
        )
        if serialized_approved_artifacts:
            body["approved_artifacts"] = serialized_approved_artifacts
        if approved_artifact_id:
            body["approved_artifact_id"] = approved_artifact_id
        payload = self._request_json("POST", "/platform/ingest/raw", json_body=body)
        return RawIngestResult.from_dict(payload)

    def graphs(self) -> List[GraphTarget]:
        payload = self._request_json("GET", "/graphs")
        return [GraphTarget.from_dict(item) for item in payload.get("graphs", [])]

    def databases(self) -> List[str]:
        payload = self._request_json("GET", "/databases")
        return [str(item) for item in payload.get("databases", [])]

    def agents(self) -> List[str]:
        payload = self._request_json("GET", "/agents")
        return [str(item) for item in payload.get("agents", [])]

    def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        return self._request_json("GET", f"/health/{scope}")

    def ensure_fulltext_indexes(
        self,
        *,
        databases: Optional[Sequence[str]] = None,
        index_name: str = "entity_fulltext",
        labels: Optional[Sequence[str]] = None,
        properties: Optional[Sequence[str]] = None,
        create_if_missing: bool = True,
    ) -> FulltextIndexResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "index_name": index_name,
            "create_if_missing": create_if_missing,
        }
        if databases:
            body["databases"] = list(databases)
        if labels:
            body["labels"] = list(labels)
        if properties:
            body["properties"] = list(properties)
        payload = self._request_json("POST", "/indexes/fulltext/ensure", json_body=body)
        return FulltextIndexResponse.from_dict(payload)

    def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if status:
            params["status"] = status
        payload = self._request_json("GET", "/semantic/artifacts", params=params)
        return [SemanticArtifactSummary.from_dict(item) for item in payload.get("artifacts", [])]

    def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        params = {"workspace_id": self.workspace_id}
        payload = self._request_json("GET", f"/semantic/artifacts/{artifact_id}", params=params)
        return SemanticArtifact.from_dict(payload)

    def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        payload = serialize_optional_mapping(draft, field_name="draft")
        if payload is None:
            raise TypeError("draft must be provided")
        body = {
            "workspace_id": self.workspace_id,
            **payload,
        }
        response = self._request_json("POST", "/semantic/artifacts/drafts", json_body=body)
        return SemanticArtifact.from_dict(response)

    def approve_artifact(
        self,
        artifact_id: str,
        *,
        approved_by: str,
        approval_note: Optional[str] = None,
    ) -> SemanticArtifact:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "approved_by": approved_by,
        }
        if approval_note:
            body["approval_note"] = approval_note
        payload = self._request_json("POST", f"/semantic/artifacts/{artifact_id}/approve", json_body=body)
        return SemanticArtifact.from_dict(payload)

    def deprecate_artifact(
        self,
        artifact_id: str,
        *,
        deprecated_by: str,
        deprecation_note: Optional[str] = None,
    ) -> SemanticArtifact:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "deprecated_by": deprecated_by,
        }
        if deprecation_note:
            body["deprecation_note"] = deprecation_note
        payload = self._request_json("POST", f"/semantic/artifacts/{artifact_id}/deprecate", json_body=body)
        return SemanticArtifact.from_dict(payload)

    def validate_artifact(
        self,
        artifact: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactValidationResult:
        return validate_artifact_payload(artifact)

    def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        return diff_artifact_payloads(left, right)

    def close(self) -> None:
        self._session.close()

    def _scope_payload(
        self,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        resolved_user_id = user_id if user_id is not None else self.user_id
        resolved_agent_id = agent_id if agent_id is not None else self.agent_id
        resolved_session_id = session_id if session_id is not None else self.session_id
        if resolved_user_id:
            payload["user_id"] = resolved_user_id
        if resolved_agent_id:
            payload["agent_id"] = resolved_agent_id
        if resolved_session_id:
            payload["session_id"] = resolved_session_id
        return payload

    def _query_payload(
        self,
        *,
        query: str,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": query,
            "workspace_id": self.workspace_id,
            "user_id": user_id if user_id is not None else self.user_id or "user_default",
        }
        if graph_ids:
            payload["graph_ids"] = list(graph_ids)
        return payload

    @staticmethod
    def _serialize_entity_overrides(
        entity_overrides: Sequence[EntityOverride | Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for item in entity_overrides:
            if isinstance(item, EntityOverride):
                serialized.append(item.to_dict())
            elif isinstance(item, dict):
                serialized.append(dict(item))
            else:
                raise TypeError("entity_overrides must contain dict objects or EntityOverride values")
        return serialized

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SeochoConnectionError(f"Could not reach SEOCHO at {url}: {exc}") from exc

        if response.status_code >= 400:
            detail: Any
            try:
                payload = response.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = response.text
            raise SeochoHTTPError(status_code=response.status_code, path=path, detail=detail)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SeochoConnectionError(f"SEOCHO returned invalid JSON for {path}") from exc

        if not isinstance(payload, dict):
            raise SeochoConnectionError(f"SEOCHO returned unexpected payload for {path}")
        return payload


class AsyncSeocho:
    """Async wrapper around the sync client for notebook and app usage."""

    def __init__(self, **kwargs: Any) -> None:
        self._client = Seocho(**kwargs)

    async def add(self, content: str, **kwargs: Any) -> Memory:
        return await asyncio.to_thread(self._client.add, content, **kwargs)

    async def add_with_details(self, content: str, **kwargs: Any) -> MemoryCreateResult:
        return await asyncio.to_thread(self._client.add_with_details, content, **kwargs)

    async def apply_artifact(self, artifact_id: str, content: str, **kwargs: Any) -> MemoryCreateResult:
        return await asyncio.to_thread(self._client.apply_artifact, artifact_id, content, **kwargs)

    async def get(self, memory_id: str, **kwargs: Any) -> Memory:
        return await asyncio.to_thread(self._client.get, memory_id, **kwargs)

    async def search(self, query: str, **kwargs: Any) -> List[SearchResult]:
        return await asyncio.to_thread(self._client.search, query, **kwargs)

    async def search_with_context(self, query: str, **kwargs: Any) -> SearchResponse:
        return await asyncio.to_thread(self._client.search_with_context, query, **kwargs)

    async def ask(self, message: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._client.ask, message, **kwargs)

    async def chat(self, message: str, **kwargs: Any) -> ChatResponse:
        return await asyncio.to_thread(self._client.chat, message, **kwargs)

    async def delete(self, memory_id: str, **kwargs: Any) -> ArchiveResult:
        return await asyncio.to_thread(self._client.delete, memory_id, **kwargs)

    async def router(self, query: str, **kwargs: Any) -> AgentRunResponse:
        return await asyncio.to_thread(self._client.router, query, **kwargs)

    async def semantic(self, query: str, **kwargs: Any) -> SemanticRunResponse:
        return await asyncio.to_thread(self._client.semantic, query, **kwargs)

    async def debate(self, query: str, **kwargs: Any) -> DebateRunResponse:
        return await asyncio.to_thread(self._client.debate, query, **kwargs)

    async def platform_chat(self, message: str, **kwargs: Any) -> PlatformChatResponse:
        return await asyncio.to_thread(self._client.platform_chat, message, **kwargs)

    async def session_history(self, session_id: str) -> PlatformSessionResponse:
        return await asyncio.to_thread(self._client.session_history, session_id)

    async def reset_session(self, session_id: str) -> PlatformSessionResponse:
        return await asyncio.to_thread(self._client.reset_session, session_id)

    async def raw_ingest(self, records: Sequence[Dict[str, Any]], **kwargs: Any) -> RawIngestResult:
        return await asyncio.to_thread(self._client.raw_ingest, records, **kwargs)

    async def graphs(self) -> List[GraphTarget]:
        return await asyncio.to_thread(self._client.graphs)

    async def databases(self) -> List[str]:
        return await asyncio.to_thread(self._client.databases)

    async def agents(self) -> List[str]:
        return await asyncio.to_thread(self._client.agents)

    async def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        return await asyncio.to_thread(self._client.health, scope=scope)

    async def ensure_fulltext_indexes(self, **kwargs: Any) -> FulltextIndexResponse:
        return await asyncio.to_thread(self._client.ensure_fulltext_indexes, **kwargs)

    async def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        return await asyncio.to_thread(self._client.list_artifacts, status=status)

    async def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        return await asyncio.to_thread(self._client.get_artifact, artifact_id)

    async def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        return await asyncio.to_thread(self._client.create_artifact_draft, draft)

    async def approve_artifact(
        self,
        artifact_id: str,
        *,
        approved_by: str,
        approval_note: Optional[str] = None,
    ) -> SemanticArtifact:
        return await asyncio.to_thread(
            self._client.approve_artifact,
            artifact_id,
            approved_by=approved_by,
            approval_note=approval_note,
        )

    async def deprecate_artifact(
        self,
        artifact_id: str,
        *,
        deprecated_by: str,
        deprecation_note: Optional[str] = None,
    ) -> SemanticArtifact:
        return await asyncio.to_thread(
            self._client.deprecate_artifact,
            artifact_id,
            deprecated_by=deprecated_by,
            deprecation_note=deprecation_note,
        )

    async def validate_artifact(
        self,
        artifact: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactValidationResult:
        return await asyncio.to_thread(self._client.validate_artifact, artifact)

    async def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        return await asyncio.to_thread(self._client.diff_artifacts, left, right)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._client.close)
