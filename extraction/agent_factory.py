"""
Agent Factory

Dynamically creates DB-bound agents. Each agent is scoped to a single
Neo4j database and has tools that only query that database.
"""

import logging
import json
from typing import Any, Dict, List, Optional

from agents import Agent, function_tool, RunContextWrapper

from config import GraphTarget, graph_registry
from seocho.ontology_context import (
    _clean_distinct_strings,
    build_ontology_context_summary_query,
)

logger = logging.getLogger(__name__)


def _detect_ontology_skew(
    connector: Any,
    *,
    graph_id: str,
    database: str,
    workspace_id: str,
    ontology_context: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """Probe a graph for ontology context hash drift.

    Returns a skew metadata dict ``{active_hash, indexed_hashes, graph_id,
    database}`` when at least one indexed ``_ontology_context_hash`` differs
    from the agent's compiled ontology hash. Returns ``None`` when no skew
    is detected, when ``ontology_context`` is unset, when the graph carries
    no stamped hashes yet, or when the probe itself fails (graphs may be
    unreachable; absence of evidence isn't enforcement).

    Phase 2 calls this once at agent creation and caches the result on the
    tool closures; Phase 3 will compose this with the AgentStateMachine
    so the readiness guard fires in one place.
    """

    if ontology_context is None:
        return None
    descriptor = getattr(ontology_context, "descriptor", None)
    active_hash = str(getattr(descriptor, "context_hash", "") or "").strip()
    if not active_hash:
        return None

    try:
        rows = connector.query(
            build_ontology_context_summary_query(include_runtime_fields=True),
            params={"workspace_id": workspace_id},
            database=database,
        )
    except Exception:
        logger.debug(
            "Ontology context skew probe failed for graph %s/%s; not enforcing.",
            graph_id,
            database,
            exc_info=True,
        )
        return None

    if not rows:
        return None
    row = rows[0] if isinstance(rows[0], dict) else {}
    indexed_raw = (
        row.get("raw_context_hashes")
        if row.get("raw_context_hashes") is not None
        else row.get("indexed_context_hashes", [])
    )
    indexed = _clean_distinct_strings(indexed_raw)
    if not indexed:
        return None
    if all(item == active_hash for item in indexed):
        return None
    return {
        "active_context_hash": active_hash,
        "indexed_context_hashes": indexed,
        "graph_id": graph_id,
        "database": database,
        "workspace_id": workspace_id,
    }


def _ontology_skew_error_payload(skew: Dict[str, Any]) -> str:
    """Render the structured refuse-error returned by tools when skew is detected."""

    return json.dumps(
        {
            "error": "ontology_context_mismatch",
            "graph_id": skew.get("graph_id", ""),
            "database": skew.get("database", ""),
            "workspace_id": skew.get("workspace_id", ""),
            "active_context_hash": skew.get("active_context_hash", ""),
            "indexed_context_hashes": list(skew.get("indexed_context_hashes", [])),
            "message": (
                "Refused: graph is stamped with a different ontology context hash "
                "than the agent's active ontology. Re-index the graph or route the "
                "query to one with matching ontology before retrying."
            ),
        }
    )


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

    def create_graph_agent(
        self,
        graph_target: GraphTarget,
        schema_info: str,
        *,
        ontology_context: Optional[Any] = None,
        workspace_id: str = "default",
    ) -> Agent:
        """Create an agent bound to a specific graph target.

        The agent receives:
        - ``query_graph``: execute Cypher scoped to *graph_target*
        - ``get_schema``: return schema for *graph_target*
        - ``get_graph_profile``: return graph target metadata

        Uses closures to bind *graph_target* and *schema_info* at creation time.

        When ``ontology_context`` is provided, the factory probes the graph for
        ``_ontology_context_hash`` drift at creation time. If the graph is
        stamped with a hash that differs from ``ontology_context.descriptor.context_hash``,
        every tool invocation refuses with a structured ``ontology_context_mismatch``
        error rather than answering over a graph that doesn't match the agent's
        active ontology. ``ontology_context`` is currently unset by callers
        (Phase 1.5 / seocho-4nl wires the runtime ontology loader); the
        backward-compatible default keeps existing flows unchanged.
        """
        connector = self.neo4j_conn
        _graph_id = graph_target.graph_id
        _db = graph_target.database
        _schema = schema_info
        _profile = graph_target.to_public_dict()

        _skew = _detect_ontology_skew(
            connector,
            graph_id=_graph_id,
            database=_db,
            workspace_id=workspace_id,
            ontology_context=ontology_context,
        )

        @function_tool
        def query_graph(context: RunContextWrapper, query: str) -> str:
            """Execute a Cypher query against this agent's graph target."""
            if _skew is not None:
                return _ontology_skew_error_payload(_skew)

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
            if _skew is not None:
                return _ontology_skew_error_payload(_skew)
            return _schema

        @function_tool
        def get_graph_profile() -> str:
            """Return graph routing metadata for this agent."""
            if _skew is not None:
                return _ontology_skew_error_payload(_skew)
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
        setattr(agent, "ontology_context_skew", _skew)

        self._agents[_graph_id] = agent
        if _skew is not None:
            logger.warning(
                "Created agent for graph '%s' (database '%s') with ontology context skew; "
                "tools will refuse with ontology_context_mismatch.",
                _graph_id,
                _db,
            )
        else:
            logger.info("Created agent for graph '%s' (database '%s').", _graph_id, _db)
        return agent

    def create_db_agent(
        self,
        db_name: str,
        schema_info: str,
        *,
        ontology_context: Optional[Any] = None,
        workspace_id: str = "default",
    ) -> Agent:
        """Backward-compatible alias for database-scoped creation."""
        target = graph_registry.get_graph(db_name) or graph_registry.ensure_default_graph(db_name)
        return self.create_graph_agent(
            target,
            schema_info,
            ontology_context=ontology_context,
            workspace_id=workspace_id,
        )

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

    def create_agents_for_graphs(
        self,
        graph_ids: List[str],
        db_manager,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Create agents for the requested graph IDs.

        Args:
            db_manager: ``DatabaseManager`` instance (used for schema retrieval).
            ontology_contexts: Optional mapping of ``{graph_id: CompiledOntologyContext}``
                used for ontology-context hash drift detection at agent creation.
                Phase 1.5 (bd: seocho-4nl) wires the loader that populates this.
            workspace_id: Workspace scope for the ontology hash probe.

        Returns a list of status dicts. When ontology context drift is
        detected for a graph, the entry includes ``ontology_context_mismatch``
        with ``active_context_hash``, ``indexed_context_hashes``, and the
        graph identity. Phase 3 (composed readiness guard) consumes this.
        """
        statuses: List[Dict[str, Any]] = []
        contexts = ontology_contexts or {}
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

            agent_context = contexts.get(graph_id)
            if graph_id not in self._agents:
                agent = self.create_graph_agent(
                    graph_target,
                    schema,
                    ontology_context=agent_context,
                    workspace_id=workspace_id,
                )
                reason = "created"
            else:
                agent = self._agents[graph_id]
                reason = "checked"

            entry: Dict[str, Any] = {
                "graph": graph_id,
                "database": graph_target.database,
                "status": "ready",
                "reason": reason,
            }
            skew = getattr(agent, "ontology_context_skew", None)
            if skew is not None:
                entry["ontology_context_mismatch"] = skew
                entry["status"] = "degraded"
                entry["reason"] = "ontology_context_mismatch"
            statuses.append(entry)
        return statuses

    def create_agents_for_all_graphs(
        self,
        db_manager,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        return self.create_agents_for_graphs(
            graph_registry.list_graph_ids(),
            db_manager,
            ontology_contexts=ontology_contexts,
            workspace_id=workspace_id,
        )

    def create_agents_for_all_databases(
        self,
        db_manager,
        *,
        ontology_contexts: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Backward-compatible alias for legacy callers/tests."""
        return self.create_agents_for_all_graphs(
            db_manager,
            ontology_contexts=ontology_contexts,
            workspace_id=workspace_id,
        )
