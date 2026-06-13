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

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence

from seocho.cypher_ident import IDENT_RE, is_valid_identifier
from seocho.ontology import Ontology

logger = logging.getLogger(__name__)

# Indirection so tests can monkeypatch the poll delay to run instantly.
_sleep = time.sleep

# Canonical identifier validation/quoting lives in seocho.cypher_ident; the
# ``_LABEL_RE`` alias keeps the existing call sites in this module unchanged.
_LABEL_RE = IDENT_RE


def _is_property_value(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, list):
        return all(isinstance(item, (str, int, float, bool)) or item is None for item in value)
    return False

# Neo4j database naming: 3-63 chars, lowercase alpha start, alphanumeric only
_VALID_DB_NAME_RE = re.compile(r"^[a-z][a-z0-9]{2,62}$")
_RESERVED_DB_NAMES = {"system", "neo4j"}

# F7 (seocho-zgxs): per-label / per-rel cardinality probes in
# get_index_stats are bounded by a LIMIT so a single huge label can't
# turn every 60s cache refresh into a full-graph scan. When a probe hits
# the cap the count is reported as a lower bound with sampled=True; the
# GOPTS cost model only needs relative magnitude ("this label is big"),
# so a capped value ranks correctly without paying for an exact count.
_LABEL_COUNT_SAMPLE_LIMIT = 10000


class DatabaseNameError(ValueError):
    """Raised when a database name violates Neo4j naming rules."""


class WorkspaceFilterMissingError(ValueError):
    """Raised by ``query(..., enforce_workspace_filter=True)`` when the
    Cypher does not reference ``$workspace_id``.

    Closes part of seocho-y4at — multi-tenant deployments can opt into
    this safety net to refuse cross-tenant queries at the store layer.
    """

    def __init__(self, cypher: str) -> None:
        super().__init__(
            "Cypher does not reference $workspace_id; refusing to run "
            "with enforce_workspace_filter=True. Add 'WHERE "
            "<var>._workspace_id = $workspace_id' to scope the query."
        )
        self.cypher = cypher


class EnsureConstraintsError(RuntimeError):
    """Raised by ``ensure_constraints(..., strict=True)`` when one or more
    constraint writes fail.

    The original errors list is preserved on the exception's ``errors``
    attribute so callers can inspect each failed statement.

    Closes seocho-hvoe — without strict mode, ensure_constraints returns
    a success-shaped dict even on partial failure, and callers who don't
    inspect ``summary['errors']`` write data into a database with a
    half-applied schema.
    """

    def __init__(self, summary: Dict[str, Any]) -> None:
        errors = summary.get("errors", [])
        super().__init__(
            f"ensure_constraints failed in strict mode: {len(errors)} statement(s) errored"
        )
        self.summary = summary
        self.errors = list(errors)


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
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> List[Dict[str, Any]]:
        """Execute a read-only Cypher query and return result records.

        seocho-y4at: ``workspace_id`` is auto-injected into params; when
        ``enforce_workspace_filter=True`` the cypher must reference
        ``$workspace_id`` or :class:`WorkspaceFilterMissingError` is raised.
        """

    @abstractmethod
    def ensure_constraints(
        self,
        ontology: Ontology,
        *,
        database: str = "neo4j",
        strict: bool = False,
        transactional: bool = False,
    ) -> Dict[str, Any]:
        """Apply ontology-derived constraints and indexes to the database.

        Parameters
        ----------
        ontology:
            The ontology whose schema constraints should be applied.
        database:
            Target database name.
        strict:
            seocho-hvoe — when ``True``, raise :class:`EnsureConstraintsError`
            if any individual constraint write fails. Default ``False``
            preserves the back-compat partial-success summary; callers that
            want to short-circuit on schema-write failure should opt in.
        transactional:
            seocho-c2ck — when ``True``, run all statements inside a
            single transaction so partial failures roll back atomically.
            Default ``False`` for back-compat (some Neo4j configurations
            forbid DDL inside transactions).

        Returns
        -------
        Summary dict with ``success`` count and ``errors`` list.

        Raises
        ------
        EnsureConstraintsError
            When ``strict=True`` and at least one constraint failed.
        """

    @abstractmethod
    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
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


_packstream_codec_logged = False


