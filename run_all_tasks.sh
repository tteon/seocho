#!/bin/bash
set -e

# 1. Custom agents extraction
mkdir -p extraction/custom_agents
touch extraction/custom_agents/__init__.py

cat << 'INNER_EOF' > extraction/custom_agents/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

@dataclass
class AgentConfig:
    name: str
    instructions: str
    model: str = "gpt-4o"
    tools: List[Callable] = field(default_factory=list)
    handoffs: List['BaseAgent'] = field(default_factory=list)

class BaseAgent(ABC):
    def __init__(self, name: str, instructions: str, tools=None, handoffs=None, model="gpt-4o"):
        self.name = name
        self.instructions = instructions
        self.tools = tools or []
        self.handoffs = handoffs or []
        self.model = model

    def to_openai_agent(self):
        from agents import Agent
        return Agent(
            name=self.name,
            instructions=self.instructions,
            tools=self.tools,
            handoffs=[h.to_openai_agent() if isinstance(h, BaseAgent) else h for h in self.handoffs]
        )

    @abstractmethod
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        pass
INNER_EOF

cat << 'INNER_EOF' > extraction/custom_agents/graph_dba_agent.py
import logging
from agents import Agent, function_tool, RunContextWrapper

logger = logging.getLogger(__name__)

def create_graph_dba_agent(db_name: str, schema_info: str, neo4j_conn) -> Agent:
    _db = db_name
    _schema = schema_info

    @function_tool
    def query_db(context: RunContextWrapper, query: str) -> str:
        shared_mem = getattr(getattr(context, "context", None), "shared_memory", None)
        if shared_mem is not None:
            cached = shared_mem.get_cached_query(_db, query)
            if cached is not None:
                return f"[CACHED] {cached}"
        result = neo4j_conn.run_cypher(query, database=_db)
        if shared_mem is not None:
            shared_mem.cache_query_result(_db, query, result)
        return result

    @function_tool
    def get_schema() -> str:
        return _schema

    agent = Agent(
        name=f"Agent_{_db}",
        instructions=(
            f"You are a knowledge graph specialist for the '{_db}' database.\n\n"
            f"Schema:\n{_schema}\n\n"
            "When answering questions:\n"
            "1. Use get_schema() to verify available node labels and relationships.\n"
            "2. Use query_db() to execute Cypher queries against your database.\n"
            "3. Provide factual answers based on query results.\n"
            "4. If the question is outside your database's scope, state that clearly."
        ),
        tools=[query_db, get_schema],
    )
    logger.info("Created agent for database '%s'.", _db)
    return agent
INNER_EOF

cat << 'INNER_EOF' > extraction/custom_agents/supervisor_agent.py
from typing import Any
from agents import Agent

def create_supervisor_agent() -> Any:
    return Agent(
        name="Supervisor",
        instructions="You are the Supervisor. Your goal is to collect the results from the active agents, summarize them, and present the final answer to the user. Do not call any tools. Just synthesize and complete."
    )
INNER_EOF

cat << 'INNER_EOF' > extraction/custom_agents/reasoning_agent.py
from agents import Agent
def create_reasoning_agent(handoffs) -> Agent:
    return Agent(
        name="ReasoningAgent",
        instructions="You perform multi-hop graph reasoning. Extract paths and synthesize an explanation.",
        handoffs=handoffs
    )
INNER_EOF

cat << 'INNER_EOF' > extraction/custom_agents/ontology_agent.py
from agents import Agent
def create_ontology_agent() -> Agent:
    return Agent(
        name="OntologyDesigner",
        instructions="You help users design knowledge graph ontologies by analyzing domain descriptions, identifying entities/relationships, and outputting YAML schema.",
    )
INNER_EOF

cat << 'INNER_EOF' > extraction/custom_agents/graph_builder_agent.py
from agents import Agent
def create_graph_builder_agent() -> Agent:
    return Agent(
        name="GraphBuilder",
        instructions="You construct graphs intelligently, detect duplicates, suggest missing relationships, and validate consistency against ontologies.",
    )
INNER_EOF

# 2. Modify extraction/agent_factory.py
cat << 'INNER_EOF' > extraction/agent_factory.py
"""
Agent Factory

Dynamically creates DB-bound agents. Each agent is scoped to a single
Neo4j database and has tools that only query that database.
"""

import logging
from typing import Dict, List, Optional

from agents import Agent

from config import db_registry
from custom_agents.graph_dba_agent import create_graph_dba_agent

logger = logging.getLogger(__name__)


class AgentFactory:
    """Creates and manages per-database specialist agents."""

    def __init__(self, neo4j_connector):
        """
        Args:
            neo4j_connector: An object with ``run_cypher(query, database)`` method
                             (e.g. ``Neo4jConnector`` from agent_server).
        """
        self.neo4j_conn = neo4j_connector
        self._agents: Dict[str, Agent] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_db_agent(self, db_name: str, schema_info: str) -> Agent:
        """Create an agent bound to a specific Neo4j database."""
        agent = create_graph_dba_agent(db_name, schema_info, self.neo4j_conn)
        self._agents[db_name] = agent
        return agent

    def get_agent(self, db_name: str) -> Optional[Agent]:
        """Return the agent for *db_name*, or None."""
        return self._agents.get(db_name)

    def get_all_agents(self) -> Dict[str, Agent]:
        """Return all registered agents."""
        return dict(self._agents)

    def list_agents(self) -> List[str]:
        """Return database names that have agents."""
        return list(self._agents.keys())

    def create_agents_for_all_databases(self, db_manager) -> List[Dict[str, str]]:
        """Convenience: create agents for every registered database.

        Args:
            db_manager: ``DatabaseManager`` instance (used for schema retrieval).
        """
        statuses: List[Dict[str, str]] = []
        for db_name in db_registry.list_databases():
            try:
                schema = db_manager.get_schema_info(db_name)
            except Exception as exc:
                logger.warning(
                    "Skipping agent creation for database '%s': %s",
                    db_name,
                    exc,
                )
                self._agents.pop(db_name, None)
                statuses.append(
                    {
                        "database": db_name,
                        "status": "degraded",
                        "reason": str(exc),
                    }
                )
                continue

            if db_name not in self._agents:
                self.create_db_agent(db_name, schema)
                statuses.append(
                    {
                        "database": db_name,
                        "status": "ready",
                        "reason": "created",
                    }
                )
            else:
                statuses.append(
                    {
                        "database": db_name,
                        "status": "ready",
                        "reason": "checked",
                    }
                )
        return statuses
INNER_EOF
