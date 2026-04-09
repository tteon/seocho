import pytest

from semantic_artifact_api import (
    SemanticArtifactApproveRequest,
    SemanticArtifactDeprecateRequest,
    SemanticArtifactDraftCreateRequest,
    approve_semantic_artifact_draft,
    create_semantic_artifact_draft,
    deprecate_semantic_artifact_approved,
    read_semantic_artifact,
    read_semantic_artifacts,
    resolve_approved_artifact_payload,
)


def _sample_artifacts():
    return (
        {
            "ontology_name": "finance",
            "classes": [{"name": "Company", "description": "", "properties": [{"name": "name", "datatype": "string"}]}],
            "relationships": [],
        },
        {
            "shapes": [
                {
                    "target_class": "Company",
                    "properties": [{"path": "name", "constraint": "required", "params": {"minCount": 1}}],
                }
            ]
        },
    )


def test_semantic_artifact_draft_to_approval_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMANTIC_ARTIFACT_DIR", str(tmp_path))
    ontology, shacl = _sample_artifacts()

    created = create_semantic_artifact_draft(
        SemanticArtifactDraftCreateRequest(
            workspace_id="default",
            name="draft_v1",
            ontology_candidate=ontology,
            shacl_candidate=shacl,
        )
    )
    assert created.status == "draft"

    listed_draft = read_semantic_artifacts(workspace_id="default", status="draft")
    assert len(listed_draft.artifacts) == 1
    assert listed_draft.artifacts[0]["artifact_id"] == created.artifact_id

    fetched = read_semantic_artifact(workspace_id="default", artifact_id=created.artifact_id)
    assert fetched.name == "draft_v1"

    approved = approve_semantic_artifact_draft(
        artifact_id=created.artifact_id,
        request=SemanticArtifactApproveRequest(
            workspace_id="default",
            approved_by="reviewer-a",
            approval_note="validated",
        ),
    )
    assert approved.status == "approved"
    assert approved.approved_by == "reviewer-a"
    assert approved.vocabulary_candidate["schema_version"] == "vocabulary.v2"
    assert approved.vocabulary_candidate["profile"] == "skos"

    approved_payload = resolve_approved_artifact_payload(
        workspace_id="default",
        artifact_id=created.artifact_id,
    )
    assert approved_payload["ontology_candidate"]["ontology_name"] == "finance"

    deprecated = deprecate_semantic_artifact_approved(
        artifact_id=created.artifact_id,
        request=SemanticArtifactDeprecateRequest(
            workspace_id="default",
            deprecated_by="reviewer-a",
            deprecation_note="superseded",
        ),
    )
    assert deprecated.status == "deprecated"
    assert deprecated.deprecated_by == "reviewer-a"


def test_resolve_approved_payload_fails_for_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMANTIC_ARTIFACT_DIR", str(tmp_path))
    ontology, shacl = _sample_artifacts()
    created = create_semantic_artifact_draft(
        SemanticArtifactDraftCreateRequest(
            workspace_id="default",
            ontology_candidate=ontology,
            shacl_candidate=shacl,
        )
    )
    with pytest.raises(ValueError):
        resolve_approved_artifact_payload(workspace_id="default", artifact_id=created.artifact_id)
    with pytest.raises(ValueError):
        deprecate_semantic_artifact_approved(
            artifact_id=created.artifact_id,
            request=SemanticArtifactDeprecateRequest(
                workspace_id="default",
                deprecated_by="reviewer-a",
            ),
        )
