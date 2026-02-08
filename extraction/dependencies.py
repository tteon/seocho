"""
FastAPI Dependency Injection providers.

Provides singleton and request-scoped dependencies for agent_server endpoints.
"""

import os
import functools

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


@functools.lru_cache(maxsize=1)
def get_neo4j_connector():
    """Singleton Neo4jConnector provider."""
    from agent_server import Neo4jConnector
    return Neo4jConnector()


@functools.lru_cache(maxsize=1)
def get_database_manager():
    """Singleton DatabaseManager provider."""
    from database_manager import DatabaseManager
    return DatabaseManager()


@functools.lru_cache(maxsize=1)
def get_agent_factory():
    """Singleton AgentFactory provider."""
    from agent_factory import AgentFactory
    connector = get_neo4j_connector()
    return AgentFactory(connector)


@functools.lru_cache(maxsize=1)
def get_vector_store():
    """Singleton VectorStore provider."""
    from vector_store import VectorStore
    return VectorStore(api_key=os.getenv("OPENAI_API_KEY", ""))


def get_shared_memory():
    """Request-scoped SharedMemory provider (new instance per request)."""
    from shared_memory import SharedMemory
    return SharedMemory()
