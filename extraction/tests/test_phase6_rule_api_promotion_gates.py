"""Phase 6 — promotion gate wiring for /rules/* endpoints.

The structural promise: every rule profile written via the API carries
``ontology_identity_hash``, and every read or assessment surfaces drift
against the workspace's active ontology when the registry is populated.
Phase 5 supplied the schema + storage; Phase 6 wires the runtime
ontology registry into the four operator-facing rule_api handlers.
"""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _ensure_paths_and_clean_registry(tmp_path, monkeypatch):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extraction_path = os.path.join(repo_root, "extraction")
    if extraction_path not in sys.path:
        sys.path.insert(0, extraction_path)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Isolated rule profile DB per test
    monkeypatch.setenv("RULE_PROFILE_DIR", str(tmp_path / "rule_profiles"))

    from runtime.ontology_registry import reset_runtime_ontology_registry

    reset_runtime_ontology_registry()
    yield
    reset_runtime_ontology_registry()


def _ontology(name: str, *, version: str = "1.0.0"):
    from seocho.ontology import NodeDef, Ontology, P, RelDef

    return Ontology(
        name=name,
        package_id=f"c-{name}",
        version=version,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company"),
        },
    )


def _register(workspace_id: str, graph_id: str, database: str, ontology) -> str:
    from runtime.ontology_registry import get_runtime_ontology_registry

    ctx = get_runtime_ontology_registry().register(
        graph_id, database, ontology, workspace_id=workspace_id
    )
    return ctx.descriptor.context_hash


# ---------------------------------------------------------------------------
# _active_ontology_hash helper
# ---------------------------------------------------------------------------


def test_helper_returns_hash_for_single_ontology_workspace():
    import rule_api

    expected = _register("acme", "finance", "kgnormal", _ontology("finance"))
    assert rule_api._active_ontology_hash("acme") == expected


def test_helper_returns_empty_when_workspace_unknown():
    import rule_api

    _register("acme", "finance", "kgnormal", _ontology("finance"))
    assert rule_api._active_ontology_hash("other") == ""


def test_helper_returns_empty_for_multi_ontology_workspace():
    """Multi-graph workspaces with distinct ontologies cannot pick a single
    active hash from a workspace_id-only request — returns empty,
    consistent with Phase 5's `unknown` trichotomy."""

    import rule_api

    _register("acme", "finance", "kgnormal", _ontology("finance"))
    _register("acme", "legal", "kglegal", _ontology("legal"))
    assert rule_api._active_ontology_hash("acme") == ""


def test_helper_returns_empty_for_empty_registry():
    import rule_api

    assert rule_api._active_ontology_hash("acme") == ""


# ---------------------------------------------------------------------------
# infer_rule_profile — stamps active hash
# ---------------------------------------------------------------------------


def test_infer_stamps_active_ontology_hash():
    import rule_api

    expected = _register("acme", "finance", "kgnormal", _ontology("finance"))

    response = rule_api.infer_rule_profile(
        rule_api.RuleInferRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
        )
    )
    assert response.ontology_identity_hash == expected
    assert response.rule_profile["ontology_identity_hash"] == expected


def test_infer_leaves_hash_empty_when_registry_inactive():
    import rule_api

    response = rule_api.infer_rule_profile(
        rule_api.RuleInferRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
        )
    )
    assert response.ontology_identity_hash == ""


# ---------------------------------------------------------------------------
# create_rule_profile — stamps active hash
# ---------------------------------------------------------------------------


def test_create_stamps_active_hash_when_caller_omits_one():
    import rule_api

    expected = _register("acme", "finance", "kgnormal", _ontology("finance"))

    response = rule_api.create_rule_profile(
        rule_api.RuleProfileCreateRequest(
            workspace_id="acme",
            rule_profile={"schema_version": "rules.v1", "rules": []},
        )
    )
    assert response.ontology_identity_hash == expected


def test_create_preserves_caller_supplied_hash_through_round_trip():
    """When the caller's rule_profile already has ontology_identity_hash
    (e.g. it came back from /rules/infer just now), the saved profile
    keeps that hash. save_rule_profile prefers the explicit kwarg, so
    the active hash takes precedence — but they should match for fresh
    inference, which is the common path."""

    import rule_api

    expected = _register("acme", "finance", "kgnormal", _ontology("finance"))

    inferred = rule_api.infer_rule_profile(
        rule_api.RuleInferRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
        )
    )
    saved = rule_api.create_rule_profile(
        rule_api.RuleProfileCreateRequest(
            workspace_id="acme",
            rule_profile=inferred.rule_profile,
        )
    )
    assert saved.ontology_identity_hash == expected


