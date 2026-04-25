from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


def build_ontology_context_summary_query(*, include_runtime_fields: bool = False) -> str:
    """Return a document-scoped metadata query for ontology context checks.

    `Document` nodes are the stable per-record provenance carrier across local
    and runtime ingestion paths. Querying that label first avoids noisy
    whole-graph property scans on mixed-property graphs while preserving the
    ontology-context mismatch signal for SEOCHO-managed data.
    """

    projections = [
        "collect(DISTINCT coalesce(n._ontology_context_hash, '')) AS raw_context_hashes",
        "count(n) AS scoped_nodes",
        "sum(CASE WHEN coalesce(n._ontology_context_hash, '') = '' THEN 1 ELSE 0 END) AS missing_context_nodes",
    ]
    if include_runtime_fields:
        projections.insert(
            1,
            "collect(DISTINCT coalesce(n._ontology_id, '')) AS raw_ontology_ids",
        )
        projections.insert(
            2,
            "collect(DISTINCT coalesce(n._ontology_profile, '')) AS raw_profiles",
        )
        projections.append(
            "sum(CASE WHEN coalesce(n._ontology_context_hash, '') = '' AND coalesce(n._ontology_id, '') = '' THEN 1 ELSE 0 END) AS missing_context_hash_nodes"
        )
    return (
        "OPTIONAL MATCH (n:Document)\n"
        "WHERE coalesce(n._workspace_id, n.workspace_id, $workspace_id) = $workspace_id\n"
        f"RETURN {', '.join(projections)}"
    )


def _stable_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class OntologyContextDescriptor:
    """Stable identity for the ontology context used by a run.

    The descriptor is intentionally compact. Large prompt/artifact bodies stay
    inside the compiled context cache, while logs, benchmark records, and graph
    metadata can carry this stable identity to prove that indexing, querying,
    and agent interaction used the same ontology contract.
    """

    workspace_id: str
    ontology_id: str
    ontology_name: str
    ontology_version: str
    profile: str
    graph_model: str
    context_hash: str
    artifact_hash: str
    glossary_hash: str
    node_count: int
    relationship_count: int
    glossary_term_count: int = 0
    node_labels: List[str] = field(default_factory=list)
    relationship_types: List[str] = field(default_factory=list)
    deterministic_intents: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompiledOntologyContext:
    """Compiled ontology artifacts for hot-path reuse."""

    descriptor: OntologyContextDescriptor
    extraction_context: Dict[str, str]
    query_context: Dict[str, str]
    query_profile: Dict[str, Any]
    agent_context: str

    def metadata(self, *, usage: str) -> Dict[str, Any]:
        payload = self.descriptor.to_dict()
        payload["usage"] = usage
        return payload


def compile_ontology_context(
    ontology: Any,
    *,
    workspace_id: str = "default",
    profile: str = "default",
) -> CompiledOntologyContext:
    """Compile ontology context shared by indexing, query, and agents."""

    extraction_context = dict(ontology.to_extraction_context())
    query_context = dict(ontology.to_query_context())
    query_profile = dict(ontology.to_query_profile())
    vocabulary_candidate: Dict[str, Any] = {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []}
    try:
        from .ontology_artifacts import ontology_to_vocabulary_candidate

        raw_vocabulary = ontology_to_vocabulary_candidate(ontology)
        if hasattr(raw_vocabulary, "to_dict"):
            vocabulary_candidate = dict(raw_vocabulary.to_dict())
    except Exception:
        vocabulary_candidate = {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []}
    ontology_id = str(getattr(ontology, "package_id", "") or getattr(ontology, "name", "ontology"))
    ontology_name = str(getattr(ontology, "name", ontology_id))
    ontology_version = str(getattr(ontology, "version", ""))
    graph_model = str(getattr(ontology, "graph_model", "lpg"))
    node_labels = sorted(str(item) for item in getattr(ontology, "nodes", {}).keys())
    relationship_types = sorted(str(item) for item in getattr(ontology, "relationships", {}).keys())
    deterministic_intents = [
        str(item)
        for item in query_profile.get("deterministic_intents", [])
        if str(item).strip()
    ]

    artifact_payload = {
        "extraction_context": extraction_context,
        "query_context": query_context,
        "query_profile": query_profile,
        "vocabulary_candidate": vocabulary_candidate,
    }
    artifact_hash = _hash_payload(artifact_payload)
    glossary_hash = _hash_payload(vocabulary_candidate)
    glossary_term_count = len(vocabulary_candidate.get("terms", []))
    identity_payload = {
        "workspace_id": workspace_id,
        "ontology_id": ontology_id,
        "ontology_name": ontology_name,
        "ontology_version": ontology_version,
        "profile": profile,
        "graph_model": graph_model,
        "artifact_hash": artifact_hash,
        "glossary_hash": glossary_hash,
        "node_labels": node_labels,
        "relationship_types": relationship_types,
        "deterministic_intents": deterministic_intents,
    }
    context_hash = _hash_payload(identity_payload)
    descriptor = OntologyContextDescriptor(
        workspace_id=workspace_id,
        ontology_id=ontology_id,
        ontology_name=ontology_name,
        ontology_version=ontology_version,
        profile=profile,
        graph_model=graph_model,
        context_hash=context_hash,
        artifact_hash=artifact_hash,
        glossary_hash=glossary_hash,
        node_count=len(node_labels),
        relationship_count=len(relationship_types),
        glossary_term_count=glossary_term_count,
        node_labels=node_labels,
        relationship_types=relationship_types,
        deterministic_intents=deterministic_intents,
    )
    agent_context = (
        "=== Ontology Context ===\n"
        f"  id: {ontology_id}\n"
        f"  version: {ontology_version}\n"
        f"  profile: {profile}\n"
        f"  graph_model: {graph_model}\n"
        f"  context_hash: {context_hash}\n"
        f"  glossary_terms: {glossary_term_count}"
    )
    return CompiledOntologyContext(
        descriptor=descriptor,
        extraction_context=extraction_context,
        query_context=query_context,
        query_profile=query_profile,
        agent_context=agent_context,
    )


