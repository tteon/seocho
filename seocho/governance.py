from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

from .semantic import SemanticArtifact, SemanticArtifactDraftInput
from .types import JsonSerializable

_DEFAULT_VOCABULARY = {
    "schema_version": "vocabulary.v2",
    "profile": "skos",
    "terms": [],
}


@dataclass(slots=True)
class ArtifactValidationMessage(JsonSerializable):
    level: str
    code: str
    message: str
    path: str = ""


@dataclass(slots=True)
class ArtifactValidationResult(JsonSerializable):
    ok: bool
    errors: List[ArtifactValidationMessage] = field(default_factory=list)
    warnings: List[ArtifactValidationMessage] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactDiff(JsonSerializable):
    left_name: str
    right_name: str
    changes: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)


def validate_artifact_payload(
    artifact: SemanticArtifact | SemanticArtifactDraftInput | Mapping[str, Any],
) -> ArtifactValidationResult:
    materialized = _coerce_artifact(artifact, field_name="artifact")
    errors: List[ArtifactValidationMessage] = []
    warnings: List[ArtifactValidationMessage] = []

    ontology = materialized.ontology_candidate
    shacl = materialized.shacl_candidate
    vocabulary = materialized.vocabulary_candidate

    if not ontology.ontology_name.strip():
        warnings.append(
            ArtifactValidationMessage(
                level="warning",
                code="ontology.name_missing",
                path="ontology_candidate.ontology_name",
                message="Ontology name is empty; graph metadata or developer context may need to provide one.",
            )
        )

    class_names: Dict[str, int] = {}
    for index, item in enumerate(ontology.classes):
        path = f"ontology_candidate.classes[{index}]"
        name = item.name.strip()
        if not name:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="ontology.class_name_missing",
                    path=f"{path}.name",
                    message="Ontology class name is required.",
                )
            )
            continue
        if name in class_names:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="ontology.class_duplicate",
                    path=f"{path}.name",
                    message=f"Ontology class '{name}' is defined more than once.",
                )
            )
        else:
            class_names[name] = index

        property_names: set[str] = set()
        for prop_index, prop in enumerate(item.properties):
            prop_path = f"{path}.properties[{prop_index}]"
            prop_name = prop.name.strip()
            if not prop_name:
                errors.append(
                    ArtifactValidationMessage(
                        level="error",
                        code="ontology.property_name_missing",
                        path=f"{prop_path}.name",
                        message=f"Property name is required for class '{name}'.",
                    )
                )
                continue
            if prop_name in property_names:
                errors.append(
                    ArtifactValidationMessage(
                        level="error",
                        code="ontology.property_duplicate",
                        path=f"{prop_path}.name",
                        message=f"Property '{prop_name}' is duplicated in class '{name}'.",
                    )
                )
            property_names.add(prop_name)

    if not ontology.classes and not ontology.relationships:
        warnings.append(
            ArtifactValidationMessage(
                level="warning",
                code="ontology.empty",
                path="ontology_candidate",
                message="Ontology candidate has no classes or relationships.",
            )
        )

    relationship_keys: set[tuple[str, str, str]] = set()
    for index, relationship in enumerate(ontology.relationships):
        path = f"ontology_candidate.relationships[{index}]"
        rel_type = relationship.type.strip()
        if not rel_type:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="ontology.relationship_type_missing",
                    path=f"{path}.type",
                    message="Relationship type is required.",
                )
            )
            continue
        key = (rel_type, relationship.source.strip(), relationship.target.strip())
        if key in relationship_keys:
            warnings.append(
                ArtifactValidationMessage(
                    level="warning",
                    code="ontology.relationship_duplicate",
                    path=path,
                    message=(
                        "Relationship "
                        f"'{rel_type}' ({relationship.source or '?'} -> {relationship.target or '?'}) "
                        "is duplicated."
                    ),
                )
            )
        relationship_keys.add(key)
        if not relationship.source.strip() or not relationship.target.strip():
            warnings.append(
                ArtifactValidationMessage(
                    level="warning",
                    code="ontology.relationship_endpoint_missing",
                    path=path,
                    message=f"Relationship '{rel_type}' should declare both source and target classes.",
                )
            )

    shape_targets: set[str] = set()
    for index, shape in enumerate(shacl.shapes):
        path = f"shacl_candidate.shapes[{index}]"
        target_class = shape.target_class.strip()
        if not target_class:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="shacl.target_class_missing",
                    path=f"{path}.target_class",
                    message="SHACL shape target_class is required.",
                )
            )
            continue
        if target_class in shape_targets:
            warnings.append(
                ArtifactValidationMessage(
                    level="warning",
                    code="shacl.shape_duplicate",
                    path=f"{path}.target_class",
                    message=f"SHACL shape for target class '{target_class}' is duplicated.",
                )
            )
        shape_targets.add(target_class)
        if class_names and target_class not in class_names:
            warnings.append(
                ArtifactValidationMessage(
                    level="warning",
                    code="shacl.target_class_unknown",
                    path=f"{path}.target_class",
                    message=f"SHACL target class '{target_class}' is not declared in ontology classes.",
                )
            )
        for prop_index, prop in enumerate(shape.properties):
            prop_path = f"{path}.properties[{prop_index}]"
            if not prop.path.strip():
                errors.append(
                    ArtifactValidationMessage(
                        level="error",
                        code="shacl.path_missing",
                        path=f"{prop_path}.path",
                        message=f"SHACL property path is required for target class '{target_class}'.",
                    )
                )
            if not prop.constraint.strip():
                errors.append(
                    ArtifactValidationMessage(
                        level="error",
                        code="shacl.constraint_missing",
                        path=f"{prop_path}.constraint",
                        message=f"SHACL constraint is required for target class '{target_class}'.",
                    )
                )

    if not shacl.shapes:
        warnings.append(
            ArtifactValidationMessage(
                level="warning",
                code="shacl.empty",
                path="shacl_candidate",
                message="SHACL candidate has no shapes.",
            )
        )

    vocabulary_labels: set[str] = set()
    if not vocabulary.terms:
        warnings.append(
            ArtifactValidationMessage(
                level="warning",
                code="vocabulary.empty",
                path="vocabulary_candidate",
                message="Vocabulary candidate has no terms.",
            )
        )
    for index, term in enumerate(vocabulary.terms):
        path = f"vocabulary_candidate.terms[{index}]"
        label = term.pref_label.strip()
        if not label:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="vocabulary.pref_label_missing",
                    path=f"{path}.pref_label",
                    message="Vocabulary pref_label is required.",
                )
            )
            continue
        if label in vocabulary_labels:
            errors.append(
                ArtifactValidationMessage(
                    level="error",
                    code="vocabulary.pref_label_duplicate",
                    path=f"{path}.pref_label",
                    message=f"Vocabulary term '{label}' is duplicated.",
                )
            )
        vocabulary_labels.add(label)
        if label in set(term.alt_labels):
            warnings.append(
                ArtifactValidationMessage(
                    level="warning",
                    code="vocabulary.alt_label_repeats_pref_label",
                    path=f"{path}.alt_labels",
                    message=f"Vocabulary term '{label}' repeats pref_label in alt_labels.",
                )
            )

    summary = {
        "class_count": len(ontology.classes),
        "relationship_count": len(ontology.relationships),
        "shape_count": len(shacl.shapes),
        "vocabulary_term_count": len(vocabulary.terms),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    return ArtifactValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        summary=summary,
    )


