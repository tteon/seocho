"""
Database connections for Graph RAG system.
Provides singleton-like access to Neo4j, LanceDB, and OpenAI clients.
"""
import lancedb
from openai import OpenAI
from neo4j import GraphDatabase, Driver
from typing import Optional

from src.config.settings import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    LANCEDB_PATH
)

# Global connection instances
_neo4j_driver: Optional[Driver] = None
_lancedb_conn = None
_openai_client: Optional[OpenAI] = None


def get_neo4j_driver() -> Driver:
    """Get or create Neo4j driver instance."""
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            NEO4J_URI, 
            auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _neo4j_driver


def get_lancedb():
    """Get or create LanceDB connection."""
    global _lancedb_conn
    if _lancedb_conn is None:
        _lancedb_conn = lancedb.connect(LANCEDB_PATH)
    return _lancedb_conn


def get_openai_client() -> OpenAI:
    """Get or create OpenAI client instance."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def close_connections():
    """Close all database connections."""
    global _neo4j_driver, _lancedb_conn
    if _neo4j_driver:
        _neo4j_driver.close()
        _neo4j_driver = None
    _lancedb_conn = None
