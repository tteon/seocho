from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


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


def same_ontology_context_hash(values: Iterable[Dict[str, Any]]) -> bool:
    hashes = {
        str(item.get("ontology_context_hash") or item.get("context_hash") or "").strip()
        for item in values
    }
    hashes.discard("")
    return len(hashes) <= 1