def diff_artifact_payloads(
    left: SemanticArtifact | SemanticArtifactDraftInput | Mapping[str, Any],
    right: SemanticArtifact | SemanticArtifactDraftInput | Mapping[str, Any],
) -> ArtifactDiff:
    left_artifact = _coerce_artifact(left, field_name="left")
    right_artifact = _coerce_artifact(right, field_name="right")

    metadata_changed = [
        field_name
        for field_name in (
            "name",
            "status",
            "approved_by",
            "deprecated_by",
        )
        if getattr(left_artifact, field_name) != getattr(right_artifact, field_name)
    ]

    left_classes = _map_by_key(
        [(item.name, item.to_dict()) for item in left_artifact.ontology_candidate.classes if item.name.strip()]
    )
    right_classes = _map_by_key(
        [(item.name, item.to_dict()) for item in right_artifact.ontology_candidate.classes if item.name.strip()]
    )
    left_relationships = _map_by_key(
        [
            (
                _relationship_key(item.type, item.source, item.target),
                item.to_dict(),
            )
            for item in left_artifact.ontology_candidate.relationships
            if item.type.strip()
        ]
    )
    right_relationships = _map_by_key(
        [
            (
                _relationship_key(item.type, item.source, item.target),
                item.to_dict(),
            )
            for item in right_artifact.ontology_candidate.relationships
            if item.type.strip()
        ]
    )
    left_shapes = _map_by_key(
        [(item.target_class, item.to_dict()) for item in left_artifact.shacl_candidate.shapes if item.target_class.strip()]
    )
    right_shapes = _map_by_key(
        [(item.target_class, item.to_dict()) for item in right_artifact.shacl_candidate.shapes if item.target_class.strip()]
    )
    left_terms = _map_by_key(
        [(item.pref_label, item.to_dict()) for item in left_artifact.vocabulary_candidate.terms if item.pref_label.strip()]
    )
    right_terms = _map_by_key(
        [(item.pref_label, item.to_dict()) for item in right_artifact.vocabulary_candidate.terms if item.pref_label.strip()]
    )

    changes = {
        "metadata": {"changed": metadata_changed},
        "ontology_classes": _diff_mapping(left_classes, right_classes),
        "ontology_relationships": _diff_mapping(left_relationships, right_relationships),
        "shacl_shapes": _diff_mapping(left_shapes, right_shapes),
        "vocabulary_terms": _diff_mapping(left_terms, right_terms),
    }
    summary = {
        "metadata_changed": len(metadata_changed),
        "classes_added": len(changes["ontology_classes"]["added"]),
        "classes_removed": len(changes["ontology_classes"]["removed"]),
        "classes_changed": len(changes["ontology_classes"]["changed"]),
        "relationships_added": len(changes["ontology_relationships"]["added"]),
        "relationships_removed": len(changes["ontology_relationships"]["removed"]),
        "relationships_changed": len(changes["ontology_relationships"]["changed"]),
        "shapes_added": len(changes["shacl_shapes"]["added"]),
        "shapes_removed": len(changes["shacl_shapes"]["removed"]),
        "shapes_changed": len(changes["shacl_shapes"]["changed"]),
        "terms_added": len(changes["vocabulary_terms"]["added"]),
        "terms_removed": len(changes["vocabulary_terms"]["removed"]),
        "terms_changed": len(changes["vocabulary_terms"]["changed"]),
    }
    return ArtifactDiff(
        left_name=left_artifact.name or left_artifact.artifact_id,
        right_name=right_artifact.name or right_artifact.artifact_id,
        changes=changes,
        summary=summary,
    )


