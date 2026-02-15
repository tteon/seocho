from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


DEFAULT_RULE_PROFILE_DIR = "outputs/rule_profiles"


def save_rule_profile(
    workspace_id: str,
    rule_profile: Dict[str, Any],
    name: Optional[str] = None,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> Dict[str, Any]:
    workspace_path = _workspace_dir(base_dir, workspace_id)
    workspace_path.mkdir(parents=True, exist_ok=True)

    profile_id = _new_profile_id()
    created_at = _now_iso()
    payload = {
        "profile_id": profile_id,
        "workspace_id": workspace_id,
        "name": name or profile_id,
        "created_at": created_at,
        "schema_version": rule_profile.get("schema_version", "rules.v1"),
        "rule_count": len(rule_profile.get("rules", [])),
        "rule_profile": rule_profile,
    }

    profile_path = workspace_path / f"{profile_id}.json"
    with profile_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return payload


def get_rule_profile(
    workspace_id: str,
    profile_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> Dict[str, Any]:
    profile_path = _workspace_dir(base_dir, workspace_id) / f"{profile_id}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"rule profile not found: workspace={workspace_id}, profile_id={profile_id}")
    with profile_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_rule_profiles(
    workspace_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> List[Dict[str, Any]]:
    workspace_path = _workspace_dir(base_dir, workspace_id)
    if not workspace_path.exists():
        return []

    rows: List[Dict[str, Any]] = []
    for file_path in workspace_path.glob("*.json"):
        with file_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
            rows.append(
                {
                    "profile_id": payload.get("profile_id"),
                    "workspace_id": payload.get("workspace_id"),
                    "name": payload.get("name"),
                    "created_at": payload.get("created_at"),
                    "schema_version": payload.get("schema_version"),
                    "rule_count": payload.get("rule_count"),
                }
            )

    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows


def _workspace_dir(base_dir: str, workspace_id: str) -> Path:
    return Path(base_dir) / workspace_id


def _new_profile_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"rp_{ts}_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
