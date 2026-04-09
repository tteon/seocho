"""
Database Manager

Manages the lifecycle of Neo4j databases for the agent-driven platform:
provision (create + schema) → load data → register in the global registry.
"""

import logging
from typing import Optional

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from config import (
    NEO4J_URI,
    NEO4J_USER,
    NEO4J_PASSWORD,
    _VALID_DB_NAME_RE,
    db_registry,
    graph_registry,
)
from graph_loader import GraphLoader
from ontology.base import Ontology
from exceptions import InvalidDatabaseNameError, Neo4jConnectionError
from retry_utils import neo4j_retry

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
        self._drivers: dict = {
            (neo4j_uri, neo4j_user, neo4j_password): self.driver
        }
        self._schema_manager = schema_manager
        self._graph_loaders: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @neo4j_retry
    def provision_database(
        self,
        db_name: str,
        ontology: Optional[Ontology] = None,
        graph_id: Optional[str] = None,
        graph_uri: Optional[str] = None,
        graph_user: Optional[str] = None,
        graph_password: Optional[str] = None,
        vocabulary_profile: str = "vocabulary.v2",
        description: Optional[str] = None,
    ) -> str:
        """Create a Neo4j database, apply ontology constraints, and register.

        Steps:
        1. Validate db_name against _VALID_DB_NAME_RE
        2. CREATE DATABASE IF NOT EXISTS (via system DB)
        3. Apply ontology constraints if provided
        4. Register in db_registry

        Returns the database name on success.

        Raises:
            InvalidDatabaseNameError: For names failing regex validation.
            Neo4jConnectionError: On transient Neo4j failures (retried automatically).
        """
        if not _VALID_DB_NAME_RE.match(db_name):
            raise InvalidDatabaseNameError(
                f"Invalid DB name '{db_name}': "
                "must be alphanumeric and start with a letter"
            )

        provision_uri = graph_uri or self._uri
        provision_user = graph_user or self._user
        provision_password = graph_password or self._password
        driver = self._get_driver(provision_uri, provision_user, provision_password)

        # Create database
        logger.info("Provisioning database '%s'...", db_name)
        try:
            with driver.session(database="system") as session:
                session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
            logger.info("Database '%s' created (or already exists).", db_name)
        except (ServiceUnavailable, SessionExpired) as e:
            raise Neo4jConnectionError(
                f"Neo4j connection failed during provisioning '{db_name}': {e}"
            ) from e

        # Apply ontology constraints
        if ontology is not None:
            result = ontology.apply_to_neo4j(driver, database=db_name)
            logger.info(
                "Applied ontology to '%s': %d constraints, %d errors",
                db_name,
                result["success"],
                len(result["errors"]),
            )

        # Register
        db_registry.register(db_name)
        resolved_graph_id = graph_id or db_name
        if graph_registry.get_graph(resolved_graph_id) is None:
            graph_registry.register(
                graph_id=resolved_graph_id,
                database=db_name,
                uri=provision_uri,
                user=provision_user,
                password=provision_password,
                ontology_id=getattr(ontology, "name", None) or resolved_graph_id,
                vocabulary_profile=vocabulary_profile,
                description=description or f"Graph target for database '{db_name}'.",
            )
        logger.info("Database '%s' registered in global registry.", db_name)
        return db_name

    def load_data(
        self,
        db_name: str,
        graph_data: dict,
        source_id: str,
        workspace_id: str = "default",
        graph_id: Optional[str] = None,
    ) -> None:
        """Load graph data into a specific database using GraphLoader."""
        if not db_registry.is_valid(db_name):
            raise InvalidDatabaseNameError(f"Database '{db_name}' is not registered.")

        target = graph_registry.get_graph(graph_id) if graph_id else graph_registry.find_by_database(db_name)
        resolved_db_name = target.database if target is not None else db_name
        loader_key = graph_id or resolved_db_name
        loader = self._get_loader(
            loader_key,
            uri=target.uri if target is not None else self._uri,
            user=target.user if target is not None else self._user,
            password=target.password if target is not None else self._password,
        )
        loader.load_graph(
            graph_data,
            source_id,
            database=resolved_db_name,
            workspace_id=workspace_id,
        )
        logger.info("Loaded data for source '%s' into '%s'.", source_id, db_name)

    def get_schema_info(self, db_name: str) -> str:
        """Retrieve schema information for a database as human-readable text."""
        try:
            return self._schema_info_from_driver(self.driver, db_name)
        except (ServiceUnavailable, SessionExpired) as e:
            raise Neo4jConnectionError(
                f"Neo4j connection failed retrieving schema for '{db_name}': {e}"
            ) from e

    def get_graph_schema_info(self, graph_id: str) -> str:
        """Retrieve schema information for a registered graph target."""
        target = graph_registry.get_graph(graph_id)
        if target is None:
            raise InvalidDatabaseNameError(f"Graph '{graph_id}' is not registered.")

        try:
            driver = self._get_driver(target.uri, target.user, target.password)
            return self._schema_info_from_driver(
                driver,
                target.database,
                graph_id=target.graph_id,
                ontology_id=target.ontology_id,
                vocabulary_profile=target.vocabulary_profile,
                description=target.description,
            )
        except (ServiceUnavailable, SessionExpired) as e:
            raise Neo4jConnectionError(
                f"Neo4j connection failed retrieving schema for graph '{graph_id}': {e}"
            ) from e

    def close(self) -> None:
        """Close driver and all cached graph loaders."""
        for loader in self._graph_loaders.values():
            loader.close()
        self._graph_loaders.clear()
        for driver in self._drivers.values():
            driver.close()
        self._drivers.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_loader(
        self,
        loader_key: str,
        *,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> GraphLoader:
        if loader_key not in self._graph_loaders:
            self._graph_loaders[loader_key] = GraphLoader(
                uri or self._uri,
                user or self._user,
                password or self._password,
            )
        return self._graph_loaders[loader_key]

    def _get_driver(self, uri: str, user: str, password: str):
        key = (uri, user, password)
        if key not in self._drivers:
            self._drivers[key] = GraphDatabase.driver(uri, auth=(user, password))
        return self._drivers[key]

    @staticmethod
    def _schema_info_from_driver(
        driver,
        database: str,
        *,
        graph_id: Optional[str] = None,
        ontology_id: Optional[str] = None,
        vocabulary_profile: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        with driver.session(database=database) as session:
            labels_result = session.run("CALL db.labels()")
            labels = [r["label"] for r in labels_result]

            rels_result = session.run("CALL db.relationshipTypes()")
            rel_types = [r["relationshipType"] for r in rels_result]

            props_result = session.run("CALL db.propertyKeys()")
            prop_keys = [r["propertyKey"] for r in props_result]

        lines = []
        if graph_id:
            lines.append(f"Graph ID: {graph_id}")
        lines.append(f"Database: {database}")
        if ontology_id:
            lines.append(f"Ontology ID: {ontology_id}")
        if vocabulary_profile:
            lines.append(f"Vocabulary Profile: {vocabulary_profile}")
        if description:
            lines.append(f"Description: {description}")
        lines.extend(
            [
                f"Node Labels: {', '.join(labels) or 'none'}",
                f"Relationship Types: {', '.join(rel_types) or 'none'}",
                f"Property Keys: {', '.join(prop_keys) or 'none'}",
            ]
        )
        return "\n".join(lines)
