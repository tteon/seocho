from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import JsonSerializable


def _coerce_text_list(values: Any) -> List[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    out: List[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def serialize_optional_mapping(value: Any, *, field_name: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        if not isinstance(payload, dict):
            raise TypeError(f"{field_name} must serialize to a JSON object")
        return payload
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"{field_name} must be a dict or typed SEOCHO semantic model")


@dataclass(slots=True)
class KnownEntity(JsonSerializable):
    name: str
    label: str = "Entity"
    entity_id: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "KnownEntity":
        return cls(
            name=str(payload.get("name") or payload.get("value") or payload.get("id") or "").strip(),
            label=str(payload.get("label", "Entity")).strip() or "Entity",
            entity_id=str(payload.get("entity_id") or payload.get("id") or "").strip() or None,
        )


@dataclass(slots=True)
class OntologyProperty(JsonSerializable):
    name: str
    datatype: str = "string"
    description: str = ""
    aliases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OntologyProperty":
        return cls(
            name=str(payload.get("name", "")).strip(),
            datatype=str(payload.get("datatype", "string")).strip() or "string",
            description=str(payload.get("description", "")).strip(),
            aliases=_coerce_text_list(payload.get("aliases")),
        )


@dataclass(slots=True)
class OntologyClass(JsonSerializable):
    name: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    broader: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    properties: List[OntologyProperty] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OntologyClass":
        return cls(
            name=str(payload.get("name", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            aliases=_coerce_text_list(payload.get("aliases")),
            broader=_coerce_text_list(payload.get("broader")),
            related=_coerce_text_list(payload.get("related")),
            properties=[
                OntologyProperty.from_dict(item)
                for item in payload.get("properties", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class OntologyRelationship(JsonSerializable):
    type: str
    source: str = ""
    target: str = ""
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OntologyRelationship":
        return cls(
            type=str(payload.get("type", "")).strip(),
            source=str(payload.get("source", "")).strip(),
            target=str(payload.get("target", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            aliases=_coerce_text_list(payload.get("aliases")),
            related=_coerce_text_list(payload.get("related")),
        )


@dataclass(slots=True)
class OntologyCandidate(JsonSerializable):
    ontology_name: str = ""
    classes: List[OntologyClass] = field(default_factory=list)
    relationships: List[OntologyRelationship] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OntologyCandidate":
        return cls(
            ontology_name=str(payload.get("ontology_name", "")).strip(),
            classes=[
                OntologyClass.from_dict(item)
                for item in payload.get("classes", [])
                if isinstance(item, dict)
            ],
            relationships=[
                OntologyRelationship.from_dict(item)
                for item in payload.get("relationships", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class ShaclPropertyConstraint(JsonSerializable):
    path: str
    constraint: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ShaclPropertyConstraint":
        return cls(
            path=str(payload.get("path", "")).strip(),
            constraint=str(payload.get("constraint", "")).strip(),
            params=dict(payload.get("params", {})) if isinstance(payload.get("params"), dict) else {},
        )


@dataclass(slots=True)
class ShaclShape(JsonSerializable):
    target_class: str
    properties: List[ShaclPropertyConstraint] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ShaclShape":
        return cls(
            target_class=str(payload.get("target_class", "")).strip(),
            properties=[
                ShaclPropertyConstraint.from_dict(item)
                for item in payload.get("properties", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class ShaclCandidate(JsonSerializable):
    shapes: List[ShaclShape] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ShaclCandidate":
        return cls(
            shapes=[
                ShaclShape.from_dict(item)
                for item in payload.get("shapes", [])
                if isinstance(item, dict)
            ]
        )


@dataclass(slots=True)
class VocabularyTerm(JsonSerializable):
    pref_label: str
    alt_labels: List[str] = field(default_factory=list)
    hidden_labels: List[str] = field(default_factory=list)
    broader: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    definition: str = ""
    examples: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VocabularyTerm":
        pref_label = str(
            payload.get("pref_label")
            or payload.get("canonical")
            or payload.get("name")
            or ""
        ).strip()
        return cls(
            pref_label=pref_label,
            alt_labels=_coerce_text_list(payload.get("alt_labels") or payload.get("aliases")),
            hidden_labels=_coerce_text_list(payload.get("hidden_labels")),
            broader=_coerce_text_list(payload.get("broader")),
            related=_coerce_text_list(payload.get("related")),
            sources=_coerce_text_list(payload.get("sources")),
            definition=str(payload.get("definition", "")).strip(),
            examples=_coerce_text_list(payload.get("examples")),
        )


@dataclass(slots=True)
class VocabularyCandidate(JsonSerializable):
    schema_version: str = "vocabulary.v2"
    profile: str = "skos"
    terms: List[VocabularyTerm] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VocabularyCandidate":
        return cls(
            schema_version=str(payload.get("schema_version", "vocabulary.v2")).strip() or "vocabulary.v2",
            profile=str(payload.get("profile", "skos")).strip() or "skos",
            terms=[
                VocabularyTerm.from_dict(item)
                for item in payload.get("terms", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class SemanticPromptContext(JsonSerializable):
    instructions: List[str] = field(default_factory=list)
    known_entities: List[KnownEntity] = field(default_factory=list)
    ontology_candidate: Optional[OntologyCandidate] = None
    shacl_candidate: Optional[ShaclCandidate] = None
    vocabulary_candidate: Optional[VocabularyCandidate] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.instructions:
            payload["instructions"] = list(self.instructions)
        if self.known_entities:
            payload["known_entities"] = [item.to_dict() for item in self.known_entities]
        if self.ontology_candidate is not None:
            payload["ontology_candidate"] = self.ontology_candidate.to_dict()
        if self.shacl_candidate is not None:
            payload["shacl_candidate"] = self.shacl_candidate.to_dict()
        if self.vocabulary_candidate is not None:
            payload["vocabulary_candidate"] = self.vocabulary_candidate.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SemanticPromptContext":
        known_entities: List[KnownEntity] = []
        for item in payload.get("known_entities", []):
            if isinstance(item, dict):
                known_entities.append(KnownEntity.from_dict(item))
            elif isinstance(item, str):
                known_entities.append(KnownEntity(name=item))
        return cls(
            instructions=_coerce_text_list(payload.get("instructions") or payload.get("notes")),
            known_entities=known_entities,
            ontology_candidate=(
                OntologyCandidate.from_dict(payload["ontology_candidate"])
                if isinstance(payload.get("ontology_candidate"), dict)
                else None
            ),
            shacl_candidate=(
                ShaclCandidate.from_dict(payload["shacl_candidate"])
                if isinstance(payload.get("shacl_candidate"), dict)
                else None
            ),
            vocabulary_candidate=(
                VocabularyCandidate.from_dict(payload["vocabulary_candidate"])
                if isinstance(payload.get("vocabulary_candidate"), dict)
                else None
            ),
        )


@dataclass(slots=True)
class ApprovedArtifacts(JsonSerializable):
    ontology_candidate: Optional[OntologyCandidate] = None
    shacl_candidate: Optional[ShaclCandidate] = None
    vocabulary_candidate: Optional[VocabularyCandidate] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.ontology_candidate is not None:
            payload["ontology_candidate"] = self.ontology_candidate.to_dict()
        if self.shacl_candidate is not None:
            payload["shacl_candidate"] = self.shacl_candidate.to_dict()
        if self.vocabulary_candidate is not None:
            payload["vocabulary_candidate"] = self.vocabulary_candidate.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ApprovedArtifacts":
        return cls(
            ontology_candidate=(
                OntologyCandidate.from_dict(payload["ontology_candidate"])
                if isinstance(payload.get("ontology_candidate"), dict)
                else None
            ),
            shacl_candidate=(
                ShaclCandidate.from_dict(payload["shacl_candidate"])
                if isinstance(payload.get("shacl_candidate"), dict)
                else None
            ),
            vocabulary_candidate=(
                VocabularyCandidate.from_dict(payload["vocabulary_candidate"])
                if isinstance(payload.get("vocabulary_candidate"), dict)
                else None
            ),
        )


@dataclass(slots=True)
class SemanticArtifactDraftInput(JsonSerializable):
    ontology_candidate: OntologyCandidate
    shacl_candidate: ShaclCandidate
    vocabulary_candidate: Optional[VocabularyCandidate] = None
    name: Optional[str] = None
    source_summary: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SemanticArtifactDraftInput":
        return cls(
            name=str(payload.get("name", "")).strip() or None,
            ontology_candidate=OntologyCandidate.from_dict(payload.get("ontology_candidate", {})),
            shacl_candidate=ShaclCandidate.from_dict(payload.get("shacl_candidate", {})),
            vocabulary_candidate=(
                VocabularyCandidate.from_dict(payload["vocabulary_candidate"])
                if isinstance(payload.get("vocabulary_candidate"), dict)
                else None
            ),
            source_summary=dict(payload.get("source_summary", {})) if isinstance(payload.get("source_summary"), dict) else {},
        )


@dataclass(slots=True)
class SemanticArtifactSummary(JsonSerializable):
    artifact_id: str
    workspace_id: str
    name: Optional[str] = None
    created_at: str = ""
    status: str = ""
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    deprecated_at: Optional[str] = None
    deprecated_by: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SemanticArtifactSummary":
        return cls(
            artifact_id=str(payload.get("artifact_id", "")).strip(),
            workspace_id=str(payload.get("workspace_id", "")).strip(),
            name=str(payload.get("name", "")).strip() or None,
            created_at=str(payload.get("created_at", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            approved_at=str(payload.get("approved_at", "")).strip() or None,
            approved_by=str(payload.get("approved_by", "")).strip() or None,
            deprecated_at=str(payload.get("deprecated_at", "")).strip() or None,
            deprecated_by=str(payload.get("deprecated_by", "")).strip() or None,
        )


@dataclass(slots=True)
class SemanticArtifact(JsonSerializable):
    workspace_id: str
    artifact_id: str
    name: str
    status: str
    created_at: str
    ontology_candidate: OntologyCandidate
    shacl_candidate: ShaclCandidate
    vocabulary_candidate: VocabularyCandidate
    source_summary: Dict[str, Any] = field(default_factory=dict)
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    approval_note: Optional[str] = None
    deprecated_at: Optional[str] = None
    deprecated_by: Optional[str] = None
    deprecation_note: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SemanticArtifact":
        return cls(
            workspace_id=str(payload.get("workspace_id", "")).strip(),
            artifact_id=str(payload.get("artifact_id", "")).strip(),
            name=str(payload.get("name", "")).strip(),
            status=str(payload.get("status", "")).strip(),
            created_at=str(payload.get("created_at", "")).strip(),
            approved_at=str(payload.get("approved_at", "")).strip() or None,
            approved_by=str(payload.get("approved_by", "")).strip() or None,
            approval_note=str(payload.get("approval_note", "")).strip() or None,
            deprecated_at=str(payload.get("deprecated_at", "")).strip() or None,
            deprecated_by=str(payload.get("deprecated_by", "")).strip() or None,
            deprecation_note=str(payload.get("deprecation_note", "")).strip() or None,
            source_summary=dict(payload.get("source_summary", {})) if isinstance(payload.get("source_summary"), dict) else {},
            ontology_candidate=OntologyCandidate.from_dict(payload.get("ontology_candidate", {})),
            shacl_candidate=ShaclCandidate.from_dict(payload.get("shacl_candidate", {})),
            vocabulary_candidate=VocabularyCandidate.from_dict(
                payload.get("vocabulary_candidate", {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []})
            ),
        )
