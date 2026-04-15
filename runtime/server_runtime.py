from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from agent_factory import AgentFactory
from config import db_registry, graph_registry
from database_manager import DatabaseManager
from fulltext_index import FulltextIndexManager
from graph_connector import MultiGraphConnector
from runtime.memory_service import GraphMemoryService
from platform_agents import (
    BackendSpecialistAgent,
    FrontendSpecialistAgent,
    PlatformSessionStore,
)
from runtime.runtime_ingest import RuntimeRawIngestor
from semantic_query_flow import SemanticAgentFlow
from shared_memory import SharedMemory

logger = logging.getLogger(__name__)


@dataclass
class ServerContext:
    user_id: str
    workspace_id: str = "default"
    trace_path: List[str] = field(default_factory=list)
    last_query: str = ""
    shared_memory: Optional[SharedMemory] = None
    allowed_databases: List[str] = field(default_factory=list)
    tool_budget: int = 4
    tool_invocations: int = 0

    def log_activity(self, agent_name: str) -> None:
        if not self.trace_path or self.trace_path[-1] != agent_name:
            self.trace_path.append(agent_name)

    def can_query_database(self, database: str) -> bool:
        if not self.allowed_databases:
            return True
        return database in self.allowed_databases

    def consume_tool_budget(self) -> bool:
        if self.tool_invocations >= self.tool_budget:
            return False
        self.tool_invocations += 1
        return True


class Neo4jConnector(MultiGraphConnector):
    """Backward-compatible alias for the multi-instance graph connector."""


_neo4j_conn: Optional[Neo4jConnector] = None
_db_manager: Optional[DatabaseManager] = None
_agent_factory: Optional[AgentFactory] = None
_vector_store: Optional[object] = None
_semantic_agent_flow: Optional[SemanticAgentFlow] = None
_fulltext_index_manager: Optional[FulltextIndexManager] = None
_platform_session_store: Optional[PlatformSessionStore] = None
_backend_specialist_agent: Optional[BackendSpecialistAgent] = None
_frontend_specialist_agent: Optional[FrontendSpecialistAgent] = None
_runtime_raw_ingestor: Optional[RuntimeRawIngestor] = None
_memory_service: Optional[GraphMemoryService] = None


def get_neo4j_connector_service() -> Neo4jConnector:
    global _neo4j_conn
    if _neo4j_conn is None:
        _neo4j_conn = Neo4jConnector()
    return _neo4j_conn


def get_db_manager_service() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_agent_factory_service() -> AgentFactory:
    global _agent_factory
    if _agent_factory is None:
        _agent_factory = AgentFactory(get_neo4j_connector_service())
    return _agent_factory


def get_vector_store_service():
    global _vector_store
    if _vector_store is None:
        from vector_store import VectorStore

        _vector_store = VectorStore(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _vector_store


def get_semantic_agent_flow_service() -> SemanticAgentFlow:
    global _semantic_agent_flow
    if _semantic_agent_flow is None:
        _semantic_agent_flow = SemanticAgentFlow(
            get_neo4j_connector_service(),
            graph_targets=graph_registry.list_graphs(),
        )
    return _semantic_agent_flow


def get_fulltext_index_manager_service() -> FulltextIndexManager:
    global _fulltext_index_manager
    if _fulltext_index_manager is None:
        _fulltext_index_manager = FulltextIndexManager(get_neo4j_connector_service())
    return _fulltext_index_manager


def get_platform_session_store_service() -> PlatformSessionStore:
    global _platform_session_store
    if _platform_session_store is None:
        _platform_session_store = PlatformSessionStore()
    return _platform_session_store


def get_backend_specialist_agent_service() -> BackendSpecialistAgent:
    global _backend_specialist_agent
    if _backend_specialist_agent is None:
        _backend_specialist_agent = BackendSpecialistAgent()
    return _backend_specialist_agent


def get_frontend_specialist_agent_service() -> FrontendSpecialistAgent:
    global _frontend_specialist_agent
    if _frontend_specialist_agent is None:
        _frontend_specialist_agent = FrontendSpecialistAgent()
    return _frontend_specialist_agent


def get_runtime_raw_ingestor() -> RuntimeRawIngestor:
    global _runtime_raw_ingestor
    if _runtime_raw_ingestor is None:
        _runtime_raw_ingestor = RuntimeRawIngestor(db_manager=get_db_manager_service())
    return _runtime_raw_ingestor


def get_memory_service() -> GraphMemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = GraphMemoryService(
            db_manager=get_db_manager_service(),
            runtime_raw_ingestor=get_runtime_raw_ingestor(),
            semantic_agent_flow=get_semantic_agent_flow_service(),
        )
    return _memory_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_status_file_path() -> str:
    return os.getenv("SEOCHO_BATCH_STATUS_FILE", "/tmp/seocho_batch_status")


def invalidate_semantic_vocabulary_cache() -> None:
    try:
        get_semantic_agent_flow_service().resolver.vocabulary_resolver.clear_cache()
    except Exception:
        logger.debug("Semantic vocabulary cache invalidation skipped.", exc_info=True)


def get_databases_impl() -> str:
    dbs = db_registry.list_databases()
    graphs = graph_registry.list_graph_ids()
    return f"Available Graphs: {graphs}; Databases: {dbs}"


def get_graphs_impl() -> str:
    return str(
        [
            {
                "graph_id": target.graph_id,
                "database": target.database,
                "description": target.description,
                "ontology_id": target.ontology_id,
                "vocabulary_profile": target.vocabulary_profile,
                "workspace_scope": target.workspace_scope,
            }
            for target in graph_registry.list_graphs()
        ]
    )


def get_schema_impl(database: str = "neo4j") -> str:
    if not db_registry.is_valid(database):
        return f"Error: Unknown database '{database}'. Valid: {db_registry.list_databases()}"
    return get_neo4j_connector_service().get_schema(database)
