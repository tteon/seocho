from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


DEFAULT_SEMANTIC_ARTIFACT_DIR = "outputs/semantic_artifacts"


def save_semantic_artifact(
    workspace_id: str,
    ontology_candidate: Dict[str, Any],
    shacl_candidate: Dict[str, Any],
    name: Optional[str] = None,
    source_summary: Optional[Dict[str, Any]] = None,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
) -> Dict[str, Any]:
    workspace_path = _workspace_dir(base_dir, workspace_id)
    workspace_path.mkdir(parents=True, exist_ok=True)

    artifact_id = _new_artifact_id()
    created_at = _now_iso()
    payload = {
        "artifact_id": artifact_id,
        "workspace_id": workspace_id,
        "name": name or artifact_id,
        "created_at": created_at,
        "status": "draft",
        "source_summary": source_summary or {},
        "ontology_candidate": ontology_candidate,
        "shacl_candidate": shacl_candidate,
        "approved_by": None,
        "approval_note": None,
        "approved_at": None,
    }

    artifact_path = workspace_path / f"{artifact_id}.json"
    with artifact_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def get_semantic_artifact(
    workspace_id: str,
    artifact_id: str,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
) -> Dict[str, Any]:
    artifact_path = _workspace_dir(base_dir, workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def list_semantic_artifacts(
    workspace_id: str,
    status: Optional[str] = None,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
) -> List[Dict[str, Any]]:
    workspace_path = _workspace_dir(base_dir, workspace_id)
    if not workspace_path.exists():
        return []

    rows: List[Dict[str, Any]] = []
    for path in workspace_path.glob("*.json"):
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        row = {
            "artifact_id": payload.get("artifact_id"),
            "workspace_id": payload.get("workspace_id"),
            "name": payload.get("name"),
            "created_at": payload.get("created_at"),
            "status": payload.get("status", "draft"),
            "approved_at": payload.get("approved_at"),
            "approved_by": payload.get("approved_by"),
        }
        if status and row["status"] != status:
            continue
        rows.append(row)
    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows


def approve_semantic_artifact(
    workspace_id: str,
    artifact_id: str,
    approved_by: str,
    approval_note: Optional[str] = None,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
) -> Dict[str, Any]:
    artifact_path = _workspace_dir(base_dir, workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    payload["status"] = "approved"
    payload["approved_by"] = approved_by
    payload["approval_note"] = approval_note
    payload["approved_at"] = _now_iso()

    with artifact_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def _workspace_dir(base_dir: str, workspace_id: str) -> Path:
    return Path(base_dir) / workspace_id


def _new_artifact_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"sa_{ts}_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
