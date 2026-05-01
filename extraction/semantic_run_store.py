from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_SEMANTIC_METADATA_DIR = "outputs/semantic_metadata"


def save_semantic_run(
    record: Dict[str, Any],
    *,
    base_dir: str = DEFAULT_SEMANTIC_METADATA_DIR,
) -> Dict[str, Any]:
    semantic_package = (
        record.get("semantic_package", {})
        if isinstance(record.get("semantic_package", {}), dict)
        else {}
    )
    with _connect(base_dir) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO semantic_runs (
              run_id,
              workspace_id,
              timestamp,
              route,
              intent_id,
              query_preview,
              query_hash,
              semantic_package_id,
              semantic_package_hash,
              semantic_package_json,
              stage_metrics_json,
              policy_metrics_json,
              support_status,
              support_reason,
              support_coverage,
              support_assessment_json,
              strategy_decision_json,
              reasoning_json,
              evidence_summary_json,
              lpg_record_count,
              rdf_record_count,
              response_preview,
              record_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.get("run_id", "")).strip(),
                str(record.get("workspace_id", "")).strip(),
                str(record.get("timestamp", "")).strip() or _now_iso(),
                str(record.get("route", "")).strip(),
                str(record.get("intent_id", "")).strip(),
                str(record.get("query_preview", "")),
                str(record.get("query_hash", "")),
                str(record.get("semantic_package_id") or semantic_package.get("package_id", "")).strip(),
                str(record.get("semantic_package_hash") or semantic_package.get("package_hash", "")).strip(),
                json.dumps(semantic_package, ensure_ascii=True),
                json.dumps(record.get("stage_metrics", {}), ensure_ascii=True),
                json.dumps(record.get("policy_metrics", {}), ensure_ascii=True),
                str(record.get("support_assessment", {}).get("status", "")).strip(),
                str(record.get("support_assessment", {}).get("reason", "")).strip(),
                float(record.get("support_assessment", {}).get("coverage", 0.0) or 0.0),
                json.dumps(record.get("support_assessment", {}), ensure_ascii=True),
                json.dumps(record.get("strategy_decision", {}), ensure_ascii=True),
                json.dumps(record.get("reasoning", {}), ensure_ascii=True),
                json.dumps(record.get("evidence_summary", {}), ensure_ascii=True),
                int(record.get("lpg_record_count", 0) or 0),
                int(record.get("rdf_record_count", 0) or 0),
                str(record.get("response_preview", "")),
                json.dumps(record, ensure_ascii=True),
            ),
        )
    return {
        "run_id": str(record.get("run_id", "")).strip(),
        "timestamp": str(record.get("timestamp", "")).strip() or _now_iso(),
        "db_path": str(_resolve_db_path(base_dir)),
    }


def get_semantic_run(
    workspace_id: str,
    run_id: str,
    *,
    base_dir: str = DEFAULT_SEMANTIC_METADATA_DIR,
) -> Dict[str, Any]:
    with _connect(base_dir) as conn:
        row = conn.execute(
            """
            SELECT record_json
            FROM semantic_runs
            WHERE workspace_id = ? AND run_id = ?
            """,
            (workspace_id, run_id),
        ).fetchone()
    if row is None:
        raise FileNotFoundError(f"semantic run not found: workspace={workspace_id}, run_id={run_id}")
    return json.loads(str(row["record_json"]))


