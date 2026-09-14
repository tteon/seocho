"""Read-only semantic artifact projection shared by query consumers."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SEMANTIC_ARTIFACT_DIR = "outputs/semantic_artifacts"


def list_artifacts(
    base_dir: str,
    workspace_id: str,
    *,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    workspace_path = Path(base_dir) / workspace_id
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
            "deprecated_at": payload.get("deprecated_at"),
            "deprecated_by": payload.get("deprecated_by"),
        }
        if status and row["status"] != status:
            continue
        rows.append(row)
    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows


def get_artifact(base_dir: str, workspace_id: str, artifact_id: str) -> Dict[str, Any]:
    artifact_path = (Path(base_dir) / workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)
