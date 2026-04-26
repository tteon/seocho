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
    vocabulary_candidate: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    source_summary: Optional[Dict[str, Any]] = None,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
    *,
    ontology_identity_hash: str = "",
) -> Dict[str, Any]:
    """Persist a semantic artifact draft for later approval/deprecation.

    Phase 5: ``ontology_identity_hash`` (typically
    ``OntologyContextDescriptor.context_hash``) is stamped on the
    payload so the loader can refuse to apply an artifact whose source
    ontology identity has drifted from the active runtime ontology.
    Empty string preserves the pre-Phase-5 legacy behavior.
    """

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
        "vocabulary_candidate": vocabulary_candidate or {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        "ontology_identity_hash": str(ontology_identity_hash or ""),
        "approved_by": None,
        "approval_note": None,
        "approved_at": None,
        "deprecated_by": None,
        "deprecation_note": None,
        "deprecated_at": None,
    }

    artifact_path = workspace_path / f"{artifact_id}.json"
    with artifact_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def get_semantic_artifact(
    workspace_id: str,
    artifact_id: str,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
    *,
    expected_ontology_hash: str = "",
) -> Dict[str, Any]:
    """Load a semantic artifact by id.

    Phase 5: when ``expected_ontology_hash`` is non-empty, the returned
    payload carries an ``artifact_ontology_mismatch`` block summarizing
    whether the stored ``ontology_identity_hash`` matches. Reads
    themselves don't fail on mismatch — the caller decides whether to
    refuse application — so existing audit/inspection paths keep
    working while promotion gates can hard-block.
    """

    artifact_path = _workspace_dir(base_dir, workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    if expected_ontology_hash:
        payload["artifact_ontology_mismatch"] = assess_artifact_ontology_match(
            payload, active_ontology_hash=expected_ontology_hash
        )
    return payload


def assess_artifact_ontology_match(
    payload: Dict[str, Any],
    *,
    active_ontology_hash: str,
) -> Dict[str, Any]:
    """Compare an artifact's stored ontology_identity_hash to the active hash.

    Returns a structured dict callers can attach to responses or check
    before applying the artifact's contents. ``mismatch`` is True only
    when both sides are non-empty and differ — empty stored hash means
    "pre-Phase-5 artifact, parity unknowable" and is reported with
    ``status="unknown"`` rather than ``mismatch``.
    """

    stored = str(payload.get("ontology_identity_hash", "") or "").strip()
    active = str(active_ontology_hash or "").strip()
    if not stored:
        return {
            "stored_ontology_hash": "",
            "active_ontology_hash": active,
            "mismatch": False,
            "status": "unknown",
            "warning": (
                "Artifact has no ontology_identity_hash stamped. "
                "Re-save under Phase 5 to gain hash parity guarantees."
            ),
        }
    if stored == active:
        return {
            "stored_ontology_hash": stored,
            "active_ontology_hash": active,
            "mismatch": False,
            "status": "match",
            "warning": "",
        }
    return {
        "stored_ontology_hash": stored,
        "active_ontology_hash": active,
        "mismatch": True,
        "status": "drift",
        "warning": (
            "Artifact ontology_identity_hash differs from active runtime hash. "
            "Refuse application or re-derive the artifact from the active ontology."
        ),
    }


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
            "deprecated_at": payload.get("deprecated_at"),
            "deprecated_by": payload.get("deprecated_by"),
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
    payload["deprecated_by"] = None
    payload["deprecation_note"] = None
    payload["deprecated_at"] = None

    with artifact_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    return payload


def deprecate_semantic_artifact(
    workspace_id: str,
    artifact_id: str,
    deprecated_by: str,
    deprecation_note: Optional[str] = None,
    base_dir: str = DEFAULT_SEMANTIC_ARTIFACT_DIR,
) -> Dict[str, Any]:
    artifact_path = _workspace_dir(base_dir, workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if payload.get("status") != "approved":
        raise ValueError(
            f"semantic artifact '{artifact_id}' must be approved before deprecation "
            f"(status={payload.get('status')})"
        )

    payload["status"] = "deprecated"
    payload["deprecated_by"] = deprecated_by
    payload["deprecation_note"] = deprecation_note
    payload["deprecated_at"] = _now_iso()

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
