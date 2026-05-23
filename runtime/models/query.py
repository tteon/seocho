from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from seocho.runtime_contract import (
    DEFAULT_QUERY_MODE,
    INDEX_NAME_PATTERN,
    WORKSPACE_ID_PATTERN,
)


class QueryRequest(BaseModel):
    """Base request for agent query endpoints."""

    query: str = Field(..., max_length=2000, description="Natural-language question to process.")
    user_id: str = Field(default="user_default", description="Caller identity for tracing and access control.")
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN, description="Workspace scope for multi-tenant isolation.")
    graph_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of graph IDs to target for debate/runtime routing.",
    )
    reasoning_cycle: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional anomaly-driven inquiry contract surfaced when semantic support is insufficient.",
    )


class EntityOverride(BaseModel):
    """UI-assisted entity disambiguation: pin a question entity to a specific graph node."""

    question_entity: str = Field(..., min_length=1, max_length=256, description="Entity mention as it appears in the user question.")
    database: str = Field(..., min_length=1, max_length=64, description="Target database containing the resolved node.")
    node_id: str | int = Field(description="elementId or name of the resolved graph node.")
    display_name: Optional[str] = Field(default=None, description="Human-readable label shown in the UI.")
    labels: List[str] = Field(default_factory=list, description="Neo4j labels of the resolved node.")


class SemanticQueryRequest(QueryRequest):
    databases: Optional[List[str]] = Field(
        default=None,
        description="Optional list of target databases for semantic entity resolution.",
    )
    entity_overrides: Optional[List[EntityOverride]] = Field(
        default=None,
        description="Optional fixed entity mapping from UI-assisted disambiguation.",
    )
    reasoning_mode: bool = Field(
        default=False,
        description="Enable bounded semantic repair loop when constrained retrieval is insufficient.",
    )
    repair_budget: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Maximum number of additional constrained retrieval repair attempts.",
    )
    query_mode: Literal["semantic", "graph_cot"] = Field(
        default=DEFAULT_QUERY_MODE,
        description="Semantic query execution mode: 'semantic' (default) or 'graph_cot' (Graph-CoT-oriented traversal mode).",
    )


class AgentResponse(BaseModel):
    """Standard response envelope for single-agent execution."""

    response: str = Field(description="Final synthesized answer text.")
    trace_steps: List[Dict[str, Any]] = Field(description="Ordered list of agent execution trace steps for DAG rendering.")
    ontology_context_mismatch: Dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime graph ontology-context parity metadata for the databases touched by this run.",
    )


class SemanticAgentResponse(AgentResponse):
    """Extended response for semantic entity-resolution query flow."""

    route: str = Field(description="Selected query route: 'lpg', 'rdf', or 'hybrid'.")
    query_mode: Literal["semantic", "graph_cot"] = Field(
        default=DEFAULT_QUERY_MODE,
        description="Semantic execution sub-mode that handled the request.",
    )
    semantic_context: Dict[str, Any] = Field(description="Resolved entities and disambiguation metadata.")
    lpg_result: Optional[Dict[str, Any]] = Field(default=None, description="Raw LPG agent query results.")
    rdf_result: Optional[Dict[str, Any]] = Field(default=None, description="Raw RDF agent query results.")
    support_assessment: Dict[str, Any] = Field(default_factory=dict, description="Intent-support coverage analysis.")
    strategy_decision: Dict[str, Any] = Field(default_factory=dict, description="Execution strategy reasoning trace.")
    run_metadata: Dict[str, Any] = Field(default_factory=dict, description="Semantic run audit metadata (run_id, timestamps).")
    evidence_bundle: Dict[str, Any] = Field(default_factory=dict, description="Structured evidence bundle with slot fills and required relations.")
    semantic_package: Dict[str, Any] = Field(default_factory=dict, description="Compiled semantic package selection governing this semantic query run.")
    stage_metrics: Dict[str, Any] = Field(default_factory=dict, description="Stage-level timing metrics for resolver, routing, specialists, and synthesis.")
    policy_metrics: Dict[str, Any] = Field(default_factory=dict, description="Decision-policy metrics covering routing, repair/tool use, and next-mode hints.")
    reasoning_cycle: Dict[str, Any] = Field(default_factory=dict, description="Compact inquiry-cycle anomaly report for unsupported semantic outcomes.")
    ontology_context_mismatch: Dict[str, Any] = Field(default_factory=dict, description="Runtime graph ontology-context parity metadata.")
    graph_cot: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured Graph-CoT lane artifacts: supervisor directive, evidence packet, answer draft, guardrail verdict, and final answer.",
    )


