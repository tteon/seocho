#!/usr/bin/env python3
"""Persist and optionally approve a mined context-graph guardrail artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "extraction"))

from semantic_artifact_store import (  # noqa: E402
    approve_semantic_artifact,
    get_semantic_artifact,
    save_semantic_artifact,
)


def _load_candidate(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = ("ontology_candidate", "shacl_candidate")
    missing = [key for key in required if not isinstance(payload.get(key), dict)]
    if missing:
        raise ValueError(f"candidate missing required object(s): {', '.join(missing)}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ontology_identity_hash(candidate: dict[str, Any]) -> str:
    graph = (candidate.get("source_summary") or {}).get("graph_observation")
    if not isinstance(graph, dict):
        return ""
    hashes = graph.get("ontology_context_hashes")
    if not isinstance(hashes, list):
        return ""
    for value in hashes:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="SemanticArtifactDraftInput JSON")
    ap.add_argument("--workspace-id", default="contextgraph_guardrails")
    ap.add_argument("--artifact-dir", default="outputs/semantic_artifacts")
    ap.add_argument("--name", default=None)
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--approved-by", default="codex")
    ap.add_argument("--approval-note", default="Approved from contextgraph guardrail experiment.")
    ap.add_argument("--no-governance-enforce", action="store_true")
    ap.add_argument("--no-reasoner", action="store_true")
    ap.add_argument("--out", required=True, help="summary JSON output path")
    args = ap.parse_args()

    candidate = _load_candidate(Path(args.candidate))
    saved = save_semantic_artifact(
        workspace_id=args.workspace_id,
        name=args.name or candidate.get("name"),
        ontology_candidate=candidate["ontology_candidate"],
        shacl_candidate=candidate["shacl_candidate"],
        vocabulary_candidate=candidate.get("vocabulary_candidate"),
        source_summary=candidate.get("source_summary"),
        base_dir=args.artifact_dir,
        ontology_identity_hash=_ontology_identity_hash(candidate),
    )

    status = "draft"
    error = ""
    approved: dict[str, Any] | None = None
    if args.approve:
        try:
            approved = approve_semantic_artifact(
                workspace_id=args.workspace_id,
                artifact_id=str(saved["artifact_id"]),
                approved_by=args.approved_by,
                approval_note=args.approval_note,
                base_dir=args.artifact_dir,
                governance_enforce=not args.no_governance_enforce,
                run_reasoner=not args.no_reasoner,
            )
            status = "approved"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            saved = get_semantic_artifact(
                workspace_id=args.workspace_id,
                artifact_id=str(saved["artifact_id"]),
                base_dir=args.artifact_dir,
            )
            status = str(saved.get("status") or "draft")

    final_payload = approved or saved
    summary = {
        "workspace_id": args.workspace_id,
        "artifact_id": final_payload["artifact_id"],
        "artifact_path": str(Path(args.artifact_dir) / args.workspace_id / f"{final_payload['artifact_id']}.json"),
        "name": final_payload.get("name"),
        "status": status,
        "approved_at": final_payload.get("approved_at"),
        "governance": final_payload.get("governance"),
        "error": error,
    }
    _write_json(Path(args.out), summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if error and args.approve and not args.no_governance_enforce else 0


if __name__ == "__main__":
    raise SystemExit(main())
