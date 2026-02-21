from __future__ import annotations

import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from semantic_artifact_store import (
    approve_semantic_artifact,
    get_semantic_artifact,
    list_semantic_artifacts,
    save_semantic_artifact,
)


class SemanticArtifactDraftCreateRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    name: Optional[str] = None
    ontology_candidate: Dict[str, Any]
    shacl_candidate: Dict[str, Any]
    source_summary: Optional[Dict[str, Any]] = None


class SemanticArtifactApproveRequest(BaseModel):
    workspace_id: str = Field(default="default", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    approved_by: str = Field(..., min_length=1, max_length=120)
    approval_note: Optional[str] = Field(default=None, max_length=1000)


class SemanticArtifactResponse(BaseModel):
    workspace_id: str
    artifact_id: str
    name: str
    status: str
    created_at: str
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    approval_note: Optional[str] = None
    source_summary: Dict[str, Any] = Field(default_factory=dict)
    ontology_candidate: Dict[str, Any]
    shacl_candidate: Dict[str, Any]


class SemanticArtifactListResponse(BaseModel):
    workspace_id: str
    artifacts: list[Dict[str, Any]]


def _semantic_artifact_dir() -> str:
    return os.getenv("SEMANTIC_ARTIFACT_DIR", "outputs/semantic_artifacts")


def create_semantic_artifact_draft(
    request: SemanticArtifactDraftCreateRequest,
) -> SemanticArtifactResponse:
    payload = save_semantic_artifact(
        workspace_id=request.workspace_id,
        name=request.name,
        ontology_candidate=request.ontology_candidate,
        shacl_candidate=request.shacl_candidate,
        source_summary=request.source_summary,
        base_dir=_semantic_artifact_dir(),
    )
    return SemanticArtifactResponse(**payload)


def approve_semantic_artifact_draft(
    artifact_id: str,
    request: SemanticArtifactApproveRequest,
) -> SemanticArtifactResponse:
    payload = approve_semantic_artifact(
        workspace_id=request.workspace_id,
        artifact_id=artifact_id,
        approved_by=request.approved_by,
        approval_note=request.approval_note,
        base_dir=_semantic_artifact_dir(),
    )
    return SemanticArtifactResponse(**payload)


def read_semantic_artifact(
    workspace_id: str,
    artifact_id: str,
) -> SemanticArtifactResponse:
    payload = get_semantic_artifact(
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        base_dir=_semantic_artifact_dir(),
    )
    return SemanticArtifactResponse(**payload)


def read_semantic_artifacts(
    workspace_id: str,
    status: Optional[str] = None,
) -> SemanticArtifactListResponse:
    rows = list_semantic_artifacts(
        workspace_id=workspace_id,
        status=status,
        base_dir=_semantic_artifact_dir(),
    )
    return SemanticArtifactListResponse(workspace_id=workspace_id, artifacts=rows)


def resolve_approved_artifact_payload(
    workspace_id: str,
    artifact_id: str,
) -> Dict[str, Dict[str, Any]]:
    payload = get_semantic_artifact(
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        base_dir=_semantic_artifact_dir(),
    )
    if payload.get("status") != "approved":
        raise ValueError(
            f"semantic artifact '{artifact_id}' is not approved (status={payload.get('status')})"
        )
    return {
        "ontology_candidate": payload.get("ontology_candidate", {}),
        "shacl_candidate": payload.get("shacl_candidate", {}),
    }
