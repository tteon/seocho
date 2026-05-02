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

    # ------------------------------------------------------------------
    # seocho-x0t5 — KV-cache-aware layout helpers
    #
    # Anthropic's prompt caching reuses identical prefix bytes across
    # calls. OpenAI's API does prefix caching automatically. The seocho
    # contract is: the ontology context is the "stable" portion that
    # should sit BEFORE any per-call user input. Compose system prompts
    # as ``stable_prefix() + variable_suffix(user_input)`` to maximise
    # cache reuse across multi-turn sessions.
    # ------------------------------------------------------------------

    def stable_prefix(self) -> str:
        """Return the cacheable system-prompt portion (ontology + tools header).

        Two calls in the same Session under the same ontology version
        produce identical bytes here — the prefix-cache hits as long as
        the consumer composes the system prompt consistently.
        """
        ext = self.extraction_context
        return (
            f"{self.agent_context}\n"
            f"=== Ontology Vocabulary ===\n"
            f"Entity types:\n{ext.get('entity_types', '')}\n"
            f"Relationship types:\n{ext.get('relationship_types', '')}\n"
            f"Constraints:\n{ext.get('constraints_summary', '')}\n"
        )

    @staticmethod
    def variable_suffix(user_input: str) -> str:
        """Return the per-call portion appended after the stable prefix."""
        return f"=== User Input ===\n{user_input}\n"

    def kv_cache_layout(self) -> Dict[str, Any]:
        """Layout metadata for instrumentation and cache-aware composition."""
        prefix = self.stable_prefix()
        # SHA256 of the prefix bytes — useful as a cache_hit probe in trace
        # metadata, and as the cache key when the consumer wraps the LLM
        # call in their own response cache.
        import hashlib as _hashlib
        prefix_hash = _hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]
        return {
            "stable_prefix": prefix,
            "stable_prefix_bytes": len(prefix.encode("utf-8")),
            "stable_prefix_hash": prefix_hash,
            "context_hash": self.descriptor.context_hash,
        }


def apply_anthropic_cache_control(
    *,
    stable_prefix: str,
    user_input: str,
    cache_breakpoints: int = 1,
) -> Dict[str, Any]:
    """Compose Anthropic-format system + user messages with cache_control markers.

    Closes seocho-x0t5. Returns a dict the caller can spread into
    ``anthropic.messages.create(...)``::

        layout = compiled.kv_cache_layout()
        msg = apply_anthropic_cache_control(
            stable_prefix=layout["stable_prefix"],
            user_input="Who runs Apple?",
        )
        client.messages.create(model="claude-...", **msg)

    The system block carries ``cache_control={"type": "ephemeral"}``
    so Anthropic's prompt cache reuses the stable bytes across calls.
    OpenAI consumers can ignore this layout — OpenAI prefix-caches
    automatically once the same prefix is repeated.
    """
    return {
        "system": [
            {
                "type": "text",
                "text": stable_prefix,
                # Anthropic-only field; the OpenAI / Kimi / DeepSeek SDKs ignore it.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {"role": "user", "content": user_input},
        ],
        "_cache_breakpoints": int(cache_breakpoints),
    }


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


class OntologyDriftError(RuntimeError):
    """Raised by ``enforce_drift_policy(..., policy='raise')`` when the
    active ontology context_hash does not match what the graph was
    indexed with.

    Closes seocho-mcj0 (depends-on seocho-cimb). Lets callers — rule
    apply, semantic artifact approve, runtime middleware — opt into
    loud drift errors instead of the back-compat advisory log warning.

    The ``assessment`` dict produced by
    :func:`assess_ontology_context_mismatch` is preserved on the
    exception's ``assessment`` attribute so callers can surface it.
    """

    def __init__(self, assessment: Dict[str, Any]) -> None:
        active = assessment.get("active_context_hash") or "(unset)"
        observed = assessment.get("indexed_context_hashes") or []
        super().__init__(
            f"Ontology context drift: active context_hash={active!r} but "
            f"graph carries {observed!r} (configure drift_policy='warn' to "
            f"downgrade to a logged warning)."
        )
        self.assessment = dict(assessment)


def enforce_drift_policy(
    assessment: Dict[str, Any],
    *,
    policy: str = "warn",
    logger_obj: Any = None,
) -> Dict[str, Any]:
    """Apply a drift policy to an assessment from
    :func:`assess_ontology_context_mismatch`.

    seocho-mcj0 — closes the gap between drift detection and drift
    enforcement. Callers (rule apply, artifact approve, etc.) used to
    inspect ``assessment['mismatch']`` and at most log a warning. This
    helper centralises the choice:

    - ``policy='warn'`` (default, back-compat): if mismatch, attach
      ``policy='warn'`` and ``enforced=False`` to the assessment dict
      and log a warning. Caller proceeds.
    - ``policy='raise'``: if mismatch, raise :class:`OntologyDriftError`.
    - ``policy='block'``: if mismatch, attach ``policy='block'``,
      ``enforced=True``, ``blocked=True`` to the assessment dict so the
      caller can refuse the operation without raising. Useful in
      HTTP handlers that want to return 409 Conflict instead of 500.
    """
    out = dict(assessment)
    out["drift_policy"] = policy
    out["enforced"] = False
    out["blocked"] = False
    if not out.get("mismatch"):
        return out

    pol = str(policy).lower()
    if pol == "raise":
        raise OntologyDriftError(out)
    if pol == "block":
        out["enforced"] = True
        out["blocked"] = True
        return out
    # 'warn' or unknown — log and proceed.
    if logger_obj is not None:
        logger_obj.warning(out.get("warning") or "Ontology context drift detected")
    return out


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
