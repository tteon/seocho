import logging
import functools
import json
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# OpenAI Agent SDK Imports (Local Shim)
from agents import Agent, Runner, function_tool, RunContextWrapper, trace

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, db_registry, validate_config
from shared_memory import SharedMemory
from agent_factory import AgentFactory
from database_manager import DatabaseManager
from exceptions import (
    SeochoError,
    ConfigurationError,
    InfrastructureError,
    DataValidationError,
    PipelineError,
)
from middleware import RequestIDMiddleware
from tracing import configure_opik, track, update_current_span, update_current_trace
from policy import require_runtime_permission

logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Server")

# Request ID middleware
app.add_middleware(RequestIDMiddleware)

# CORS — restrict to local development origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:3000"],
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

    from middleware import get_request_id
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
# 1. Context & Trace Logic
# ------------------------------------------------------------------
@dataclass
class ServerContext:
    user_id: str
    workspace_id: str = "default"
    trace_path: List[str] = field(default_factory=list)
    last_query: str = ""
    shared_memory: Optional[SharedMemory] = None

    def log_activity(self, agent_name: str):
        if not self.trace_path or self.trace_path[-1] != agent_name:
            self.trace_path.append(agent_name)

# ------------------------------------------------------------------
# 2. Tools & Agents Definition
# ------------------------------------------------------------------

# --- Real Managers ---
from vector_store import VectorStore
from neo4j import GraphDatabase
from dependencies import (
    get_neo4j_connector,
    get_database_manager,
    get_agent_factory,
    get_vector_store,
)


class Neo4jConnector:
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    def run_cypher(self, query: str, database: str = "neo4j") -> str:
        try:
            if not db_registry.is_valid(database):
                return f"Error: Invalid database '{database}'. Valid options: {db_registry.list_databases()}"

            with self.driver.session(database=database) as session:
                result = session.run(query)
                data = [record.data() for record in result]
                return json.dumps(data)
        except Exception as e:
            logger.error("Error executing Cypher in '%s': %s", database, e)
            return f"Error executing Cypher in '{database}': {e}"


# --- Singletons ---
neo4j_conn = Neo4jConnector()
db_manager = DatabaseManager()
agent_factory = AgentFactory(neo4j_conn)
faiss_manager = VectorStore(api_key=os.getenv("OPENAI_API_KEY", ""))

# --- Tools ---

def get_databases_impl() -> str:
    """Returns a list of available graph databases."""
    dbs = db_registry.list_databases()
    return f"Available Databases: {dbs}"

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
def execute_cypher_tool(context: RunContextWrapper, query: str, database: str = "neo4j") -> str:
    """
    Executes a Cypher query against the specified database.
    database: The name of the database to query (e.g., 'kgnormal', 'kgfibo'). Default is 'neo4j'.
    """
    return neo4j_conn.run_cypher(query, database=database)

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
    2. **Database Selection**: You have access to multiple databases. Use `get_databases_tool()` to check availability.
       - `kgnormal` (General/Baseline knowledge)
       - `kgfibo` (Financial Ontology specific)
       Check which database is requested by the context.
    3. **Execution & Retry**: Use `execute_cypher_tool(query, database=...)`.
       - If the tool returns a syntax error, analyze the error, FIX the query, and RETRY immediately.
    4. **Ontology Compliance**: When querying `kgfibo`, ensure you ONLY use node labels and relationship types defined in the FIBO ontology schema.

    # Constraints
    - Use efficient Cypher patterns (e.g., limit paths, use indexed lookups).
    - If the user asks for a multi-hop path, use variable length relationships e.g., `-[*1..3]-`.

    # Output Format
    After successful execution, handoff back to 'GraphAgent' with a summary and the raw data.
    """,
    tools=[get_databases_tool, get_schema_tool, execute_cypher_tool],
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
    query: str = Field(..., max_length=2000)
    user_id: str = "user_default"
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")

class AgentResponse(BaseModel):
    response: str
    trace_steps: List[Dict[str, Any]]

class DebateResponse(BaseModel):
    response: str
    trace_steps: List[Dict[str, Any]]
    debate_results: List[Dict[str, Any]]

# ------------------------------------------------------------------
# 4. Endpoints
# ------------------------------------------------------------------

@app.post("/run_agent", response_model=AgentResponse)
@track("agent_server.run_agent")
async def run_agent(request: QueryRequest):
    """Legacy single-router endpoint."""
    try:
        require_runtime_permission(role="user", action="run_agent", workspace_id=request.workspace_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    update_current_trace(
        metadata={
            "user_id": request.user_id,
            "workspace_id": request.workspace_id,
            "query": request.query[:200],
        },
        tags=["router-mode"],
    )
    update_current_span(
        metadata={"mode": "router", "user_id": request.user_id, "workspace_id": request.workspace_id}
    )
    srv_context = ServerContext(
        user_id=request.user_id,
        workspace_id=request.workspace_id,
        last_query=request.query,
        shared_memory=SharedMemory(),
    )

    try:
        with trace(f"Request {request.user_id} - {request.query[:20]}"):
            result = await Runner.run(
                agent=agent_router,
                input=request.query,
                context=srv_context
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

        return AgentResponse(
            response=str(result.final_output),
            trace_steps=mapped_steps
        )
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Agent execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Agent execution failed. Check server logs for details.")


@app.post("/run_debate", response_model=DebateResponse)
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
        },
        tags=["debate-mode"],
    )
    update_current_span(
        metadata={"mode": "debate", "user_id": request.user_id, "workspace_id": request.workspace_id}
    )
    from debate import DebateOrchestrator

    memory = SharedMemory()
    srv_context = ServerContext(
        user_id=request.user_id,
        workspace_id=request.workspace_id,
        last_query=request.query,
        shared_memory=memory,
    )

    # Ensure agents exist for all registered databases
    agent_factory.create_agents_for_all_databases(db_manager)

    all_agents = agent_factory.get_all_agents()
    if not all_agents:
        raise HTTPException(
            status_code=400,
            detail="No database agents available. Provision databases first.",
        )

    orchestrator = DebateOrchestrator(
        agents=all_agents,
        supervisor=agent_supervisor,
        shared_memory=memory,
    )

    try:
        result = await orchestrator.run_debate(request.query, srv_context)
        return DebateResponse(**result)
    except SeochoError:
        raise
    except Exception as e:
        logger.error("Debate execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Debate execution failed. Check server logs for details.")


@app.get("/databases")
async def list_databases():
    """List all registered databases."""
    return {"databases": db_registry.list_databases()}


@app.get("/agents")
async def list_agents():
    """List all active DB-bound agents."""
    return {"agents": agent_factory.list_agents()}
