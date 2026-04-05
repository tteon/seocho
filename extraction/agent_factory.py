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
