from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

from .ontology_context import CompiledOntologyContext


def _stable_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]


def _clean_strings(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


@dataclass(frozen=True, slots=True)
class SemanticPackage:
    """Canonical semantic control-plane package for one ontology/database view."""

    package_id: str
    package_version: str
    package_hash: str
    workspace_id: str
    ontology_id: str
    ontology_name: str
    ontology_version: str
    ontology_profile: str
    vocabulary_profile: str
    graph_model: str
    graph_id: str = ""
    database: str = ""
    source: str = "ontology_context"
    ontology_context_hash: str = ""
    ontology_artifact_hash: str = ""
    glossary_hash: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    deterministic_intents: list[str] = field(default_factory=list)
    schema_version: str = "semantic_package.v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SemanticPackageSelection:
    """Deterministic multi-database selection wrapper for semantic query runs."""

    package_id: str
    package_version: str
    package_hash: str
    workspace_id: str
    source: str
    databases: list[str] = field(default_factory=list)
    package_ids: list[str] = field(default_factory=list)
    package_hashes: list[str] = field(default_factory=list)
    packages_by_database: Dict[str, SemanticPackage] = field(default_factory=dict)
    schema_version: str = "semantic_package_selection.v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "package_hash": self.package_hash,
            "workspace_id": self.workspace_id,
            "source": self.source,
            "databases": list(self.databases),
            "package_ids": list(self.package_ids),
            "package_hashes": list(self.package_hashes),
            "packages_by_database": {
                database: package.to_dict()
                for database, package in self.packages_by_database.items()
            },
        }


def _build_package(
    *,
    workspace_id: str,
    ontology_id: str,
    ontology_name: str,
    ontology_version: str,
    ontology_profile: str,
    vocabulary_profile: str,
    graph_model: str,
    graph_id: str,
    database: str,
    source: str,
    ontology_context_hash: str,
    ontology_artifact_hash: str,
    glossary_hash: str,
    artifact_ids: Sequence[Any],
    entity_types: Sequence[Any],
    relationship_types: Sequence[Any],
    deterministic_intents: Sequence[Any],
) -> SemanticPackage:
    artifact_list = _clean_strings(artifact_ids)
    entity_list = _clean_strings(entity_types)
    relationship_list = _clean_strings(relationship_types)
    intent_list = _clean_strings(deterministic_intents)
    graph_id_value = str(graph_id or database or ontology_id).strip()
    database_value = str(database or "").strip()
    version_value = str(ontology_version or "").strip()
    profile_value = str(ontology_profile or "default").strip() or "default"
    vocabulary_value = str(vocabulary_profile or "vocabulary.v2").strip() or "vocabulary.v2"
    graph_model_value = str(graph_model or "lpg").strip() or "lpg"
    package_id = ":".join(
        [
            str(ontology_id or ontology_name or "ontology").strip() or "ontology",
            profile_value,
            version_value or ontology_context_hash[:8] or "runtime",
            graph_id_value or "graph",
        ]
    )
    identity_payload = {
        "workspace_id": workspace_id,
        "ontology_id": ontology_id,
        "ontology_name": ontology_name,
        "ontology_version": version_value,
        "ontology_profile": profile_value,
        "vocabulary_profile": vocabulary_value,
        "graph_model": graph_model_value,
        "graph_id": graph_id_value,
        "database": database_value,
        "source": source,
        "ontology_context_hash": ontology_context_hash,
        "ontology_artifact_hash": ontology_artifact_hash,
        "glossary_hash": glossary_hash,
        "artifact_ids": artifact_list,
        "entity_types": entity_list,
        "relationship_types": relationship_list,
        "deterministic_intents": intent_list,
    }
    return SemanticPackage(
        package_id=package_id,
        package_version="semantic_package.v1",
        package_hash=_hash_payload(identity_payload),
        workspace_id=str(workspace_id or "default").strip() or "default",
        ontology_id=str(ontology_id or ontology_name or "ontology").strip() or "ontology",
        ontology_name=str(ontology_name or ontology_id or "ontology").strip() or "ontology",
        ontology_version=version_value,
        ontology_profile=profile_value,
        vocabulary_profile=vocabulary_value,
        graph_model=graph_model_value,
        graph_id=graph_id_value,
        database=database_value,
        source=str(source or "ontology_context").strip() or "ontology_context",
        ontology_context_hash=str(ontology_context_hash or "").strip(),
        ontology_artifact_hash=str(ontology_artifact_hash or "").strip(),
        glossary_hash=str(glossary_hash or "").strip(),
        artifact_ids=artifact_list,
        entity_types=entity_list,
        relationship_types=relationship_list,
        deterministic_intents=intent_list,
    )


def compile_semantic_package(
    ontology_context: CompiledOntologyContext,
    *,
    graph_id: str = "",
    database: str = "",
    vocabulary_profile: str = "vocabulary.v2",
    artifact_ids: Optional[Sequence[Any]] = None,
    source: str = "ontology_context",
) -> SemanticPackage:
    """Compile one canonical semantic package from a compiled ontology context."""

    descriptor = ontology_context.descriptor
    return _build_package(
        workspace_id=descriptor.workspace_id,
        ontology_id=descriptor.ontology_id,
        ontology_name=descriptor.ontology_name,
        ontology_version=descriptor.ontology_version,
        ontology_profile=descriptor.profile,
        vocabulary_profile=vocabulary_profile,
        graph_model=descriptor.graph_model,
        graph_id=graph_id,
        database=database,
        source=source,
        ontology_context_hash=descriptor.context_hash,
        ontology_artifact_hash=descriptor.artifact_hash,
        glossary_hash=descriptor.glossary_hash,
        artifact_ids=artifact_ids or (),
        entity_types=descriptor.node_labels,
        relationship_types=descriptor.relationship_types,
        deterministic_intents=descriptor.deterministic_intents,
    )


