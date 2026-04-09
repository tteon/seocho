"""
Agent Factory

Dynamically creates DB-bound agents. Each agent is scoped to a single
Neo4j database and has tools that only query that database.
"""

import logging
import json
from typing import Dict, List, Optional

from agents import Agent, function_tool, RunContextWrapper

from config import GraphTarget, graph_registry

logger = logging.getLogger(__name__)


class AgentFactory:
    """Creates and manages per-graph specialist agents."""

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

    def create_graph_agent(self, graph_target: GraphTarget, schema_info: str) -> Agent:
        """Create an agent bound to a specific graph target.

        The agent receives:
        - ``query_graph``: execute Cypher scoped to *graph_target*
        - ``get_schema``: return schema for *graph_target*
        - ``get_graph_profile``: return graph target metadata

        Uses closures to bind *graph_target* and *schema_info* at creation time.
        """
        connector = self.neo4j_conn
        _graph_id = graph_target.graph_id
        _db = graph_target.database
        _schema = schema_info
        _profile = graph_target.to_public_dict()

        @function_tool
        def query_graph(context: RunContextWrapper, query: str) -> str:
            """Execute a Cypher query against this agent's graph target."""
            # SharedMemory cache integration (if available)
            shared_mem = getattr(getattr(context, "context", None), "shared_memory", None)
            if shared_mem is not None:
                cached = shared_mem.get_cached_query(_graph_id, query)
                if cached is not None:
                    return f"[CACHED] {cached}"

            result = connector.run_cypher(query, graph_id=_graph_id, database=_db)

            if shared_mem is not None:
                shared_mem.cache_query_result(_graph_id, query, result)

            return result

        @function_tool
        def get_schema() -> str:
            """Return the schema for this agent's graph."""
            return _schema

        @function_tool
        def get_graph_profile() -> str:
            """Return graph routing metadata for this agent."""
            return json.dumps(_profile)

        agent = Agent(
            name=f"Agent_{_graph_id}",
            instructions=(
                f"You are a knowledge graph specialist for the '{_graph_id}' graph.\n\n"
                f"Graph Profile:\n{json.dumps(_profile, indent=2)}\n\n"
                f"Schema:\n{_schema}\n\n"
                "When answering questions:\n"
                "1. Use get_graph_profile() first to confirm graph scope, ontology, and vocabulary profile.\n"
                "2. Use get_schema() to verify available node labels and relationships.\n"
                "3. Use query_graph() to execute Cypher queries against your graph only.\n"
                "4. Provide factual answers based on query results and cite scope limitations.\n"
                "5. If the question is outside your graph's scope, state that clearly."
            ),
            tools=[get_graph_profile, get_schema, query_graph],
        )
        setattr(agent, "graph_id", _graph_id)
        setattr(agent, "graph_database", _db)
        setattr(agent, "graph_profile", _profile)

        self._agents[_graph_id] = agent
        logger.info("Created agent for graph '%s' (database '%s').", _graph_id, _db)
        return agent

    def create_db_agent(self, db_name: str, schema_info: str) -> Agent:
        """Backward-compatible alias for database-scoped creation."""
        target = graph_registry.get_graph(db_name) or graph_registry.ensure_default_graph(db_name)
        return self.create_graph_agent(target, schema_info)

    def get_agent(self, db_name: str) -> Optional[Agent]:
        """Return the agent for *db_name*, or None."""
        return self._agents.get(db_name)

    def get_all_agents(self) -> Dict[str, Agent]:
        """Return all registered agents."""
        return dict(self._agents)

    def list_agents(self) -> List[str]:
        """Return graph IDs that have agents."""
        return list(self._agents.keys())

    def get_agents_for_graphs(self, graph_ids: List[str]) -> Dict[str, Agent]:
        return {
            graph_id: self._agents[graph_id]
            for graph_id in graph_ids
            if graph_id in self._agents
        }

    def create_agents_for_graphs(self, graph_ids: List[str], db_manager) -> List[Dict[str, str]]:
        """Create agents for the requested graph IDs.

        Args:
            db_manager: ``DatabaseManager`` instance (used for schema retrieval).
        """
        statuses: List[Dict[str, str]] = []
        for graph_id in graph_ids:
            graph_target = graph_registry.get_graph(graph_id)
            if graph_target is None:
                statuses.append(
                    {
                        "graph": graph_id,
                        "database": graph_id,
                        "status": "degraded",
                        "reason": f"Graph not registered: {graph_id}",
                    }
                )
                continue
            try:
                schema = db_manager.get_graph_schema_info(graph_id)
            except Exception as exc:
                logger.warning(
                    "Skipping agent creation for graph '%s': %s",
                    graph_id,
                    exc,
                )
                self._agents.pop(graph_id, None)
                statuses.append(
                    {
                        "graph": graph_id,
                        "database": graph_target.database,
                        "status": "degraded",
                        "reason": str(exc),
                    }
                )
                continue

            if graph_id not in self._agents:
                self.create_graph_agent(graph_target, schema)
                statuses.append(
                    {
                        "graph": graph_id,
                        "database": graph_target.database,
                        "status": "ready",
                        "reason": "created",
                    }
                )
            else:
                statuses.append(
                    {
                        "graph": graph_id,
                        "database": graph_target.database,
                        "status": "ready",
                        "reason": "checked",
                    }
                )
        return statuses

    def create_agents_for_all_graphs(self, db_manager) -> List[Dict[str, str]]:
        return self.create_agents_for_graphs(graph_registry.list_graph_ids(), db_manager)

    def create_agents_for_all_databases(self, db_manager) -> List[Dict[str, str]]:
        """Backward-compatible alias for legacy callers/tests."""
        return self.create_agents_for_all_graphs(db_manager)
