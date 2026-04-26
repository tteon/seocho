import asyncio
import logging
import functools
import json
import os
from typing import List, Dict, Any, Optional, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Depends, Query

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# OpenAI Agent SDK Imports (Local Shim)
from agents import Agent, function_tool, RunContextWrapper

from config import db_registry, graph_registry, validate_config
from runtime.agent_readiness import summarize_readiness
from agents_runtime import get_agents_runtime
from shared_memory import SharedMemory
from exceptions import (
    SeochoError,
    ConfigurationError,
    InfrastructureError,
    DataValidationError,
    PipelineError,
    InvalidDatabaseNameError,
)
from runtime.middleware import RequestIDMiddleware
from tracing import configure_opik, track, update_current_span, update_current_trace
from runtime.policy import require_runtime_permission
from seocho.runtime_contract import (
    DATABASE_NAME_PATTERN,
    INDEX_NAME_PATTERN,
    RuntimePath,
    WORKSPACE_ID_PATTERN,
)
from rule_api import (
    RuleInferRequest,
    RuleInferResponse,
    RuleAssessRequest,
    RuleAssessResponse,
    RuleProfileCreateRequest,
    RuleProfileCreateResponse,
    RuleProfileGetResponse,
    RuleProfileListResponse,
    RuleExportCypherRequest,
    RuleExportCypherResponse,
    RuleExportShaclRequest,
    RuleExportShaclResponse,
    RuleValidateRequest,
    RuleValidateResponse,
    create_rule_profile,
    read_rule_profile,
    read_rule_profiles,
    assess_rule_profile,
    export_rule_profile_to_cypher,
    export_rule_profile_to_shacl,
    infer_rule_profile,
    validate_rule_profile,
)
from runtime.public_memory_api import build_public_memory_router
from debate import DebateOrchestrator
from runtime.server_runtime import (
    ServerContext,
    batch_status_file_path,
    get_agent_factory_service,
    get_backend_specialist_agent_service,
    get_db_manager_service,
    get_frontend_specialist_agent_service,
    get_fulltext_index_manager_service,
    get_graph_query_proxy_service,
    get_memory_service,
    get_platform_session_store_service,
    get_runtime_raw_ingestor,
    get_schema_impl,
    get_semantic_agent_flow_service,
    get_vector_store_service,
    get_graphs_impl,
    get_databases_impl,
    invalidate_semantic_vocabulary_cache,
    utc_now_iso,
)
from semantic_artifact_api import (
    SemanticArtifactApproveRequest,
    SemanticArtifactDeprecateRequest,
    SemanticArtifactListResponse,
    SemanticArtifactDraftCreateRequest,
    SemanticArtifactResponse,
    approve_semantic_artifact_draft,
    create_semantic_artifact_draft,
    deprecate_semantic_artifact_approved,
    read_semantic_artifact,
    read_semantic_artifacts,
    resolve_approved_artifact_payload,
)
from semantic_run_store import get_semantic_run, list_semantic_runs
from seocho.query.query_proxy import QueryRequest as GraphQueryRequest

logger = logging.getLogger(__name__)


class _LazyServiceProxy:
    """Backward-compatible lazy proxy for shared runtime services."""

    def __init__(self, resolver):
        self._resolver = resolver

    def __getattr__(self, name):
        return getattr(self._resolver(), name)

app = FastAPI(title="Agent Server")

db_manager = _LazyServiceProxy(get_db_manager_service)
agent_factory = _LazyServiceProxy(get_agent_factory_service)
query_proxy = _LazyServiceProxy(get_graph_query_proxy_service)
faiss_manager = _LazyServiceProxy(get_vector_store_service)
semantic_agent_flow = _LazyServiceProxy(get_semantic_agent_flow_service)
fulltext_index_manager = _LazyServiceProxy(get_fulltext_index_manager_service)
platform_session_store = _LazyServiceProxy(get_platform_session_store_service)
backend_specialist_agent = _LazyServiceProxy(get_backend_specialist_agent_service)
frontend_specialist_agent = _LazyServiceProxy(get_frontend_specialist_agent_service)
memory_service = _LazyServiceProxy(get_memory_service)

# Request ID middleware
app.add_middleware(RequestIDMiddleware)

