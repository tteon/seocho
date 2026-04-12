from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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
    ontology_version: str
    ok: bool
    errors: List[str]
    warnings: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ontology_name": self.ontology_name,
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_name": self.left_name,
            "right_name": self.right_name,
            "changes": self.changes,
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

    changes = {
        "metadata": {
            "changed": [
                key
                for key in ("graph_type", "version", "description", "graph_model", "namespace")
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

    return OntologyDiffResult(
        left_name=f"{left.name}@{left.version}",
        right_name=f"{right.name}@{right.version}",
        changes=changes,
    )


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
