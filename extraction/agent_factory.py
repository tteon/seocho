"""
Agent Factory

Dynamically creates DB-bound agents. Each agent is scoped to a single
Neo4j database and has tools that only query that database.
"""

import logging
from typing import Dict, List, Optional

from agents import Agent, function_tool, RunContextWrapper

from config import db_registry

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
        """Create an agent bound to a specific Neo4j database.

        The agent receives:
        - ``query_db``: execute Cypher scoped to *db_name*
        - ``get_schema``: return schema for *db_name*

        Uses closures to bind *db_name* and *schema_info* at creation time.
        """
        connector = self.neo4j_conn
        _db = db_name
        _schema = schema_info

        @function_tool
        def query_db(context: RunContextWrapper, query: str) -> str:
            """Execute a Cypher query against this agent's database."""
            # SharedMemory cache integration (if available)
            shared_mem = getattr(getattr(context, "context", None), "shared_memory", None)
            if shared_mem is not None:
                cached = shared_mem.get_cached_query(_db, query)
                if cached is not None:
                    return f"[CACHED] {cached}"

            result = connector.run_cypher(query, database=_db)

            if shared_mem is not None:
                shared_mem.cache_query_result(_db, query, result)

            return result

        @function_tool
        def get_schema() -> str:
            """Return the schema for this agent's database."""
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

        self._agents[_db] = agent
        logger.info("Created agent for database '%s'.", _db)
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
            if db_name not in self._agents:
                try:
                    schema = db_manager.get_schema_info(db_name)
                except Exception as exc:
                    logger.warning(
                        "Skipping agent creation for database '%s': %s",
                        db_name,
                        exc,
                    )
                    statuses.append(
                        {
                            "database": db_name,
                            "status": "degraded",
                            "reason": str(exc),
                        }
                    )
                    continue
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
                        "reason": "cached",
                    }
                )
        return statuses
