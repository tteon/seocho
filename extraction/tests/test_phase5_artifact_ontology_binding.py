"""Phase 5 — artifact-to-ontology binding via identity hash.

Three structural properties:
1. RuleSet carries ontology_identity_hash through serialization.
2. Semantic artifacts stamp + verify ontology_identity_hash with a
   match/drift/unknown trichotomy.
3. Rule profile sqlite store stamps + verifies ontology_identity_hash;
   pre-Phase-5 databases auto-migrate via ALTER TABLE.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest


@pytest.fixture(autouse=True)
def _ensure_extraction_on_path():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extraction_path = os.path.join(repo_root, "extraction")
    if extraction_path not in sys.path:
        sys.path.insert(0, extraction_path)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


# ---------------------------------------------------------------------------
# 5.1 — RuleSet identity hash
# ---------------------------------------------------------------------------


def test_ruleset_round_trip_preserves_ontology_identity_hash():
    from seocho.rules import Rule, RuleSet

    original = RuleSet(
        rules=[Rule(label="C", property_name="name", kind="required", params={})],
        ontology_identity_hash="hash-A",
    )
    payload = original.to_dict()
    assert payload["ontology_identity_hash"] == "hash-A"

    restored = RuleSet.from_dict(payload)
    assert restored.ontology_identity_hash == "hash-A"


def test_ruleset_from_dict_backward_compat_when_hash_missing():
    """Pre-Phase-5 RuleSet dicts have no ontology_identity_hash. Reader must
    treat it as empty string, not raise."""

    from seocho.rules import RuleSet

    legacy_payload = {"schema_version": "rules.v1", "rules": []}
    rs = RuleSet.from_dict(legacy_payload)
    assert rs.ontology_identity_hash == ""


def test_infer_rules_stamps_ontology_identity_hash():
    from seocho.rules import infer_rules_from_graph

    rs = infer_rules_from_graph(
        {"nodes": [{"label": "Company", "properties": {"name": "Acme"}}]},
        ontology_identity_hash="hash-X",
    )
    assert rs.ontology_identity_hash == "hash-X"


def test_infer_rules_default_hash_is_empty_string():
    from seocho.rules import infer_rules_from_graph

    rs = infer_rules_from_graph({"nodes": [{"label": "Company", "properties": {}}]})
    assert rs.ontology_identity_hash == ""


# ---------------------------------------------------------------------------
# 5.2 — Semantic artifact store
# ---------------------------------------------------------------------------


def test_save_semantic_artifact_stamps_ontology_identity_hash(tmp_path):
    import semantic_artifact_store as sas

    payload = sas.save_semantic_artifact(
        workspace_id="acme",
        ontology_candidate={"k": "v"},
        shacl_candidate={},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    assert payload["ontology_identity_hash"] == "hash-A"

    fetched = sas.get_semantic_artifact("acme", payload["artifact_id"], base_dir=str(tmp_path))
    assert fetched["ontology_identity_hash"] == "hash-A"


def test_get_semantic_artifact_match(tmp_path):
    import semantic_artifact_store as sas

    payload = sas.save_semantic_artifact(
        workspace_id="acme",
        ontology_candidate={},
        shacl_candidate={},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )

    fetched = sas.get_semantic_artifact(
        "acme",
        payload["artifact_id"],
        base_dir=str(tmp_path),
        expected_ontology_hash="hash-A",
    )
    block = fetched["artifact_ontology_mismatch"]
    assert block["mismatch"] is False
    assert block["status"] == "match"


def test_get_semantic_artifact_drift(tmp_path):
    import semantic_artifact_store as sas

    payload = sas.save_semantic_artifact(
        workspace_id="acme",
        ontology_candidate={},
        shacl_candidate={},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )

    fetched = sas.get_semantic_artifact(
        "acme",
        payload["artifact_id"],
        base_dir=str(tmp_path),
        expected_ontology_hash="hash-B",
    )
    block = fetched["artifact_ontology_mismatch"]
    assert block["mismatch"] is True
    assert block["status"] == "drift"
    assert block["stored_ontology_hash"] == "hash-A"
    assert block["active_ontology_hash"] == "hash-B"
    assert "Refuse application" in block["warning"]


def test_get_semantic_artifact_legacy_unknown(tmp_path):
    """An artifact saved before Phase 5 has no ontology_identity_hash.
    Reader must report status=unknown, NOT mismatch=true. This keeps the
    pre-Phase-5 audit/inspection path unblocked while still flagging the
    parity gap to the operator."""

    import json as _json
    import semantic_artifact_store as sas

    payload = sas.save_semantic_artifact(
        workspace_id="acme",
        ontology_candidate={},
        shacl_candidate={},
        base_dir=str(tmp_path),
    )
    artifact_path = sas._workspace_dir(str(tmp_path), "acme") / f"{payload['artifact_id']}.json"
    raw = _json.loads(artifact_path.read_text())
    raw.pop("ontology_identity_hash", None)
    artifact_path.write_text(_json.dumps(raw))

    fetched = sas.get_semantic_artifact(
        "acme",
        payload["artifact_id"],
        base_dir=str(tmp_path),
        expected_ontology_hash="hash-X",
    )
    block = fetched["artifact_ontology_mismatch"]
    assert block["status"] == "unknown"
    assert block["mismatch"] is False


def test_get_semantic_artifact_no_expected_hash_skips_drift_block(tmp_path):
    """Backward compat: callers that don't pass expected_ontology_hash get
    the legacy payload without the artifact_ontology_mismatch block."""

    import semantic_artifact_store as sas

    payload = sas.save_semantic_artifact(
        workspace_id="acme",
        ontology_candidate={},
        shacl_candidate={},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    fetched = sas.get_semantic_artifact("acme", payload["artifact_id"], base_dir=str(tmp_path))
    assert "artifact_ontology_mismatch" not in fetched


# ---------------------------------------------------------------------------
# 5.3 — Rule profile sqlite store + migration
# ---------------------------------------------------------------------------


def test_rule_profile_save_and_get_round_trip_hash(tmp_path):
    import rule_profile_store as rps

    out = rps.save_rule_profile(
        "acme",
        {"schema_version": "rules.v1", "rules": []},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    assert out["ontology_identity_hash"] == "hash-A"

    fetched = rps.get_rule_profile("acme", out["profile_id"], base_dir=str(tmp_path))
    assert fetched["ontology_identity_hash"] == "hash-A"
    assert fetched["rule_profile"]["ontology_identity_hash"] == "hash-A"


def test_rule_profile_drift_detection(tmp_path):
    import rule_profile_store as rps

    out = rps.save_rule_profile(
        "acme",
        {"schema_version": "rules.v1", "rules": []},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    drifted = rps.get_rule_profile(
        "acme",
        out["profile_id"],
        base_dir=str(tmp_path),
        expected_ontology_hash="hash-B",
    )
    assert drifted["artifact_ontology_mismatch"]["mismatch"] is True
    assert drifted["artifact_ontology_mismatch"]["status"] == "drift"


def test_rule_profile_migration_adds_column_to_legacy_db(tmp_path):
    """Pre-Phase-5 sqlite DBs lack ontology_identity_hash. Connecting via
    the Phase 5 _connect must ALTER the table without dropping data."""

    import rule_profile_store as rps

    db_path = tmp_path / "rule_profiles.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        """
        CREATE TABLE rule_profiles (
          profile_id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL,
          profile_version INTEGER NOT NULL,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          rule_count INTEGER NOT NULL,
          rule_profile_json TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        "INSERT INTO rule_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "acme", 1, "legacy", "2025-01-01", "rules.v1", 0, "{}"),
    )
    legacy.commit()
    legacy.close()

    # Save a new profile through Phase 5 store — should ALTER the table
    # and leave the legacy row intact.
    rps.save_rule_profile(
        "acme",
        {"rules": []},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )

    items = rps.list_rule_profiles("acme", base_dir=str(tmp_path))
    profile_ids = {item["profile_id"] for item in items}
    assert "legacy" in profile_ids
    assert any(item["ontology_identity_hash"] == "hash-A" for item in items)
    legacy_item = next(item for item in items if item["profile_id"] == "legacy")
    assert legacy_item["ontology_identity_hash"] == ""


