from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from .ontology import Ontology


def load_ontology_file(path: str | Path) -> Ontology:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Ontology file not found: {source}")
    return Ontology.load(source)


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


@dataclass(slots=True)
class GovernanceValidationResult:
    name: str
    available: bool
    ok: bool
    error: Optional[str]
    errors: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "ok": self.ok,
            "error": self.error,
            "errors": list(self.errors),
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class OntologyGovernanceReport:
    source: str
    ok: bool
    ontology_check: OntologyCheckResult
    context_descriptor: Dict[str, Any]
    artifact_draft: Dict[str, Any]
    shacl_export: Dict[str, Any]
    sample_data_validation: GovernanceValidationResult
    owlready2_inspection: Optional[Owlready2InspectionResult]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "ontology_check": self.ontology_check.to_dict(),
            "context_descriptor": dict(self.context_descriptor),
            "artifact_draft": dict(self.artifact_draft),
            "shacl_export": dict(self.shacl_export),
            "sample_data_validation": self.sample_data_validation.to_dict(),
            "owlready2_inspection": (
                self.owlready2_inspection.to_dict()
                if self.owlready2_inspection is not None
                else None
            ),
            "notes": list(self.notes),
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


def build_ontology_governance_report(
    source: str | Path,
    *,
    artifact_name: Optional[str] = None,
    include_owl_inspection: bool = True,
) -> OntologyGovernanceReport:
    from .ontology_context import compile_ontology_context

    ontology = load_ontology_file(source)
    source_path = str(source)
    ontology_check = check_ontology(ontology)
    compiled_context = compile_ontology_context(ontology)
    artifact_draft_obj = ontology.to_semantic_artifact_draft(name=artifact_name)
    artifact_draft = (
        artifact_draft_obj.to_dict()
        if hasattr(artifact_draft_obj, "to_dict")
        else dict(artifact_draft_obj)
    )
    shacl_export = _build_shacl_export(ontology)
    sample_data = _build_sample_instance_graph(ontology)
    sample_errors = ontology.validate_with_shacl(sample_data)
    sample_validation = GovernanceValidationResult(
        name="synthetic_sample_data",
        available=True,
        ok=len(sample_errors) == 0,
        error=None,
        errors=sample_errors,
        stats={
            "node_count": len(sample_data.get("nodes", [])),
            "relationship_count": len(sample_data.get("relationships", [])),
        },
    )

    owlready2_inspection: Optional[Owlready2InspectionResult] = None
    notes: List[str] = []
    if include_owl_inspection:
        owlready2_inspection = inspect_owl_ontology(source_path)
        if owlready2_inspection.available and owlready2_inspection.error is None:
            notes.append("owlready2 inspection available for offline ontology governance.")
        elif not owlready2_inspection.available:
            notes.append("owlready2 is unavailable; heavy reasoning remains disabled for this environment.")
        elif owlready2_inspection.error:
            notes.append("owlready2 inspection failed; review the ontology source before promotion.")

    if shacl_export["stats"]["node_shape_count"] == 0:
        notes.append("Generated SHACL export contains no node shapes.")
    if shacl_export["stats"]["property_shape_count"] == 0:
        notes.append("Generated SHACL export contains no property constraints.")

    ok = ontology_check.ok and sample_validation.ok
    if shacl_export.get("unsupported_rules"):
        ok = False
        notes.append("Generated SHACL export contains unsupported rules.")

    return OntologyGovernanceReport(
        source=source_path,
        ok=ok,
        ontology_check=ontology_check,
        context_descriptor=compiled_context.descriptor.to_dict(),
        artifact_draft=artifact_draft,
        shacl_export=shacl_export,
        sample_data_validation=sample_validation,
        owlready2_inspection=owlready2_inspection,
        notes=notes,
    )


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

    cleanup_path: Optional[Path] = None
    try:
        prepared_source, cleanup_path = _prepare_owlready2_source(source_str)
        onto = get_ontology(prepared_source).load()
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
    except Exception as exc:
        return Owlready2InspectionResult(
            source=source_str,
            available=True,
            error=str(exc),
            stats={},
        )
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def _build_shacl_export(ontology: Ontology) -> Dict[str, Any]:
    shacl_document = ontology.to_shacl()
    return {
        "document": shacl_document,
        "turtle": _render_shacl_turtle(shacl_document),
        "unsupported_rules": [],
        "stats": {
            "node_shape_count": len(shacl_document.get("shapes", [])),
            "property_shape_count": sum(
                len(shape.get("properties", []))
                for shape in shacl_document.get("shapes", [])
                if isinstance(shape, dict)
            ),
        },
    }


