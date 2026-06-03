from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seocho.ontology_control_plane import (
    OntologyControlPlane,
    OntologyProfile,
    OntologyProfileRegistry,
    OntologySignal,
)


DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR = "outputs/ontology_control_plane"


def save_ontology_profile(
    profile: Dict[str, Any],
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    item = OntologyProfile.from_dict(profile)
    if not item.profile_id:
        raise ValueError("profile_id is required")
    payload = item.to_dict()
    payload.setdefault("created_at", _now_iso())
    payload["updated_at"] = _now_iso()
    path = _profile_path(base_dir, item.workspace_id, item.profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def get_ontology_profile(
    workspace_id: str,
    profile_id: str,
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    path = _profile_path(base_dir, workspace_id, profile_id)
    if not path.exists():
        raise FileNotFoundError(f"ontology profile not found: workspace={workspace_id}, profile_id={profile_id}")
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def list_ontology_profiles(
    workspace_id: str,
    status: Optional[str] = None,
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> List[Dict[str, Any]]:
    path = _workspace_dir(base_dir, workspace_id) / "profiles"
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for file_path in path.glob("*.json"):
        with file_path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if status and payload.get("status") != status:
            continue
        rows.append(_profile_summary(payload))
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return rows


def promote_ontology_profile(
    workspace_id: str,
    profile_id: str,
    *,
    promoted_by: str,
    promotion_note: Optional[str] = None,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    payload = get_ontology_profile(workspace_id, profile_id, base_dir=base_dir)
    payload["status"] = "approved"
    payload["promoted_by"] = promoted_by
    payload["promotion_note"] = promotion_note
    payload["promoted_at"] = _now_iso()
    return save_ontology_profile(payload, base_dir=base_dir)


def save_ontology_signal(
    signal: Dict[str, Any],
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    item = OntologySignal.from_dict(signal)
    payload = item.to_dict()
    payload["signal_id"] = _signal_id(item, base_dir=base_dir)
    payload["created_at"] = _now_iso()
    path = _signal_path(base_dir, item.workspace_id, payload["signal_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def list_ontology_signals(
    workspace_id: str,
    source: Optional[str] = None,
    kind: Optional[str] = None,
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> List[Dict[str, Any]]:
    path = _workspace_dir(base_dir, workspace_id) / "signals"
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for file_path in path.glob("*.json"):
        with file_path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if source and payload.get("source") != source:
            continue
        if kind and payload.get("kind") != kind:
            continue
        rows.append(payload)
    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows


def compile_ontology_profile(
    workspace_id: str,
    profile_id: str,
    *,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    control = _control_plane_for_workspace(base_dir, workspace_id)
    return control.compile_profile(profile_id, workspace_id=workspace_id).to_dict()


def select_ontology_profile(
    workspace_id: str,
    question: str,
    *,
    route_profile: Optional[Dict[str, Any]] = None,
    include_drafts: bool = False,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    control = _control_plane_for_workspace(base_dir, workspace_id)
    return control.select_profile(
        question,
        workspace_id=workspace_id,
        route_profile=route_profile,
        include_drafts=include_drafts,
    ).to_dict()


def evaluate_ontology_profile(
    workspace_id: str,
    profile_id: str,
    *,
    baseline_profile_id: Optional[str] = None,
    base_dir: str = DEFAULT_ONTOLOGY_CONTROL_PLANE_DIR,
) -> Dict[str, Any]:
    control = _control_plane_for_workspace(base_dir, workspace_id)
    return control.evaluate_profile(
        profile_id,
        baseline=baseline_profile_id,
        workspace_id=workspace_id,
    ).to_dict()


def _control_plane_for_workspace(base_dir: str, workspace_id: str) -> OntologyControlPlane:
    profiles = [
        OntologyProfile.from_dict(payload)
        for payload in (
            get_ontology_profile(workspace_id, row["profile_id"], base_dir=base_dir)
            for row in list_ontology_profiles(workspace_id, base_dir=base_dir)
        )
    ]
    registry = OntologyProfileRegistry(profiles)
    control = OntologyControlPlane(registry)
    for signal in list_ontology_signals(workspace_id, base_dir=base_dir):
        control.collect_signal(signal)
    return control


def _profile_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "profile_id": payload.get("profile_id", ""),
        "workspace_id": payload.get("workspace_id", "default"),
        "ontology_id": payload.get("ontology_id", ""),
        "version": payload.get("version", ""),
        "status": payload.get("status", "draft"),
        "updated_at": payload.get("updated_at"),
        "created_at": payload.get("created_at"),
        "metrics": payload.get("metrics", {}),
        "tags": payload.get("tags", []),
    }


def _workspace_dir(base_dir: str, workspace_id: str) -> Path:
    return Path(base_dir) / workspace_id


def _profile_path(base_dir: str, workspace_id: str, profile_id: str) -> Path:
    return _workspace_dir(base_dir, workspace_id) / "profiles" / f"{profile_id}.json"


def _signal_path(base_dir: str, workspace_id: str, signal_id: str) -> Path:
    return _workspace_dir(base_dir, workspace_id) / "signals" / f"{signal_id}.json"


def _signal_id(signal: OntologySignal, *, base_dir: str) -> str:
    count = len(list_ontology_signals(signal.workspace_id, base_dir=base_dir)) + 1
    return f"os_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{count:04d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