def _compile_constraint_slice_package(
    constraint_slice: Mapping[str, Any],
    *,
    workspace_id: str,
    database: str,
) -> SemanticPackage:
    ontology_candidate = constraint_slice.get("ontology_candidate", {})
    vocabulary_candidate = constraint_slice.get("vocabulary_candidate", {})
    artifact_payload = {
        "ontology_candidate": ontology_candidate if isinstance(ontology_candidate, dict) else {},
        "shacl_candidate": constraint_slice.get("shacl_candidate", {}),
        "vocabulary_candidate": vocabulary_candidate if isinstance(vocabulary_candidate, dict) else {},
        "artifact_ids": constraint_slice.get("artifact_ids", []),
    }
    glossary_payload = vocabulary_candidate if isinstance(vocabulary_candidate, dict) else {}
    context_payload = {
        "workspace_id": workspace_id,
        "graph_id": str(constraint_slice.get("graph_id", "")).strip(),
        "database": database,
        "ontology_id": str(constraint_slice.get("ontology_id", "")).strip(),
        "vocabulary_profile": str(constraint_slice.get("vocabulary_profile", "")).strip(),
        "allowed_labels": list(constraint_slice.get("allowed_labels", [])),
        "allowed_relationship_types": list(constraint_slice.get("allowed_relationship_types", [])),
        "allowed_properties": list(constraint_slice.get("allowed_properties", [])),
        "label_aliases": constraint_slice.get("label_aliases", {}),
        "relation_aliases": constraint_slice.get("relation_aliases", {}),
    }
    ontology_name = ""
    if isinstance(ontology_candidate, dict):
        ontology_name = str(ontology_candidate.get("ontology_name", "")).strip()
    ontology_id = str(constraint_slice.get("ontology_id", "")).strip() or ontology_name or database
    graph_id = str(constraint_slice.get("graph_id", "")).strip() or database
    return _build_package(
        workspace_id=workspace_id,
        ontology_id=ontology_id,
        ontology_name=ontology_name or ontology_id,
        ontology_version="",
        ontology_profile="runtime-derived",
        vocabulary_profile=str(constraint_slice.get("vocabulary_profile", "")).strip() or "vocabulary.v2",
        graph_model=str(constraint_slice.get("graph_model", "")).strip() or "lpg",
        graph_id=graph_id,
        database=database,
        source="constraint_slice",
        ontology_context_hash=_hash_payload(context_payload),
        ontology_artifact_hash=_hash_payload(artifact_payload),
        glossary_hash=_hash_payload(glossary_payload),
        artifact_ids=constraint_slice.get("artifact_ids", []),
        entity_types=constraint_slice.get("allowed_labels", []),
        relationship_types=constraint_slice.get("allowed_relationship_types", []),
        deterministic_intents=(),
    )


def select_semantic_packages(
    *,
    databases: Sequence[str],
    workspace_id: str,
    ontology_contexts: Optional[Mapping[str, CompiledOntologyContext]] = None,
    constraint_slices: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> SemanticPackageSelection:
    """Resolve the semantic packages that govern a semantic query run."""

    resolved_packages: Dict[str, SemanticPackage] = {}
    sources: list[str] = []
    contexts = dict(ontology_contexts or {})
    slices = dict(constraint_slices or {})

    for raw_database in databases:
        database = str(raw_database).strip()
        if not database:
            continue
        constraint_slice = slices.get(database, {})
        graph_id = str(constraint_slice.get("graph_id", "")).strip() or database
        ontology_context = contexts.get(graph_id)
        if ontology_context is not None:
            package = compile_semantic_package(
                ontology_context,
                graph_id=graph_id,
                database=database,
                vocabulary_profile=str(
                    constraint_slice.get("vocabulary_profile", "vocabulary.v2")
                ).strip()
                or "vocabulary.v2",
                artifact_ids=constraint_slice.get("artifact_ids", []),
                source="ontology_context",
            )
        elif constraint_slice:
            package = _compile_constraint_slice_package(
                constraint_slice,
                workspace_id=workspace_id,
                database=database,
            )
        else:
            continue
        resolved_packages[database] = package
        if package.source not in sources:
            sources.append(package.source)

    source = sources[0] if len(sources) == 1 else ("mixed" if sources else "none")
    packages_by_database = {
        database: package.to_dict()
        for database, package in resolved_packages.items()
    }
    selection_payload = {
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "source": source,
        "databases": list(resolved_packages.keys()),
        "packages_by_database": packages_by_database,
    }
    selection_hash = _hash_payload(selection_payload)
    return SemanticPackageSelection(
        package_id=f"semantic-selection:{selection_hash}",
        package_version="semantic_package_selection.v1",
        package_hash=selection_hash,
        workspace_id=str(workspace_id or "default").strip() or "default",
        source=source,
        databases=list(resolved_packages.keys()),
        package_ids=[package.package_id for package in resolved_packages.values()],
        package_hashes=[package.package_hash for package in resolved_packages.values()],
        packages_by_database=resolved_packages,
    )


__all__ = [
    "SemanticPackage",
    "SemanticPackageSelection",
    "compile_semantic_package",
    "select_semantic_packages",
]
