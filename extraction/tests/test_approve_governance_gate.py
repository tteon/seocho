"""Approve-gate governance wiring (gap-closure plan items #4 + #5).

The draft->approve transition runs an OFFLINE governance gate (structural
validate + FIBO/ISO-704 hygiene lint + optional OWL 2 DL consistency) and
stamps the verdict on ``payload["governance"]``. A structural/lint error or a
proven inconsistency refuses approval; a missing reasoner (no JVM) degrades to
``available=False`` and never blocks. No services, pure file I/O + model walk.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_artifact_store import (  # noqa: E402
    approve_semantic_artifact,
    get_semantic_artifact,
    save_semantic_artifact,
)


def _save(base_dir, classes, relationships=None, workspace_id="default"):
    return save_semantic_artifact(
        workspace_id=workspace_id,
        name="art",
        ontology_candidate={
            "ontology_name": "fin",
            "classes": classes,
            "relationships": relationships or [],
        },
        shacl_candidate={"shapes": []},
        base_dir=str(base_dir),
    )


def test_valid_ontology_approves_and_stamps_governance(tmp_path):
    art = _save(
        tmp_path,
        classes=[
            {"name": "FinancialMetric", "description": "A reported financial figure",
             "properties": [{"name": "name", "datatype": "string"}]},
            {"name": "Revenue", "description": "Top-line revenue", "broader": ["FinancialMetric"],
             "properties": [{"name": "name", "datatype": "string"}]},
        ],
    )
    approved = approve_semantic_artifact(
        workspace_id="default", artifact_id=art["artifact_id"], approved_by="rv",
        base_dir=str(tmp_path), run_reasoner=False,
    )
    assert approved["status"] == "approved"
    gov = approved["governance"]
    assert gov["ok"] is True and gov["applicable"] is True
    assert gov["structural"]["ok"] is True
    assert gov["lint"]["ok"] is True
    assert "checked_at" in gov
    # reasoner explicitly skipped here
    assert gov["consistency"]["available"] is False


def test_invalid_label_refuses_approval_and_persists_verdict(tmp_path):
    art = _save(
        tmp_path,
        classes=[{"name": "Bad Label", "description": "d",  # space => invalid LPG label
                  "properties": [{"name": "name", "datatype": "string"}]}],
    )
    with pytest.raises(ValueError, match="failed governance gate"):
        approve_semantic_artifact(
            workspace_id="default", artifact_id=art["artifact_id"], approved_by="rv",
            base_dir=str(tmp_path), run_reasoner=False,
        )
    # the failed verdict is persisted for audit; status stays draft
    stored = get_semantic_artifact(workspace_id="default", artifact_id=art["artifact_id"],
                                   base_dir=str(tmp_path))
    assert stored["status"] == "draft"
    assert stored["governance"]["ok"] is False
    assert stored["governance"]["structural"]["ok"] is False


def test_circular_broader_refuses_approval(tmp_path):
    art = _save(
        tmp_path,
        classes=[
            {"name": "A", "description": "d", "broader": ["B"],
             "properties": [{"name": "name", "datatype": "string"}]},
            {"name": "B", "description": "d", "broader": ["A"],
             "properties": [{"name": "name", "datatype": "string"}]},
        ],
    )
    with pytest.raises(ValueError, match="failed governance gate"):
        approve_semantic_artifact(
            workspace_id="default", artifact_id=art["artifact_id"], approved_by="rv",
            base_dir=str(tmp_path), run_reasoner=False,
        )


def test_governance_enforce_false_allows_approval_but_stamps_failure(tmp_path):
    art = _save(
        tmp_path,
        classes=[{"name": "Bad Label", "description": "d",
                  "properties": [{"name": "name", "datatype": "string"}]}],
    )
    approved = approve_semantic_artifact(
        workspace_id="default", artifact_id=art["artifact_id"], approved_by="rv",
        base_dir=str(tmp_path), run_reasoner=False, governance_enforce=False,
    )
    assert approved["status"] == "approved"
    assert approved["governance"]["ok"] is False  # verdict still recorded honestly


def test_vocabulary_only_artifact_not_subject_to_gate(tmp_path):
    # no classes => governance not applicable, approval proceeds
    art = _save(tmp_path, classes=[])
    approved = approve_semantic_artifact(
        workspace_id="default", artifact_id=art["artifact_id"], approved_by="rv",
        base_dir=str(tmp_path),
    )
    assert approved["status"] == "approved"
    assert approved["governance"]["applicable"] is False
    assert approved["governance"]["ok"] is True
