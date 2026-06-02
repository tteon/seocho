from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class QualificationRunResult:
    run_id: str
    workspace_id: str
    graph_id: str
    database: str
    store_backend: str
    curation_design_name: str
    modes: List[str] = field(default_factory=list)
    observed_entity_count: int = 0
    observed_relation_count: int = 0
    case_count: int = 0
    auto_promotable_cases: int = 0
    status: str = "completed"
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "QualificationRunResult":
        return cls(
            run_id=str(payload.get("run_id", "")),
            workspace_id=str(payload.get("workspace_id", "")),
            graph_id=str(payload.get("graph_id", "")),
            database=str(payload.get("database", "")),
            store_backend=str(payload.get("store_backend", "")),
            curation_design_name=str(payload.get("curation_design_name", "")),
            modes=[str(item) for item in payload.get("modes", [])],
            observed_entity_count=int(payload.get("observed_entity_count", 0) or 0),
            observed_relation_count=int(payload.get("observed_relation_count", 0) or 0),
            case_count=int(payload.get("case_count", 0) or 0),
            auto_promotable_cases=int(payload.get("auto_promotable_cases", 0) or 0),
            status=str(payload.get("status", "completed")),
            summary=dict(payload.get("summary", {})),
        )


@dataclass(slots=True)
class QualificationCase:
    case_id: str
    run_id: str
    case_type: str
    status: str
    candidate_ids: List[str] = field(default_factory=list)
    recommended_action: str = ""
    scores: Dict[str, Any] = field(default_factory=dict)
    rationale: Dict[str, Any] = field(default_factory=dict)
    blocked_reasons: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "QualificationCase":
        return cls(
            case_id=str(payload.get("case_id", "")),
            run_id=str(payload.get("run_id", "")),
            case_type=str(payload.get("case_type", "")),
            status=str(payload.get("status", "")),
            candidate_ids=[str(item) for item in payload.get("candidate_ids", [])],
            recommended_action=str(payload.get("recommended_action", "")),
            scores=dict(payload.get("scores", {})),
            rationale=dict(payload.get("rationale", {})),
            blocked_reasons=[str(item) for item in payload.get("blocked_reasons", [])],
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(slots=True)
class CurationPreview:
    case_id: str
    action: str
    canonical_entity_id: Optional[str] = None
    candidate_ids: List[str] = field(default_factory=list)
    merged_properties: Dict[str, Any] = field(default_factory=dict)
    property_diff: Dict[str, Any] = field(default_factory=dict)
    blocked_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CurationPreview":
        return cls(
            case_id=str(payload.get("case_id", "")),
            action=str(payload.get("action", "")),
            canonical_entity_id=(
                str(payload["canonical_entity_id"])
                if payload.get("canonical_entity_id") not in (None, "")
                else None
            ),
            candidate_ids=[str(item) for item in payload.get("candidate_ids", [])],
            merged_properties=dict(payload.get("merged_properties", {})),
            property_diff=dict(payload.get("property_diff", {})),
            blocked_reasons=[str(item) for item in payload.get("blocked_reasons", [])],
        )


@dataclass(slots=True)
class CurationDecisionResult:
    decision_id: str
    case_id: str
    action: str
    status: str
    canonical_entity_id: Optional[str] = None
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CurationDecisionResult":
        return cls(
            decision_id=str(payload.get("decision_id", "")),
            case_id=str(payload.get("case_id", "")),
            action=str(payload.get("action", "")),
            status=str(payload.get("status", "")),
            canonical_entity_id=(
                str(payload["canonical_entity_id"])
                if payload.get("canonical_entity_id") not in (None, "")
                else None
            ),
            summary=dict(payload.get("summary", {})),
        )


@dataclass(slots=True)
class CanonicalEntityRecord:
    entity_id: str
    entity_type: str
    canonical_name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    support_count: int = 0


@dataclass(slots=True)
class CanonicalRelationRecord:
    relation_id: str
    rel_type: str
    source_entity_id: str
    target_entity_id: str
    properties: Dict[str, Any] = field(default_factory=dict)
    support_count: int = 0


@dataclass(slots=True)
class GraphProjectionSnapshot:
    snapshot_id: str
    workspace_id: str
    graph_id: str
    database: str
    entities: List[CanonicalEntityRecord] = field(default_factory=list)
    relationships: List[CanonicalRelationRecord] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphProjectionResult:
    snapshot_id: str
    workspace_id: str
    graph_id: str
    database: str
    store_backend: str
    nodes_written: int = 0
    relationships_written: int = 0
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GraphProjectionResult":
        return cls(
            snapshot_id=str(payload.get("snapshot_id", "")),
            workspace_id=str(payload.get("workspace_id", "")),
            graph_id=str(payload.get("graph_id", "")),
            database=str(payload.get("database", "")),
            store_backend=str(payload.get("store_backend", "")),
            nodes_written=int(payload.get("nodes_written", 0) or 0),
            relationships_written=int(payload.get("relationships_written", 0) or 0),
            summary=dict(payload.get("summary", {})),
        )
