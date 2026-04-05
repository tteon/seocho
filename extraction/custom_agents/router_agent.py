import logging
from typing import List, Optional
from agents import Agent

logger = logging.getLogger(__name__)

def create_router_agent(child_agents: List[Agent]) -> Agent:
    return Agent(
        name="Router",
        instructions="""
# Role
You are the Router Agent. Route the user's query to the most appropriate sub-agent (Graph, Vector, Web, Table).

# Rules
- If the question involves paths, relationships, entities connected to other entities, route to GraphAgent.
- If the question involves similarity, text meaning, matching concepts implicitly, route to VectorAgent.
- If the question involves finding external public information, route to WebAgent.
- If the question involves aggregations, counts, statistical aggregations across flat records, route to TableAgent.
- Otherwise, pick the most appropriate agent.
""",
        handoffs=child_agents,
    )
