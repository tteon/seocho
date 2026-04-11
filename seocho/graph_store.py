"""
Graph store abstraction — pluggable backend for writing and querying
knowledge graphs.

Currently ships with :class:`Neo4jGraphStore` for DozerDB / Neo4j.

Usage::

    from seocho import Ontology
    from seocho.graph_store import Neo4jGraphStore

    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
    store.ensure_constraints(ontology)
    store.write(nodes, relationships, database="mydb")
    result = store.query("MATCH (n:Company) RETURN n.name", database="mydb")
    store.close()
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from .ontology import Ontology

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class GraphStore(ABC):
    """Abstract interface for graph storage backends."""

    @abstractmethod
    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
    ) -> Dict[str, Any]:
        """Write extracted nodes and relationships to the graph.

        Parameters
        ----------
        nodes:
            List of dicts ``{"id", "label", "properties": {...}}``.
        relationships:
            List of dicts ``{"source", "target", "type", "properties": {...}}``.
        database:
            Target database name.
        workspace_id:
            Tenant scope.
        source_id:
            Provenance identifier for the source document.

        Returns
        -------
        Summary dict with ``nodes_created``, ``relationships_created``,
        ``errors``.
        """

    @abstractmethod
    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> List[Dict[str, Any]]:
        """Execute a read-only Cypher query and return result records."""

    @abstractmethod
    def ensure_constraints(
        self,
        ontology: Ontology,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Apply ontology-derived constraints and indexes to the database.

        Returns
        -------
        Summary dict with ``success`` count and ``errors`` list.
        """

    @abstractmethod
    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        """Retrieve the current graph schema (labels, relationship types,
        property keys)."""

    @abstractmethod
    def delete_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Delete all nodes and relationships created by a given source_id.

        Returns summary with ``nodes_deleted``, ``relationships_deleted``.
        """

    @abstractmethod
    def count_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, int]:
        """Count nodes and relationships for a source_id.

        Returns ``{"nodes": N, "relationships": N}``.
        """

    @abstractmethod
    def close(self) -> None:
        """Release all resources (drivers, connections)."""


# ---------------------------------------------------------------------------
# Neo4j / DozerDB implementation
# ---------------------------------------------------------------------------


class Neo4jGraphStore(GraphStore):
    """Graph store backed by Neo4j or DozerDB.

    Requires the ``neo4j`` Python driver (optional dependency).

    Parameters
    ----------
    uri:
        Bolt URI, e.g. ``"bolt://localhost:7687"``.
    user:
        Database user.
    password:
        Database password.
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ImportError(
                "Neo4jGraphStore requires the 'neo4j' package. "
                "Install it with: pip install neo4j"
            ) from exc

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._uri = uri

    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
    ) -> Dict[str, Any]:
        summary = {"nodes_created": 0, "relationships_created": 0, "errors": []}

        with self._driver.session(database=database) as session:
            # --- Nodes ---
            for node in nodes:
                label = node.get("label", "Entity")
                if not _LABEL_RE.match(label):
                    summary["errors"].append(f"Invalid label: {label}")
                    continue
                props = dict(node.get("properties", {}))
                props["_source_id"] = source_id
                props["_workspace_id"] = workspace_id
                node_id = node.get("id", props.get("name", ""))
                props["id"] = node_id

                try:
                    session.run(
                        f"MERGE (n:{label} {{id: $id}}) SET n += $props",
                        id=node_id,
                        props=props,
                    )
                    summary["nodes_created"] += 1
                except Exception as exc:
                    summary["errors"].append(f"Node {node_id}: {exc}")

            # --- Relationships ---
            for rel in relationships:
                rtype = rel.get("type", "RELATED_TO")
                if not _LABEL_RE.match(rtype):
                    summary["errors"].append(f"Invalid relationship type: {rtype}")
                    continue
                src = rel.get("source", "")
                tgt = rel.get("target", "")
                props = dict(rel.get("properties", {}))
                props["_source_id"] = source_id
                props["_workspace_id"] = workspace_id

                try:
                    session.run(
                        f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
                        f"MERGE (a)-[r:{rtype}]->(b) SET r += $props",
                        src=src,
                        tgt=tgt,
                        props=props,
                    )
                    summary["relationships_created"] += 1
                except Exception as exc:
                    summary["errors"].append(f"Rel {src}-[{rtype}]->{tgt}: {exc}")

        return summary

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> List[Dict[str, Any]]:
        with self._driver.session(database=database) as session:
            result = session.run(cypher, parameters=params or {})
            return [record.data() for record in result]

    def ensure_constraints(
        self,
        ontology: Ontology,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        stmts = ontology.to_cypher_constraints()
        summary = {"success": 0, "errors": []}

        with self._driver.session(database=database) as session:
            for stmt in stmts:
                try:
                    session.run(stmt)
                    summary["success"] += 1
                except Exception as exc:
                    summary["errors"].append(f"{stmt}: {exc}")

        return summary

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        with self._driver.session(database=database) as session:
            labels_result = session.run("CALL db.labels()")
            labels = [r["label"] for r in labels_result]

            rel_types_result = session.run("CALL db.relationshipTypes()")
            rel_types = [r["relationshipType"] for r in rel_types_result]

            props_result = session.run("CALL db.propertyKeys()")
            prop_keys = [r["propertyKey"] for r in props_result]

        return {
            "labels": labels,
            "relationship_types": rel_types,
            "property_keys": prop_keys,
        }

    def delete_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        summary = {"nodes_deleted": 0, "relationships_deleted": 0, "errors": []}

        with self._driver.session(database=database) as session:
            # Delete relationships first (they reference nodes)
            try:
                result = session.run(
                    "MATCH ()-[r]->() WHERE r._source_id = $sid "
                    "WITH r LIMIT 10000 DELETE r RETURN count(r) AS cnt",
                    sid=source_id,
                )
                record = result.single()
                summary["relationships_deleted"] = record["cnt"] if record else 0
            except Exception as exc:
                summary["errors"].append(f"Rel delete: {exc}")

            # Delete orphaned nodes from this source
            try:
                result = session.run(
                    "MATCH (n) WHERE n._source_id = $sid "
                    "WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS cnt",
                    sid=source_id,
                )
                record = result.single()
                summary["nodes_deleted"] = record["cnt"] if record else 0
            except Exception as exc:
                summary["errors"].append(f"Node delete: {exc}")

        return summary

    def count_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, int]:
        with self._driver.session(database=database) as session:
            node_result = session.run(
                "MATCH (n) WHERE n._source_id = $sid RETURN count(n) AS cnt",
                sid=source_id,
            )
            node_count = node_result.single()["cnt"]

            rel_result = session.run(
                "MATCH ()-[r]->() WHERE r._source_id = $sid RETURN count(r) AS cnt",
                sid=source_id,
            )
            rel_count = rel_result.single()["cnt"]

        return {"nodes": node_count, "relationships": rel_count}

    def close(self) -> None:
        self._driver.close()

    def __repr__(self) -> str:
        return f"Neo4jGraphStore(uri={self._uri!r})"