def _coerce_artifact(
    value: SemanticArtifact | SemanticArtifactDraftInput | Mapping[str, Any],
    *,
    field_name: str,
) -> SemanticArtifact:
    if isinstance(value, SemanticArtifact):
        return value
    if isinstance(value, SemanticArtifactDraftInput):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise TypeError(f"{field_name} must be a semantic artifact, draft input, or mapping")

    normalized = {
        "workspace_id": str(payload.get("workspace_id", "default")).strip() or "default",
        "artifact_id": str(payload.get("artifact_id") or payload.get("name") or "draft").strip() or "draft",
        "name": str(payload.get("name") or payload.get("artifact_id") or "draft").strip() or "draft",
        "status": str(payload.get("status", "draft")).strip() or "draft",
        "created_at": str(payload.get("created_at", "")).strip(),
        "approved_at": payload.get("approved_at"),
        "approved_by": payload.get("approved_by"),
        "approval_note": payload.get("approval_note"),
        "deprecated_at": payload.get("deprecated_at"),
        "deprecated_by": payload.get("deprecated_by"),
        "deprecation_note": payload.get("deprecation_note"),
        "source_summary": dict(payload.get("source_summary", {})) if isinstance(payload.get("source_summary"), dict) else {},
        "ontology_candidate": payload.get("ontology_candidate", {}),
        "shacl_candidate": payload.get("shacl_candidate", {}),
        "vocabulary_candidate": payload.get("vocabulary_candidate", _DEFAULT_VOCABULARY),
    }
    return SemanticArtifact.from_dict(normalized)


def _map_by_key(items: List[tuple[str, Dict[str, Any]]]) -> Dict[str, str]:
    return {
        key: json.dumps(value, sort_keys=True)
        for key, value in items
    }


def _diff_mapping(left: Dict[str, str], right: Dict[str, str]) -> Dict[str, List[str]]:
    left_keys = set(left)
    right_keys = set(right)
    return {
        "added": sorted(right_keys - left_keys),
        "removed": sorted(left_keys - right_keys),
        "changed": sorted(key for key in (left_keys & right_keys) if left[key] != right[key]),
    }


def _relationship_key(rel_type: str, source: str, target: str) -> str:
    left = source.strip() or "?"
    right = target.strip() or "?"
    return f"{rel_type.strip()} ({left} -> {right})"