def list_semantic_runs(
    workspace_id: str,
    *,
    limit: int = 20,
    route: Optional[str] = None,
    intent_id: Optional[str] = None,
    base_dir: str = DEFAULT_SEMANTIC_METADATA_DIR,
) -> List[Dict[str, Any]]:
    where = ["workspace_id = ?"]
    params: List[Any] = [workspace_id]
    if route:
        where.append("route = ?")
        params.append(route)
    if intent_id:
        where.append("intent_id = ?")
        params.append(intent_id)
    params.append(max(1, int(limit or 20)))

    with _connect(base_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT
              run_id,
              workspace_id,
              timestamp,
              route,
              intent_id,
              query_preview,
              semantic_package_id,
              semantic_package_hash,
              semantic_package_json,
              stage_metrics_json,
              policy_metrics_json,
              support_status,
              support_reason,
              support_coverage,
              support_assessment_json,
              strategy_decision_json,
              reasoning_json,
              evidence_summary_json,
              lpg_record_count,
              rdf_record_count,
              response_preview
            FROM semantic_runs
            WHERE {' AND '.join(where)}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "workspace_id": row["workspace_id"],
            "timestamp": row["timestamp"],
            "route": row["route"],
            "intent_id": row["intent_id"],
            "query_preview": row["query_preview"],
            "semantic_package_id": row["semantic_package_id"],
            "semantic_package_hash": row["semantic_package_hash"],
            "semantic_package": json.loads(str(row["semantic_package_json"] or "{}")),
            "stage_metrics": json.loads(str(row["stage_metrics_json"] or "{}")),
            "policy_metrics": json.loads(str(row["policy_metrics_json"] or "{}")),
            "support_status": row["support_status"],
            "support_reason": row["support_reason"],
            "support_coverage": row["support_coverage"],
            "support_assessment": json.loads(str(row["support_assessment_json"] or "{}")),
            "strategy_decision": json.loads(str(row["strategy_decision_json"] or "{}")),
            "reasoning": json.loads(str(row["reasoning_json"] or "{}")),
            "evidence_summary": json.loads(str(row["evidence_summary_json"] or "{}")),
            "lpg_record_count": row["lpg_record_count"],
            "rdf_record_count": row["rdf_record_count"],
            "response_preview": row["response_preview"],
        }
        for row in rows
    ]


def _connect(base_dir: str) -> sqlite3.Connection:
    db_path = _resolve_db_path(base_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_runs (
          run_id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          route TEXT NOT NULL,
          intent_id TEXT NOT NULL,
          query_preview TEXT NOT NULL,
          query_hash TEXT NOT NULL,
          semantic_package_id TEXT NOT NULL DEFAULT '',
          semantic_package_hash TEXT NOT NULL DEFAULT '',
          semantic_package_json TEXT NOT NULL DEFAULT '{}',
          stage_metrics_json TEXT NOT NULL DEFAULT '{}',
          policy_metrics_json TEXT NOT NULL DEFAULT '{}',
          support_status TEXT NOT NULL,
          support_reason TEXT NOT NULL,
          support_coverage REAL NOT NULL,
          support_assessment_json TEXT NOT NULL,
          strategy_decision_json TEXT NOT NULL,
          reasoning_json TEXT NOT NULL,
          evidence_summary_json TEXT NOT NULL,
          lpg_record_count INTEGER NOT NULL,
          rdf_record_count INTEGER NOT NULL,
          response_preview TEXT NOT NULL,
          record_json TEXT NOT NULL
        )
        """
    )
    _ensure_semantic_run_columns(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_semantic_runs_workspace_time
        ON semantic_runs(workspace_id, timestamp DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_semantic_runs_workspace_intent
        ON semantic_runs(workspace_id, intent_id, timestamp DESC)
        """
    )
    return conn


def _resolve_db_path(base_dir: str) -> Path:
    configured = str(os.getenv("SEOCHO_SEMANTIC_METADATA_DB", base_dir)).strip() or base_dir
    base_path = Path(configured)
    if base_path.suffix in {".db", ".sqlite", ".sqlite3"}:
        return base_path
    return base_path / "semantic_runs.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_semantic_run_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(semantic_runs)").fetchall()
    }
    if "semantic_package_id" not in existing:
        conn.execute(
            "ALTER TABLE semantic_runs ADD COLUMN semantic_package_id TEXT NOT NULL DEFAULT ''"
        )
    if "semantic_package_hash" not in existing:
        conn.execute(
            "ALTER TABLE semantic_runs ADD COLUMN semantic_package_hash TEXT NOT NULL DEFAULT ''"
        )
    if "semantic_package_json" not in existing:
        conn.execute(
            "ALTER TABLE semantic_runs ADD COLUMN semantic_package_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "stage_metrics_json" not in existing:
        conn.execute(
            "ALTER TABLE semantic_runs ADD COLUMN stage_metrics_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "policy_metrics_json" not in existing:
        conn.execute(
            "ALTER TABLE semantic_runs ADD COLUMN policy_metrics_json TEXT NOT NULL DEFAULT '{}'"
        )
