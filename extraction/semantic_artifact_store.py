from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


logger = logging.getLogger(__name__)

DEFAULT_SEMANTIC_ARTIFACT_DIR = "outputs/semantic_artifacts"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically: serialize to a temp file in the same directory,
    fsync, then os.replace into place. A crash or concurrent write can no
    longer leave a truncated artifact that breaks every later read (issue #139).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    _atomic_write_json(artifact_path, payload)
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
        try:
            with path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (json.JSONDecodeError, OSError) as exc:
            # A single corrupt/partially-written artifact must not take down the
            # whole list endpoint (issue #139). Skip it and keep going.
            logger.warning("Skipping unreadable semantic artifact %s: %s", path, exc)
            continue
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
    *,
    governance_enforce: bool = True,
    run_reasoner: bool = True,
) -> Dict[str, Any]:
    """Promote a draft artifact to ``approved``.

    Phase: an **offline** governance gate runs at approval time (file I/O, never
    a query path). It builds the candidate ontology from the artifact and runs
    the structural check + FIBO/ISO-704 hygiene lint + (optional) OWL 2 DL
    consistency reasoner, stamping the verdict onto ``payload["governance"]``.
    When ``governance_enforce`` is True (default) a structural error, lint error,
    or proven inconsistency refuses approval (mirrors the deprecate guard). The
    reasoner degrades to ``available=False`` without a JVM and never blocks on
    its own absence.
    """
    artifact_path = _workspace_dir(base_dir, workspace_id) / f"{artifact_id}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
        )
    with artifact_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    governance = _run_governance_gate(payload, run_reasoner=run_reasoner)
    payload["governance"] = governance
    if governance_enforce and not governance.get("ok", True):
        # Persist the failed verdict so the refusal is auditable, then refuse.
        _atomic_write_json(artifact_path, payload)
        raise ValueError(
            f"semantic artifact '{artifact_id}' failed governance gate "
            f"(structural_ok={governance['structural']['ok']}, "
            f"lint_ok={governance['lint'].get('ok')}, "
            f"consistent={governance['consistency'].get('consistent')}). "
            f"Fix the ontology or approve with governance_enforce=False."
        )

    payload["status"] = "approved"
    payload["approved_by"] = approved_by
    payload["approval_note"] = approval_note
    payload["approved_at"] = _now_iso()
    payload["deprecated_by"] = None
    payload["deprecation_note"] = None
    payload["deprecated_at"] = None

    _atomic_write_json(artifact_path, payload)
    return payload


def _run_governance_gate(payload: Dict[str, Any], *, run_reasoner: bool) -> Dict[str, Any]:
    """Build the candidate ontology from an artifact payload and run the offline
    governance gate. Returns a JSON-serializable verdict with a ``checked_at``
    stamp. A build/gate failure is recorded (not silently swallowed) and treated
    as a structural failure so it cannot pass enforcement silently."""
    from seocho.ontology import Ontology
    from seocho.ontology_governance import governance_gate

    checked_at = _now_iso()

    # A vocabulary-only or empty draft carries no ontology to govern — don't
    # block its approval on "no node types". Governance applies only when the
    # candidate actually declares classes.
    onto_cand = payload.get("ontology_candidate") or {}
    if hasattr(onto_cand, "to_dict"):
        onto_cand = onto_cand.to_dict()
    if not (isinstance(onto_cand, dict) and onto_cand.get("classes")):
        return {
            "ok": True,
            "applicable": False,
            "reason": "artifact declares no ontology classes; governance gate not applicable",
            "checked_at": checked_at,
        }

    try:
        ontology = Ontology.from_artifact(payload)
        verdict = governance_gate(ontology, run_reasoner=run_reasoner)
        verdict["applicable"] = True
    except Exception as exc:  # build or gate failure -> auditable, blocks approval
        return {
            "ok": False,
            "structural": {"ok": False, "errors": [f"governance build failed: {exc}"], "warnings": []},
            "lint": {"ok": False, "errors": [str(exc)], "warnings": [], "findings": []},
            "consistency": {"consistent": None, "available": False, "reasoner": None, "error": str(exc), "unsatisfiable_classes": []},
            "checked_at": checked_at,
        }
    verdict["checked_at"] = checked_at
    return verdict


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

    _atomic_write_json(artifact_path, payload)
    return payload


def _workspace_dir(base_dir: str, workspace_id: str) -> Path:
    return Path(base_dir) / workspace_id


def _new_artifact_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"sa_{ts}_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