class OntologyContextCache:
    """Small LRU cache for compiled ontology context artifacts."""

    def __init__(self, *, max_size: int = 32) -> None:
        self.max_size = max(max_size, 1)
        self._cache: OrderedDict[Tuple[int, str, str], CompiledOntologyContext] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(
        self,
        ontology: Any,
        *,
        workspace_id: str = "default",
        profile: str = "default",
    ) -> CompiledOntologyContext:
        key = (id(ontology), workspace_id, profile)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            self._cache.move_to_end(key)
            return cached

        self.misses += 1
        compiled = compile_ontology_context(
            ontology,
            workspace_id=workspace_id,
            profile=profile,
        )
        self._cache[key] = compiled
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)
        return compiled

    def stats(self) -> Dict[str, int]:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
        }

    def clear(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0


def merge_ontology_context_metadata(
    metadata: Optional[Dict[str, Any]],
    context: CompiledOntologyContext,
    *,
    usage: str,
) -> Dict[str, Any]:
    """Return metadata with the compact ontology context descriptor attached."""

    merged = dict(metadata or {})
    merged.setdefault("ontology_context", context.metadata(usage=usage))
    return merged


def ontology_context_graph_properties(context: CompiledOntologyContext | Dict[str, Any]) -> Dict[str, str]:
    """Return graph-safe properties for persisted nodes and relationships."""

    payload = context.metadata(usage="graph_write") if isinstance(context, CompiledOntologyContext) else dict(context)
    return {
        "_ontology_context_hash": str(payload.get("context_hash", "")),
        "_ontology_artifact_hash": str(payload.get("artifact_hash", "")),
        "_ontology_glossary_hash": str(payload.get("glossary_hash", "")),
        "_ontology_id": str(payload.get("ontology_id", "")),
        "_ontology_name": str(payload.get("ontology_name", "")),
        "_ontology_version": str(payload.get("ontology_version", "")),
        "_ontology_profile": str(payload.get("profile", "")),
        "_ontology_graph_model": str(payload.get("graph_model", "")),
    }


def apply_ontology_context_to_graph_payload(
    nodes: Iterable[Dict[str, Any]],
    relationships: Iterable[Dict[str, Any]],
    context: CompiledOntologyContext | Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Attach ontology context properties without mutating caller-owned payloads."""

    context_props = ontology_context_graph_properties(context)
    out_nodes: List[Dict[str, Any]] = []
    out_relationships: List[Dict[str, Any]] = []
    for node in nodes:
        copied = dict(node)
        properties = dict(copied.get("properties", {}) or {})
        for key, value in context_props.items():
            if value:
                properties.setdefault(key, value)
        copied["properties"] = properties
        out_nodes.append(copied)
    for rel in relationships:
        copied = dict(rel)
        properties = dict(copied.get("properties", {}) or {})
        for key, value in context_props.items():
            if value:
                properties.setdefault(key, value)
        copied["properties"] = properties
        out_relationships.append(copied)
    return out_nodes, out_relationships


def assess_ontology_context_mismatch(
    active_context: CompiledOntologyContext | Dict[str, Any],
    indexed_hashes: Iterable[Any],
    *,
    missing_context_nodes: int = 0,
    scoped_nodes: int = 0,
) -> Dict[str, Any]:
    """Compare active ontology context with hashes observed in the graph."""

    payload = (
        active_context.metadata(usage="query")
        if isinstance(active_context, CompiledOntologyContext)
        else dict(active_context)
    )
    active_hash = str(payload.get("context_hash", "")).strip()
    observed = sorted(
        {
            str(item).strip()
            for item in indexed_hashes
            if str(item).strip() and str(item).strip().lower() != "none"
        }
    )
    mismatched = bool(active_hash and observed and any(item != active_hash for item in observed))
    warning = ""
    if mismatched:
        warning = (
            "Active ontology context differs from at least one indexed graph context. "
            "Re-index or query with the matching ontology profile before trusting strict comparisons."
        )
    return {
        "active_context_hash": active_hash,
        "indexed_context_hashes": observed,
        "mismatch": mismatched,
        "missing_context_nodes": int(missing_context_nodes or 0),
        "scoped_nodes": int(scoped_nodes or 0),
        "warning": warning,
    }


def assess_graph_ontology_context_status(
    *,
    database: str,
    workspace_id: str,
    indexed_context_hashes: Iterable[Any] = (),
    indexed_ontology_ids: Iterable[Any] = (),
    indexed_profiles: Iterable[Any] = (),
    expected_ontology_id: str = "",
    expected_profile: str = "",
    expected_context_hash: str = "",
    missing_context_nodes: int = 0,
    missing_context_hash_nodes: int = 0,
    scoped_nodes: int = 0,
) -> Dict[str, Any]:
    """Summarize graph-level ontology context provenance for runtime responses.

    When ``expected_context_hash`` is non-empty, indexed context hashes that
    differ from it surface as ``indexed_context_hash_differs_from_active`` in
    ``mismatch_reasons``. This is the structural drift detection promised by
    the middleware contract: ontology_id can match by name yet still index
    against a different schema version.
    """

    context_hashes = _clean_distinct_strings(indexed_context_hashes)
    ontology_ids = _clean_distinct_strings(indexed_ontology_ids)
    profiles = _clean_distinct_strings(indexed_profiles)
    expected_ontology = str(expected_ontology_id or "").strip()
    expected_profile_value = str(expected_profile or "").strip()
    expected_hash = str(expected_context_hash or "").strip()

    reasons: List[str] = []
    if len(context_hashes) > 1:
        reasons.append("multiple_indexed_context_hashes")
    if expected_ontology and ontology_ids and any(item != expected_ontology for item in ontology_ids):
        reasons.append("indexed_ontology_id_differs_from_target")
    if expected_profile_value and profiles and any(item != expected_profile_value for item in profiles):
        reasons.append("indexed_profile_differs_from_target")
    if expected_hash and context_hashes and any(item != expected_hash for item in context_hashes):
        reasons.append("indexed_context_hash_differs_from_active")

    missing_nodes = int(missing_context_nodes or 0)
    missing_hash_nodes = int(missing_context_hash_nodes or 0)
    scoped_count = int(scoped_nodes or 0)
    warning = ""
    if reasons:
        warning = (
            "Indexed graph ontology context differs from the active runtime graph target. "
            "Re-index or route the query to the matching graph target before trusting strict comparisons."
        )
    elif scoped_count and (missing_nodes or missing_hash_nodes):
        warning = (
            "Some indexed graph nodes do not carry full ontology context metadata. "
            "Results are readable, but context parity cannot be fully verified."
        )

    return {
        "database": str(database or ""),
        "workspace_id": str(workspace_id or "default"),
        "expected_ontology_id": expected_ontology,
        "expected_profile": expected_profile_value,
        "expected_context_hash": expected_hash,
        "indexed_context_hashes": context_hashes,
        "indexed_ontology_ids": ontology_ids,
        "indexed_profiles": profiles,
        "mismatch": bool(reasons),
        "mismatch_reasons": reasons,
        "missing_context_nodes": missing_nodes,
        "missing_context_hash_nodes": missing_hash_nodes,
        "scoped_nodes": scoped_count,
        "warning": warning,
    }


def query_ontology_context_mismatch(
    graph_store: Any,
    active_context: CompiledOntologyContext | Dict[str, Any],
    *,
    workspace_id: str = "default",
    database: str = "neo4j",
) -> Dict[str, Any]:
    """Inspect graph metadata and compare it with the active ontology context."""

    try:
        rows = graph_store.query(
            build_ontology_context_summary_query(),
            params={"workspace_id": workspace_id},
            database=database,
        )
    except Exception as exc:
        result = assess_ontology_context_mismatch(active_context, [])
        result["error"] = str(exc)
        return result

    row = rows[0] if rows else {}
    return assess_ontology_context_mismatch(
        active_context,
        _clean_distinct_strings(
            (
                row.get("raw_context_hashes")
                if isinstance(row, dict) and row.get("raw_context_hashes") is not None
                else row.get("indexed_context_hashes", [])
                if isinstance(row, dict)
                else []
            )
        ),
        missing_context_nodes=(
            int(row.get("missing_context_nodes", 0) or 0)
            if isinstance(row, dict)
            else 0
        ),
        scoped_nodes=(
            int(row.get("scoped_nodes", 0) or 0)
            if isinstance(row, dict)
            else 0
        ),
    )


def same_ontology_context_hash(values: Iterable[Dict[str, Any]]) -> bool:
    hashes = {
        str(item.get("ontology_context_hash") or item.get("context_hash") or "").strip()
        for item in values
    }
    hashes.discard("")
    return len(hashes) <= 1


def _clean_distinct_strings(values: Iterable[Any]) -> List[str]:
    return sorted(
        {
            str(item).strip()
            for item in values
            if str(item).strip() and str(item).strip().lower() != "none"
        }
    )
