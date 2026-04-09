"""Multi-instance graph connector utilities."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from neo4j import GraphDatabase

from config import (
    GraphTarget,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    db_registry,
    graph_registry,
)

logger = logging.getLogger(__name__)


class MultiGraphConnector:
    """Execute Cypher against graph-scoped Neo4j/DozerDB targets."""

    def __init__(
        self,
        default_uri: str = NEO4J_URI,
        default_user: str = NEO4J_USER,
        default_password: str = NEO4J_PASSWORD,
    ):
        self._default_uri = default_uri
        self._default_user = default_user
        self._default_password = default_password
        self._drivers: Dict[Tuple[str, str, str], Any] = {}

    def resolve_target(
        self,
        *,
        graph_id: Optional[str] = None,
        database: str = "neo4j",
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> GraphTarget:
        if graph_id:
            target = graph_registry.get_graph(graph_id)
            if target is None:
                raise ValueError(
                    f"Invalid graph '{graph_id}'. Valid options: {graph_registry.list_graph_ids()}"
                )
            return target

        if not db_registry.is_valid(database):
            raise ValueError(
                f"Invalid database '{database}'. Valid options: {db_registry.list_databases()}"
            )

        default_target = graph_registry.find_by_database(database)
        if default_target is not None and uri is None and user is None and password is None:
            return default_target

        return GraphTarget(
            graph_id=graph_id or database,
            database=database,
            uri=uri or self._default_uri,
            user=user or self._default_user,
            password=password or self._default_password,
            ontology_id=graph_id or database,
            vocabulary_profile="vocabulary.v2",
            description=f"Direct database target for '{database}'.",
        )

    def run_cypher(
        self,
        query: str,
        database: str = "neo4j",
        params: Optional[Dict[str, Any]] = None,
        graph_id: Optional[str] = None,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> str:
        try:
            target = self.resolve_target(
                graph_id=graph_id,
                database=database,
                uri=uri,
                user=user,
                password=password,
            )
            driver = self._get_driver(target.uri, target.user, target.password)
            with driver.session(database=target.database) as session:
                result = session.run(query, parameters=(params or {}))
                data = [record.data() for record in result]
                return json.dumps(data)
        except Exception as exc:
            scope = graph_id or database
            logger.error("Error executing Cypher in '%s': %s", scope, exc)
            return f"Error executing Cypher in '{scope}': {exc}"

    def close(self) -> None:
        for driver in self._drivers.values():
            try:
                driver.close()
            except Exception:
                logger.debug("Failed to close graph driver cleanly.", exc_info=True)
        self._drivers.clear()

    def _get_driver(self, uri: str, user: str, password: str):
        key = (uri, user, password)
        if key not in self._drivers:
            self._drivers[key] = GraphDatabase.driver(uri, auth=(user, password))
        return self._drivers[key]
