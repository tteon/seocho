"""Lifecycle guard for the semantic artifact store (#138).

approve_semantic_artifact set status="approved" unconditionally, so a
deprecated artifact could be silently revived to approved and served as the
active artifact. Approval is now a guarded transition.
"""

from __future__ import annotations

import pytest

from extraction.semantic_artifact_store import (
    approve_semantic_artifact,
    deprecate_semantic_artifact,
    save_semantic_artifact,
)


def _draft(base_dir):
    # No ontology classes -> the governance gate is "not applicable" and the
    # heavy ontology build is skipped, keeping the test offline and fast.
    return save_semantic_artifact(
        workspace_id="ws",
        ontology_candidate={},
        shacl_candidate={},
        base_dir=str(base_dir),
    )


def test_draft_can_be_approved_then_deprecated(tmp_path):
    art = _draft(tmp_path)
    aid = art["artifact_id"]
    approved = approve_semantic_artifact("ws", aid, "alice", base_dir=str(tmp_path))
    assert approved["status"] == "approved"
    deprecated = deprecate_semantic_artifact("ws", aid, "bob", base_dir=str(tmp_path))
    assert deprecated["status"] == "deprecated"


def test_deprecated_artifact_cannot_be_reapproved(tmp_path):
    art = _draft(tmp_path)
    aid = art["artifact_id"]
    approve_semantic_artifact("ws", aid, "alice", base_dir=str(tmp_path))
    deprecate_semantic_artifact("ws", aid, "bob", base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="deprecated"):
        approve_semantic_artifact("ws", aid, "alice", base_dir=str(tmp_path))


def test_reapproving_an_approved_artifact_is_allowed(tmp_path):
    # Idempotent re-approval of a still-active artifact stays permitted.
    art = _draft(tmp_path)
    aid = art["artifact_id"]
    approve_semantic_artifact("ws", aid, "alice", base_dir=str(tmp_path))
    again = approve_semantic_artifact("ws", aid, "alice", base_dir=str(tmp_path))
    assert again["status"] == "approved"
