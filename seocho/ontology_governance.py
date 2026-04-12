from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .ontology import Ontology


def load_ontology_file(path: str | Path) -> Ontology:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Ontology file not found: {source}")
    if source.suffix.lower() in {".yaml", ".yml"}:
        return Ontology.from_yaml(source)
    return Ontology.from_jsonld(source)


@dataclass(slots=True)
class OntologyCheckResult:
    ontology_name: str
    package_id: str
    ontology_version: str
    ok: bool
    errors: List[str]
    warnings: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ontology_name": self.ontology_name,
            "package_id": self.package_id,
            "ontology_version": self.ontology_version,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class OntologyDiffResult:
    left_name: str
    right_name: str
    changes: Dict[str, Any]
    package_id: str
    recommended_bump: str
    requires_migration: bool
    migration_warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_name": self.left_name,
            "right_name": self.right_name,
            "changes": self.changes,
            "package_id": self.package_id,
            "recommended_bump": self.recommended_bump,
            "requires_migration": self.requires_migration,
            "migration_warnings": list(self.migration_warnings),
        }


@dataclass(slots=True)
class Owlready2InspectionResult:
    source: str
    available: bool
    error: Optional[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "available": self.available,
            "error": self.error,
            "stats": dict(self.stats),
        }


def check_ontology(ontology: Ontology) -> OntologyCheckResult:
    raw_findings = ontology.validate()
    errors: List[str] = []
    warnings: List[str] = []

    for item in raw_findings:
        if "consider adding one" in item:
            warnings.append(item)
        else:
            errors.append(item)

    if ontology.graph_model not in {"lpg", "rdf", "hybrid"}:
        errors.append(
            f"Unsupported graph_model '{ontology.graph_model}'. Expected one of: lpg, rdf, hybrid."
        )

    if not ontology.nodes:
        errors.append("Ontology defines no node types.")

    stats = {
        "package_id": ontology.package_id,
        "graph_model": ontology.graph_model,
        "namespace": ontology.namespace,
        "node_count": len(ontology.nodes),
        "relationship_count": len(ontology.relationships),
        "unique_property_count": sum(
            len(node_def.unique_properties) for node_def in ontology.nodes.values()
        ),
        "indexed_property_count": sum(
            len(node_def.indexed_properties) for node_def in ontology.nodes.values()
        ),
    }
    return OntologyCheckResult(
        ontology_name=ontology.name,
        package_id=ontology.package_id,
        ontology_version=ontology.version,
        ok=not errors,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def export_ontology_payload(
    ontology: Ontology,
    *,
    output_format: str,
) -> Dict[str, Any] | List[Any]:
    normalized = output_format.lower()
    if normalized == "jsonld":
        return ontology.to_jsonld()
    if normalized == "yaml":
        return ontology.to_dict()
    if normalized == "dict":
        return ontology.to_dict()
    if normalized == "shacl":
        return ontology.to_shacl()
    raise ValueError(f"Unsupported ontology export format: {output_format}")


def diff_ontologies(left: Ontology, right: Ontology) -> OntologyDiffResult:
    left_dict = left.to_dict()
    right_dict = right.to_dict()

    left_nodes = left_dict.get("nodes", {})
    right_nodes = right_dict.get("nodes", {})
    left_rels = left_dict.get("relationships", {})
    right_rels = right_dict.get("relationships", {})

    def _changed_keys(left_map: Dict[str, Any], right_map: Dict[str, Any]) -> List[str]:
        shared = set(left_map) & set(right_map)
        changed: List[str] = []
        for key in sorted(shared):
            if json.dumps(left_map[key], sort_keys=True) != json.dumps(right_map[key], sort_keys=True):
                changed.append(key)
        return changed

    package_id = right.package_id or left.package_id

    changes = {
        "metadata": {
            "changed": [
                key
                for key in ("graph_type", "package_id", "version", "description", "graph_model", "namespace")
                if left_dict.get(key) != right_dict.get(key)
            ],
        },
        "nodes": {
            "added": sorted(set(right_nodes) - set(left_nodes)),
            "removed": sorted(set(left_nodes) - set(right_nodes)),
            "changed": _changed_keys(left_nodes, right_nodes),
        },
        "relationships": {
            "added": sorted(set(right_rels) - set(left_rels)),
            "removed": sorted(set(left_rels) - set(right_rels)),
            "changed": _changed_keys(left_rels, right_rels),
        },
    }

    breaking = any(
        [
            bool(changes["nodes"]["removed"]),
            bool(changes["relationships"]["removed"]),
            bool(changes["nodes"]["changed"]),
            bool(changes["relationships"]["changed"]),
            "graph_model" in changes["metadata"]["changed"],
            "namespace" in changes["metadata"]["changed"],
            "package_id" in changes["metadata"]["changed"],
        ]
    )
    additive = any(
        [
            bool(changes["nodes"]["added"]),
            bool(changes["relationships"]["added"]),
        ]
    )
    metadata_only = bool(changes["metadata"]["changed"]) and not any(
        [
            changes["nodes"]["added"],
            changes["nodes"]["removed"],
            changes["nodes"]["changed"],
            changes["relationships"]["added"],
            changes["relationships"]["removed"],
            changes["relationships"]["changed"],
        ]
    )

    if breaking:
        recommended_bump = "major"
    elif additive:
        recommended_bump = "minor"
    elif metadata_only:
        recommended_bump = "patch"
    else:
        recommended_bump = "none"

    migration_warnings = _build_migration_warnings(
        left=left,
        right=right,
        changes=changes,
        recommended_bump=recommended_bump,
    )

    return OntologyDiffResult(
        left_name=f"{left.name}@{left.version}",
        right_name=f"{right.name}@{right.version}",
        changes=changes,
        package_id=package_id,
        recommended_bump=recommended_bump,
        requires_migration=breaking,
        migration_warnings=migration_warnings,
    )


def _parse_semver(value: str) -> Optional[Tuple[int, int, int]]:
    raw = value.strip()
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _build_migration_warnings(
    *,
    left: Ontology,
    right: Ontology,
    changes: Dict[str, Any],
    recommended_bump: str,
) -> List[str]:
    warnings: List[str] = []

    if left.package_id != right.package_id:
        warnings.append(
            f"package_id changed from '{left.package_id}' to '{right.package_id}'; treat this as a package migration boundary."
        )

    left_semver = _parse_semver(left.version)
    right_semver = _parse_semver(right.version)
    if left_semver is None or right_semver is None:
        warnings.append(
            f"Version comparison skipped because semver parsing failed ({left.version!r} -> {right.version!r})."
        )
        return warnings

    if recommended_bump == "none" and left_semver != right_semver:
        warnings.append("Version changed but no schema-level ontology changes were detected.")
        return warnings

    if recommended_bump == "patch":
        if right_semver <= left_semver:
            warnings.append(
                "Patch-level metadata changes were detected but the ontology version did not increase."
            )
    elif recommended_bump == "minor":
        if right_semver[0] == left_semver[0] and right_semver[1] <= left_semver[1]:
            warnings.append(
                "Additive ontology changes were detected; expected at least a minor version bump."
            )
    elif recommended_bump == "major":
        if right_semver[0] <= left_semver[0]:
            warnings.append(
                "Breaking ontology changes were detected; expected a major version bump."
            )
        if changes["nodes"]["removed"] or changes["relationships"]["removed"]:
            warnings.append(
                "Removed node/relationship types may require data migration and downstream query updates."
            )
        if changes["nodes"]["changed"] or changes["relationships"]["changed"]:
            warnings.append(
                "Changed existing node/relationship definitions may invalidate prompt assumptions, constraints, or denormalization behavior."
            )

    if "graph_model" in changes["metadata"]["changed"]:
        warnings.append("graph_model changed; treat this as a runtime/query migration, not just a schema patch.")
    if "namespace" in changes["metadata"]["changed"]:
        warnings.append("namespace changed; RDF identifiers and exported JSON-LD consumers may require remapping.")

    return warnings


def inspect_owl_ontology(source: str | Path) -> Owlready2InspectionResult:
    source_str = str(source)
    try:
        from owlready2 import get_ontology
    except Exception as exc:  # pragma: no cover - exercised by unit patching
        return Owlready2InspectionResult(
            source=source_str,
            available=False,
            error=f"owlready2 unavailable: {exc}",
            stats={},
        )

    try:
        onto = get_ontology(source_str).load()
    except Exception as exc:
        return Owlready2InspectionResult(
            source=source_str,
            available=True,
            error=str(exc),
            stats={},
        )

    classes: Sequence[Any] = list(onto.classes())
    individuals: Sequence[Any] = list(onto.individuals())
    properties: Sequence[Any] = list(onto.properties())
    imports: Sequence[Any] = list(getattr(onto, "imported_ontologies", []))

    return Owlready2InspectionResult(
        source=source_str,
        available=True,
        error=None,
        stats={
            "class_count": len(classes),
            "individual_count": len(individuals),
            "property_count": len(properties),
            "import_count": len(imports),
            "sample_classes": [str(getattr(item, "name", item)) for item in classes[:10]],
            "sample_properties": [str(getattr(item, "name", item)) for item in properties[:10]],
        },
    )
