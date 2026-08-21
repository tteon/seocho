"""
Graph store abstraction — pluggable backend for writing and querying
knowledge graphs.

Ships with :class:`Neo4jGraphStore` for DozerDB / Neo4j.

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

import json
import logging
import os
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


class WorkspaceScopeViolationError(ValueError):
    """Raised by the governed read path when a query would escape workspace
    scope or read-safety despite naming ``$workspace_id``.

    Distinct from ``WorkspaceFilterMissingError`` (which only means the token
    is absent): this fires when the token is present but the surrounding Cypher
    smuggles a widening tautology, a write, or a procedure call past the naive
    substring check that the security review (2026-08-15) demonstrated was
    bypassable via ``... OR true`` and comment-embedded ``$workspace_id``.

    NOTE (honest scope): this is a defense-in-depth *blocklist* run after
    comment stripping, not a proof of workspace binding. A blocklist cannot be
    complete; the sound fix is parse/AST-level verification that every returned
    binding is constrained to ``_workspace_id = $workspace_id``, or DB-side
    per-workspace databases/credentials. Tracked as a follow-up ticket.
    """

    def __init__(self, cypher: str, reason: str) -> None:
        super().__init__(
            f"Cypher rejected by governed read path ({reason}); refusing to run "
            "with enforce_workspace_filter=True."
        )
        self.cypher = cypher
        self.reason = reason


# Line (// ...) and block (/* ... */) comment strippers. Comment smuggling —
# `MATCH (n) RETURN n /* $workspace_id */` — otherwise satisfies the token
# check while contributing nothing to scoping.
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Widening tautologies that neutralize a WHERE scope even when $workspace_id is
# named elsewhere. Matched on comment-stripped, whitespace-normalized, upper-
# cased text. Defense-in-depth, not exhaustive.
_TAUTOLOGY_RE = re.compile(
    r"\bOR\b\s+(?:TRUE\b|\d+\s*=\s*\d+|'[^']*'\s*=\s*'[^']*'|\d+\b(?!\s*=))"
)

# Write/procedure tokens matched on word boundaries (not space-padding, which
# `CALL{CREATE...}` slipped) over comment-stripped uppercased text. apoc/n10s
# write-capable procedures are refused on the read path; the ontology guardrail
# owns the fine-grained allow-list.
_WRITE_TOKEN_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b"
)
_PROC_CALL_RE = re.compile(r"\bCALL\b")


def _strip_cypher_comments(cypher: str) -> str:
    return _BLOCK_COMMENT_RE.sub(" ", _LINE_COMMENT_RE.sub(" ", cypher))


# Constructs that rebind variables (WITH), fuse result sets (UNION), or expand
# rows (UNWIND). Binding verification below cannot reason about them safely, so
# it declines to analyze such queries rather than risk a false rejection — it
# only ever ADDS rejections for queries it can positively prove are unscoped.
_REBIND_CONSTRUCTS_RE = re.compile(r"\b(WITH|UNION|UNWIND)\b", re.IGNORECASE)
# MATCH clause body up to the next clause keyword (where node patterns bind).
_MATCH_SEGMENT_RE = re.compile(
    r"\bMATCH\b(.*?)(?=\b(?:WHERE|RETURN|WITH|MATCH|OPTIONAL|ORDER|SKIP|LIMIT|"
    r"UNION|CALL|UNWIND)\b|$)", re.IGNORECASE | re.DOTALL)
# A node variable is the identifier immediately after a pattern '(' .
_NODE_VAR_RE = re.compile(r"\(\s*([A-Za-z_]\w*)")
_RETURN_CLAUSE_RE = re.compile(
    r"\bRETURN\b(.*?)(?=\b(?:ORDER|SKIP|LIMIT|UNION)\b|$)",
    re.IGNORECASE | re.DOTALL)
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def _bound_node_vars(stripped: str) -> set:
    vars_: set = set()
    for seg in _MATCH_SEGMENT_RE.finditer(stripped):
        vars_.update(_NODE_VAR_RE.findall(seg.group(1)))
    return vars_


def _var_is_workspace_scoped(var: str, stripped: str) -> bool:
    # WHERE style: var._workspace_id = $workspace_id
    if re.search(rf"\b{re.escape(var)}\._workspace_id\s*=\s*\$workspace_id",
                 stripped):
        return True
    # Inline pattern style: (var ... {_workspace_id: $workspace_id})
    if re.search(rf"\(\s*{re.escape(var)}\b[^()]*\{{[^}}]*_workspace_id\s*:\s*"
                 r"\$workspace_id", stripped):
        return True
    return False


def verify_workspace_binding(cypher: str) -> None:
    """Best-effort proof that every RETURNed node is scoped to the workspace.

    Closes the class the token-presence check misses: ``$workspace_id`` appears
    but constrains the *wrong* node, e.g. ``MATCH (n),(m) WHERE m._workspace_id
    = $workspace_id RETURN n`` — token present, ``n`` returned unscoped. This
    finds the node variables bound in MATCH, the node variables named in RETURN,
    and rejects when a returned node variable has no ``_workspace_id =
    $workspace_id`` binding (WHERE or inline).

    Conservative by construction: if the query rebinds variables (WITH / UNION /
    UNWIND) it is not analyzed — declined, never falsely rejected. So this only
    ADDS rejections for queries it can positively prove unscoped; it never
    refuses a query it cannot understand. Still NOT a full AST proof (see
    ``WorkspaceScopeViolationError``); the sound form is DB-side per-workspace
    enforcement.
    """
    stripped = _strip_cypher_comments(cypher)
    if _REBIND_CONSTRUCTS_RE.search(stripped):
        return
    ret = _RETURN_CLAUSE_RE.search(stripped)
    if not ret:
        return
    bound = _bound_node_vars(stripped)
    if not bound:
        return
    returned_idents = set(_IDENT_RE.findall(ret.group(1)))
    for var in bound & returned_idents:
        if not _var_is_workspace_scoped(var, stripped):
            raise WorkspaceScopeViolationError(cypher, "unbound_return")


def enforce_read_workspace_scope(cypher: str) -> None:
    """Gate a query on the governed read path.

    Order matters: strip comments first, then require the ``$workspace_id``
    token in the *stripped* text (so comment-embedded tokens don't count), then
    reject widening tautologies, writes, and procedure calls, then verify that
    the token actually binds the returned nodes. Raises
    ``WorkspaceFilterMissingError`` or ``WorkspaceScopeViolationError``.

    This does not prove the query is scope-safe (see
    ``WorkspaceScopeViolationError``); it removes the specific bypasses the
    security review found and pairs with the driver-level READ access mode.
    """
    stripped = _strip_cypher_comments(cypher)
    if "$workspace_id" not in stripped:
        raise WorkspaceFilterMissingError(cypher)
    normalized = " " + re.sub(r"\s+", " ", stripped.upper()) + " "
    if _TAUTOLOGY_RE.search(normalized):
        raise WorkspaceScopeViolationError(cypher, "widening_tautology")
    if _WRITE_TOKEN_RE.search(normalized):
        raise WorkspaceScopeViolationError(cypher, "write_on_read_path")
    if _PROC_CALL_RE.search(normalized):
        raise WorkspaceScopeViolationError(cypher, "procedure_call_on_read_path")
    verify_workspace_binding(cypher)


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
            Callers that know endpoint types should also supply
            ``source_label`` and ``target_label``. Neo4j-compatible backends
            then use label-specific indexes instead of global node scans.
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
_packstream_codec: Optional[str] = None


def packstream_codec() -> str:
    """Active PackStream codec: ``rust-ext`` | ``pure-python`` | ``unknown``.

    ADR-0111: the ``neo4j-rust-ext`` codec is an install-time drop-in whose win
    is result hydration. ADR-0144 stamps this on ``db.query`` spans so a silent
    fallback to pure-python (e.g. a lost wheel) shows up as elevated hydration
    latency in traces instead of going unnoticed. Cached after first probe.
    """
    global _packstream_codec
    if _packstream_codec is not None:
        return _packstream_codec
    try:
        from neo4j._codec.packstream import RUST_AVAILABLE
        _packstream_codec = "rust-ext" if RUST_AVAILABLE else "pure-python"
    except ImportError:  # private flag moved — report honestly, don't guess
        _packstream_codec = "unknown"
    return _packstream_codec


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
    logger.info("neo4j packstream codec: %s active", packstream_codec())


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
        self._closed = False
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
        # The Python SDK remains the ontology/policy control plane.  When this
        # explicit opt-in socket is configured, the canonical DozerDB projection
        # crosses the OS boundary to the Rust Bolt driver and fails closed.
        # Reading, query safety, and schema management remain in this adapter.
        rust_socket = os.environ.get("SEOCHO_RUST_PROJECTOR_SOCKET")
        if rust_socket:
            if graph_model == "rdf" and triples:
                raise RuntimeError("seochod supports approved LPG projection only")
            from ..dataplane.seochod import SeochodProjectionClient
            from ..ontology.projection_receipt import (
                load_projection_admission_from_env,
                load_projection_receipt_from_env,
            )

            result = SeochodProjectionClient(rust_socket).project(
                nodes,
                relationships,
                database=database,
                workspace_id=workspace_id,
                source_id=source_id,
                semantic_receipt=load_projection_receipt_from_env(),
                admission=load_projection_admission_from_env(),
            )
            self.invalidate_schema_cache(database)
            return {
                "nodes_created": result.get("nodes_created", 0),
                "relationships_created": result.get("relationships_created", 0),
                "errors": result.get("errors", []),
                "merge_conflicts": result.get("merge_conflicts", []),
                "driver": result.get("driver", "rust-neo4j"),
            }

        # Validate database name (skip for default 'neo4j')
        if database != "neo4j":
            validate_database_name(database)

        # RDF mode: write triples via n10s
        if graph_model == "rdf" and triples:
            return self._write_rdf(triples, database=database, source_id=source_id)

        # seocho-uxs.1: merge_conflicts surfaces value divergence when a MERGE
        # lands on an existing node whose user-facing property already holds a
        # different value (audit signal for silent overwrites).
        summary = {
            "nodes_created": 0,
            "relationships_created": 0,
            "errors": [],
            "merge_conflicts": [],
        }

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
        # Extraction-local IDs (often ``c1``) are not stable across documents.
        # Prefer a declared business name when present and remap relationship
        # endpoints in this payload to the same canonical identity.  Otherwise
        # a graph-level UNIQUE(name) constraint rejects a second source that
        # describes the same company/person with a different local ID.
        canonical_ids: Dict[str, str] = {}
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
            original_id = str(node.get("id", ""))
            node_id = str(props.get("name") or original_id or props.get("id", ""))
            if original_id:
                canonical_ids[original_id] = node_id
            props["id"] = node_id
            nodes_by_label.setdefault(label, []).append({"id": node_id, "props": props})

        rels_by_type: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for rel in relationships:
            rtype = rel.get("type", "RELATED_TO")
            if not _LABEL_RE.match(rtype):
                summary["errors"].append(f"Invalid relationship type: {rtype}")
                continue
            source_label = str(rel.get("source_label", "") or "")
            target_label = str(rel.get("target_label", "") or "")
            if source_label and not _LABEL_RE.match(source_label):
                summary["errors"].append(f"Invalid source label: {source_label}")
                continue
            if target_label and not _LABEL_RE.match(target_label):
                summary["errors"].append(f"Invalid target label: {target_label}")
                continue
            props = {k: v for k, v in dict(rel.get("properties", {})).items()
                     if _is_property_value(v)}
            props["_source_id"] = source_id
            props["_workspace_id"] = workspace_id
            props["_writer_ts"] = now
            props["_writer_agent"] = source_id or "unknown"
            rels_by_type.setdefault((rtype, source_label, target_label), []).append(
                {"src": canonical_ids.get(str(rel.get("source", "")), rel.get("source", "")),
                 "tgt": canonical_ids.get(str(rel.get("target", "")), rel.get("target", "")), "props": props})

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
                # seocho-uxs.1: compute conflicts BEFORE the SET, so n[k] is
                # the pre-write value. A conflict = a user-facing (non
                # ``_``-prefixed) property whose stored non-null value differs
                # from the incoming one. ``{P}`` is the props expression in
                # scope (row.props for the batch, $props for the fallback).
                def _conflict_with(prop_expr: str, carry: str) -> str:
                    return (
                        f" WITH {carry}, ["
                        f"k IN keys({prop_expr}) WHERE NOT k STARTS WITH '_' "
                        f"AND k <> 'id' "
                        f"AND n[k] IS NOT NULL AND n[k] <> {prop_expr}[k] "
                        f"| {{property: k, existing: toString(n[k]), incoming: toString({prop_expr}[k])}}"
                        "] AS _conflicts"
                    )

                def _collect_conflicts(record: Any) -> None:
                    for c in (record["conflicts"] or []):
                        summary["merge_conflicts"].append(
                            {"label": label, "key": record["id"], **c, "source_id": source_id}
                        )

                batch_q = (
                    # seocho review-#6: scope node identity by (id, _workspace_id) so
                    # two tenants' identical id (e.g. the source-agnostic ~xs|<name>
                    # from cross-source convergence) NEVER MERGE onto one physical
                    # node in a shared graph. _workspace_id is always set on props
                    # (below), so existing nodes still match — no migration break.
                    f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id, _workspace_id: $ws}})"
                    + _conflict_with("row.props", "n, row")
                    + " SET n += CASE WHEN n._writer_ts IS NULL "
                    "OR n._writer_ts <= row.props._writer_ts THEN row.props ELSE {} END"
                    + sources_clause.format(p="row.props")
                    + " RETURN row.id AS id, _conflicts AS conflicts"
                )
                try:
                    _res = session.run(batch_q, rows=rows, ws=workspace_id)
                    for record in _res:
                        _collect_conflicts(record)
                    # Real created count, not one-per-submitted-row (a MERGE onto
                    # an existing node creates zero). audit 2026-08-17.
                    summary["nodes_created"] += _res.consume().counters.nodes_created
                except Exception:
                    for row in rows:
                        try:
                            single_q = (
                                f"MERGE (n:{label} {{id: $id, _workspace_id: $ws}})"
                                + _conflict_with("$props", "n")
                                + " SET n += CASE WHEN "
                                "n._writer_ts IS NULL OR n._writer_ts <= $props._writer_ts "
                                "THEN $props ELSE {} END"
                                + sources_clause.format(p="$props")
                                + " RETURN $id AS id, _conflicts AS conflicts"
                            )
                            _res = session.run(single_q, id=row["id"], props=row["props"], ws=workspace_id)
                            for record in _res:
                                _collect_conflicts(record)
                            summary["nodes_created"] += _res.consume().counters.nodes_created
                        except Exception as exc:
                            summary["errors"].append(f"Node {row['id']}: {exc}")

            # --- Relationships (one UNWIND per type) ---
            for (rtype, source_label, target_label), rows in rels_by_type.items():
                # rtype validated against _LABEL_RE above; interpolated raw
                rel_sources_clause = (
                    " SET r._sources = CASE WHEN r._sources IS NULL THEN [{p}._source_id] "
                    "WHEN NOT {p}._source_id IN r._sources THEN r._sources + {p}._source_id "
                    "ELSE r._sources END"
                )
                # endpoints matched within the SAME workspace (review-#6): a rel
                # never bridges two tenants' nodes even when they share an id.
                source_pattern = (f"(a:{source_label} {{id: row.src, _workspace_id: $ws}})"
                                  if source_label else "(a {id: row.src, _workspace_id: $ws})")
                target_pattern = (f"(b:{target_label} {{id: row.tgt, _workspace_id: $ws}})"
                                  if target_label else "(b {id: row.tgt, _workspace_id: $ws})")
                batch_q = (f"UNWIND $rows AS row MATCH {source_pattern}, {target_pattern} "
                           f"MERGE (a)-[r:{rtype}]->(b) "
                           "SET r += CASE WHEN r._writer_ts IS NULL "
                           "OR r._writer_ts <= row.props._writer_ts THEN row.props ELSE {} END"
                           + rel_sources_clause.format(p="row.props"))
                try:
                    # Count edges ACTUALLY created (not submitted rows): the
                    # batch is MATCH (a),(b) MERGE, so a row whose endpoint node
                    # is absent creates zero edges, and a MERGE onto an existing
                    # edge creates zero. len(rows) counted both as writes,
                    # inflating total_relationships and masking the exact
                    # orphan-drop the survival census exists to catch (audit
                    # 2026-08-17). Read the real server counter, as execute_write
                    # already does.
                    _res = session.run(batch_q, rows=rows, ws=workspace_id)
                    summary["relationships_created"] += _res.consume().counters.relationships_created
                except Exception:
                    for row in rows:
                        try:
                            source_single = (f"(a:{source_label} {{id: $src, _workspace_id: $ws}})"
                                             if source_label else "(a {id: $src, _workspace_id: $ws})")
                            target_single = (f"(b:{target_label} {{id: $tgt, _workspace_id: $ws}})"
                                             if target_label else "(b {id: $tgt, _workspace_id: $ws})")
                            _res = session.run(
                                f"MATCH {source_single}, {target_single} "
                                f"MERGE (a)-[r:{rtype}]->(b) SET r += CASE WHEN "
                                "r._writer_ts IS NULL OR r._writer_ts <= $props._writer_ts "
                                "THEN $props ELSE {} END"
                                + rel_sources_clause.format(p="$props"),
                                src=row["src"], tgt=row["tgt"], props=row["props"], ws=workspace_id)
                            summary["relationships_created"] += _res.consume().counters.relationships_created
                        except Exception as exc:
                            summary["errors"].append(
                                f"Rel {row['src']}-[{rtype}]->{row['tgt']}: {exc}")

        if summary["nodes_created"] or summary["relationships_created"]:
            self.invalidate_schema_cache(database)
        return summary

    def explain_plan(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Optional[Dict[str, Any]]:
        """Compile the query and return its plan tree. Does NOT execute it.

        Separate from `query` because the plan lives on the result summary and
        `query` is contracted to return records; widening that return type to
        smuggle a plan out would change every caller. Returns None when the
        backend reports no plan, so a caller can tell "no signal" from "bad
        plan" rather than conflating them.

        Verified against DozerDB 5.26.3: EXPLAIN yields args.EstimatedRows and
        no dbHits, with operators suffixed as `NodeByLabelScan@neo4j`.
        """
        try:
            with self._driver.session(
                    database=database, default_access_mode="READ") as session:
                summary = session.run(f"EXPLAIN {cypher}",
                                      parameters=dict(params or {})).consume()
                plan = getattr(summary, "plan", None)
                return dict(plan) if plan else None
        except Exception:  # noqa: BLE001 — planning is advisory; never fail a caller
            logger.debug("EXPLAIN unavailable for query: %s", cypher[:120])
            return None

    def profile_plan(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Optional[Dict[str, Any]]:
        """Execute the query and return its profiled plan tree, with real counters.

        Same reason `explain_plan` exists: the profile lives on the ResultSummary,
        not in the result rows, and `query()` returns `[record.data() ...]` and
        discards the summary. Sending `PROFILE <cypher>` through `query()` and
        then scanning the rows for a `profile` key -- which is what the sampling
        path did -- can never find one. It collected nothing while paying for a
        second full execution of the query.

        EXPLAIN gives estimates; this gives dbHits, rows and page-cache counters,
        which is what separates "the planner was wrong" from "the query was
        wrong". Executes, so callers must sample.
        """
        try:
            with self._driver.session(
                    database=database, default_access_mode="READ") as session:
                result = session.run(f"PROFILE {cypher}",
                                     parameters=dict(params or {}))
                result.consume()  # drain before the summary is complete
                profile = getattr(result.consume(), "profile", None)
                return dict(profile) if profile else None
        except Exception:  # noqa: BLE001 — profiling is advisory; never fail a caller
            logger.debug("PROFILE unavailable for query: %s", cypher[:120])
            return None

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
        if enforce_workspace_filter:
            enforce_read_workspace_scope(cypher)

        from ..tracing import (
            capture_text,
            content_capture_enabled,
            is_tracing_enabled,
            start_span,
        )

        ws = workspace_id or merged_params.get("workspace_id") or ""
        # This method's contract is read-only (docstring + CLAUDE.md read-safety
        # guardrail). Pin the driver session to READ so a write that slips the
        # token blocklist still cannot reach the database — enforcement at the
        # driver, not just a naming convention. Routing a write here now errors
        # at Bolt rather than mutating the graph. (Security review 2026-08-15.)
        if not is_tracing_enabled():
            with self._driver.session(
                database=database, default_access_mode="READ") as session:
                result = session.run(cypher, parameters=merged_params)
                return [record.data() for record in result]

        # ADR-0144: instrument the read at the execution boundary. The
        # record.data() loop is where the PackStream codec runs, so we split
        # server time (ResultSummary) from client hydration — the slice the
        # ADR-0111 rust-ext win lives in — and stamp the active codec.
        with start_span(
            "db.query",
            metadata={"db.system": "neo4j", "db.name": database, "workspace_id": ws},
            tags=["db"],
        ) as span:
            with self._driver.session(
                    database=database, default_access_mode="READ") as session:
                started = time.perf_counter()
                result = session.run(cypher, parameters=merged_params)
                rows = [record.data() for record in result]
                wall_ms = (time.perf_counter() - started) * 1000.0
                try:
                    summary = result.consume()
                    server_ms = float(getattr(summary, "result_available_after", 0) or 0) + float(
                        getattr(summary, "result_consumed_after", 0) or 0
                    )
                except Exception:
                    server_ms = None
            attrs: Dict[str, Any] = {
                "db.rows_returned": len(rows),
                "db.client.codec": packstream_codec(),
            }
            if server_ms is not None:
                attrs["db.duration_server_ms"] = round(server_ms, 2)
                attrs["db.duration_hydrate_ms"] = round(max(0.0, wall_ms - server_ms), 2)
            if content_capture_enabled():
                stmt = capture_text(cypher)
                if stmt:
                    attrs["db.statement"] = stmt
            span.set_metadata(attrs)
            # Spans carry the per-query detail; the histogram is what a p95
            # panel can aggregate. server_share preserves the ADR-0111
            # server-vs-hydration split without a second histogram of the
            # same wall time.
            self._record_client_metrics("query", wall_ms, server_ms)
            return rows

    @staticmethod
    def _record_client_metrics(
        operation: str, wall_ms: float, server_ms: Optional[float]
    ) -> None:
        try:
            from seocho.metrics import get_metrics

            metrics = get_metrics()
            metrics.record(
                "db.client.operation.duration",
                wall_ms / 1000.0,
                {"db.system": "neo4j", "operation": operation, "outcome": "ok"},
            )
            if server_ms is not None and wall_ms > 0:
                metrics.record(
                    "db.client.operation.server_share",
                    max(0.0, min(1.0, server_ms / wall_ms)),
                    {"db.system": "neo4j", "operation": operation},
                )
        except Exception:  # metrics must never fail a query
            pass

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
        if enforce_workspace_filter:
            enforce_read_workspace_scope(cypher)
        from ..tracing import is_tracing_enabled, start_span

        ws = workspace_id or merged_params.get("workspace_id") or ""

        def _run() -> Any:
            started = time.perf_counter()
            with self._driver.session(database=database) as session:
                result = session.run(cypher, parameters=merged_params)
                counters = result.consume().counters
            self._record_client_metrics(
                "write", (time.perf_counter() - started) * 1000.0, None
            )
            return counters

        if is_tracing_enabled():
            with start_span(
                "db.execute_write",
                metadata={"db.system": "neo4j", "db.name": database, "workspace_id": ws},
                tags=["db", "write"],
            ) as span:
                counters = _run()
                span.set_metadata(
                    {
                        "db.client.codec": packstream_codec(),
                        "db.nodes_created": getattr(counters, "nodes_created", 0),
                        "db.nodes_deleted": getattr(counters, "nodes_deleted", 0),
                        "db.relationships_created": getattr(counters, "relationships_created", 0),
                        "db.relationships_deleted": getattr(counters, "relationships_deleted", 0),
                        "db.properties_set": getattr(counters, "properties_set", 0),
                    }
                )
        else:
            counters = _run()
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
        # Idempotent: releasing the driver's connection pool more than once
        # (e.g. explicit close() then __del__/__exit__) must be a no-op.
        if not getattr(self, "_closed", True):
            self._closed = True
            self._driver.close()

    def __enter__(self) -> "Neo4jGraphStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:
        # Best-effort safety net so a store that goes out of scope without an
        # explicit close()/with-block does not leak its connection pool for the
        # process lifetime (issue #135). Guarded: __del__ can run during
        # interpreter shutdown when modules/globals are already torn down.
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"Neo4jGraphStore(uri={self._uri!r})"


# ---------------------------------------------------------------------------
