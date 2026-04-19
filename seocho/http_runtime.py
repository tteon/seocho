from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .indexing_design import build_query_reasoning_cycle_report
from .runtime_contract import (
    RuntimePath,
    WORKSPACE_ID_PATTERN,
)
from .runtime_bundle import RuntimeBundle, create_client_from_runtime_bundle

_SEARCH_PROPERTIES = ["name", "title", "id", "uri", "description", "content_preview", "content"]


class BundleMemoryCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    database: Optional[str] = None
    category: str = "memory"


class BundleMemorySearchRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    query: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    databases: Optional[List[str]] = None


class BundleChatRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    message: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    databases: Optional[List[str]] = None


class BundleSemanticRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN)
    query: str = Field(..., min_length=1)
    databases: Optional[List[str]] = None
    reasoning_mode: bool = False
    repair_budget: int = Field(default=0, ge=0, le=8)
    reasoning_cycle: Optional[Dict[str, Any]] = None


def create_bundle_runtime_app(
    bundle_source: RuntimeBundle | str,
    *,
    client: Optional[Any] = None,
) -> FastAPI:
    bundle = bundle_source if isinstance(bundle_source, RuntimeBundle) else RuntimeBundle.load(bundle_source)
    runtime_client = client or create_client_from_runtime_bundle(bundle)

    app = FastAPI(title=f"{bundle.app_name} Bundle Runtime")

    def _ensure_workspace(request_workspace_id: str) -> None:
        if request_workspace_id != bundle.workspace_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Workspace mismatch: bundle runtime is pinned to '{bundle.workspace_id}', "
                    f"got '{request_workspace_id}'."
                ),
            )

    def _resolve_database(databases: Optional[Sequence[str]], explicit_database: Optional[str] = None) -> str:
        if explicit_database:
            return explicit_database
        for item in databases or []:
            if str(item).strip():
                return str(item).strip()
        return bundle.default_database

    def _route_for_database(database: str) -> str:
        graph_lookup = {item.database: item for item in bundle.graphs}
        graph = graph_lookup.get(database)
        graph_model = str(graph.graph_model if graph else bundle.ontology.get("graph_model", "lpg")).strip() or "lpg"
        routing = str(bundle.agent_config.get("routing", "auto")).strip() or "auto"
        if routing == "rdf_only":
            return "rdf"
        if routing == "lpg_only":
            return "lpg"
        if graph_model == "rdf":
            return "rdf"
        if graph_model == "hybrid":
            return "hybrid"
        return "lpg"

    def _search_graph(query_text: str, *, database: str, limit: int) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE any(key IN $properties
            WHERE n[key] IS NOT NULL
              AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN coalesce(n.memory_id, n._source_id, n.id, elementId(n)) AS memory_id,
               coalesce(n.content, n.content_preview, n.description, n.name, n.title, '') AS content,
               coalesce(n.content_preview, n.description, n.name, n.title, '') AS content_preview,
               properties(n) AS metadata,
               coalesce(n.name, n.title, n.id, n.uri, '') AS matched_entity
        LIMIT $limit
        """
        try:
            rows = runtime_client.query(
                query,
                params={
                    "properties": list(_SEARCH_PROPERTIES),
                    "query": query_text,
                    "limit": limit,
                },
                database=database,
            )
        except Exception:
            rows = []

        results: List[Dict[str, Any]] = []
        for row in rows:
            matched_entity = str(row.get("matched_entity", "")).strip()
            lexical = (
                SequenceMatcher(None, query_text.lower(), matched_entity.lower()).ratio()
                if matched_entity
                else 0.0
            )
            content = str(row.get("content", "")).strip()
            content_preview = str(row.get("content_preview", "")).strip()
            if not content_preview:
                content_preview = content[:240]
            results.append(
                {
                    "memory_id": str(row.get("memory_id", "")).strip(),
                    "content": content,
                    "content_preview": content_preview,
                    "metadata": dict(row.get("metadata", {})) if isinstance(row.get("metadata"), dict) else {},
                    "score": round(lexical, 4),
                    "reasons": ["bundle_local_engine_search"],
                    "matched_entities": [matched_entity] if matched_entity else [],
                    "database": database,
                    "status": "active",
                    "evidence_bundle": {
                        "schema_version": "bundle_search_evidence.v1",
                        "database": database,
                        "matched_entity": matched_entity,
                    },
                }
            )
        return results

    @app.get("/health/runtime")
    async def health_runtime() -> Dict[str, Any]:
        return {
            "status": "ready",
            "runtime_mode": "bundle_local_engine",
            "app_name": bundle.app_name,
            "workspace_id": bundle.workspace_id,
            "default_database": bundle.default_database,
        }

    @app.get("/graphs")
    async def graphs() -> Dict[str, Any]:
        return {
            "graphs": [item.to_public_dict(workspace_id=bundle.workspace_id) for item in bundle.graphs],
        }

    @app.post(RuntimePath.API_MEMORIES)
    async def create_memory(request: BundleMemoryCreateRequest) -> Dict[str, Any]:
        _ensure_workspace(request.workspace_id)
        memory = runtime_client.add(
            request.content,
            metadata=request.metadata,
            database=request.database or bundle.default_database,
            category=request.category,
        )
        return {
            "memory": memory.to_dict(),
            "ingest_summary": {
                "runtime_mode": "bundle_local_engine",
                "bundle_app": bundle.app_name,
            },
            "trace_id": "bundle_local_engine",
        }

    @app.post(RuntimePath.API_MEMORIES_SEARCH)
    async def search_memories(request: BundleMemorySearchRequest) -> Dict[str, Any]:
        _ensure_workspace(request.workspace_id)
        database = _resolve_database(request.databases)
        results = _search_graph(request.query, database=database, limit=request.limit)
        return {
            "results": results,
            "semantic_context": {
                "runtime_mode": "bundle_local_engine",
                "bundle_app": bundle.app_name,
                "route": _route_for_database(database),
            },
            "trace_id": "bundle_local_engine",
        }

    @app.post(RuntimePath.API_CHAT)
    async def chat(request: BundleChatRequest) -> Dict[str, Any]:
        _ensure_workspace(request.workspace_id)
        database = _resolve_database(request.databases)
        search_results = _search_graph(request.message, database=database, limit=request.limit)
        answer = runtime_client.ask(
            request.message,
            database=database,
            reasoning_mode=bool(bundle.agent_config.get("reasoning_mode", False)),
            repair_budget=int(bundle.agent_config.get("repair_budget", 0) or 0),
        )
        return {
            "assistant_message": answer,
            "memory_hits": [],
            "search_results": search_results,
            "semantic_context": {
                "runtime_mode": "bundle_local_engine",
                "bundle_app": bundle.app_name,
                "route": _route_for_database(database),
            },
            "evidence_bundle": {
                "schema_version": "bundle_chat_evidence.v1",
                "database": database,
                "search_result_count": len(search_results),
            },
            "trace_id": "bundle_local_engine",
        }

    @app.post(RuntimePath.RUN_AGENT_SEMANTIC)
    async def run_agent_semantic(request: BundleSemanticRequest) -> Dict[str, Any]:
        _ensure_workspace(request.workspace_id)
        database = _resolve_database(request.databases)
        route = _route_for_database(database)
        search_results = _search_graph(request.query, database=database, limit=5)
        answer = runtime_client.ask(
            request.query,
            database=database,
            reasoning_mode=request.reasoning_mode,
            repair_budget=request.repair_budget,
        )
        supported = bool(search_results)
        timestamp = datetime.now(timezone.utc).isoformat()
        run_id = f"bundle_run_{uuid4().hex}"
        support_assessment = {
            "schema_version": "intent_support.v1",
            "supported": supported,
            "status": "supported" if supported else "unsupported",
            "reason": "bundle_local_engine_proxy",
            "coverage": 1.0 if supported else 0.0,
        }
        strategy_decision = {
            "schema_version": "strategy_decision.v1",
            "requested_mode": "semantic",
            "initial_mode": "semantic_repair" if request.reasoning_mode else "semantic_direct",
            "executed_mode": "semantic_repair" if request.reasoning_mode else "semantic_direct",
            "reason": "bundle runtime proxied a local-engine query over HTTP.",
            "repair_budget": request.repair_budget,
            "reasoning_mode_requested": request.reasoning_mode,
            "advanced_debate_recommended": False,
        }
        evidence_bundle = {
            "schema_version": "evidence_bundle.v2",
            "database": database,
            "grounded_slots": ["target_entity"] if supported else [],
            "missing_slots": [] if supported else ["target_entity"],
            "candidate_entities": [
                {
                    "display_name": item["matched_entities"][0] if item["matched_entities"] else "",
                    "database": item["database"],
                    "confidence": item["score"],
                }
                for item in search_results[:3]
            ],
        }
        run_metadata = {
            "schema_version": "semantic_run_registry.v1",
            "run_id": run_id,
            "recorded": False,
            "registry_path": "",
            "timestamp": timestamp,
        }
        reasoning_cycle = build_query_reasoning_cycle_report(
            request.reasoning_cycle,
            support_assessment=support_assessment,
            query_diagnostics=[],
        )
        return {
            "response": answer,
            "route": route,
            "trace_steps": [
                {
                    "id": "bundle-runtime-1",
                    "type": "SYSTEM",
                    "agent": "bundle-runtime",
                    "content": "Executed local engine semantic proxy.",
                }
            ],
            "semantic_context": {
                "runtime_mode": "bundle_local_engine",
                "bundle_app": bundle.app_name,
                "support_assessment": support_assessment,
                "strategy_decision": strategy_decision,
                "run_metadata": run_metadata,
                "evidence_bundle_preview": evidence_bundle,
                "reasoning_cycle": reasoning_cycle or {},
            },
            "lpg_result": {
                "mode": route,
                "summary": "Bundle runtime proxied the local engine through a semantic compatibility surface.",
                "records": search_results,
            },
            "rdf_result": None,
            "support_assessment": support_assessment,
            "strategy_decision": strategy_decision,
            "run_metadata": run_metadata,
            "evidence_bundle": evidence_bundle,
            "reasoning_cycle": reasoning_cycle or {},
        }

    @app.post(RuntimePath.RUN_DEBATE)
    async def run_debate() -> Dict[str, Any]:
        raise HTTPException(
            status_code=501,
            detail="Portable bundle runtime currently supports add/chat/search/semantic compatibility only.",
        )

    return app
