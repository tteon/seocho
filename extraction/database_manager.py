"""
Database Manager

Manages the lifecycle of Neo4j databases for the agent-driven platform:
provision (create + schema) → load data → register in the global registry.
"""

import logging
from typing import Optional

from neo4j import GraphDatabase

from config import (
    NEO4J_URI,
    NEO4J_USER,
    NEO4J_PASSWORD,
    _VALID_DB_NAME_RE,
    db_registry,
)
from graph_loader import GraphLoader
from ontology.base import Ontology

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Orchestrates DB creation, schema application, and data loading."""

    def __init__(
        self,
        neo4j_uri: str = NEO4J_URI,
        neo4j_user: str = NEO4J_USER,
        neo4j_password: str = NEO4J_PASSWORD,
        schema_manager=None,
    ):
        self._uri = neo4j_uri
        self._user = neo4j_user
        self._password = neo4j_password
        self.driver = GraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        )
        self._schema_manager = schema_manager
        self._graph_loaders: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def provision_database(
        self,
        db_name: str,
        ontology: Optional[Ontology] = None,
    ) -> str:
        """Create a Neo4j database, apply ontology constraints, and register.

        Steps:
        1. Validate db_name against _VALID_DB_NAME_RE
        2. CREATE DATABASE IF NOT EXISTS (via system DB)
        3. Apply ontology constraints if provided
        4. Register in db_registry

        Returns the database name on success.
        Raises ValueError for invalid names, RuntimeError for creation failures.
        """
        if not _VALID_DB_NAME_RE.match(db_name):
            raise ValueError(
                f"Invalid DB name '{db_name}': "
                "must be alphanumeric and start with a letter"
            )

        # Create database
        logger.info("Provisioning database '%s'...", db_name)
        try:
            with self.driver.session(database="system") as session:
                session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
            logger.info("Database '%s' created (or already exists).", db_name)
        except Exception as e:
            logger.error("Failed to create database '%s': %s", db_name, e)
            raise RuntimeError(f"Database creation failed: {e}") from e

        # Apply ontology constraints
        if ontology is not None:
            result = ontology.apply_to_neo4j(self.driver, database=db_name)
            logger.info(
                "Applied ontology to '%s': %d constraints, %d errors",
                db_name,
                result["success"],
                len(result["errors"]),
            )

        # Register
        db_registry.register(db_name)
        logger.info("Database '%s' registered in global registry.", db_name)
        return db_name

    def load_data(
        self,
        db_name: str,
        graph_data: dict,
        source_id: str,
    ) -> None:
        """Load graph data into a specific database using GraphLoader."""
        if not db_registry.is_valid(db_name):
            raise ValueError(f"Database '{db_name}' is not registered.")

        loader = self._get_loader(db_name)
        loader.load_graph(graph_data, source_id)
        logger.info("Loaded data for source '%s' into '%s'.", source_id, db_name)

    def get_schema_info(self, db_name: str) -> str:
        """Retrieve schema information for a database as human-readable text."""
        try:
            with self.driver.session(database=db_name) as session:
                # Node labels
                labels_result = session.run("CALL db.labels()")
                labels = [r["label"] for r in labels_result]

                # Relationship types
                rels_result = session.run("CALL db.relationshipTypes()")
                rel_types = [r["relationshipType"] for r in rels_result]

                # Property keys
                props_result = session.run("CALL db.propertyKeys()")
                prop_keys = [r["propertyKey"] for r in props_result]

            lines = [
                f"Database: {db_name}",
                f"Node Labels: {', '.join(labels) or 'none'}",
                f"Relationship Types: {', '.join(rel_types) or 'none'}",
                f"Property Keys: {', '.join(prop_keys) or 'none'}",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.error("Failed to get schema for '%s': %s", db_name, e)
            return f"Error retrieving schema for '{db_name}': {e}"

    def close(self) -> None:
        """Close driver and all cached graph loaders."""
        for loader in self._graph_loaders.values():
            loader.close()
        self._graph_loaders.clear()
        self.driver.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_loader(self, db_name: str) -> GraphLoader:
        if db_name not in self._graph_loaders:
            self._graph_loaders[db_name] = GraphLoader(
                self._uri, self._user, self._password
            )
        return self._graph_loaders[db_name]