class SemanticRunRecordResponse(BaseModel):
    """Audit record for a single semantic query execution."""

    run_id: str = Field(description="Unique identifier for this semantic run.")
    workspace_id: str = Field(description="Workspace that owns this run.")
    timestamp: str = Field(description="ISO-8601 timestamp of run execution.")
    route: str = Field(description="Query route used: 'lpg', 'rdf', or 'hybrid'.")
    intent_id: str = Field(description="Inferred question intent identifier.")
    query_preview: str = Field(description="Truncated user question for display.")
    semantic_package_id: str = Field(default="", description="Deterministic semantic package selection identifier.")
    semantic_package_hash: str = Field(default="", description="Deterministic semantic package selection hash.")
    semantic_package: Dict[str, Any] = Field(default_factory=dict, description="Full semantic package selection payload when available.")
    stage_metrics: Dict[str, Any] = Field(default_factory=dict, description="Stage-level timing metrics recorded for the semantic run.")
    policy_metrics: Dict[str, Any] = Field(default_factory=dict, description="Decision-policy metrics recorded for the semantic run.")
    support_status: str = Field(default="", description="Intent-support verdict: 'supported', 'partial', or 'unsupported'.")
    support_reason: str = Field(default="", description="Human-readable explanation of support status.")
    support_coverage: float = Field(default=0.0, description="Fraction of required intent slots filled (0.0-1.0).")
    support_assessment: Dict[str, Any] = Field(default_factory=dict, description="Full intent-support assessment payload.")
    strategy_decision: Dict[str, Any] = Field(default_factory=dict, description="Strategy reasoning trace.")
    reasoning: Dict[str, Any] = Field(default_factory=dict, description="Query reasoning and repair trace.")
    evidence_summary: Dict[str, Any] = Field(default_factory=dict, description="Summarized evidence bundle.")
    lpg_record_count: int = Field(default=0, description="Number of records returned by LPG agent.")
    rdf_record_count: int = Field(default=0, description="Number of records returned by RDF agent.")
    response_preview: str = Field(default="", description="Truncated final answer for display.")


class SemanticRunRecordListResponse(BaseModel):
    """Paginated list of semantic run audit records."""

    runs: List[SemanticRunRecordResponse] = Field(default_factory=list, description="List of semantic run records.")


class DebateResponse(BaseModel):
    """Response from parallel multi-agent debate execution."""

    response: str = Field(description="Supervisor-synthesized final answer.")
    trace_steps: List[Dict[str, Any]] = Field(description="Per-agent trace steps for DAG rendering.")
    debate_results: List[Dict[str, Any]] = Field(description="Individual agent answers before synthesis.")
    agent_statuses: List[Dict[str, str]] = Field(default_factory=list, description="Per-agent readiness status (ready/degraded/blocked).")
    debate_state: Literal["ready", "degraded", "blocked"] = Field(default="ready", description="Overall debate readiness state.")
    degraded: bool = Field(default=False, description="True if one or more agents were unavailable.")
    reasoning_cycle: Dict[str, Any] = Field(default_factory=dict, description="Compact inquiry-cycle anomaly report surfaced from semantic preflight or fallback.")
    ontology_context_mismatch: Dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime graph ontology-context parity metadata for debated graph databases.",
    )


class FulltextIndexEnsureRequest(BaseModel):
    """Request to ensure fulltext indexes exist on target databases."""

    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN, description="Workspace scope.")
    databases: Optional[List[str]] = Field(default=None, description="Databases to index; defaults to all registered databases.")
    index_name: str = Field(default="entity_fulltext", pattern=INDEX_NAME_PATTERN, description="Name of the fulltext index to create or verify.")
    labels: List[str] = Field(
        default_factory=lambda: ["Entity", "Company", "Person", "Organization", "Concept", "Document", "Resource"],
        description="Node labels to include in the fulltext index.",
    )
    properties: List[str] = Field(
        default_factory=lambda: ["name", "title", "id", "uri", "alias", "code", "symbol", "content_preview", "content", "memory_id"],
        description="Node properties to include in the fulltext index.",
    )
    create_if_missing: bool = Field(default=True, description="Create the index if it does not exist.")


class FulltextIndexEnsureResult(BaseModel):
    database: str
    index_name: str
    exists: bool
    created: bool
    state: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    properties: List[str] = Field(default_factory=list)
    message: str = ""


class FulltextIndexEnsureResponse(BaseModel):
    results: List[FulltextIndexEnsureResult]
