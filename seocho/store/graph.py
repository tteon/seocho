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

from seocho.ontology import Ontology

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Neo4j database naming: 3-63 chars, lowercase alpha start, alphanumeric only
_VALID_DB_NAME_RE = re.compile(r"^[a-z][a-z0-9]{2,62}$")
_RESERVED_DB_NAMES = {"system", "neo4j"}


class DatabaseNameError(ValueError):
    """Raised when a database name violates Neo4j naming rules."""


def validate_database_name(name: str) -> str:
    """Validate a Neo4j database name.

    Rules:
    - 3–63 characters
    - Starts with a lowercase letter
    - Lowercase alphanumeric only (no hyphens, underscores, dots)
    - ``system`` and ``neo4j`` are reserved

    Raises :class:`DatabaseNameError` with a clear message if invalid.
    """
    if name in _RESERVED_DB_NAMES:
        raise DatabaseNameError(
            f"'{name}' is a reserved Neo4j database name. "
            f"Choose a different name."
        )
    if not _VALID_DB_NAME_RE.match(name):
        suggestions = []
        if len(name) < 3:
            suggestions.append("must be at least 3 characters")
        if name != name.lower():
            suggestions.append("must be lowercase")
        if re.search(r"[^a-z0-9]", name):
            suggestions.append("only lowercase letters and digits allowed (no hyphens, underscores, dots)")
        if name and not name[0].isalpha():
            suggestions.append("must start with a letter")
        if len(name) > 63:
            suggestions.append("must be 63 characters or fewer")

        hint = "; ".join(suggestions) if suggestions else "invalid format"
        raise DatabaseNameError(
            f"Invalid Neo4j database name: '{name}'. {hint}.\n"
            f"Example valid names: 'financedemo', 'finderlpg', 'myproject2025'"
        )
    return name


def sanitize_database_name(raw: str) -> str:
    """Convert a raw string into a valid Neo4j database name.

    - Lowercases
    - Strips non-alphanumeric characters
    - Ensures minimum length
    - Prepends 'db' if starts with digit
    """
    name = re.sub(r"[^a-z0-9]", "", raw.lower())
    if not name:
        name = "seocho"
    if name[0].isdigit():
        name = "db" + name
    if len(name) < 3:
        name = name + "db"
    if len(name) > 63:
        name = name[:63]
    if name in _RESERVED_DB_NAMES:
        name = name + "data"
    return name


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
        self._user = user

    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
        triples: Optional[Sequence[Dict[str, Any]]] = None,
        graph_model: str = "lpg",
    ) -> Dict[str, Any]:
        # RDF mode: write triples via n10s
        if graph_model == "rdf" and triples:
            return self._write_rdf(triples, database=database, source_id=source_id)

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

    def _write_rdf(
        self,
        triples: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        source_id: str = "",
    ) -> Dict[str, Any]:
        """Write RDF triples via n10s (neosemantics).

        Each triple is ``{"subject": "uri", "predicate": "pred", "object": "uri_or_literal"}``.
        Converts to Turtle format and uses ``n10s.rdf.import.inline()``.
        """
        summary = {"nodes_created": 0, "relationships_created": 0, "triples_imported": 0, "errors": []}

        if not triples:
            return summary

        # Build Turtle string from triples
        turtle_lines = ["@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ."]
        for t in triples:
            subj = t.get("subject", "")
            pred = t.get("predicate", "")
            obj = t.get("object", "")
            if not subj or not pred or not obj:
                continue

            # Determine if object is a URI or literal
            if obj.startswith("http") or obj.startswith("urn:") or ":" in obj.split("/")[0]:
                turtle_lines.append(f"<{subj}> <{pred}> <{obj}> .")
            else:
                # Escape quotes in literals
                escaped = obj.replace('"', '\\"')
                turtle_lines.append(f'<{subj}> <{pred}> "{escaped}" .')

        turtle_str = "\n".join(turtle_lines)

        with self._driver.session(database=database) as session:
            try:
                # First ensure n10s is configured
                try:
                    session.run("CALL n10s.graphconfig.show.n10sConfig()")
                except Exception:
                    # Init n10s config if not already done
                    try:
                        session.run(
                            "CALL n10s.graphconfig.init("
                            "{handleVocabUris: 'MAP', handleMultival: 'ARRAY'})"
                        )
                    except Exception as init_exc:
                        summary["errors"].append(f"n10s init: {init_exc}")

                # Import triples
                result = session.run(
                    "CALL n10s.rdf.import.inline($rdf, 'Turtle')",
                    rdf=turtle_str,
                )
                record = result.single()
                if record:
                    summary["triples_imported"] = record.get("triplesLoaded", 0)
                    summary["nodes_created"] = record.get("triplesParsed", 0)
                    if record.get("extraInfo"):
                        summary["errors"].append(str(record["extraInfo"]))

            except Exception as exc:
                summary["errors"].append(f"n10s import: {exc}")

                # Fallback: write as LPG nodes if n10s not available
                logger.warning("n10s not available, falling back to LPG write for triples")
                for t in triples:
                    subj = t.get("subject", "")
                    pred = t.get("predicate", "")
                    obj = t.get("object", "")
                    if pred == "rdf:type" or pred.endswith("#type"):
                        try:
                            session.run(
                                "MERGE (n:Resource {uri: $uri}) SET n._source_id = $sid",
                                uri=subj, sid=source_id,
                            )
                            summary["nodes_created"] += 1
                        except Exception:
                            pass

        return summary

    def ensure_database(self, name: str) -> bool:
        """Create a database if it doesn't exist.

        Validates the name against Neo4j rules first.

        Returns True if the database was created, False if it already existed.

        Raises :class:`DatabaseNameError` if the name is invalid.
        """
        validate_database_name(name)

        try:
            with self._driver.session(database="system") as session:
                result = session.run("SHOW DATABASES")
                existing = {r["name"] for r in result}
                if name in existing:
                    return False
                session.run(f"CREATE DATABASE {name} IF NOT EXISTS")
                logger.info("Created database: %s", name)
                return True
        except Exception as exc:
            logger.warning("Could not create database '%s': %s", name, exc)
            return False

    def list_databases(self) -> List[str]:
        """List all available databases."""
        try:
            with self._driver.session(database="system") as session:
                result = session.run("SHOW DATABASES")
                return [r["name"] for r in result if r["name"] not in _RESERVED_DB_NAMES]
        except Exception:
            return []

    def close(self) -> None:
        self._driver.close()

    def __repr__(self) -> str:
        return f"Neo4jGraphStore(uri={self._uri!r})"
