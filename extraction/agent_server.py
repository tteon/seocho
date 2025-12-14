from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from dataclasses import dataclass, field
import asyncio
import json
import os

# OpenAI Agent SDK Imports (Local Shim)
from agents import Agent, Runner, function_tool, RunContextWrapper, trace

app = FastAPI(title="Agent Server")

# ------------------------------------------------------------------
# 1. Context & Trace Logic
# ------------------------------------------------------------------
@dataclass
class ServerContext:
    user_id: str
    trace_path: List[str] = field(default_factory=list)
    last_query: str = ""

    def log_activity(self, agent_name: str):
        if not self.trace_path or self.trace_path[-1] != agent_name:
            self.trace_path.append(agent_name)

# ------------------------------------------------------------------
# 2. Tools & Agents Definition
# ------------------------------------------------------------------

# --- Real Managers ---
from vector_store import VectorStore
from neo4j import GraphDatabase


class Neo4jConnector:
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def run_cypher(self, query: str, database: str = "neo4j") -> str:
        try:
            # Validate database name to prevent injection or errors
            valid_dbs = ["neo4j", "system", "kgnormal", "kgfibo", "agent_traces"]
            if database not in valid_dbs:
                return f"Error: Invalid database '{database}'. Valid options: {valid_dbs}"

            with self.driver.session(database=database) as session:
                # Simple read query execution
                result = session.run(query)
                # Convert to list of dicts
                data = [record.data() for record in result]
                return json.dumps(data)
        except Exception as e:
            return f"Error executing Cypher in '{database}': {e}"

# ... (FAISSManager, SchemaManager)


# --- Tools ---

# Implementations for Testing
def get_databases_impl() -> str:
    """
    Returns a list of available graph databases (ontologies).
    """
    return "Available Databases: ['kgnormal', 'kgfibo', 'neo4j']"

def get_schema_impl(database: str = "neo4j") -> str:
    """
    Returns the schema for the specified database.
    """
    # Mapping logic for schemas
    schema_map = {
        "kgnormal": "outputs/schema_baseline.yaml",
        "kgfibo": "outputs/schema_fibo.yaml",
        "neo4j": "outputs/schema.yaml"
    }
    
    path = schema_map.get(database, "outputs/schema.yaml")
    
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()
    
    # Fallback to reading from DB directly if file not found (simulated)
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
    return faiss_manager.search(query)

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
# --- Agents ---

# --- Agents ---

# 1. Supervisor (The Collector)
agent_supervisor = Agent(
    name="Supervisor",
    instructions="You are the Supervisor. Your goal is to collect the results from the active agents, summarize them, and present the final answer to the user. Do not call any tools. Just synthesize and complete."
)

# 2. Graph DBA (The Executor)
# Forward declaration issue: GraphAgent is needed for handoff.
# We will define it later or update handoff list.
# Let's define GraphAgent first without handoffs, then DBA, then update GraphAgent.

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
    # Handoffs will be set after DBA definition
)


agent_graph_dba = Agent(
    name="GraphDBA",
    instructions="""
    You are the Graph Customer DBA, specialized in Text2Cypher.
    
    # Workflow
    1. Identify Available Databases: Call `get_databases_tool` to see what is available (e.g., 'kgnormal' for generic, 'kgfibo' for financial).
    2. Select Database: Choose the one that matches the user's domain.
    3. Inspect Schema: Call `get_schema_tool(database=...)` for the chosen DB.
    4. Generate Cypher: Write a precise Cypher query.
    5. Execute: Call `execute_cypher_tool(query, database=...)`.
    6. Report: Handoff results back to 'GraphAgent'.

    # Few-Shot Text2Cypher Examples
    
    ## Case: Generic/Baseline (kgnormal)
    User: "How is A related to B?"
    Cypher: "MATCH (a:Entity {name: 'A'})-[r]-(b:Entity {name: 'B'}) RETURN type(r) AS relation"
    
    ## Case: Financial (kgfibo)
    User: "Who sets the interest rate?"
    Cypher: "MATCH (org:Organization)-[:SETS]->(ir:Indicator {name: 'Interest Rate'}) RETURN org.name"
    
    ## Case: Multi-hop
    User: "Find connection between Apple and Fed."
    Cypher: "MATCH (a:Entity {name: 'Apple'}), (b:Entity {name: 'Fed'}), p=shortestPath((a)-[*]-(b)) RETURN p"
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
# 3. API Endpoint
# ------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    user_id: str = "user_default"

class AgentResponse(BaseModel):
    response: str
    trace_steps: List[Dict[str, Any]]

@app.post("/run_agent", response_model=AgentResponse)
async def run_agent(request: QueryRequest):
    # Context can still track simple path if needed
    srv_context = ServerContext(user_id=request.user_id, last_query=request.query)
    
    try:
        # Run the agent
        # trace() context sends data to OpenAI Dashboard
        # match the user_id for better observablity
        with trace(f"Request {request.user_id} - {request.query[:20]}"):
            result = await Runner.run(
                agent=agent_router,
                input=request.query,
                context=srv_context
            )
        
        # Extract Trace Steps from Result History
        # Assuming result has 'chat_history' or 'messages'
        # We'll look for standard attributes
        history = getattr(result, "chat_history", [])
        if not history:
            history = getattr(result, "messages", [])
            
        mapped_steps = []
        for i, msg in enumerate(history):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
            if content is None: content = ""
            
            # Simple mapping
            step_type = "UNKNOWN"
            if role == "user":
                step_type = "USER_INPUT"
            elif role == "assistant":
                if getattr(msg, "tool_calls", None):
                    step_type = "THOUGHT" # Proxy for thought/action
                    content = f"Tools: {[tc.function.name for tc in msg.tool_calls]}"
                else:
                    step_type = "GENERATION"
            elif role == "tool":
                step_type = "TOOL_RESULT"
                
            # Attempt to capture agent name if available in message metadata
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
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
