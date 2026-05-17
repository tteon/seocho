from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from seocho.runtime_contract import DATABASE_NAME_PATTERN, WORKSPACE_ID_PATTERN

from .query import EntityOverride


class PlatformChatRequest(BaseModel):
    """Interactive chat request from the frontend platform."""

    session_id: str = Field(default_factory=lambda: uuid4().hex, description="Session ID for conversation continuity; auto-generated if omitted.")
    message: str = Field(..., min_length=1, max_length=2000, description="User message text.")
    mode: Literal["router", "debate", "semantic"] = Field(default="semantic", description="Execution mode: 'router' (single agent), 'debate' (parallel), or 'semantic' (entity-resolution).")
    user_id: str = Field(default="user_default", description="Caller identity for tracing.")
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN, description="Workspace scope.")
    graph_ids: Optional[List[str]] = Field(default=None, description="Graph IDs for debate mode routing.")
    databases: Optional[List[str]] = Field(default=None, description="Databases for semantic mode entity resolution.")
    entity_overrides: Optional[List[EntityOverride]] = Field(default=None, description="UI-assisted entity disambiguation overrides.")
    reasoning_cycle: Optional[Dict[str, Any]] = Field(default=None, description="Optional anomaly-driven inquiry contract forwarded to semantic/debate execution.")


class PlatformTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlatformChatResponse(BaseModel):
    """Response from the interactive platform chat endpoint."""

    session_id: str = Field(description="Session ID for this conversation.")
    mode: str = Field(description="Execution mode that was used.")
    assistant_message: str = Field(description="Assistant response text.")
    trace_steps: List[Dict[str, Any]] = Field(description="Agent execution trace for frontend DAG rendering.")
    ui_payload: Dict[str, Any] = Field(description="Frontend-specific rendering hints (disambiguation, graph previews).")
    runtime_payload: Dict[str, Any] = Field(description="Backend runtime metadata (semantic context, support assessment).")
    ontology_context_mismatch: Dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime graph ontology-context parity metadata surfaced for direct SDK/UI access.",
    )
    history: List[PlatformTurn] = Field(description="Full session conversation history.")


class PlatformSessionResponse(BaseModel):
    session_id: str
    history: List[PlatformTurn]


class RawIngestRecord(BaseModel):
    """Single raw record to ingest into the knowledge graph."""

    id: Optional[str] = Field(default=None, description="Unique record identifier; auto-generated as 'raw_{idx}' if omitted.")
    content: str = Field(..., min_length=1, max_length=2000000, description="Raw text content to extract entities from.")
    category: str = Field(default="general", max_length=100, description="Data category for prompt routing (e.g. 'finance', 'medical').")
    source_type: Literal["text", "pdf", "csv"] = Field(default="text", description="Content format.")
    content_encoding: Literal["plain", "base64"] = Field(default="plain", description="Encoding of the content field.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata attached to the record.")


class PlatformRawIngestRequest(BaseModel):
    """Batch raw-data ingestion request."""

    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN, description="Workspace scope.")
    target_database: str = Field(default="kgnormal", pattern=DATABASE_NAME_PATTERN, description="DozerDB database to load extracted graph data into.")
    records: List[RawIngestRecord] = Field(..., min_length=1, max_length=100, description="Raw records to ingest (1-100 per batch).")
    enable_rule_constraints: bool = Field(default=True, description="Infer and apply SHACL-like rules to extracted nodes.")
    create_database_if_missing: bool = Field(default=True, description="Provision the target database if it does not exist.")
    semantic_artifact_policy: Literal["auto", "draft_only", "approved_only"] = Field(default="auto", description="Artifact promotion policy: 'auto' applies drafts, 'approved_only' requires approval.")
    approved_artifacts: Optional[Dict[str, Any]] = Field(default=None, description="Pre-approved ontology/SHACL artifacts to use instead of draft inference.")
    approved_artifact_id: Optional[str] = Field(default=None, description="ID of an approved artifact to resolve from the artifact store.")


class RawIngestError(BaseModel):
    record_id: str
    error_type: str
    message: str


class RawIngestWarning(BaseModel):
    record_id: str
    warning_type: str
    message: str


class PlatformRawIngestResponse(BaseModel):
    """Response summarizing a batch ingestion result."""

    ok: bool = Field(
        default=False,
        description="True when at least one record was processed and the batch status is not failed.",
    )
    workspace_id: str = Field(description="Workspace that owns the ingested data.")
    target_database: str = Field(description="Database the data was loaded into.")
    records_received: int = Field(description="Total records submitted in the request.")
    records_processed: int = Field(description="Records successfully extracted and loaded.")
    records_failed: int = Field(description="Records that failed during processing.")
    total_nodes: int = Field(description="Total graph nodes created or merged.")
    total_relationships: int = Field(description="Total graph relationships created or merged.")
    fallback_records: int = Field(default=0, description="Records that used fallback (non-LLM) extraction.")
    rule_profile: Optional[Dict[str, Any]] = Field(default=None, description="Inferred rule profile applied to the batch.")
    semantic_artifacts: Optional[Dict[str, Any]] = Field(default=None, description="Ontology, SHACL, and vocabulary artifacts from this batch.")
    status: str = Field(description="Batch outcome: 'success', 'partial_success', 'success_with_fallback', or 'failed'.")
    domain_error: str = Field(default="", description="Human-readable domain failure summary when ok is false.")
    warnings: List[RawIngestWarning] = Field(default_factory=list, description="Non-fatal warnings encountered during processing.")
    errors: List[RawIngestError] = Field(default_factory=list, description="Fatal errors per failed record.")
