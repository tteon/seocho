from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from dataclasses import dataclass, field
import asyncio
import json
import os

# OpenAI Agent SDK Imports (Local Shim)
from agents import Agent, Runner, function_tool, RunContextWrapper

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

    def run_cypher(self, query: str) -> str:
        try:
            with self.driver.session() as session:
                # Simple read query execution
                result = session.run(query)
                # Convert to list of dicts
                data = [record.data() for record in result]
                return json.dumps(data)
        except Exception as e:
            return f"Error executing Cypher: {e}"

class FAISSManager:
    def __init__(self):
        # API Key for embedding
        self.store = VectorStore(api_key=os.getenv("OPENAI_API_KEY"))
        # Load existing index if available
        self.store.load_index("output")

    def search(self, query: str) -> str:
        results = self.store.search(query, k=3)
        if not results:
            return "No relevant documents found."
        return "\n".join([f"Doc {r['id']}: {r['text']}..." for r in results])

neo4j_conn = Neo4jConnector()
faiss_manager = FAISSManager()

# --- Tools ---
@function_tool
def execute_cypher_tool(context: RunContextWrapper, query: str) -> str:
    # Context handling wrapper if needed, or direct call
    # The Mock SDK passes context as first arg if typed? 
    # Let's assume the SDK handles injection or we just accept args.
    # For this simplified version, we just return data.
    return neo4j_conn.run_cypher(query)

@function_tool
def search_vector_tool(query: str) -> str:
    return faiss_manager.search(query)

@function_tool
def web_search_tool(query: str) -> str:
    return f"[Google] Latest news for: {query}"

# --- Agents ---
# --- Agents ---
agent_graph = Agent(name="GraphAgent", instructions="Graph expert. Use execute_cypher_tool to look up entities and relationships.", tools=[execute_cypher_tool])
agent_vector = Agent(name="VectorAgent", instructions="Vector expert. Use search_vector_tool to find documents.", tools=[search_vector_tool])
agent_web = Agent(name="WebSearchAgent", instructions="Web expert.", tools=[web_search_tool])
agent_reasoning = Agent(name="ReasoningAgent", instructions="You are a logic and general knowledge expert. Answer questions directly using your internal knowledge. Do not use tools.", tools=[])

# Supervisor
agent_supervisor = Agent(
    name="Supervisor",
    instructions="Delegate tasks. If the query requires financial data, use GraphAgent or VectorAgent. If it is a general question or requires pure reasoning, use ReasoningAgent.",
    tools=[
        agent_graph.as_tool(tool_name="call_graph", tool_description="Graph Data"),
        agent_vector.as_tool(tool_name="call_vector", tool_description="Documents"),
        agent_web.as_tool(tool_name="call_web", tool_description="Web News"),
        agent_reasoning.as_tool(tool_name="call_reasoning", tool_description="General Reasoning"),
    ],
)

# Router
agent_router = Agent(
    name="Router",
    instructions="Route queries.",
    handoffs=[agent_supervisor],
)

# ------------------------------------------------------------------
# 3. API Endpoint
# ------------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str
    user_id: str = "user_default"

class AgentResponse(BaseModel):
    response: str
    trace_path: List[str]

@app.post("/run_agent", response_model=AgentResponse)
async def run_agent(request: QueryRequest):
    srv_context = ServerContext(user_id=request.user_id, last_query=request.query)
    
    try:
        result = await Runner.run(
            agent=agent_router,
            input=request.query,
            context=srv_context
        )
        
        return AgentResponse(
            response=str(result.final_output),
            trace_path=srv_context.trace_path
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
