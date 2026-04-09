from __future__ import annotations

import json

import pytest

from rule_profile_store import get_rule_profile, list_rule_profiles, save_rule_profile


def _profile(rule_name: str) -> dict:
    return {
        "schema_version": "rules.v1",
        "rules": [
            {
                "label": "Company",
                "property_name": rule_name,
                "kind": "required",
                "params": {"minCount": 1},
            }
        ],
    }


def test_rule_profile_store_assigns_incremental_versions(tmp_path) -> None:
    base_dir = str(tmp_path / "profiles")
    first = save_rule_profile("default", _profile("name"), name="v1", base_dir=base_dir)
    second = save_rule_profile("default", _profile("employees"), name="v2", base_dir=base_dir)

    assert first["profile_version"] == 1
    assert second["profile_version"] == 2

    listed = list_rule_profiles("default", base_dir=base_dir)
    assert [row["profile_version"] for row in listed] == [2, 1]

    fetched = get_rule_profile("default", second["profile_id"], base_dir=base_dir)
    assert fetched["profile_version"] == 2


def test_rule_profile_store_retention_policy(tmp_path, monkeypatch) -> None:
    base_dir = str(tmp_path / "profiles")
    monkeypatch.setenv("RULE_PROFILE_RETENTION_MAX", "2")

    first = save_rule_profile("default", _profile("name"), name="v1", base_dir=base_dir)
    second = save_rule_profile("default", _profile("employees"), name="v2", base_dir=base_dir)
    third = save_rule_profile("default", _profile("ticker"), name="v3", base_dir=base_dir)

    listed = list_rule_profiles("default", base_dir=base_dir)
    assert len(listed) == 2
    assert [row["profile_version"] for row in listed] == [3, 2]

    with pytest.raises(FileNotFoundError):
        get_rule_profile("default", first["profile_id"], base_dir=base_dir)

    assert get_rule_profile("default", second["profile_id"], base_dir=base_dir)["profile_version"] == 2
    assert get_rule_profile("default", third["profile_id"], base_dir=base_dir)["profile_version"] == 3


def test_rule_profile_store_imports_legacy_json_files(tmp_path) -> None:
    base_dir = tmp_path / "profiles"
    workspace_dir = base_dir / "default"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    payload_1 = {
        "profile_id": "legacy_1",
        "workspace_id": "default",
        "name": "legacy-1",
        "created_at": "2026-02-01T00:00:00+00:00",
        "schema_version": "rules.v1",
        "rule_count": 1,
        "rule_profile": _profile("name"),
    }
    payload_2 = {
        "profile_id": "legacy_2",
        "workspace_id": "default",
        "name": "legacy-2",
        "created_at": "2026-02-02T00:00:00+00:00",
        "schema_version": "rules.v1",
        "rule_count": 1,
        "rule_profile": _profile("employees"),
    }

    (workspace_dir / "legacy_1.json").write_text(json.dumps(payload_1), encoding="utf-8")
    (workspace_dir / "legacy_2.json").write_text(json.dumps(payload_2), encoding="utf-8")

    listed = list_rule_profiles("default", base_dir=str(base_dir))
    assert len(listed) == 2
    assert [row["profile_id"] for row in listed] == ["legacy_2", "legacy_1"]
    assert [row["profile_version"] for row in listed] == [2, 1]