def _render_shacl_turtle(shacl_document: Dict[str, Any]) -> str:
    lines = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix seocho: <https://seocho.dev/ontology/> .",
        "",
    ]
    for shape in shacl_document.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        target_class = str(shape.get("targetClass", "")).strip()
        if not target_class:
            continue
        shape_name = target_class.split(":", 1)[-1] + "Shape"
        lines.append(f"seocho:{shape_name} a sh:NodeShape ;")
        lines.append(f"  sh:targetClass {target_class} ;")
        properties = [
            prop
            for prop in shape.get("properties", [])
            if isinstance(prop, dict)
        ]
        if not properties:
            lines[-1] = lines[-1].rstrip(" ;") + " ."
            lines.append("")
            continue
        for index, prop in enumerate(properties):
            block_suffix = " ;" if index < len(properties) - 1 else " ."
            lines.append("  sh:property [")
            terms = [f"sh:path {prop.get('path')}"]
            if prop.get("datatype"):
                terms.append(f"sh:datatype {prop.get('datatype')}")
            if prop.get("minCount") is not None:
                terms.append(f"sh:minCount {int(prop.get('minCount', 0))}")
            if prop.get("maxCount") is not None:
                terms.append(f"sh:maxCount {int(prop.get('maxCount', 0))}")
            for term_index, term in enumerate(terms):
                suffix = " ;" if term_index < len(terms) - 1 else ""
                lines.append(f"    {term}{suffix}")
            lines.append(f"  ]{block_suffix}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_sample_instance_graph(ontology: Ontology) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    node_ids: Dict[str, str] = {}

    for label, node_def in ontology.nodes.items():
        node_id = f"sample_{label.lower()}"
        node_ids[label] = node_id
        properties: Dict[str, Any] = {}
        for prop_name, prop_def in node_def.properties.items():
            properties[prop_name] = _sample_property_value(label, prop_name, prop_def.property_type.value)
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "properties": properties,
            }
        )

    for rel_type, rel_def in ontology.relationships.items():
        source_id = node_ids.get(rel_def.source)
        target_id = node_ids.get(rel_def.target)
        if not source_id or not target_id:
            continue
        relationships.append(
            {
                "source": source_id,
                "target": target_id,
                "type": rel_type,
                "properties": {},
            }
        )

    return {"nodes": nodes, "relationships": relationships}


def _sample_property_value(label: str, property_name: str, property_type: str) -> Any:
    normalized = str(property_type).strip().upper()
    if normalized == "INTEGER":
        return 1
    if normalized == "FLOAT":
        return 1.0
    if normalized == "BOOLEAN":
        return True
    if normalized == "DATE":
        return "2026-01-01"
    if normalized == "DATETIME":
        return "2026-01-01T00:00:00Z"
    return f"{label}_{property_name}"


def _prepare_owlready2_source(source: str) -> Tuple[str, Optional[Path]]:
    local_path = _coerce_local_path(source)
    if local_path is None or local_path.suffix.lower() != ".ttl":
        return source, None

    try:
        import rdflib
    except Exception as exc:
        raise RuntimeError(f"rdflib unavailable for TTL -> RDF/XML conversion: {exc}") from exc

    graph = rdflib.Graph()
    graph.parse(str(local_path), format="turtle")
    with tempfile.NamedTemporaryFile(suffix=".rdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    graph.serialize(destination=str(tmp_path), format="xml")
    return tmp_path.as_uri(), tmp_path


def _coerce_local_path(source: str) -> Optional[Path]:
    raw = str(source).strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        return Path(unquote(parsed.path))
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    return None
