"""
Graph store abstraction — pluggable backend for writing and querying
knowledge graphs.

Currently ships with :class:`LadybugGraphStore` for embedded local use and
:class:`Neo4jGraphStore` for DozerDB / Neo4j.

Usage::

    from seocho import Ontology
    from seocho.graph_store import LadybugGraphStore, Neo4jGraphStore

    store = LadybugGraphStore(".seocho/local.lbug")
    # or:
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
    store.ensure_constraints(ontology)
    store.write(nodes, relationships, database="mydb")
    result = store.query("MATCH (n:Company) RETURN n.name", database="mydb")
    store.close()
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence

from seocho.ontology import Ontology

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_property_value(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, list):
        return all(isinstance(item, (str, int, float, bool)) or item is None for item in value)
    return False

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
    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Execute a write Cypher statement (MERGE, DELETE, SET, REMOVE, etc.).

        Returns summary dict with ``nodes_affected`` and ``relationships_affected``.
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
        self._schema_cache: Dict[str, Dict[str, Any]] = {}
        self._schema_cache_ts: Dict[str, float] = {}
        self._schema_cache_ttl = 60.0  # seconds

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
        # Validate database name (skip for default 'neo4j')
        if database != "neo4j":
            validate_database_name(database)

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

                try:
                    set_clauses = [
                        "r._source_id = $source_id",
                        "r._workspace_id = $workspace_id",
                    ]
                    params: Dict[str, Any] = {
                        "src": src,
                        "tgt": tgt,
                        "source_id": source_id,
                        "workspace_id": workspace_id,
                    }
                    for idx, (key, value) in enumerate(props.items()):
                        if not _is_property_value(value):
                            continue
                        safe_key = key.replace("`", "")
                        param_name = f"prop_{idx}"
                        set_clauses.append(f"r.`{safe_key}` = ${param_name}")
                        params[param_name] = value
                    session.run(
                        f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
                        f"MERGE (a)-[r:{rtype}]->(b) "
                        f"SET {', '.join(set_clauses)}",
                        **params,
                    )
                    summary["relationships_created"] += 1
                except Exception as exc:
                    summary["errors"].append(f"Rel {src}-[{rtype}]->{tgt}: {exc}")

        if summary["nodes_created"] or summary["relationships_created"]:
            self.invalidate_schema_cache(database)
        return summary

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> List[Dict[str, Any]]:
        if database != "neo4j":
            validate_database_name(database)
        with self._driver.session(database=database) as session:
            result = session.run(cypher, parameters=params or {})
            return [record.data() for record in result]

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        if database != "neo4j":
            validate_database_name(database)
        with self._driver.session(database=database) as session:
            result = session.run(cypher, parameters=params or {})
            counters = result.consume().counters
            return {
                "nodes_affected": (
                    getattr(counters, "nodes_created", 0)
                    + getattr(counters, "nodes_deleted", 0)
                ),
                "relationships_affected": (
                    getattr(counters, "relationships_created", 0)
                    + getattr(counters, "relationships_deleted", 0)
                ),
                "properties_set": getattr(counters, "properties_set", 0),
            }

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
        now = time.monotonic()
        cached_ts = self._schema_cache_ts.get(database, 0.0)
        if database in self._schema_cache and (now - cached_ts) < self._schema_cache_ttl:
            return self._schema_cache[database]

        try:
            with self._driver.session(database=database) as session:
                labels_result = session.run("CALL db.labels()")
                labels = [r["label"] for r in labels_result]

                rel_types_result = session.run("CALL db.relationshipTypes()")
                rel_types = [r["relationshipType"] for r in rel_types_result]

                props_result = session.run("CALL db.propertyKeys()")
                prop_keys = [r["propertyKey"] for r in props_result]

            schema = {
                "labels": labels,
                "relationship_types": rel_types,
                "property_keys": prop_keys,
            }
            self._schema_cache[database] = schema
            self._schema_cache_ts[database] = now
            return schema
        except Exception as exc:
            logger.warning("get_schema failed for database '%s': %s", database, exc)
            return {"labels": [], "relationship_types": [], "property_keys": []}

    def invalidate_schema_cache(self, database: Optional[str] = None) -> None:
        """Clear the schema cache for a database or all databases."""
        if database is not None:
            self._schema_cache.pop(database, None)
            self._schema_cache_ts.pop(database, None)
        else:
            self._schema_cache.clear()
            self._schema_cache_ts.clear()

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


# ---------------------------------------------------------------------------
# LadybugDB / embedded implementation — zero-config, pip-installable
# ---------------------------------------------------------------------------

_LADYBUG_TYPE_MAP = {
    "string": "STRING",
    "integer": "INT64",
    "float": "DOUBLE",
    "number": "DOUBLE",
    "boolean": "BOOL",
    "datetime": "STRING",
    "date": "STRING",
}
_LADYBUG_COMMON_NODE_STRING_COLUMNS = (
    "name",
    "title",
    "uri",
    "description",
    "content",
    "content_preview",
    "memory_id",
    "source_id",
    "workspace_id",
    "status",
    "category",
    "source_type",
    "created_at",
    "updated_at",
    "archived_at",
    "metadata_json",
    "user_id",
    "agent_id",
    "session_id",
    "_ontology_context_hash",
    "_ontology_id",
    "_ontology_profile",
    "_workspace_id",
    "_source_id",
)
_LADYBUG_COMMON_REL_STRING_COLUMNS = (
    "type",
    "memory_id",
    "source_id",
    "workspace_id",
    "_workspace_id",
    "_source_id",
    "_ontology_context_hash",
    "_ontology_id",
    "_ontology_profile",
)
_LADYBUG_NODE_PROJECTION_KEYS = (
    "id",
    "name",
    "title",
    "uri",
    "description",
    "content",
    "content_preview",
    "memory_id",
    "source_id",
    "workspace_id",
    "status",
    "category",
    "source_type",
    "created_at",
    "updated_at",
    "archived_at",
    "_ontology_context_hash",
    "_ontology_id",
    "_ontology_profile",
)
_LADYBUG_REL_PROJECTION_KEYS = (
    "type",
    "memory_id",
    "source_id",
    "workspace_id",
    "_workspace_id",
    "_source_id",
    "_ontology_context_hash",
    "_ontology_id",
    "_ontology_profile",
)
_LADYBUG_FULLTEXT_SHOW_PATTERNS = (
    "SHOW FULLTEXT INDEXES",
    "SHOW INDEXES",
)


def _ladybug_column_names(columns: Sequence[str]) -> set[str]:
    names: set[str] = set()
    for column in columns:
        match = re.match(r"`([^`]+)`\s+", str(column))
        if match:
            names.add(match.group(1))
    return names


def _append_ladybug_string_columns(columns: List[str], names: Sequence[str]) -> None:
    existing = _ladybug_column_names(columns)
    for name in names:
        if name in existing:
            continue
        columns.append(f"`{name}` STRING")
        existing.add(name)


def _ladybug_property_projection(variable: str, *, relation: bool) -> str:
    keys = _LADYBUG_REL_PROJECTION_KEYS if relation else _LADYBUG_NODE_PROJECTION_KEYS
    items = ", ".join(f"{key}: coalesce({variable}.{key}, '')" for key in keys)
    return "{" + items + "}"


def _ladybug_expand_param_predicate(
    cypher: str,
    params: Dict[str, Any],
    *,
    pattern: str,
    param_name: str,
    param_prefix: str,
    clause_factory: Callable[[str], str],
    joiner: str,
) -> str:
    values = [str(value) for value in (params.get(param_name) or []) if str(value).strip()]
    if not re.search(pattern, cypher, flags=re.IGNORECASE):
        return cypher
    if not values:
        return re.sub(pattern, "TRUE", cypher, flags=re.IGNORECASE)

    clauses: List[str] = []
    for index, value in enumerate(values):
        key = f"{param_prefix}_{index}"
        params[key] = value
        clauses.append(clause_factory(key))
    replacement = "(" + joiner.join(clauses) + ")"
    return re.sub(pattern, replacement, cypher, flags=re.IGNORECASE)


def _rewrite_ladybug_query(cypher: str, params: Optional[Dict[str, Any]] = None) -> tuple[str, Dict[str, Any]]:
    query_params = dict(params or {})
    rewritten = _ladybug_expand_param_predicate(
        cypher,
        query_params,
        pattern=(
            r"\(\$relationship_candidates\s*=\s*\[\]\s+OR\s+type\(r\)\s+IN\s+\$relationship_candidates\s*\)"
        ),
        param_name="relationship_candidates",
        param_prefix="__ladybug_relationship_candidate",
        clause_factory=lambda key: f"coalesce(r.type, '') = ${key}",
        joiner=" OR ",
    )
    rewritten = _ladybug_expand_param_predicate(
        rewritten,
        query_params,
        pattern=(
            r"\(\$metric_aliases\s*=\s*\[\]\s+OR\s+ANY\(\s*alias\s+IN\s+\$metric_aliases\s+WHERE\s+"
            r"toLower\(coalesce\(m\.name,\s*m\.uri,\s*''\)\)\s+CONTAINS\s+alias\s*\)\s*\)"
        ),
        param_name="metric_aliases",
        param_prefix="__ladybug_metric_alias",
        clause_factory=lambda key: f"toLower(coalesce(m.name, m.uri, '')) CONTAINS ${key}",
        joiner=" OR ",
    )
    rewritten = _ladybug_expand_param_predicate(
        rewritten,
        query_params,
        pattern=(
            r"\(\$metric_scope_tokens\s*=\s*\[\]\s+OR\s+ALL\(\s*token\s+IN\s+\$metric_scope_tokens\s+WHERE\s+"
            r"toLower\(coalesce\(m\.name,\s*m\.uri,\s*''\)\)\s+CONTAINS\s+token\s*\)\s*\)"
        ),
        param_name="metric_scope_tokens",
        param_prefix="__ladybug_metric_scope_token",
        clause_factory=lambda key: f"toLower(coalesce(m.name, m.uri, '')) CONTAINS ${key}",
        joiner=" AND ",
    )
    rewritten = _ladybug_expand_param_predicate(
        rewritten,
        query_params,
        pattern=(
            r"\(\$years\s*=\s*\[\]\s+OR\s+ANY\(\s*year\s+IN\s+\$years\s+WHERE\s+"
            r"coalesce\(toString\(m\.year\),\s*''\)\s*=\s*year\s+OR\s+"
            r"toLower\(coalesce\(m\.name,\s*m\.uri,\s*''\)\)\s+CONTAINS\s+year\s*\)\s*\)"
        ),
        param_name="years",
        param_prefix="__ladybug_year",
        clause_factory=(
            lambda key: (
                f"(coalesce(m.year, '') = ${key} OR "
                f"toLower(coalesce(m.name, m.uri, '')) CONTAINS ${key})"
            )
        ),
        joiner=" OR ",
    )
    rewritten = re.sub(
        r"elementId\((\w+)\)",
        lambda match: f"coalesce({match.group(1)}.id, '')",
        rewritten,
    )
    rewritten = re.sub(
        r"coalesce\(toString\(([^)]+)\),\s*''\)",
        lambda match: f"coalesce({match.group(1)}, '')",
        rewritten,
    )
    rewritten = re.sub(r"toString\(\$(\w+)\)", lambda match: f"${match.group(1)}", rewritten)
    rewritten = re.sub(r"toString\((\w+\.\w+)\)", lambda match: f"coalesce({match.group(1)}, '')", rewritten)
    rewritten = re.sub(
        r"CASE\s+WHEN\s+(\w+\.\w+)\s+IS\s+NULL\s+THEN\s+''\s+ELSE\s+toString\(\1\)\s+END",
        lambda match: f"coalesce({match.group(1)}, '')",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(r"type\((\w+)\)", lambda match: f"coalesce({match.group(1)}.type, '')", rewritten)
    rewritten = re.sub(
        r"properties\((\w+)\)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)",
        lambda match: (
            f"{_ladybug_property_projection(match.group(1), relation=match.group(1).lower() in {'r', 'rel', 'relationship'})} "
            f"AS {match.group(2)}"
        ),
        rewritten,
    )
    return rewritten, query_params


def _lbug_type(py_value: Any) -> str:
    if isinstance(py_value, bool):
        return "BOOL"
    if isinstance(py_value, int):
        return "INT64"
    if isinstance(py_value, float):
        return "DOUBLE"
    return "STRING"


class LadybugGraphStore(GraphStore):
    """Embedded graph store backed by LadybugDB.

    Zero-config, file-based, Cypher-native. Install with::

        pip install "seocho[local]"      # or: pip install "seocho[embedded]"

    Usage::

        from seocho.store.graph import LadybugGraphStore
        store = LadybugGraphStore("./mygraph.lbug")
        store.ensure_constraints(ontology)   # creates NODE/REL tables
        store.write(nodes, relationships)

    Unlike Neo4j, LadybugDB is **schema-first** — node and relationship
    tables must exist before writes. ``ensure_constraints(ontology)`` uses
    the provided :class:`~seocho.ontology.Ontology` to declare all
    NODE/REL tables up front.

    For ad-hoc writes without a pre-registered ontology, tables are
    auto-declared on first use with best-effort property typing.

    Parameters
    ----------
    path:
        Filesystem path where the Ladybug database files live.
        Defaults to ``.seocho/local.lbug`` in the current directory.
    """

    def __init__(self, path: str = ".seocho/local.lbug") -> None:
        try:
            import real_ladybug as _lb
        except ImportError as exc:
            raise ImportError(
                "LadybugGraphStore requires 'real_ladybug'. "
                "Install it with: pip install 'seocho[local]'"
            ) from exc

        import os as _os

        self._lb = _lb
        self._path = path
        _os.makedirs(_os.path.dirname(_os.path.abspath(path)) or ".", exist_ok=True)
        self._db = _lb.Database(path)
        self._conn = _lb.Connection(self._db)
        self._declared_node_tables: set = set()
        self._declared_rel_tables: set = set()
        self._load_existing_schema()

    def _load_existing_schema(self) -> None:
        """Populate declared-table sets from the existing database."""
        try:
            result = self._conn.execute("CALL show_tables() RETURN *")
            for row in result:
                row_list = row if isinstance(row, list) else list(row)
                if len(row_list) >= 2:
                    table_name = str(row_list[1])
                    table_type = str(row_list[2]).upper() if len(row_list) > 2 else ""
                    if "NODE" in table_type:
                        self._declared_node_tables.add(table_name)
                    elif "REL" in table_type:
                        self._declared_rel_tables.add(table_name)
        except Exception:
            # CALL show_tables() may not be supported; ignore
            pass

    def _ensure_node_table(self, label: str, sample_props: Dict[str, Any]) -> None:
        if label in self._declared_node_tables or not _LABEL_RE.match(label):
            return
        cols: List[str] = []
        for key, value in (sample_props or {}).items():
            if not _LABEL_RE.match(key):
                continue
            cols.append(f"`{key}` {_lbug_type(value)}")
        _append_ladybug_string_columns(cols, _LADYBUG_COMMON_NODE_STRING_COLUMNS)
        # Primary key: prefer ``id`` if present, else auto-generated ``_node_id``
        pk = "id" if "id" in (sample_props or {}) else "_node_id"
        if pk == "_node_id":
            cols.append("`_node_id` STRING")
        col_list = ", ".join(cols)
        try:
            self._conn.execute(
                f"CREATE NODE TABLE IF NOT EXISTS `{label}` ({col_list}, PRIMARY KEY (`{pk}`))"
            )
            self._declared_node_tables.add(label)
        except Exception as exc:
            logger.warning("Failed to create node table %s: %s", label, exc)

    def _ensure_rel_table(
        self,
        rel_type: str,
        source_label: str,
        target_label: str,
        sample_props: Dict[str, Any],
    ) -> None:
        if rel_type in self._declared_rel_tables or not _LABEL_RE.match(rel_type):
            return
        if source_label not in self._declared_node_tables:
            return
        if target_label not in self._declared_node_tables:
            return
        cols: List[str] = []
        for key, value in (sample_props or {}).items():
            if not _LABEL_RE.match(key):
                continue
            cols.append(f"`{key}` {_lbug_type(value)}")
        _append_ladybug_string_columns(cols, _LADYBUG_COMMON_REL_STRING_COLUMNS)
        col_list = (", " + ", ".join(cols)) if cols else ""
        try:
            self._conn.execute(
                f"CREATE REL TABLE IF NOT EXISTS `{rel_type}`(FROM `{source_label}` TO `{target_label}`{col_list})"
            )
            self._declared_rel_tables.add(rel_type)
        except Exception as exc:
            logger.warning("Failed to create rel table %s: %s", rel_type, exc)

    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        summary = {"nodes_created": 0, "relationships_created": 0, "errors": []}
        node_label_by_id: Dict[str, str] = {}

        for node in nodes:
            label = str(node.get("label", "Entity"))
            if not _LABEL_RE.match(label):
                summary["errors"].append(f"Invalid label: {label}")
                continue
            props = dict(node.get("properties", {}))
            node_id = str(node.get("id") or props.get("name") or "")
            if not node_id:
                continue
            props.setdefault("id", node_id)
            props["_workspace_id"] = workspace_id
            props["_source_id"] = source_id

            self._ensure_node_table(label, props)
            node_label_by_id[node_id] = label

            prop_keys = [k for k in props if _LABEL_RE.match(k)]
            set_clause = ", ".join(f"`{k}`: $p_{i}" for i, k in enumerate(prop_keys))
            params = {f"p_{i}": props[k] for i, k in enumerate(prop_keys)}
            try:
                self._conn.execute(
                    f"MERGE (n:`{label}` {{id: $id}}) SET n = {{{set_clause}}}",
                    {"id": node_id, **params},
                )
                summary["nodes_created"] += 1
            except Exception:
                # Fallback: CREATE if MERGE dialect differs
                try:
                    self._conn.execute(
                        f"CREATE (n:`{label}` {{{set_clause}}})",
                        params,
                    )
                    summary["nodes_created"] += 1
                except Exception as exc:
                    summary["errors"].append(f"Node {node_id}: {exc}")

        for rel in relationships:
            rtype = str(rel.get("type", "RELATED_TO"))
            src_id = str(rel.get("source", ""))
            tgt_id = str(rel.get("target", ""))
            if not (src_id and tgt_id and _LABEL_RE.match(rtype)):
                continue
            src_label = node_label_by_id.get(src_id)
            tgt_label = node_label_by_id.get(tgt_id)
            if not (src_label and tgt_label):
                continue

            rprops = dict(rel.get("properties", {}))
            rprops.setdefault("type", rtype)
            rprops["_workspace_id"] = workspace_id
            rprops["_source_id"] = source_id

            self._ensure_rel_table(rtype, src_label, tgt_label, rprops)

            prop_keys = [k for k in rprops if _LABEL_RE.match(k)]
            set_clause = (
                "{" + ", ".join(f"`{k}`: $p_{i}" for i, k in enumerate(prop_keys)) + "}"
                if prop_keys else ""
            )
            params = {"src": src_id, "tgt": tgt_id,
                      **{f"p_{i}": rprops[k] for i, k in enumerate(prop_keys)}}
            try:
                self._conn.execute(
                    f"MATCH (a:`{src_label}` {{id: $src}}), (b:`{tgt_label}` {{id: $tgt}}) "
                    f"CREATE (a)-[r:`{rtype}`{(' ' + set_clause) if set_clause else ''}]->(b)",
                    params,
                )
                summary["relationships_created"] += 1
            except Exception as exc:
                summary["errors"].append(f"Rel {src_id}-[{rtype}]->{tgt_id}: {exc}")

        return summary

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> List[Dict[str, Any]]:
        compact = " ".join(str(cypher).upper().split())
        if any(compact.startswith(pattern) for pattern in _LADYBUG_FULLTEXT_SHOW_PATTERNS):
            return []
        if "CALL DB.INDEX.FULLTEXT.QUERYNODES" in compact:
            return []
        cypher, query_params = _rewrite_ladybug_query(cypher, params=params)
        try:
            result = self._conn.execute(cypher, query_params)
            out: List[Dict[str, Any]] = []
            # Ladybug returns rows as lists; convert to dicts using column names
            col_names = getattr(result, "column_names", None) or []
            for row in result:
                row_list = row if isinstance(row, list) else list(row)
                if col_names and len(col_names) == len(row_list):
                    out.append({name: val for name, val in zip(col_names, row_list)})
                else:
                    out.append({f"col_{i}": val for i, val in enumerate(row_list)})
            return out
        except Exception as exc:
            logger.warning("Ladybug query failed: %s", exc)
            return []

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        try:
            self._conn.execute(cypher, params or {})
            return {"nodes_affected": 0, "relationships_affected": 0, "properties_set": 0}
        except Exception as exc:
            return {"error": str(exc)}

    def ensure_constraints(
        self,
        ontology: Ontology,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Create NODE/REL tables declared by the ontology."""
        summary = {"success": 0, "errors": []}

        for label, node_def in ontology.nodes.items():
            if not _LABEL_RE.match(label):
                continue
            cols: List[str] = []
            pk_col: Optional[str] = None
            for prop_name, prop in node_def.properties.items():
                if not _LABEL_RE.match(prop_name):
                    continue
                py_type = str(prop.property_type.value).lower()
                lbug_type = _LADYBUG_TYPE_MAP.get(py_type, "STRING")
                cols.append(f"`{prop_name}` {lbug_type}")
                if prop.unique and pk_col is None:
                    pk_col = prop_name

            _append_ladybug_string_columns(cols, ("id", * _LADYBUG_COMMON_NODE_STRING_COLUMNS))
            pk_col = pk_col or "id"

            try:
                self._conn.execute(
                    f"CREATE NODE TABLE IF NOT EXISTS `{label}` "
                    f"({', '.join(cols)}, PRIMARY KEY (`{pk_col}`))"
                )
                self._declared_node_tables.add(label)
                summary["success"] += 1
            except Exception as exc:
                summary["errors"].append(f"Node table {label}: {exc}")

        for rtype, rel_def in ontology.relationships.items():
            if not _LABEL_RE.match(rtype):
                continue
            src, tgt = rel_def.source, rel_def.target
            if src not in self._declared_node_tables or tgt not in self._declared_node_tables:
                continue
            try:
                self._conn.execute(
                    f"CREATE REL TABLE IF NOT EXISTS `{rtype}`"
                    f"(FROM `{src}` TO `{tgt}`, {', '.join(f'`{name}` STRING' for name in _LADYBUG_COMMON_REL_STRING_COLUMNS)})"
                )
                self._declared_rel_tables.add(rtype)
                summary["success"] += 1
            except Exception as exc:
                summary["errors"].append(f"Rel table {rtype}: {exc}")

        return summary

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        return {
            "labels": sorted(self._declared_node_tables),
            "relationship_types": sorted(self._declared_rel_tables),
            "property_keys": [],
        }

    def delete_by_source(self, source_id: str, *, database: str = "neo4j") -> Dict[str, Any]:
        summary = {"nodes_deleted": 0, "relationships_deleted": 0}
        for label in list(self._declared_node_tables):
            try:
                self._conn.execute(
                    f"MATCH (n:`{label}`) WHERE n._source_id = $sid DETACH DELETE n",
                    {"sid": source_id},
                )
            except Exception:
                pass
        return summary

    def count_by_source(self, source_id: str, *, database: str = "neo4j") -> Dict[str, int]:
        total = 0
        for label in self._declared_node_tables:
            try:
                result = self._conn.execute(
                    f"MATCH (n:`{label}`) WHERE n._source_id = $sid RETURN count(n)",
                    {"sid": source_id},
                )
                for row in result:
                    total += int(row[0] if isinstance(row, list) else list(row)[0])
            except Exception:
                pass
        return {"nodes": total, "relationships": 0}

    def close(self) -> None:
        try:
            del self._conn
            del self._db
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"LadybugGraphStore(path={self._path!r})"