# ---------------------------------------------------------------------------
# read_rule_profile — surfaces drift block
# ---------------------------------------------------------------------------


def test_read_returns_match_when_stored_equals_active():
    import rule_api

    active = _register("acme", "finance", "kgnormal", _ontology("finance"))

    saved = rule_api.create_rule_profile(
        rule_api.RuleProfileCreateRequest(
            workspace_id="acme",
            rule_profile={"schema_version": "rules.v1", "rules": []},
        )
    )

    fetched = rule_api.read_rule_profile("acme", saved.profile_id)
    assert fetched.ontology_identity_hash == active
    assert fetched.artifact_ontology_mismatch is not None
    assert fetched.artifact_ontology_mismatch["status"] == "match"


def test_read_returns_drift_after_ontology_changes():
    """The structural promise: re-registering the ontology under a new
    version makes the previously-saved profile read as drift."""

    import rule_api

    _register("acme", "finance", "kgnormal", _ontology("finance", version="1.0.0"))

    saved = rule_api.create_rule_profile(
        rule_api.RuleProfileCreateRequest(
            workspace_id="acme",
            rule_profile={"schema_version": "rules.v1", "rules": []},
        )
    )

    # Re-register with a different version → new hash → old profile drifts
    from runtime.ontology_registry import reset_runtime_ontology_registry

    reset_runtime_ontology_registry()
    new_active = _register("acme", "finance", "kgnormal", _ontology("finance", version="2.0.0"))

    fetched = rule_api.read_rule_profile("acme", saved.profile_id)
    assert fetched.artifact_ontology_mismatch["status"] == "drift"
    assert fetched.artifact_ontology_mismatch["mismatch"] is True
    assert fetched.artifact_ontology_mismatch["active_ontology_hash"] == new_active
    assert fetched.artifact_ontology_mismatch["stored_ontology_hash"] != new_active


def test_read_omits_drift_block_when_registry_inactive():
    """Backward compat: workspaces without registered ontologies see no
    drift block; the legacy endpoint shape is preserved."""

    import rule_api

    saved = rule_api.create_rule_profile(
        rule_api.RuleProfileCreateRequest(
            workspace_id="acme",
            rule_profile={"schema_version": "rules.v1", "rules": []},
        )
    )

    fetched = rule_api.read_rule_profile("acme", saved.profile_id)
    assert fetched.artifact_ontology_mismatch is None


# ---------------------------------------------------------------------------
# assess_rule_profile — surfaces drift on supplied profile + stamps on infer
# ---------------------------------------------------------------------------


def test_assess_with_supplied_profile_surfaces_drift():
    import rule_api

    active = _register("acme", "finance", "kgnormal", _ontology("finance"))

    response = rule_api.assess_rule_profile(
        rule_api.RuleAssessRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
            rule_profile={
                "schema_version": "rules.v1",
                "rules": [],
                "ontology_identity_hash": "stale-hash",
            },
        )
    )
    assert response.artifact_ontology_mismatch is not None
    assert response.artifact_ontology_mismatch["status"] == "drift"
    assert response.artifact_ontology_mismatch["active_ontology_hash"] == active
    assert response.artifact_ontology_mismatch["stored_ontology_hash"] == "stale-hash"


def test_assess_with_inferred_profile_returns_match():
    import rule_api

    _register("acme", "finance", "kgnormal", _ontology("finance"))

    response = rule_api.assess_rule_profile(
        rule_api.RuleAssessRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
        )
    )
    assert response.ontology_identity_hash != ""
    assert response.artifact_ontology_mismatch["status"] == "match"


def test_assess_with_legacy_supplied_profile_returns_unknown():
    """Pre-Phase-5 profiles have no stored hash. Comparing against an
    active workspace hash must report status=unknown rather than drift,
    so legacy assessment paths keep working until profiles are re-saved."""

    import rule_api

    _register("acme", "finance", "kgnormal", _ontology("finance"))

    response = rule_api.assess_rule_profile(
        rule_api.RuleAssessRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
            rule_profile={"schema_version": "rules.v1", "rules": []},
        )
    )
    assert response.artifact_ontology_mismatch["status"] == "unknown"
    assert response.artifact_ontology_mismatch["mismatch"] is False


def test_assess_with_no_active_hash_reports_no_drift_block():
    import rule_api

    response = rule_api.assess_rule_profile(
        rule_api.RuleAssessRequest(
            workspace_id="acme",
            graph={"nodes": [{"label": "Company", "properties": {"name": "A"}}]},
        )
    )
    assert response.artifact_ontology_mismatch is None
    # Inferred ruleset has no stamp because helper returned empty
    assert response.ontology_identity_hash == ""