# CORS — configurable via SEOCHO_CORS_ORIGINS env var (comma-separated)
_DEFAULT_CORS = "http://localhost:8501,http://localhost:3000"
_cors_origins = [
    o.strip() for o in os.getenv("SEOCHO_CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Exception handlers — structured error responses
# ------------------------------------------------------------------

class ErrorDetail(BaseModel):
    error_code: str
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


_EXCEPTION_STATUS_MAP = {
    ConfigurationError: 400,
    DataValidationError: 422,
    PipelineError: 422,
    InfrastructureError: 502,
}


@app.exception_handler(SeochoError)
async def seocho_error_handler(request: Request, exc: SeochoError):
    status_code = 500
    for exc_type, code in _EXCEPTION_STATUS_MAP.items():
        if isinstance(exc, exc_type):
            status_code = code
            break

    from runtime.middleware import get_request_id
    request_id = get_request_id()

    body = ErrorResponse(
        error=ErrorDetail(
            error_code=type(exc).__name__,
            message=str(exc),
            request_id=request_id,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.on_event("startup")
async def _startup():
    validate_config()
    configure_opik()

# ------------------------------------------------------------------
# 2. Tools & Agents Definition
# ------------------------------------------------------------------

# --- Tools ---

def get_databases_impl() -> str:
    """Returns a list of available graph databases."""
    dbs = db_registry.list_databases()
    graphs = graph_registry.list_graph_ids()
    return f"Available Graphs: {graphs}; Databases: {dbs}"


def get_graphs_impl() -> str:
    """Returns registered graph targets with ontology/vocabulary metadata."""
    graphs = [target.to_public_dict() for target in graph_registry.list_graphs()]
    return json.dumps(graphs)

@functools.lru_cache(maxsize=8)
def get_schema_impl(database: str = "neo4j") -> str:
    """Returns the schema for the specified database (cached)."""
    schema_map = {
        "kgnormal": "outputs/schema_baseline.yaml",
        "kgfibo": "outputs/schema_fibo.yaml",
        "neo4j": "outputs/schema.yaml"
    }

    path = schema_map.get(database, "outputs/schema.yaml")

    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()

    return f"Schema file for '{database}' not found. Please assume standard labels for this ontology."

@function_tool
def get_databases_tool() -> str:
    """
    Returns a list of available graph databases (ontologies).
    Use this to decide which database to query.
    """
    return get_databases_impl()


@function_tool
def get_graphs_tool() -> str:
    """
    Returns graph target descriptors. Use this before choosing a graph-specific agent path.
    """
    return get_graphs_impl()

@function_tool
def execute_cypher_tool(context: RunContextWrapper, query: str, database: str = "neo4j") -> str:
    """
    Executes a Cypher query against the specified database.
    database: The name of the database to query (e.g., 'kgnormal', 'kgfibo'). Default is 'neo4j'.
    """
    server_context = getattr(context, "context", None)
    if isinstance(server_context, ServerContext):
        if not server_context.can_query_database(database):
            return f"Database '{database}' is outside the allowed graph scope."
        if not server_context.consume_tool_budget():
            return "Tool budget exhausted for this request."
    workspace_id = (
        server_context.workspace_id
        if isinstance(server_context, ServerContext)
        else "default"
    )
    target = graph_registry.find_by_database(database)
    ontology_profile = str(getattr(target, "vocabulary_profile", "") or "default")
    try:
        rows = query_proxy.query(
            GraphQueryRequest(
                cypher=query,
                database=database,
                workspace_id=workspace_id,
                ontology_profile=ontology_profile,
            )
        )
    except Exception as exc:
        logger.error("Error executing Cypher in '%s': %s", database, exc)
        return f"Error executing Cypher in '{database}': {exc}"
    return json.dumps(rows)

@function_tool
def search_vector_tool(query: str) -> str:
    """Searches the FAISS vector index for semantically similar documents."""
    results = faiss_manager.search(query)
    if not results:
        return "No results found in vector index."
    return json.dumps(results)

@function_tool
def web_search_tool(query: str) -> str:
    return f"[Google] Latest news for: {query}"

@function_tool
def get_schema_tool(database: str = "neo4j") -> str:
    """
    Returns the current graph database schema (node labels, relationships, properties) to help generate correct Cypher queries.
    """
    return get_schema_impl(database)

# --- Agents ---

# 1. Supervisor (The Collector)
agent_supervisor = Agent(
    name="Supervisor",
    instructions="You are the Supervisor. Your goal is to collect the results from the active agents, summarize them, and present the final answer to the user. Do not call any tools. Just synthesize and complete."
)

# 2. Graph DBA (The Executor)
# Forward declaration: GraphAgent defined first without handoffs, then DBA, then update GraphAgent.

agent_graph = Agent(
    name="GraphAgent",
    instructions="""
    You are the Graph Analyst.
    1. Receive task from Router.
    2. Analyze the user's intent and formulate a plan to fetch data.
    3. Handoff to 'GraphDBA' to inspect schema or execute queries.
    4. When 'GraphDBA' returns results, verify them.
       - If useful, summarize and handoff to 'Supervisor'.
       - If not useful or error, refine plan and handoff to 'GraphDBA' again.
    """,
)

agent_graph_dba = Agent(
    name="GraphDBA",
    instructions="""
    # Role
    You are a **Neo4j Cypher Query Specialist**. Your goal is to translate natural language questions into executable Cypher queries for a specific Neo4j database instance.

    # Capabilities & Workflow
    1. **Schema Check First**: NEVER guess the schema. Always use the provided schema information or retrieve it using `get_schema_tool(database=...)`.
    2. **Graph Selection**: You have access to multiple graph targets. Use `get_graphs_tool()` or `get_databases_tool()` to check availability.
       Check which graph/database is requested by the context.
    3. **Execution & Retry**: Use `execute_cypher_tool(query, database=...)`.
       - If the tool returns a syntax error, analyze the error, FIX the query, and RETRY immediately.
    4. **Ontology Compliance**: When querying `kgfibo`, ensure you ONLY use node labels and relationship types defined in the FIBO ontology schema.

    # Constraints
    - Use efficient Cypher patterns (e.g., limit paths, use indexed lookups).
    - If the user asks for a multi-hop path, use variable length relationships e.g., `-[*1..3]-`.

    # Output Format
    After successful execution, handoff back to 'GraphAgent' with a summary and the raw data.
    """,
    tools=[get_graphs_tool, get_databases_tool, get_schema_tool, execute_cypher_tool],
    handoffs=[agent_graph]
)

# Update GraphAgent Handoffs
agent_graph.handoffs = [agent_graph_dba, agent_supervisor]

# 3. Other Specialists
agent_vector = Agent(
    name="VectorAgent",
    instructions="Vector expert. Use search_vector_tool. Then handoff to Supervisor.",
    tools=[search_vector_tool],
    handoffs=[agent_supervisor]
)

agent_web = Agent(
    name="WebAgent",
    instructions="Web expert. Use web_search_tool. Then handoff to Supervisor.",
    tools=[web_search_tool],
    handoffs=[agent_supervisor]
)

agent_table = Agent(
    name="TableAgent",
    instructions="Structured data expert. Then handoff to Supervisor.",
    tools=[],
    handoffs=[agent_supervisor]
)

# 4. Router (The Entry Point)
agent_router = Agent(
    name="Router",
    instructions="""
# Role
You are the Router Agent. Route the user's query to the most appropriate sub-agent (Graph, Vector, Web, Table).

# Output Format
JSON object with `target_agent` and `reasoning`.
""",
    handoffs=[agent_table, agent_vector, agent_graph, agent_web],
)

# ------------------------------------------------------------------
# 3. API Models
# ------------------------------------------------------------------

class QueryRequest(BaseModel):
    """Base request for agent query endpoints."""

    query: str = Field(..., max_length=2000, description="Natural-language question to process.")
    user_id: str = Field(default="user_default", description="Caller identity for tracing and access control.")
    workspace_id: str = Field(default="default", pattern=WORKSPACE_ID_PATTERN, description="Workspace scope for multi-tenant isolation.")
    graph_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of graph IDs to target for debate/runtime routing.",
    )
    max_steps: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description="Maximum agent turns allowed for runtime react/debate execution.",
    )
    tool_budget: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="Maximum tool calls allowed for runtime react/debate execution.",
    )
    prefer_agentic_tools: bool = Field(
        default=False,
        description="Bypass graph-scoped semantic shortcut and force the agentic router path.",
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
    semantic_context: Dict[str, Any] = Field(description="Resolved entities and disambiguation metadata.")
    lpg_result: Optional[Dict[str, Any]] = Field(default=None, description="Raw LPG agent query results.")
    rdf_result: Optional[Dict[str, Any]] = Field(default=None, description="Raw RDF agent query results.")
    support_assessment: Dict[str, Any] = Field(default_factory=dict, description="Intent-support coverage analysis.")
    strategy_decision: Dict[str, Any] = Field(default_factory=dict, description="Execution strategy reasoning trace.")
    run_metadata: Dict[str, Any] = Field(default_factory=dict, description="Semantic run audit metadata (run_id, timestamps).")
    evidence_bundle: Dict[str, Any] = Field(default_factory=dict, description="Structured evidence bundle with slot fills and required relations.")
    reasoning_cycle: Dict[str, Any] = Field(default_factory=dict, description="Compact inquiry-cycle anomaly report for unsupported semantic outcomes.")
    latency_breakdown_ms: Dict[str, float] = Field(default_factory=dict, description="Stage-level retrieval/generation latency breakdown in milliseconds.")
    agent_pattern: Dict[str, Any] = Field(default_factory=dict, description="Agent design pattern receipt for the selected execution path.")
    answer_envelope: Dict[str, Any] = Field(default_factory=dict, description="Canonical answer/evidence/latency/cost envelope shared with local SDK query metadata.")
    ontology_context_mismatch: Dict[str, Any] = Field(default_factory=dict, description="Runtime graph ontology-context parity metadata.")


class SemanticRunRecordResponse(BaseModel):
    """Audit record for a single semantic query execution."""

    run_id: str = Field(description="Unique identifier for this semantic run.")
    workspace_id: str = Field(description="Workspace that owns this run.")
    timestamp: str = Field(description="ISO-8601 timestamp of run execution.")
    route: str = Field(description="Query route used: 'lpg', 'rdf', or 'hybrid'.")
    intent_id: str = Field(description="Inferred question intent identifier.")
    query_preview: str = Field(description="Truncated user question for display.")
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


class HealthComponent(BaseModel):
    name: str
    status: Literal["ready", "degraded", "blocked"]
    detail: str = ""


class HealthResponse(BaseModel):
    scope: Literal["runtime", "batch"]
    status: Literal["ready", "degraded", "blocked"]
    generated_at: str
    components: List[HealthComponent]


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


def ensure_fulltext_indexes_impl(request: FulltextIndexEnsureRequest) -> FulltextIndexEnsureResponse:
    target_dbs = request.databases or db_registry.list_databases()
    resolved_dbs: List[str] = []
    for db_name in target_dbs:
        if not db_registry.is_valid(db_name):
            raise ValueError(
                f"Invalid database '{db_name}'. Valid options: {db_registry.list_databases()}"
            )
        resolved_dbs.append(db_name)

    if not resolved_dbs:
        raise ValueError("No target databases provided.")

    results: List[FulltextIndexEnsureResult] = []
    for db_name in resolved_dbs:
        result = fulltext_index_manager.ensure_index(
            database=db_name,
            index_name=request.index_name,
            labels=request.labels,
            properties=request.properties,
            create_if_missing=request.create_if_missing,
        )
        results.append(FulltextIndexEnsureResult(**result))
    return FulltextIndexEnsureResponse(results=results)


def _raw_ingest_response_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach domain success metadata without conflating it with HTTP status."""
    payload = dict(result)
    status = str(payload.get("status", ""))
    records_processed = int(payload.get("records_processed", 0) or 0)
    records_failed = int(payload.get("records_failed", 0) or 0)
    ok = status in {"success", "success_with_fallback", "partial_success"} and records_processed > 0
    payload["ok"] = ok
    if ok:
        payload.setdefault("domain_error", "")
    else:
        payload["domain_error"] = (
            f"status={status or 'unknown'}, "
            f"records_processed={records_processed}, records_failed={records_failed}"
        )
    return payload


def _resolve_graph_scope(graph_ids: Optional[List[str]]) -> tuple[List[str], List[str]]:
    """Resolve public graph IDs into database names for agent/tool middleware."""

    requested_graph_ids = graph_ids or graph_registry.list_graph_ids()
    valid_graph_ids: List[str] = []
    databases: List[str] = []
    for graph_id in requested_graph_ids:
        if graph_registry.is_valid_graph(graph_id):
            target = graph_registry.get_graph(graph_id)
        elif db_registry.is_valid(graph_id):
            # Raw ingest can provision a database before any public graph alias exists.
            # Accept the database name here and promote it to a default graph target so
            # router/debate paths can operate on the same runtime-created graph.
            target = graph_registry.find_by_database(graph_id) or graph_registry.ensure_default_graph(graph_id)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid graph '{graph_id}'. Valid options: {graph_registry.list_graph_ids()}",
            )
        if target is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid graph '{graph_id}'. Valid options: {graph_registry.list_graph_ids()}",
            )
        database = str(getattr(target, "database", "") or graph_id).strip()
        resolved_graph_id = str(getattr(target, "graph_id", "") or graph_id).strip()
        valid_graph_ids.append(resolved_graph_id)
        if database and database not in databases:
            databases.append(database)
    return valid_graph_ids, databases


def _ontology_context_middleware_status(
    *,
    workspace_id: str,
    databases: List[str],
) -> Dict[str, Any]:
    """Attach non-blocking ontology/database parity metadata to agent responses."""

    return memory_service.ontology_context_mismatch(
        workspace_id=workspace_id,
        databases=databases or None,
    )


def _coerce_usage_payload(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict"):
        value = value.dict()
    if not isinstance(value, dict):
        return {}

    input_tokens = value.get("input_tokens", value.get("prompt_tokens"))
    output_tokens = value.get("output_tokens", value.get("completion_tokens"))
    total_tokens = value.get("total_tokens")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return {}

    payload: Dict[str, Any] = {"source": "provider", "exact": True}
    if input_tokens is not None:
        payload["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        payload["output_tokens"] = int(output_tokens)
    if total_tokens is not None:
        payload["total_tokens"] = int(total_tokens)
    elif input_tokens is not None and output_tokens is not None:
        payload["total_tokens"] = int(input_tokens) + int(output_tokens)
    return payload


def _extract_provider_usage(result: Any) -> Dict[str, Any]:
    for attr in ("usage", "usage_data"):
        payload = _coerce_usage_payload(getattr(result, attr, None))
        if payload:
            return payload

    for response in getattr(result, "raw_responses", []) or []:
        payload = _coerce_usage_payload(getattr(response, "usage", None))
        if payload:
            return payload
    return {}


def _estimate_token_usage(
    *,
    query: str,
    response: str,
    trace_steps: List[Dict[str, Any]],
    result: Any = None,
) -> Dict[str, Any]:
    provider_usage = _extract_provider_usage(result)
    if provider_usage:
        return provider_usage

    trace_text = " ".join(str(step.get("content", "")) for step in trace_steps)
    input_chars = len(query) + len(trace_text)
    output_chars = len(response)
    input_tokens = max(1, round(input_chars / 4)) if input_chars else 0
    output_tokens = max(1, round(output_chars / 4)) if output_chars else 0
    return {
        "source": "estimated_char_count",
        "exact": False,
        "input_tokens_est": input_tokens,
        "output_tokens_est": output_tokens,
        "total_tokens_est": input_tokens + output_tokens,
        "note": "Provider token usage was not exposed by this runtime result.",
    }


def _append_usage_trace_step(
    trace_steps: List[Dict[str, Any]],
    *,
    query: str,
    response: str,
    mode: str,
    result: Any = None,
) -> List[Dict[str, Any]]:
    steps = list(trace_steps)
    steps.append(
        {
            "id": str(len(steps)),
            "type": "METRIC",
            "agent": "RuntimeTrace",
            "content": "Token usage metadata captured for cost and design analysis.",
            "metadata": {
                "mode": mode,
                "usage": _estimate_token_usage(
                    query=query,
                    response=response,
                    trace_steps=steps,
                    result=result,
                ),
            },
        }
    )
    return steps


app.include_router(
    build_public_memory_router(
        memory_service=memory_service,
        approved_artifact_resolver=lambda **kwargs: resolve_approved_artifact_payload(**kwargs),
    )
)

# ------------------------------------------------------------------
# 4. Endpoints
# ------------------------------------------------------------------


async def _platform_run_router(payload: Dict[str, Any]) -> AgentResponse:
    req = QueryRequest(
        query=payload["message"],
        user_id=payload["user_id"],
        workspace_id=payload["workspace_id"],
        graph_ids=payload.get("graph_ids"),
    )
    return await run_agent(req)


async def _platform_run_debate(payload: Dict[str, Any]) -> DebateResponse:
    req = QueryRequest(
        query=payload["message"],
        user_id=payload["user_id"],
        workspace_id=payload["workspace_id"],
        graph_ids=payload.get("graph_ids"),
        reasoning_cycle=payload.get("reasoning_cycle"),
    )
    return await run_debate(req)


async def _platform_run_semantic(payload: Dict[str, Any]) -> SemanticAgentResponse:
    databases = payload.get("databases")
    if not databases and payload.get("graph_ids"):
        _, databases = _resolve_graph_scope(payload.get("graph_ids"))
    req = SemanticQueryRequest(
        query=payload["message"],
        user_id=payload["user_id"],
        workspace_id=payload["workspace_id"],
        databases=databases,
        entity_overrides=payload.get("entity_overrides"),
        reasoning_cycle=payload.get("reasoning_cycle"),
    )
    return await run_agent_semantic(req)


@app.post(RuntimePath.PLATFORM_CHAT_SEND, response_model=PlatformChatResponse)
@track("agent_server.platform_chat_send")
async def platform_chat_send(request: PlatformChatRequest):
    """Custom interactive chat API for the frontend platform."""
    try:
        require_runtime_permission(role="user", action="run_platform", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    user_payload = {
        "message": request.message,
        "mode": request.mode,
        "user_id": request.user_id,
        "workspace_id": request.workspace_id,
        "graph_ids": request.graph_ids,
        "databases": request.databases,
        "entity_overrides": request.entity_overrides,
        "reasoning_cycle": request.reasoning_cycle,
    }

    platform_session_store.append(
        session_id=request.session_id,
        role="user",
        content=request.message,
        metadata={"mode": request.mode, "workspace_id": request.workspace_id},
    )

    try:
        runtime_payload = await backend_specialist_agent.execute(
            mode=request.mode,
            router_runner=_platform_run_router,
            debate_runner=_platform_run_debate,
            semantic_runner=_platform_run_semantic,
            request_payload=user_payload,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Platform execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Platform execution failed.")

    assistant_message = str(runtime_payload.get("response", ""))
    runtime_control = runtime_payload.get("runtime_control", {})
    executed_mode = str(runtime_control.get("executed_mode", request.mode))
    ui_payload = frontend_specialist_agent.build_ui_payload(mode=request.mode, runtime_payload=runtime_payload)

    platform_session_store.append(
        session_id=request.session_id,
        role="assistant",
        content=assistant_message,
        metadata={
            "mode": executed_mode,
            "requested_mode": request.mode,
            "route": runtime_payload.get("route"),
        },
    )
    history = [PlatformTurn(**row) for row in platform_session_store.get(request.session_id)]

    return PlatformChatResponse(
        session_id=request.session_id,
        mode=executed_mode,
        assistant_message=assistant_message,
        trace_steps=runtime_payload.get("trace_steps", []),
        ui_payload=ui_payload,
        runtime_payload=runtime_payload,
        ontology_context_mismatch=runtime_payload.get("ontology_context_mismatch", {}),
        history=history,
    )


@app.get(RuntimePath.PLATFORM_CHAT_SESSION, response_model=PlatformSessionResponse)
@track("agent_server.platform_chat_session_get")
async def platform_chat_session_get(session_id: str):
    history = [PlatformTurn(**row) for row in platform_session_store.get(session_id)]
    return PlatformSessionResponse(session_id=session_id, history=history)


@app.delete(RuntimePath.PLATFORM_CHAT_SESSION, response_model=PlatformSessionResponse)
@track("agent_server.platform_chat_session_reset")
async def platform_chat_session_reset(session_id: str):
    platform_session_store.clear(session_id)
    return PlatformSessionResponse(session_id=session_id, history=[])


@app.post(RuntimePath.PLATFORM_INGEST_RAW, response_model=PlatformRawIngestResponse)
@track("agent_server.platform_ingest_raw")
async def platform_ingest_raw(request: PlatformRawIngestRequest):
    """Ingest user-provided raw text records into a target graph database."""
    try:
        require_runtime_permission(role="user", action="ingest_raw", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        resolved_approved_artifacts = request.approved_artifacts
        if request.approved_artifact_id and not resolved_approved_artifacts:
            resolved_approved_artifacts = resolve_approved_artifact_payload(
                workspace_id=request.workspace_id,
                artifact_id=request.approved_artifact_id,
            )

        ingestor = get_runtime_raw_ingestor()
        result = await asyncio.to_thread(
            ingestor.ingest_records,
            records=[r.model_dump() for r in request.records],
            target_database=request.target_database,
            workspace_id=request.workspace_id,
            enable_rule_constraints=request.enable_rule_constraints,
            create_database_if_missing=request.create_database_if_missing,
            semantic_artifact_policy=request.semantic_artifact_policy,
            approved_artifacts=resolved_approved_artifacts,
        )
        payload = _raw_ingest_response_payload(result)
        payload.setdefault("workspace_id", request.workspace_id)
        return PlatformRawIngestResponse(**payload)
    except InvalidDatabaseNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Raw ingest endpoint failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Raw ingest failed. Check server logs for details.")


@app.post(RuntimePath.RUN_AGENT, response_model=AgentResponse)
@track("agent_server.run_agent")
async def run_agent(request: QueryRequest):
    """Legacy single-router endpoint."""
    try:
        require_runtime_permission(role="user", action="run_agent", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    valid_graph_ids, scoped_databases = _resolve_graph_scope(request.graph_ids)
    update_current_trace(
        metadata={
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "query": request.query[:200],
            "graph_ids": valid_graph_ids,
            "databases": scoped_databases,
        },
        tags=["router-mode"],
    )
    update_current_span(
        metadata={
            "mode": "router",
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "graph_ids": valid_graph_ids,
            "databases": scoped_databases,
        }
    )
    if request.graph_ids and not request.prefer_agentic_tools:
        if not scoped_databases:
            raise HTTPException(status_code=400, detail="No target databases available for graph scope.")
        try:
            result = await asyncio.to_thread(
                semantic_agent_flow.run,
                question=request.query,
                databases=scoped_databases,
                entity_overrides={},
                workspace_id=request.workspace_id,
                reasoning_mode=False,
                repair_budget=0,
            )
            response_text = str(result.get("response", ""))
            trace_steps = list(result.get("trace_steps", []))
            trace_steps.insert(
                0,
                {
                    "id": "graph-scope",
                    "type": "ROUTING_POLICY",
                    "agent": "RuntimeRouter",
                    "content": "Graph-scoped legacy request routed through semantic graph QA.",
                    "metadata": {
                        "requested_graph_ids": valid_graph_ids,
                        "databases": scoped_databases,
                        "fallback_from": "legacy_router",
                    },
                },
            )
            ontology_context_mismatch = _ontology_context_middleware_status(
                workspace_id=request.workspace_id,
                databases=scoped_databases,
            )
            return AgentResponse(
                response=response_text,
                trace_steps=_append_usage_trace_step(
                    trace_steps,
                    query=request.query,
                    response=response_text,
                    mode="graph-scoped-semantic",
                ),
                ontology_context_mismatch=ontology_context_mismatch,
            )
        except SeochoError:
            raise
        except Exception as e:
            logger.error("Graph-scoped agent execution failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Graph-scoped agent execution failed. Check server logs for details.",
            )

    srv_context = ServerContext(
        user_id=request.user_id,
        workspace_id=request.workspace_id,
        last_query=request.query,
        shared_memory=SharedMemory(),
        allowed_databases=scoped_databases if request.graph_ids else [],
        max_turns=max(1, int(request.max_steps or 10)),
        tool_budget=max(1, int(request.tool_budget or 4)),
    )

    try:
        agents_runtime = get_agents_runtime()
        with agents_runtime.trace(f"Request {request.user_id} - {request.query[:20]}"):
            result = await agents_runtime.run(
                agent=agent_router,
                input=request.query,
                context=srv_context,
                max_turns=srv_context.max_turns,
            )

        # Extract Trace Steps from Result History
        history = getattr(result, "chat_history", [])
        if not history:
            history = getattr(result, "messages", [])

        mapped_steps = []
        for i, msg in enumerate(history):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
            if content is None: content = ""

            step_type = "UNKNOWN"
            if role == "user":
                step_type = "USER_INPUT"
            elif role == "assistant":
                if getattr(msg, "tool_calls", None):
                    step_type = "THOUGHT"
                    content = f"Tools: {[tc.function.name for tc in msg.tool_calls]}"
                else:
                    step_type = "GENERATION"
            elif role == "tool":
                step_type = "TOOL_RESULT"

            agent_name = getattr(msg, "name", "System")

            mapped_steps.append({
                "id": str(i),
                "type": step_type,
                "agent": agent_name,
                "content": str(content),
                "metadata": {
                    "role": role
                }
            })

        ontology_context_mismatch = _ontology_context_middleware_status(
            workspace_id=request.workspace_id,
            databases=scoped_databases,
        )
        return AgentResponse(
            response=str(result.final_output),
            trace_steps=_append_usage_trace_step(
                mapped_steps,
                query=request.query,
                response=str(result.final_output),
                mode="legacy-router",
                result=result,
            ),
            ontology_context_mismatch=ontology_context_mismatch,
        )
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Agent execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Agent execution failed. Check server logs for details.")


@app.post(RuntimePath.RUN_AGENT_SEMANTIC, response_model=SemanticAgentResponse)
@track("agent_server.run_agent_semantic")
async def run_agent_semantic(request: SemanticQueryRequest):
    """Semantic entity-resolution route for graph QA."""
    try:
        require_runtime_permission(role="user", action="run_agent", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    requested_dbs = request.databases or db_registry.list_databases()
    valid_dbs = []
    for db_name in requested_dbs:
        if not db_registry.is_valid(db_name):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid database '{db_name}'. Valid options: {db_registry.list_databases()}",
            )
        valid_dbs.append(db_name)

    if not valid_dbs:
        raise HTTPException(status_code=400, detail="No target databases available.")

    overrides_by_entity: Dict[str, Dict[str, Any]] = {}
    for item in request.entity_overrides or []:
        question_entity = str(item.question_entity).strip()
        database = str(item.database).strip()
        if not question_entity:
            raise HTTPException(status_code=400, detail="entity_overrides[].question_entity is required")
        if not database:
            raise HTTPException(status_code=400, detail="entity_overrides[].database is required")
        if database not in valid_dbs:
            raise HTTPException(
                status_code=400,
                detail=f"entity_overrides contains database '{database}' outside requested databases {valid_dbs}",
            )
        overrides_by_entity[question_entity] = {
            "database": database,
            "node_id": item.node_id,
            "display_name": item.display_name,
            "labels": item.labels,
        }

    update_current_trace(
        metadata={
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "query": request.query[:200],
            "databases": valid_dbs,
        },
        tags=["semantic-route-mode"],
    )
    update_current_span(
        metadata={
            "mode": "semantic-route",
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "databases": valid_dbs,
        }
    )

    try:
        result = await asyncio.to_thread(
            semantic_agent_flow.run,
            question=request.query,
            databases=valid_dbs,
            entity_overrides=overrides_by_entity,
            workspace_id=request.workspace_id,
            reasoning_mode=request.reasoning_mode,
            repair_budget=request.repair_budget,
            reasoning_cycle=request.reasoning_cycle,
        )
        ontology_context_mismatch = memory_service.ontology_context_mismatch(
            workspace_id=request.workspace_id,
            databases=valid_dbs,
        )
        result.setdefault("semantic_context", {})[
            "ontology_context_mismatch"
        ] = ontology_context_mismatch
        result["ontology_context_mismatch"] = ontology_context_mismatch
        result["trace_steps"] = _append_usage_trace_step(
            list(result.get("trace_steps", [])),
            query=request.query,
            response=str(result.get("response", "")),
            mode="semantic-route",
        )
        return SemanticAgentResponse(**result)
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Semantic agent execution failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Semantic agent execution failed. Check server logs for details.",
        )


@app.get(RuntimePath.SEMANTIC_RUNS, response_model=SemanticRunRecordListResponse)
@track("agent_server.semantic_runs_list")
async def semantic_runs_list(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
    limit: int = Query(default=20, ge=1, le=200),
    route: Optional[str] = Query(default=None),
    intent_id: Optional[str] = Query(default=None),
):
    try:
        require_runtime_permission(role="user", action="run_agent", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        rows = list_semantic_runs(
            workspace_id=workspace_id,
            limit=limit,
            route=route,
            intent_id=intent_id,
        )
        return SemanticRunRecordListResponse(runs=[SemanticRunRecordResponse(**row) for row in rows])
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Semantic run list failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Semantic run list failed. Check server logs for details.")


@app.get(RuntimePath.SEMANTIC_RUN, response_model=SemanticRunRecordResponse)
@track("agent_server.semantic_runs_get")
async def semantic_runs_get(
    run_id: str,
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    try:
        require_runtime_permission(role="user", action="run_agent", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        payload = get_semantic_run(workspace_id=workspace_id, run_id=run_id)
        return SemanticRunRecordResponse(**payload)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Semantic run lookup failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Semantic run lookup failed. Check server logs for details.")


@app.post(RuntimePath.INDEXES_FULLTEXT_ENSURE, response_model=FulltextIndexEnsureResponse)
@track("agent_server.fulltext_index_ensure")
async def ensure_fulltext_indexes(request: FulltextIndexEnsureRequest):
    """Ensure a fulltext index exists for one or more databases."""
    try:
        require_runtime_permission(role="user", action="manage_indexes", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        return ensure_fulltext_indexes_impl(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Fulltext ensure failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Fulltext ensure failed. Check server logs for details.")


@app.post(RuntimePath.RUN_DEBATE, response_model=DebateResponse)
@track("agent_server.run_debate")
async def run_debate(request: QueryRequest):
    """Parallel Debate endpoint: all DB agents answer in parallel, Supervisor synthesises."""
    try:
        require_runtime_permission(role="user", action="run_debate", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    update_current_trace(
        metadata={
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "query": request.query[:200],
            "graph_ids": request.graph_ids or graph_registry.list_graph_ids(),
        },
        tags=["debate-mode"],
    )
    update_current_span(
        metadata={
            "mode": "debate",
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "graph_ids": request.graph_ids or graph_registry.list_graph_ids(),
        }
    )
    valid_graph_ids, scoped_databases = _resolve_graph_scope(request.graph_ids)

    memory = SharedMemory()
    srv_context = ServerContext(
        user_id=request.user_id,
        workspace_id=request.workspace_id,
        last_query=request.query,
        shared_memory=memory,
        allowed_databases=scoped_databases,
        max_turns=max(1, int(request.max_steps or 10)),
        tool_budget=max(1, int(request.tool_budget or 4)),
        semantic_agent_flow=semantic_agent_flow,
        reasoning_cycle=dict(request.reasoning_cycle or {}),
    )

    if not valid_graph_ids:
        raise HTTPException(status_code=400, detail="No target graphs available.")

    # Ensure agents exist for the requested graphs and capture readiness status.
    agent_statuses = agent_factory.create_agents_for_graphs(valid_graph_ids, db_manager)
    readiness = summarize_readiness(agent_statuses)

    all_agents = agent_factory.get_agents_for_graphs(valid_graph_ids)
    if readiness["debate_state"] == "blocked" or not all_agents:
        ontology_context_mismatch = _ontology_context_middleware_status(
            workspace_id=request.workspace_id,
            databases=scoped_databases,
        )
        return DebateResponse(
            response="Debate mode is blocked: no ready graph agents are available.",
            trace_steps=[
                {
                    "id": "0",
                    "type": "SYSTEM",
                    "agent": "DebateOrchestrator",
                    "content": "Debate blocked due to agent readiness.",
                    "metadata": {
                        "debate_state": "blocked",
                        "ready_count": readiness["ready_count"],
                        "degraded_count": readiness["degraded_count"],
                        "graph_ids": valid_graph_ids,
                    },
                }
            ],
            debate_results=[],
            agent_statuses=agent_statuses,
            debate_state="blocked",
            degraded=True,
            reasoning_cycle={},
            ontology_context_mismatch=ontology_context_mismatch,
        )

    orchestrator = DebateOrchestrator(
        agents=all_agents,
        supervisor=agent_supervisor,
        shared_memory=memory,
    )

    try:
        result = await orchestrator.run_debate(request.query, srv_context)
        result["agent_statuses"] = agent_statuses
        result["debate_state"] = readiness["debate_state"]
        result["degraded"] = readiness["degraded"]
        result["ontology_context_mismatch"] = _ontology_context_middleware_status(
            workspace_id=request.workspace_id,
            databases=scoped_databases,
        )
        result["trace_steps"] = _append_usage_trace_step(
            list(result.get("trace_steps", [])),
            query=request.query,
            response=str(result.get("response", "")),
            mode="debate",
        )
        return DebateResponse(**result)
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Debate execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Debate execution failed. Check server logs for details.")


@app.get(RuntimePath.HEALTH_RUNTIME, response_model=HealthResponse)
async def runtime_health():
    components: List[HealthComponent] = [
        HealthComponent(name="api", status="ready", detail="agent_server reachable"),
    ]

    db_status = "ready"
    db_detail = "DozerDB query ok"
    try:
        query_proxy.query(
            GraphQueryRequest(
                cypher="RETURN 1 AS ok",
                database="neo4j",
                workspace_id="runtime-health",
            )
        )
    except Exception as exc:
        db_status = "blocked"
        db_detail = str(exc)
    components.append(HealthComponent(name="dozerdb", status=db_status, detail=db_detail))

    runtime_status = "ready"
    runtime_detail = "agents runtime adapter loaded"
    try:
        get_agents_runtime()
    except Exception as exc:
        runtime_status = "degraded"
        runtime_detail = str(exc)
    components.append(HealthComponent(name="agents_runtime", status=runtime_status, detail=runtime_detail))

    overall = "ready"
    if any(comp.status == "blocked" for comp in components):
        overall = "blocked"
    elif any(comp.status == "degraded" for comp in components):
        overall = "degraded"

    return HealthResponse(
        scope="runtime",
        status=overall,
        generated_at=utc_now_iso(),
        components=components,
    )


@app.get(RuntimePath.HEALTH_BATCH, response_model=HealthResponse)
async def batch_health():
    status_file = batch_status_file_path()
    batch_status = "degraded"
    detail = f"status file not found: {status_file}"

    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as status_stream:
                raw = status_stream.read().strip().lower()
        except Exception as exc:
            raw = ""
            detail = f"failed to read status file: {exc}"
        if raw in {"success", "completed", "running"}:
            batch_status = "ready"
            detail = f"pipeline status: {raw}"
        elif raw in {"failed", "error"}:
            batch_status = "degraded"
            detail = f"pipeline status: {raw}"
        elif raw:
            batch_status = "degraded"
            detail = f"pipeline status: {raw}"

    components = [
        HealthComponent(name="pipeline", status=batch_status, detail=detail),
    ]

    return HealthResponse(
        scope="batch",
        status=batch_status,
        generated_at=utc_now_iso(),
        components=components,
    )


@app.get(RuntimePath.DATABASES)
async def list_databases(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    """List all registered databases."""
    try:
        require_runtime_permission(role="user", action="read_databases", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    # Databases themselves are global in MVP, but filtering could be added here if database registry tracked workspace_scope.
    return {"databases": db_registry.list_databases()}





@app.get(RuntimePath.GRAPHS)
async def list_graphs(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    """List registered graph targets."""
    try:
        require_runtime_permission(role="user", action="read_databases", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    graphs = [target.to_public_dict() for target in graph_registry.list_graphs()]
    return {"graphs": graphs}




@app.get(RuntimePath.AGENTS)
async def list_agents(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    try:
        require_runtime_permission(role="user", action="read_agents", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    # Agents are bound to graph targets. If graph targets become workspace-scoped, this could filter agent keys by graph workspace_scope.
    return {"agents": agent_factory.list_agents()}




@app.post("/rules/infer", response_model=RuleInferResponse)
@track("agent_server.rules_infer")
async def rules_infer(request: RuleInferRequest):
    """Infer SHACL-like rule profile from graph payload."""
    try:
        require_runtime_permission(role="user", action="infer_rules", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return infer_rule_profile(request)


@app.post("/rules/validate", response_model=RuleValidateResponse)
@track("agent_server.rules_validate")
async def rules_validate(request: RuleValidateRequest):
    """Validate graph payload against provided or inferred rule profile."""
    try:
        require_runtime_permission(role="user", action="validate_rules", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return validate_rule_profile(request)


@app.post("/rules/assess", response_model=RuleAssessResponse)
@track("agent_server.rules_assess")
async def rules_assess(request: RuleAssessRequest):
    """Assess practical readiness of SHACL-like rules for runtime and DB constraints."""
    try:
        require_runtime_permission(role="user", action="assess_rules", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return assess_rule_profile(request)


@app.post("/rules/profiles", response_model=RuleProfileCreateResponse)
@track("agent_server.rules_profiles_create")
async def rules_profiles_create(request: RuleProfileCreateRequest):
    """Persist a named rule profile for the workspace."""
    try:
        require_runtime_permission(role="user", action="manage_rule_profiles", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return create_rule_profile(request)


@app.get("/rules/profiles", response_model=RuleProfileListResponse)
@track("agent_server.rules_profiles_list")
async def rules_profiles_list(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    """List saved rule profiles in a workspace."""
    try:
        require_runtime_permission(role="user", action="manage_rule_profiles", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return read_rule_profiles(workspace_id=workspace_id)


@app.get("/rules/profiles/{profile_id}", response_model=RuleProfileGetResponse)
@track("agent_server.rules_profiles_get")
async def rules_profiles_get(
    profile_id: str,
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    """Read one saved rule profile."""
    try:
        require_runtime_permission(role="user", action="manage_rule_profiles", workspace_id=workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        return read_rule_profile(workspace_id=workspace_id, profile_id=profile_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/rules/export/cypher", response_model=RuleExportCypherResponse)
@track("agent_server.rules_export_cypher")
async def rules_export_cypher(request: RuleExportCypherRequest):
    """Export rule profile to DozerDB-compatible Cypher constraints."""
    try:
        require_runtime_permission(role="user", action="export_rules", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        return export_rule_profile_to_cypher(request)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/rules/export/shacl", response_model=RuleExportShaclResponse)
@track("agent_server.rules_export_shacl")
async def rules_export_shacl(request: RuleExportShaclRequest):
    """Export rule profile to SHACL-compatible artifacts."""
    try:
        require_runtime_permission(role="user", action="export_rules", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        return export_rule_profile_to_shacl(request)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/semantic/artifacts/drafts", response_model=SemanticArtifactResponse)
@track("agent_server.semantic_artifacts_draft_create")
async def semantic_artifacts_draft_create(request: SemanticArtifactDraftCreateRequest):
    try:
        require_runtime_permission(
            role="user",
            action="manage_semantic_artifacts",
            workspace_id=request.workspace_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    payload = create_semantic_artifact_draft(request)
    invalidate_semantic_vocabulary_cache()
    return payload


@app.post("/semantic/artifacts/{artifact_id}/approve", response_model=SemanticArtifactResponse)
@track("agent_server.semantic_artifacts_approve")
async def semantic_artifacts_approve(artifact_id: str, request: SemanticArtifactApproveRequest):
    try:
        require_runtime_permission(
            role="user",
            action="manage_semantic_artifacts",
            workspace_id=request.workspace_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        payload = approve_semantic_artifact_draft(artifact_id=artifact_id, request=request)
        invalidate_semantic_vocabulary_cache()
        return payload
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/semantic/artifacts/{artifact_id}/deprecate", response_model=SemanticArtifactResponse)
@track("agent_server.semantic_artifacts_deprecate")
async def semantic_artifacts_deprecate(artifact_id: str, request: SemanticArtifactDeprecateRequest):
    try:
        require_runtime_permission(
            role="user",
            action="manage_semantic_artifacts",
            workspace_id=request.workspace_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        payload = deprecate_semantic_artifact_approved(artifact_id=artifact_id, request=request)
        invalidate_semantic_vocabulary_cache()
        return payload
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/semantic/artifacts", response_model=SemanticArtifactListResponse)
@track("agent_server.semantic_artifacts_list")
async def semantic_artifacts_list(
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
    status: Optional[str] = Query(default=None),
):
    try:
        require_runtime_permission(
            role="user",
            action="manage_semantic_artifacts",
            workspace_id=workspace_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if status is not None and status not in {"draft", "approved", "deprecated"}:
        raise HTTPException(status_code=400, detail="status must be one of: draft, approved, deprecated")
    return read_semantic_artifacts(workspace_id=workspace_id, status=status)


@app.get("/semantic/artifacts/{artifact_id}", response_model=SemanticArtifactResponse)
@track("agent_server.semantic_artifacts_get")
async def semantic_artifacts_get(
    artifact_id: str,
    workspace_id: str = Query(default="default", pattern=WORKSPACE_ID_PATTERN),
):
    try:
        require_runtime_permission(
            role="user",
            action="manage_semantic_artifacts",
            workspace_id=workspace_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        return read_semantic_artifact(workspace_id=workspace_id, artifact_id=artifact_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
