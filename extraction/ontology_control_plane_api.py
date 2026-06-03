from __future__ import annotations

import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ontology_control_plane_store import (
    compile_ontology_profile,
    evaluate_ontology_profile,
    get_ontology_profile,
    list_ontology_profiles,
    list_ontology_signals,
    promote_ontology_profile,
    save_ontology_profile,
    save_ontology_signal,
    select_ontology_profile,
)


WORKSPACE_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$"


class OntologyProfileUpsertRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_PATTERN)
    profile_id: str = Field(..., min_length=1, max_length=120)
    ontology_id: str = ""
    version: str = "draft"
    status: str = "draft"
    ontology_candidate: Dict[str, Any] = Field(default_factory=dict)
    vocabulary_candidate: Dict[str, Any] = Field(default_factory=dict)
    shacl_candidate: Dict[str, Any] = Field(default_factory=dict)
    route_hints: Dict[str, Any] = Field(default_factory=dict)
    answer_shapes: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)
    source_signal_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class OntologySignalCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_PATTERN)
    source: str = Field(..., min_length=1, max_length=80)
    kind: str = Field(..., min_length=1, max_length=120)
    profile_id: str = ""
    canonical: str = ""
    observed: str = ""
    confidence: float = 0.0
    evidence_count: int = 1
    affected_queries: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OntologyProfilePromoteRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_PATTERN)
    promoted_by: str = Field(..., min_length=1, max_length=120)
    promotion_note: Optional[str] = Field(default=None, max_length=1000)


class OntologyProfileSelectRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_PATTERN)
    question: str = Field(..., min_length=1)
    route_profile: Dict[str, Any] = Field(default_factory=dict)
    include_drafts: bool = False


class OntologyProfileEvaluateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=WORKSPACE_PATTERN)
    baseline_profile_id: Optional[str] = None


class OntologyProfileResponse(BaseModel):
    profile_id: str
    workspace_id: str
    ontology_id: str = ""
    version: str = "draft"
    status: str = "draft"
    ontology_candidate: Dict[str, Any] = Field(default_factory=dict)
    vocabulary_candidate: Dict[str, Any] = Field(default_factory=dict)
    shacl_candidate: Dict[str, Any] = Field(default_factory=dict)
    route_hints: Dict[str, Any] = Field(default_factory=dict)
    answer_shapes: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)
    source_signal_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    promoted_at: Optional[str] = None
    promoted_by: Optional[str] = None
    promotion_note: Optional[str] = None


class OntologyProfileListResponse(BaseModel):
    workspace_id: str
    profiles: list[Dict[str, Any]]


class OntologySignalResponse(BaseModel):
    signal_id: str
    workspace_id: str
    source: str
    kind: str
    profile_id: str = ""
    canonical: str = ""
    observed: str = ""
    confidence: float = 0.0
    evidence_count: int = 1
    affected_queries: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class OntologySignalListResponse(BaseModel):
    workspace_id: str
    signals: list[Dict[str, Any]]


class OntologyCompiledProfileResponse(BaseModel):
    schema_version: str
    profile_id: str
    workspace_id: str
    ontology_id: str = ""
    version: str = ""
    status: str = "draft"
    label_aliases: Dict[str, str] = Field(default_factory=dict)
    relation_aliases: Dict[str, str] = Field(default_factory=dict)
    required_slots: list[str] = Field(default_factory=list)
    route_hints: Dict[str, Any] = Field(default_factory=dict)
    answer_shapes: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)


class OntologyProfileSelectionResponse(BaseModel):
    profile_id: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    compiled_profile: Dict[str, Any] = Field(default_factory=dict)


class OntologyProfileEvaluationResponse(BaseModel):
    profile_id: str
    baseline_profile_id: str = ""
    decision: str
    expected_effect: Dict[str, float] = Field(default_factory=dict)
    metric_deltas: Dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    user_controls: list[str] = Field(default_factory=list)


def _base_dir() -> str:
    return os.getenv("ONTOLOGY_CONTROL_PLANE_DIR", "outputs/ontology_control_plane")


def _model_payload(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def upsert_ontology_profile(request: OntologyProfileUpsertRequest) -> OntologyProfileResponse:
    return OntologyProfileResponse(**save_ontology_profile(_model_payload(request), base_dir=_base_dir()))


def read_ontology_profile(workspace_id: str, profile_id: str) -> OntologyProfileResponse:
    return OntologyProfileResponse(**get_ontology_profile(workspace_id, profile_id, base_dir=_base_dir()))


def read_ontology_profiles(workspace_id: str, status: Optional[str] = None) -> OntologyProfileListResponse:
    return OntologyProfileListResponse(
        workspace_id=workspace_id,
        profiles=list_ontology_profiles(workspace_id, status=status, base_dir=_base_dir()),
    )


def promote_ontology_profile_request(
    profile_id: str,
    request: OntologyProfilePromoteRequest,
) -> OntologyProfileResponse:
    return OntologyProfileResponse(
        **promote_ontology_profile(
            request.workspace_id,
            profile_id,
            promoted_by=request.promoted_by,
            promotion_note=request.promotion_note,
            base_dir=_base_dir(),
        )
    )


def create_ontology_signal(request: OntologySignalCreateRequest) -> OntologySignalResponse:
    return OntologySignalResponse(**save_ontology_signal(_model_payload(request), base_dir=_base_dir()))


def read_ontology_signals(
    workspace_id: str,
    source: Optional[str] = None,
    kind: Optional[str] = None,
) -> OntologySignalListResponse:
    return OntologySignalListResponse(
        workspace_id=workspace_id,
        signals=list_ontology_signals(workspace_id, source=source, kind=kind, base_dir=_base_dir()),
    )


def compile_ontology_profile_request(workspace_id: str, profile_id: str) -> OntologyCompiledProfileResponse:
    return OntologyCompiledProfileResponse(
        **compile_ontology_profile(workspace_id, profile_id, base_dir=_base_dir())
    )


def select_ontology_profile_request(request: OntologyProfileSelectRequest) -> OntologyProfileSelectionResponse:
    return OntologyProfileSelectionResponse(
        **select_ontology_profile(
            request.workspace_id,
            request.question,
            route_profile=request.route_profile,
            include_drafts=request.include_drafts,
            base_dir=_base_dir(),
        )
    )


def evaluate_ontology_profile_request(
    profile_id: str,
    request: OntologyProfileEvaluateRequest,
) -> OntologyProfileEvaluationResponse:
    return OntologyProfileEvaluationResponse(
        **evaluate_ontology_profile(
            request.workspace_id,
            profile_id,
            baseline_profile_id=request.baseline_profile_id,
            base_dir=_base_dir(),
        )
    )