def test_rule_profile_legacy_row_reads_unknown_status(tmp_path):
    """Drift detection on a pre-Phase-5 row reports status=unknown, not drift."""

    import rule_profile_store as rps

    db_path = tmp_path / "rule_profiles.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        """
        CREATE TABLE rule_profiles (
          profile_id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL,
          profile_version INTEGER NOT NULL,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          rule_count INTEGER NOT NULL,
          rule_profile_json TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        "INSERT INTO rule_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "acme", 1, "legacy", "2025-01-01", "rules.v1", 0, "{}"),
    )
    legacy.commit()
    legacy.close()

    payload = rps.get_rule_profile(
        "acme",
        "legacy",
        base_dir=str(tmp_path),
        expected_ontology_hash="hash-X",
    )
    assert payload["ontology_identity_hash"] == ""
    assert payload["artifact_ontology_mismatch"]["status"] == "unknown"
    assert payload["artifact_ontology_mismatch"]["mismatch"] is False


def test_rule_profile_save_propagates_hash_into_stored_json(tmp_path):
    """The hash is also stamped into the JSON blob, so a profile reconstructed
    from JSON alone (e.g. read by a different tool) still carries it."""

    import rule_profile_store as rps

    out = rps.save_rule_profile(
        "acme",
        {"schema_version": "rules.v1", "rules": []},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    assert out["rule_profile"]["ontology_identity_hash"] == "hash-A"
    fetched = rps.get_rule_profile("acme", out["profile_id"], base_dir=str(tmp_path))
    assert fetched["rule_profile"]["ontology_identity_hash"] == "hash-A"


def test_rule_profile_no_expected_hash_skips_drift_block(tmp_path):
    import rule_profile_store as rps

    out = rps.save_rule_profile(
        "acme",
        {"rules": []},
        base_dir=str(tmp_path),
        ontology_identity_hash="hash-A",
    )
    fetched = rps.get_rule_profile("acme", out["profile_id"], base_dir=str(tmp_path))
    assert "artifact_ontology_mismatch" not in fetched