def _log_packstream_codec_once() -> None:
    """Log which PackStream codec is live — rust-ext or pure-python.

    ADR-0111 / CLAUDE.md §21.2: the ``neo4j-rust-ext`` codec is an install-time
    drop-in, so operators and benchmarks must never have to guess which path
    they measured. Logged once per process at first driver construction.
    """
    global _packstream_codec_logged
    if _packstream_codec_logged:
        return
    _packstream_codec_logged = True
    try:
        from neo4j._codec.packstream import RUST_AVAILABLE
        codec = "rust-ext" if RUST_AVAILABLE else "pure-python"
    except ImportError:  # private flag moved — report honestly, don't guess
        codec = "unknown (neo4j._codec.packstream.RUST_AVAILABLE not found)"
    logger.info("neo4j packstream codec: %s active", codec)


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

        _log_packstream_codec_once()
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._uri = uri
        self._user = user
        self._schema_cache: Dict[str, Dict[str, Any]] = {}
        self._schema_cache_ts: Dict[str, float] = {}
        self._schema_cache_ttl = 60.0  # seconds
        self._index_stats_cache: Dict[str, Dict[str, Any]] = {}
        self._index_stats_cache_ts: Dict[str, float] = {}

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

        # seocho-4rg (Lamport): stamp a writer timestamp so concurrent/replayed
        # writes are last-writer-wins by time rather than by arrival order. The
        # MERGE guards below refuse to overwrite a node/rel that already carries
        # a NEWER _writer_ts, so a stale retry (e.g. a crashed ingest replayed)
        # cannot clobber a fresher fact. One ts per write() call.
        now = time.time()

        # Group by label/type and write each group in one UNWIND round-trip
        # (labels/rel-types can't be parameterized in MERGE, so we batch per
        # distinct label). A batch that throws falls back to per-row so one bad
        # row neither loses its siblings nor its error message — behavior stays
        # identical to the old per-row loop, just N round-trips -> #labels.
        nodes_by_label: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            label = node.get("label", "Entity")
            if not _LABEL_RE.match(label):
                summary["errors"].append(f"Invalid label: {label}")
                continue
            props = dict(node.get("properties", {}))
            props["_source_id"] = source_id
            props["_workspace_id"] = workspace_id
            props["_writer_ts"] = now
            props["_writer_agent"] = source_id or "unknown"
            node_id = node.get("id", props.get("name", ""))
            props["id"] = node_id
            nodes_by_label.setdefault(label, []).append({"id": node_id, "props": props})

        rels_by_type: Dict[str, List[Dict[str, Any]]] = {}
        for rel in relationships:
            rtype = rel.get("type", "RELATED_TO")
            if not _LABEL_RE.match(rtype):
                summary["errors"].append(f"Invalid relationship type: {rtype}")
                continue
            props = {k: v for k, v in dict(rel.get("properties", {})).items()
                     if _is_property_value(v)}
            props["_source_id"] = source_id
            props["_workspace_id"] = workspace_id
            props["_writer_ts"] = now
            props["_writer_agent"] = source_id or "unknown"
            rels_by_type.setdefault(rtype, []).append(
                {"src": rel.get("source", ""), "tgt": rel.get("target", ""), "props": props})

        with self._driver.session(database=database) as session:
            # --- Nodes (one UNWIND per label) ---
            for label, rows in nodes_by_label.items():
                # label validated against _LABEL_RE above; interpolated raw.
                # LWW guard: apply incoming props only if this write is newer
                # (or the node has no writer ts yet); stale replays no-op.
                # issue #183: _source_id stays single-valued (LWW, keeps the
                # delete/count filters working) while _sources accumulates
                # every contributing document — outside the LWW guard, since
                # a stale replay still proves that document referenced the
                # node.
                sources_clause = (
                    " SET n._sources = CASE WHEN n._sources IS NULL THEN [{p}._source_id] "
                    "WHEN NOT {p}._source_id IN n._sources THEN n._sources + {p}._source_id "
                    "ELSE n._sources END"
                )
                batch_q = (
                    f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
                    "SET n += CASE WHEN n._writer_ts IS NULL "
                    "OR n._writer_ts <= row.props._writer_ts THEN row.props ELSE {} END"
                    + sources_clause.format(p="row.props")
                )
                try:
                    session.run(batch_q, rows=rows)
                    summary["nodes_created"] += len(rows)
                except Exception:
                    for row in rows:
                        try:
                            session.run(
                                f"MERGE (n:{label} {{id: $id}}) SET n += CASE WHEN "
                                "n._writer_ts IS NULL OR n._writer_ts <= $props._writer_ts "
                                "THEN $props ELSE {} END"
                                + sources_clause.format(p="$props"),
                                id=row["id"], props=row["props"])
                            summary["nodes_created"] += 1
                        except Exception as exc:
                            summary["errors"].append(f"Node {row['id']}: {exc}")

            # --- Relationships (one UNWIND per type) ---
            for rtype, rows in rels_by_type.items():
                # rtype validated against _LABEL_RE above; interpolated raw
                rel_sources_clause = (
                    " SET r._sources = CASE WHEN r._sources IS NULL THEN [{p}._source_id] "
                    "WHEN NOT {p}._source_id IN r._sources THEN r._sources + {p}._source_id "
                    "ELSE r._sources END"
                )
                batch_q = (f"UNWIND $rows AS row MATCH (a {{id: row.src}}), (b {{id: row.tgt}}) "
                           f"MERGE (a)-[r:{rtype}]->(b) "
                           "SET r += CASE WHEN r._writer_ts IS NULL "
                           "OR r._writer_ts <= row.props._writer_ts THEN row.props ELSE {} END"
                           + rel_sources_clause.format(p="row.props"))
                try:
                    session.run(batch_q, rows=rows)
                    summary["relationships_created"] += len(rows)
                except Exception:
                    for row in rows:
                        try:
                            session.run(
                                f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
                                f"MERGE (a)-[r:{rtype}]->(b) SET r += CASE WHEN "
                                "r._writer_ts IS NULL OR r._writer_ts <= $props._writer_ts "
                                "THEN $props ELSE {} END"
                                + rel_sources_clause.format(p="$props"),
                                src=row["src"], tgt=row["tgt"], props=row["props"])
                            summary["relationships_created"] += 1
                        except Exception as exc:
                            summary["errors"].append(
                                f"Rel {row['src']}-[{rtype}]->{row['tgt']}: {exc}")

        if summary["nodes_created"] or summary["relationships_created"]:
            self.invalidate_schema_cache(database)
        return summary

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> List[Dict[str, Any]]:
        """Run a read-only Cypher query.

        seocho-y4at: writes stamp ``_workspace_id`` on every node/rel,
        but raw queries don't filter by it. The fix layered here:

        - ``workspace_id``: when provided, the value is injected into
          ``params`` as ``$workspace_id`` so the caller can write
          ``WHERE n._workspace_id = $workspace_id`` and have it resolve
          without manually copying the value into params.
        - ``enforce_workspace_filter``: when True, raises
          ``WorkspaceFilterMissingError`` if the cypher does not reference
          ``$workspace_id`` AT ALL. Conservative substring check —
          there's no auto-rewriting of arbitrary Cypher because that's
          unsafe (existing WHERE clauses, multi-MATCH, path patterns).
        """
        if database != "neo4j":
            validate_database_name(database)
        merged_params = dict(params or {})
        if workspace_id is not None and "workspace_id" not in merged_params:
            merged_params["workspace_id"] = workspace_id
        if enforce_workspace_filter and "$workspace_id" not in cypher:
            raise WorkspaceFilterMissingError(cypher)
        with self._driver.session(database=database) as session:
            result = session.run(cypher, parameters=merged_params)
            return [record.data() for record in result]

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> Dict[str, Any]:
        if database != "neo4j":
            validate_database_name(database)
        merged_params = dict(params or {})
        if workspace_id is not None and "workspace_id" not in merged_params:
            merged_params["workspace_id"] = workspace_id
        if enforce_workspace_filter and "$workspace_id" not in cypher:
            raise WorkspaceFilterMissingError(cypher)
        with self._driver.session(database=database) as session:
            result = session.run(cypher, parameters=merged_params)
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
        strict: bool = False,
        transactional: bool = False,
    ) -> Dict[str, Any]:
        """Apply ontology constraints to a Neo4j database.

        seocho-c2ck: when ``transactional=True``, all statements execute
        inside a single ``begin_transaction`` block — any statement
        failing rolls back the entire migration so the database is never
        left in a mixed-version state.

        Default ``transactional=False`` preserves the per-statement
        behaviour for back-compat (some Neo4j configurations forbid DDL
        inside transactions; opt in only when you've verified your
        deployment supports it).
        """
        stmts = ontology.to_cypher_constraints()
        summary = {"success": 0, "errors": []}

        with self._driver.session(database=database) as session:
            if transactional:
                # All-or-nothing: atomic schema migration.
                tx = session.begin_transaction()
                try:
                    for stmt in stmts:
                        tx.run(stmt)
                        summary["success"] += 1
                    tx.commit()
                except Exception as exc:
                    try:
                        tx.rollback()
                    except Exception as rollback_exc:
                        # A failed rollback can leave the transaction in an
                        # unknown state; swallowing it silently makes that
                        # impossible to diagnose. Log it, but still surface the
                        # original error below.
                        logger.warning(
                            "rollback failed after ensure_constraints error: %s",
                            rollback_exc,
                        )
                    # Reset success counter — the rollback undid everything
                    # that successfully ran in this transaction.
                    summary["success"] = 0
                    summary["errors"].append(
                        f"transactional ensure_constraints rolled back: {exc}"
                    )
            else:
                for stmt in stmts:
                    try:
                        session.run(stmt)
                        summary["success"] += 1
                    except Exception as exc:
                        summary["errors"].append(f"{stmt}: {exc}")

        # seocho-hvoe: opt-in loud failure — back-compat default is False.
        if strict and summary["errors"]:
            raise EnsureConstraintsError(summary)
        return summary

    @staticmethod
    def _schema_cache_key(database: str, workspace_id: str = "default") -> str:
        """Composite cache key — seocho-ni4u: workspace-aware invalidation."""
        return f"{database}::{workspace_id or 'default'}"

    def get_schema(
        self,
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
    ) -> Dict[str, Any]:
        # seocho-ni4u: cache key now includes workspace_id so two workspaces
        # sharing a database don't see each other's stale schema.
        key = self._schema_cache_key(database, workspace_id)
        now = time.monotonic()
        cached_ts = self._schema_cache_ts.get(key, 0.0)
        if key in self._schema_cache and (now - cached_ts) < self._schema_cache_ttl:
            return self._schema_cache[key]

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
            self._schema_cache[key] = schema
            self._schema_cache_ts[key] = now
            return schema
        except Exception as exc:
            logger.warning("get_schema failed for database '%s': %s", database, exc)
            return {"labels": [], "relationship_types": [], "property_keys": []}

    @staticmethod
    def _interpret_label_probe(probe_count: int, sample_limit: int) -> tuple[int, bool]:
        """Decide whether a LIMIT-bounded count is exact or sampled (F7).

        ``probe_count`` is the result of ``... WITH n LIMIT sample_limit
        RETURN count(n)``. If it's below the limit the whole
        workspace-label fit inside the sample, so the count is exact. If
        it reached the limit there are at least ``sample_limit`` matches
        and we report the limit as a lower bound, flagged sampled.

        Returns ``(value, is_sampled)``.
        """
        if probe_count >= sample_limit:
            return sample_limit, True
        return probe_count, False

    def get_index_stats(
        self,
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        sample_limit: int = _LABEL_COUNT_SAMPLE_LIMIT,
    ) -> Dict[str, Any]:
        """Return SHOW INDEXES + per-label/rel cardinality for the workspace.

        Feeds the GOPTS cost model (ADR-0097). Cached with the same
        TTL/composite-key shape as get_schema(). Workspace-scoped via
        $workspace_id filter on each count query per CLAUDE.md §6.1.

        F7 (seocho-zgxs): per-label/per-rel counts are bounded by
        ``sample_limit`` so a huge label can't turn the refresh into a
        full scan. ``label_counts`` / ``rel_counts`` stay plain int maps
        (cost model reads them unchanged); the additive
        ``label_count_meta`` / ``rel_count_meta`` maps carry the
        ``sampled`` flag and ``sample_limit`` for callers that care.
        """
        key = self._schema_cache_key(database, workspace_id)
        now = time.monotonic()
        cached_ts = self._index_stats_cache_ts.get(key, 0.0)
        if key in self._index_stats_cache and (now - cached_ts) < self._schema_cache_ttl:
            return self._index_stats_cache[key]

        try:
            with self._driver.session(database=database) as session:
                indexes: List[Dict[str, Any]] = []
                try:
                    rows = session.run(
                        "SHOW INDEXES YIELD name, type, state, entityType, "
                        "labelsOrTypes, properties RETURN name, type, state, "
                        "entityType, labelsOrTypes, properties"
                    )
                    for r in rows:
                        indexes.append({
                            "name": r["name"],
                            "type": r["type"],
                            "state": r["state"],
                            "entity_type": r.get("entityType"),
                            "labels_or_types": list(r.get("labelsOrTypes") or []),
                            "properties": list(r.get("properties") or []),
                        })
                except Exception as exc:
                    logger.warning("SHOW INDEXES failed for '%s': %s", database, exc)

                label_counts: Dict[str, int] = {}
                label_count_meta: Dict[str, Dict[str, Any]] = {}
                for r in session.run("CALL db.labels()"):
                    label = r["label"]
                    if not is_valid_identifier(label):
                        logger.warning("skipping non-identifier label '%s'", label)
                        continue
                    try:
                        # F7: LIMIT-bounded probe caps the scan at sample_limit.
                        count_rec = session.run(
                            # label passed is_valid_identifier above; interpolated raw
                            f"MATCH (n:{label}) "
                            "WHERE n._workspace_id = $workspace_id "
                            "WITH n LIMIT $sample_limit "
                            "RETURN count(n) AS cnt",
                            workspace_id=workspace_id,
                            sample_limit=sample_limit,
                        ).single()
                        probe = int(count_rec["cnt"]) if count_rec else 0
                        value, sampled = self._interpret_label_probe(probe, sample_limit)
                        label_counts[label] = value
                        label_count_meta[label] = {
                            "value": value,
                            "sampled": sampled,
                            "sample_limit": sample_limit,
                        }
                    except Exception as exc:
                        logger.warning("label count failed for '%s': %s", label, exc)

                rel_counts: Dict[str, int] = {}
                rel_count_meta: Dict[str, Dict[str, Any]] = {}
                for r in session.run("CALL db.relationshipTypes()"):
                    rt = r["relationshipType"]
                    if not is_valid_identifier(rt):
                        logger.warning("skipping non-identifier rel type '%s'", rt)
                        continue
                    try:
                        count_rec = session.run(
                            # rt passed is_valid_identifier above; interpolated raw
                            f"MATCH ()-[r:{rt}]->() "
                            "WHERE r._workspace_id = $workspace_id "
                            "WITH r LIMIT $sample_limit "
                            "RETURN count(r) AS cnt",
                            workspace_id=workspace_id,
                            sample_limit=sample_limit,
                        ).single()
                        probe = int(count_rec["cnt"]) if count_rec else 0
                        value, sampled = self._interpret_label_probe(probe, sample_limit)
                        rel_counts[rt] = value
                        rel_count_meta[rt] = {
                            "value": value,
                            "sampled": sampled,
                            "sample_limit": sample_limit,
                        }
                    except Exception as exc:
                        logger.warning("rel count failed for '%s': %s", rt, exc)

            payload = {
                "indexes": indexes,
                "label_counts": label_counts,
                "rel_counts": rel_counts,
                "label_count_meta": label_count_meta,
                "rel_count_meta": rel_count_meta,
            }
            self._index_stats_cache[key] = payload
            self._index_stats_cache_ts[key] = now
            return payload
        except Exception as exc:
            logger.warning("get_index_stats failed for '%s': %s", database, exc)
            return {
                "indexes": [],
                "label_counts": {},
                "rel_counts": {},
                "label_count_meta": {},
                "rel_count_meta": {},
            }

    def invalidate_schema_cache(
        self,
        database: Optional[str] = None,
        *,
        workspace_id: Optional[str] = None,
    ) -> None:
        """Clear the schema cache.

        - ``invalidate_schema_cache()`` clears everything.
        - ``invalidate_schema_cache(database)`` clears every workspace under
          that database (back-compat: callers that didn't pass workspace_id
          want to invalidate broadly when they wrote anything).
        - ``invalidate_schema_cache(database, workspace_id=...)`` clears
          exactly that (database, workspace) pair (per seocho-ni4u).
        """
        if database is None and workspace_id is None:
            self._schema_cache.clear()
            self._schema_cache_ts.clear()
            self._index_stats_cache.clear()
            self._index_stats_cache_ts.clear()
            return
        if database is not None and workspace_id is not None:
            key = self._schema_cache_key(database, workspace_id)
            self._schema_cache.pop(key, None)
            self._schema_cache_ts.pop(key, None)
            self._index_stats_cache.pop(key, None)
            self._index_stats_cache_ts.pop(key, None)
            return
        # Partial key — drop every entry whose composite key starts with database::
        if database is not None:
            prefix = f"{database}::"
            stale = [k for k in self._schema_cache if k.startswith(prefix)]
            for k in stale:
                self._schema_cache.pop(k, None)
                self._schema_cache_ts.pop(k, None)
            stale_stats = [k for k in self._index_stats_cache if k.startswith(prefix)]
            for k in stale_stats:
                self._index_stats_cache.pop(k, None)
                self._index_stats_cache_ts.pop(k, None)

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

            # issue #183: a node mentioned by several documents must survive
            # until its LAST source is deleted. First retire this source from
            # multi-source nodes (repointing _source_id when it was the
            # latest), then DETACH DELETE only sole-source nodes. Legacy
            # nodes without _sources keep the old _source_id semantics.
            try:
                session.run(
                    "MATCH (n) WHERE n._sources IS NOT NULL AND $sid IN n._sources "
                    "AND size([s IN n._sources WHERE s <> $sid]) > 0 "
                    "WITH n, [s IN n._sources WHERE s <> $sid] AS rest LIMIT 10000 "
                    "SET n._sources = rest, "
                    "    n._source_id = CASE WHEN n._source_id = $sid "
                    "THEN rest[-1] ELSE n._source_id END",
                    sid=source_id,
                )
            except Exception as exc:
                summary["errors"].append(f"Source retire: {exc}")

            try:
                result = session.run(
                    "MATCH (n) WHERE (n._sources IS NULL AND n._source_id = $sid) "
                    "OR (n._sources IS NOT NULL AND $sid IN n._sources "
                    "AND size([s IN n._sources WHERE s <> $sid]) = 0) "
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

    def ensure_database(self, name: str, *, wait_online: bool = True,
                        timeout: float = 30.0) -> bool:
        """Create a database if it doesn't exist, optionally waiting until ONLINE.

        DozerDB / Neo4j ``CREATE DATABASE`` is **asynchronous**: the statement
        returns before the database is queryable, so an immediate write can fail
        with "Graph not found". When ``wait_online`` is True (default), poll
        ``SHOW DATABASES`` until the database reports an online status, or until
        ``timeout`` seconds elapse.

        Validates the name against Neo4j rules first.

        Returns True if the database was created, False if it already existed.
        Raises :class:`DatabaseNameError` if the name is invalid.
        """
        validate_database_name(name)

        created = False
        try:
            with self._driver.session(database="system") as session:
                result = session.run("SHOW DATABASES")
                existing = {r["name"] for r in result}
                if name not in existing:
                    session.run(f"CREATE DATABASE {name} IF NOT EXISTS")
                    logger.info("Created database: %s", name)
                    created = True
        except Exception as exc:
            logger.warning("Could not create database '%s': %s", name, exc)
            return False

        if wait_online:
            self._wait_until_online(name, timeout=timeout)
        return created

    def _wait_until_online(self, name: str, *, timeout: float = 30.0) -> bool:
        """Poll ``SHOW DATABASES`` until ``name`` reports an online status.

        Returns True if confirmed online within ``timeout`` seconds, else False
        (logged as a warning). The poll delay goes through the module-level
        ``_sleep`` indirection so tests can run instantly.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                with self._driver.session(database="system") as session:
                    rows = list(session.run(
                        "SHOW DATABASES YIELD name, currentStatus "
                        "WHERE name = $n RETURN currentStatus AS status",
                        n=name,
                    ))
                if rows and str(rows[0]["status"]).lower() == "online":
                    return True
            except Exception as exc:
                logger.debug("ensure_database online-poll error for '%s': %s", name, exc)
            if time.monotonic() >= deadline:
                logger.warning("Database '%s' not confirmed ONLINE within %.0fs", name, timeout)
                return False
            _sleep(0.5)

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
    "linked_id",
    "title",
    "uri",
    "ticker",
    "period",
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
    "_ontology_artifact_hash",
    "_ontology_glossary_hash",
    "_ontology_id",
    "_ontology_name",
    "_ontology_version",
    "_ontology_profile",
    "_ontology_graph_model",
    "_ontology_schema_fingerprint",
    "_ontology_version_valid",
    "_out_of_ontology",
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
    "_ontology_artifact_hash",
    "_ontology_glossary_hash",
    "_ontology_id",
    "_ontology_name",
    "_ontology_version",
    "_ontology_profile",
    "_ontology_graph_model",
    "_ontology_schema_fingerprint",
    "_ontology_version_valid",
    "_out_of_ontology",
)
_LADYBUG_NODE_PROJECTION_KEYS = (
    "id",
    "name",
    "linked_id",
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
    "_ontology_artifact_hash",
    "_ontology_glossary_hash",
    "_ontology_id",
    "_ontology_name",
    "_ontology_version",
    "_ontology_profile",
    "_ontology_graph_model",
)
_LADYBUG_REL_PROJECTION_KEYS = (
    "type",
    "memory_id",
    "source_id",
    "workspace_id",
    "_workspace_id",
    "_source_id",
    "_ontology_context_hash",
    "_ontology_artifact_hash",
    "_ontology_glossary_hash",
    "_ontology_id",
    "_ontology_name",
    "_ontology_version",
    "_ontology_profile",
    "_ontology_graph_model",
)
_LADYBUG_FULLTEXT_SHOW_PATTERNS = (
    "SHOW FULLTEXT INDEXES",
    "SHOW INDEXES",
)
_LADYBUG_REL_TABLE_DELIM = "__seocho__"


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


def _ladybug_rel_table_name(rel_type: str, source_label: str, target_label: str) -> str:
    return f"{rel_type}{_LADYBUG_REL_TABLE_DELIM}{source_label}{_LADYBUG_REL_TABLE_DELIM}{target_label}"


def _ladybug_semantic_rel_type(table_name: str) -> str:
    if _LADYBUG_REL_TABLE_DELIM not in table_name:
        return table_name
    return table_name.split(_LADYBUG_REL_TABLE_DELIM, 1)[0]


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
    # seocho-g85: the financial-metric template moved its scope-token guard
    # to a soft ANY(...) ranking signal in ORDER BY; expand that form too,
    # or an empty token list reaches the engine as an untypeable [] param.
    rewritten = _ladybug_expand_param_predicate(
        rewritten,
        query_params,
        pattern=(
            r"\(\$metric_scope_tokens\s*=\s*\[\]\s+OR\s+ANY\(\s*token\s+IN\s+\$metric_scope_tokens\s+WHERE\s+"
            r"toLower\(coalesce\(m\.name,\s*m\.uri,\s*''\)\)\s+CONTAINS\s+token\s*\)\s*\)"
        ),
        param_name="metric_scope_tokens",
        param_prefix="__ladybug_metric_scope_token_any",
        clause_factory=lambda key: f"toLower(coalesce(m.name, m.uri, '')) CONTAINS ${key}",
        joiner=" OR ",
    )
    rewritten = _ladybug_expand_param_predicate(
        rewritten,
        query_params,
        pattern=(
            r"\(\$years\s*=\s*\[\]\s+OR\s+ANY\(\s*year\s+IN\s+\$years\s+WHERE\s+"
            r"coalesce\(toString\(m\.year\),\s*''\)\s*=\s*year\s+OR\s+"
            r"(?:toLower\(coalesce\(toString\(m\.period\),\s*''\)\)\s+CONTAINS\s+year\s+OR\s+)?"
            r"toLower\(coalesce\(m\.name,\s*m\.uri,\s*''\)\)\s+CONTAINS\s+year\s*\)\s*\)"
        ),
        param_name="years",
        param_prefix="__ladybug_year",
        clause_factory=(
            lambda key: (
                f"(coalesce(m.year, '') = ${key} OR "
                f"toLower(coalesce(m.period, '')) CONTAINS ${key} OR "
                f"toLower(coalesce(m.name, m.uri, '')) CONTAINS ${key})"
            )
        ),
        joiner=" OR ",
    )
    # seocho-g85: real_ladybug 0.15.3 asserts (parsed_parameter_expression.h:21,
    # UNREACHABLE_CODE) when a $param appears inside a quantifier predicate
    # body. labels() is scalar in ladybug (one table per node), so the
    # membership test collapses to a top-level IN — stays fully
    # parameterized, no literal inlining.
    rewritten = re.sub(
        r"ANY\(\s*(\w+)\s+IN\s+labels\((\w+)\)\s+WHERE\s+\1\s+IN\s+(\$\w+)\s*\)",
        lambda match: f"labels({match.group(2)}) IN {match.group(3)}",
        rewritten,
        flags=re.IGNORECASE,
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
        import threading as _threading

        self._lb = _lb
        self._path = path
        _os.makedirs(_os.path.dirname(_os.path.abspath(path)) or ".", exist_ok=True)
        self._db = _lb.Database(path)
        self._conn = _lb.Connection(self._db)
        # seocho-sdtq: Ladybug's Connection is not thread-safe — concurrent
        # writes from multiple threads can corrupt or interleave statements.
        # An RLock is sufficient because seocho's hot path is mostly
        # write-then-query, not high-contention read parallelism. Real
        # contention (e.g. ThreadPoolExecutor extraction in
        # runtime/runtime_ingest.py) serialises through this lock; other
        # patterns (one Session per request) see no contention.
        self._conn_lock = _threading.RLock()
        self._declared_node_tables: set = set()
        # seocho-sdtq helper installed below init via class method.
        self._declared_rel_tables: set = set()
        self._semantic_rel_types: set = set()
        self._rel_signature_to_table: Dict[tuple[str, str, str], str] = {}
        # seocho-8ct: node-table PRIMARY KEY column per label. MERGE must key
        # on the declared PK — keying on ``id`` while the PK is a unique
        # ontology property (usually ``name``) turns every cross-document
        # re-mention of an entity into a duplicate-PK write failure.
        self._node_table_pk: Dict[str, str] = {}
        # issue #183: node tables verified to carry the _sources column.
        self._sources_column_ready: set = set()
        self._load_existing_schema()

    def _locked_execute(self, *args, **kwargs):
        """Thread-safe wrapper around self._conn.execute (seocho-sdtq).

        The Ladybug ``Connection`` is not safe for concurrent use; serialising
        through this lock prevents cross-thread interleaving on a single
        store instance. Reads and writes share the same lock because Ladybug
        does not separate reader / writer connections.
        """
        with self._conn_lock:
            return self._conn.execute(*args, **kwargs)

    def _load_existing_schema(self) -> None:
        """Populate declared-table sets from the existing database."""
        try:
            result = self._locked_execute("CALL show_tables() RETURN *")
            for row in result:
                row_list = row if isinstance(row, list) else list(row)
                if len(row_list) >= 2:
                    table_name = str(row_list[1])
                    table_type = str(row_list[2]).upper() if len(row_list) > 2 else ""
                    if "NODE" in table_type:
                        self._declared_node_tables.add(table_name)
                        self._node_table_pk[table_name] = self._discover_table_pk(table_name)
                    elif "REL" in table_type:
                        self._declared_rel_tables.add(table_name)
                        self._semantic_rel_types.add(_ladybug_semantic_rel_type(table_name))
        except Exception:
            # CALL show_tables() may not be supported; ignore
            pass

    def _discover_table_pk(self, table_name: str) -> str:
        """Return the PRIMARY KEY column of an existing node table.

        ``table_info`` rows end with a primary-key flag; if the call or the
        shape is unsupported, fall back to ``id`` (the lazy-declared default).
        """
        try:
            result = self._locked_execute(f"CALL table_info('{table_name}') RETURN *")
            for row in result:
                row_list = row if isinstance(row, list) else list(row)
                if len(row_list) >= 2 and bool(row_list[-1]):
                    return str(row_list[1])
        except Exception:
            pass
        return "id"

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
            self._locked_execute(
                f"CREATE NODE TABLE IF NOT EXISTS `{label}` ({col_list}, PRIMARY KEY (`{pk}`))"
            )
            self._declared_node_tables.add(label)
            self._node_table_pk[label] = pk
        except Exception as exc:
            logger.warning("Failed to create node table %s: %s", label, exc)

    def _ensure_rel_table(
        self,
        rel_type: str,
        source_label: str,
        target_label: str,
        sample_props: Dict[str, Any],
    ) -> Optional[str]:
        if not _LABEL_RE.match(rel_type):
            return None
        if source_label not in self._declared_node_tables:
            return None
        if target_label not in self._declared_node_tables:
            return None
        signature = (rel_type, source_label, target_label)
        if signature in self._rel_signature_to_table:
            return self._rel_signature_to_table[signature]

        if rel_type in self._declared_rel_tables:
            physical_name = _ladybug_rel_table_name(rel_type, source_label, target_label)
        else:
            physical_name = rel_type
        cols: List[str] = []
        for key, value in (sample_props or {}).items():
            if not _LABEL_RE.match(key):
                continue
            cols.append(f"`{key}` {_lbug_type(value)}")
        _append_ladybug_string_columns(cols, _LADYBUG_COMMON_REL_STRING_COLUMNS)
        col_list = (", " + ", ".join(cols)) if cols else ""
        if physical_name not in self._declared_rel_tables:
            try:
                self._locked_execute(
                    f"CREATE REL TABLE IF NOT EXISTS `{physical_name}`(FROM `{source_label}` TO `{target_label}`{col_list})"
                )
                self._declared_rel_tables.add(physical_name)
            except Exception as exc:
                logger.warning("Failed to create rel table %s: %s", physical_name, exc)
                return None
        self._semantic_rel_types.add(rel_type)
        self._rel_signature_to_table[signature] = physical_name
        return physical_name

    # issue #183: Ladybug coerces string values that LOOK like JSON arrays
    # ('["a"]' comes back as '[a]'), so the JSON list is stored behind a
    # "json:" prefix, which round-trips verbatim.
    _SOURCES_PREFIX = "json:"

    @classmethod
    def _encode_sources(cls, sources: List[str]) -> str:
        return cls._SOURCES_PREFIX + json.dumps(sources)

    @classmethod
    def _decode_sources(cls, raw: Any) -> List[str]:
        text = str(raw or "")
        if text.startswith(cls._SOURCES_PREFIX):
            text = text[len(cls._SOURCES_PREFIX):]
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(s) for s in parsed if s]
        return []

    def _ensure_sources_column(self, label: str) -> None:
        """Make sure the node table carries the ``_sources`` column.

        Tables created before issue #183 lack it; Ladybug supports
        ``ALTER TABLE ... ADD`` so it is added lazily, once per label.
        """
        if label in self._sources_column_ready:
            return
        try:
            self._locked_execute(f"ALTER TABLE `{label}` ADD `_sources` STRING")
        except Exception:
            # Column already exists (new tables declare it) — fine either way.
            pass
        self._sources_column_ready.add(label)

    def _accumulated_sources_json(
        self, label: str, merge_col: str, merge_value: Any, source_id: str
    ) -> str:
        """Existing ``_sources`` of the upsert target plus ``source_id``."""
        sources: List[str] = []
        try:
            rows = self._locked_execute(
                f"MATCH (n:`{label}` {{`{merge_col}`: $v}}) "
                "RETURN n._sources, n._source_id",
                {"v": merge_value},
            )
            for row in rows:
                row_list = row if isinstance(row, list) else list(row)
                existing_json = row_list[0] if row_list else None
                existing_single = row_list[1] if len(row_list) > 1 else None
                if existing_json:
                    sources = self._decode_sources(existing_json)
                if not sources and existing_single:
                    # Legacy node written before #183: seed from _source_id.
                    sources = [str(existing_single)]
                break
        except Exception:
            # No such node / legacy table — no history to merge.
            pass
        if source_id and source_id not in sources:
            sources.append(source_id)
        return self._encode_sources(sources)

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

            # seocho-8ct: MERGE on the table's declared PRIMARY KEY. When the
            # PK is a unique ontology property (usually ``name``), keying on
            # ``id`` misses cross-document re-mentions of the same entity
            # (LLM-generated ids differ per document) and the CREATE then
            # violates the PK constraint, failing the whole file. The engine
            # also rejects SET on the PK column, so it must stay out of the
            # SET map (which previously pushed every MERGE into the CREATE
            # fallback path).
            pk_col = self._node_table_pk.get(label, "id")
            if pk_col != "id" and props.get(pk_col):
                merge_col, merge_value = pk_col, props[pk_col]
            else:
                merge_col, merge_value = "id", node_id
            # issue #183: accumulate multi-document provenance. _source_id
            # stays single-valued (latest writer — keeps the count/delete
            # filters working) while _sources is a JSON-encoded list of every
            # document that mentioned this node.
            self._ensure_sources_column(label)
            props["_sources"] = self._accumulated_sources_json(
                label, merge_col, merge_value, source_id
            )
            prop_keys = [k for k in props if _LABEL_RE.match(k)]
            set_keys = [k for k in prop_keys if k != merge_col]
            set_clause = ", ".join(f"`{k}`: $p_{i}" for i, k in enumerate(set_keys))
            set_params = {f"p_{i}": props[k] for i, k in enumerate(set_keys)}
            create_clause = ", ".join(f"`{k}`: $c_{i}" for i, k in enumerate(prop_keys))
            create_params = {f"c_{i}": props[k] for i, k in enumerate(prop_keys)}
            statement = f"MERGE (n:`{label}` {{`{merge_col}`: $merge_key}})"
            if set_clause:
                statement += f" SET n = {{{set_clause}}}"
            try:
                self._locked_execute(statement, {"merge_key": merge_value, **set_params})
                summary["nodes_created"] += 1
            except Exception:
                # Fallback: CREATE if MERGE dialect differs
                try:
                    self._locked_execute(
                        f"CREATE (n:`{label}` {{{create_clause}}})",
                        create_params,
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

            physical_rtype = self._ensure_rel_table(rtype, src_label, tgt_label, rprops)
            if physical_rtype is None:
                summary["errors"].append(
                    f"Rel {src_id}-[{rtype}]->{tgt_id}: unable to declare rel table"
                )
                continue

            prop_keys = [k for k in rprops if _LABEL_RE.match(k)]
            set_clause = (
                "{" + ", ".join(f"`{k}`: $p_{i}" for i, k in enumerate(prop_keys)) + "}"
                if prop_keys else ""
            )
            params = {"src": src_id, "tgt": tgt_id,
                      **{f"p_{i}": rprops[k] for i, k in enumerate(prop_keys)}}
            try:
                self._locked_execute(
                    f"MATCH (a:`{src_label}` {{id: $src}}), (b:`{tgt_label}` {{id: $tgt}}) "
                    f"CREATE (a)-[r:`{physical_rtype}`{(' ' + set_clause) if set_clause else ''}]->(b)",
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
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> List[Dict[str, Any]]:
        # seocho-y4at: same workspace_id contract as Neo4jGraphStore.query.
        merged_params = dict(params or {})
        if workspace_id is not None and "workspace_id" not in merged_params:
            merged_params["workspace_id"] = workspace_id
        if enforce_workspace_filter and "$workspace_id" not in cypher:
            raise WorkspaceFilterMissingError(cypher)
        params = merged_params
        compact = " ".join(str(cypher).upper().split())
        if any(compact.startswith(pattern) for pattern in _LADYBUG_FULLTEXT_SHOW_PATTERNS):
            return []
        if "CALL DB.INDEX.FULLTEXT.QUERYNODES" in compact:
            return []
        cypher, query_params = _rewrite_ladybug_query(cypher, params=params)
        try:
            result = self._locked_execute(cypher, query_params)
            out: List[Dict[str, Any]] = []
            # Ladybug returns rows as lists; convert to dicts using column names.
            # real_ladybug exposes get_column_names() as a method (not an
            # attribute) — calling it preserves user-supplied RETURN aliases
            # instead of falling through to positional col_0/col_1 keys.
            col_names_getter = getattr(result, "get_column_names", None)
            col_names: List[str] = []
            if callable(col_names_getter):
                try:
                    col_names = list(col_names_getter())
                except Exception:
                    col_names = []
            if not col_names:
                col_names = list(getattr(result, "column_names", None) or [])
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
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> Dict[str, Any]:
        # seocho-y4at: workspace-scoped write parameter merge + opt-in enforcement.
        merged_params = dict(params or {})
        if workspace_id is not None and "workspace_id" not in merged_params:
            merged_params["workspace_id"] = workspace_id
        if enforce_workspace_filter and "$workspace_id" not in cypher:
            raise WorkspaceFilterMissingError(cypher)
        params = merged_params
        try:
            self._locked_execute(cypher, params or {})
            return {"nodes_affected": 0, "relationships_affected": 0, "properties_set": 0}
        except Exception as exc:
            return {"error": str(exc)}

    def ensure_constraints(
        self,
        ontology: Ontology,
        *,
        database: str = "neo4j",
        strict: bool = False,
        transactional: bool = False,
    ) -> Dict[str, Any]:
        """Create NODE/REL tables declared by the ontology.

        ``strict`` (seocho-hvoe): when True, raise ``EnsureConstraintsError``
        if any individual table-create fails. Default False keeps the
        partial-success summary for back-compat.

        ``transactional`` (seocho-c2ck): no-op for LadybugGraphStore at
        the moment — the embedded engine does not expose explicit
        transaction control through the seocho contract. Accepted for
        API parity with Neo4jGraphStore.
        """
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
            # seocho-uxs: with a composite identity declared, no single
            # property is the identity — key on the synthesized composite
            # ``id`` (the pipeline rewrites it to label|v1|v2|...), so two
            # entities sharing one member (e.g. name) do not collapse.
            if getattr(node_def, "identity_keys", None):
                pk_col = "id"
            pk_col = pk_col or "id"

            try:
                self._locked_execute(
                    f"CREATE NODE TABLE IF NOT EXISTS `{label}` "
                    f"({', '.join(cols)}, PRIMARY KEY (`{pk_col}`))"
                )
                self._declared_node_tables.add(label)
                self._node_table_pk.setdefault(label, pk_col)
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
                self._locked_execute(
                    f"CREATE REL TABLE IF NOT EXISTS `{rtype}`"
                    f"(FROM `{src}` TO `{tgt}`, {', '.join(f'`{name}` STRING' for name in _LADYBUG_COMMON_REL_STRING_COLUMNS)})"
                )
                self._declared_rel_tables.add(rtype)
                self._semantic_rel_types.add(rtype)
                self._rel_signature_to_table[(rtype, src, tgt)] = rtype
                summary["success"] += 1
            except Exception as exc:
                summary["errors"].append(f"Rel table {rtype}: {exc}")

        # seocho-hvoe: opt-in loud failure for Ladybug too.
        if strict and summary["errors"]:
            raise EnsureConstraintsError(summary)
        return summary

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        return {
            "labels": sorted(self._declared_node_tables),
            "relationship_types": sorted(self._semantic_rel_types or self._declared_rel_tables),
            "property_keys": [],
        }

    def delete_by_source(self, source_id: str, *, database: str = "neo4j") -> Dict[str, Any]:
        before = self.count_by_source(source_id, database=database)
        summary = {
            "nodes_deleted": before["nodes"],
            "relationships_deleted": before["relationships"],
            "errors": [],
        }

        try:
            self._locked_execute(
                "MATCH ()-[r]->() WHERE r._source_id = $sid DELETE r",
                {"sid": source_id},
            )
        except Exception as exc:
            summary["errors"].append(f"relationship delete: {exc}")

        # issue #183: a node mentioned by several documents must survive
        # until its LAST source is deleted. Per node table: retire this
        # source from multi-source nodes (repointing _source_id when it was
        # the latest writer), DETACH DELETE only sole-source nodes. Legacy
        # nodes without _sources keep the old _source_id semantics.
        nodes_deleted = 0
        needle = json.dumps(source_id)  # JSON-quoted match inside the list
        for label in sorted(self._declared_node_tables):
            pk_col = self._node_table_pk.get(label, "id")
            if not (_LABEL_RE.match(label) and _LABEL_RE.match(pk_col)):
                continue
            try:
                rows = self._locked_execute(
                    f"MATCH (n:`{label}`) WHERE n._source_id = $sid "
                    "OR (n._sources IS NOT NULL AND n._sources CONTAINS $needle) "
                    f"RETURN n.`{pk_col}`, n._sources",
                    {"sid": source_id, "needle": needle},
                )
                pending = [row if isinstance(row, list) else list(row) for row in rows]
            except Exception:
                # Legacy table without the _sources column.
                try:
                    rows = self._locked_execute(
                        f"MATCH (n:`{label}`) WHERE n._source_id = $sid "
                        f"RETURN n.`{pk_col}`",
                        {"sid": source_id},
                    )
                    pending = [
                        [(row if isinstance(row, list) else list(row))[0], None]
                        for row in rows
                    ]
                except Exception as exc:
                    summary["errors"].append(f"{label} scan: {exc}")
                    continue

            for key_value, sources_json in pending:
                remaining = [
                    s for s in self._decode_sources(sources_json) if s != source_id
                ]
                if remaining:
                    try:
                        self._locked_execute(
                            f"MATCH (n:`{label}` {{`{pk_col}`: $k}}) "
                            "SET n._sources = $srcs, n._source_id = $latest",
                            {"k": key_value, "srcs": self._encode_sources(remaining),
                             "latest": remaining[-1]},
                        )
                    except Exception as exc:
                        summary["errors"].append(f"{label} retire: {exc}")
                else:
                    try:
                        self._locked_execute(
                            f"MATCH (n:`{label}` {{`{pk_col}`: $k}}) DETACH DELETE n",
                            {"k": key_value},
                        )
                        nodes_deleted += 1
                    except Exception as exc:
                        summary["errors"].append(f"{label} delete: {exc}")

        after = self.count_by_source(source_id, database=database)
        # Retired (multi-source) nodes survive on purpose — count only the
        # physical deletions, not the before/after _source_id diff.
        summary["nodes_deleted"] = nodes_deleted
        summary["relationships_deleted"] = max(0, before["relationships"] - after["relationships"])
        return summary

    def count_by_source(self, source_id: str, *, database: str = "neo4j") -> Dict[str, int]:
        node_total = 0
        relationship_total = 0

        for label in self._declared_node_tables:
            try:
                result = self._locked_execute(
                    f"MATCH (n:`{label}`) WHERE n._source_id = $sid RETURN count(n)",
                    {"sid": source_id},
                )
                for row in result:
                    node_total += int(row[0] if isinstance(row, list) else list(row)[0])
            except Exception:
                pass

        try:
            result = self._locked_execute(
                "MATCH ()-[r]->() WHERE r._source_id = $sid RETURN count(r)",
                {"sid": source_id},
            )
            for row in result:
                relationship_total += int(row[0] if isinstance(row, list) else list(row)[0])
        except Exception:
            pass

        return {"nodes": node_total, "relationships": relationship_total}

    def close(self) -> None:
        try:
            del self._conn
            del self._db
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"LadybugGraphStore(path={self._path!r})"
