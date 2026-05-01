from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4


DEFAULT_SEMANTIC_METADATA_DIR = "outputs/semantic_metadata"
logger = logging.getLogger(__name__)


class RunMetadataRegistry:
    """Persist semantic query run metadata outside the graph store."""

    def __init__(self, path: Optional[str] = None) -> None:
        default_path = (
            os.getenv("SEOCHO_SEMANTIC_METADATA_DB")
            or os.getenv("SEOCHO_RUN_METADATA_PATH")
            or DEFAULT_SEMANTIC_METADATA_DIR
        )
        self.path = path or default_path

    def record_run(
        self,
        *,
        question: str,
        workspace_id: str,
        route: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
        response: str,
    ) -> Dict[str, Any]:
        timestamp = datetime.now(timezone.utc).isoformat()
        run_id = f"run_{uuid4().hex}"
        evidence_bundle = semantic_context.get("evidence_bundle_preview", {})
        support_assessment = semantic_context.get("support_assessment", {})
        strategy_decision = semantic_context.get("strategy_decision", {})
        record = {
            "schema_version": "semantic_run_registry.v1",
            "run_id": run_id,
            "timestamp": timestamp,
            "workspace_id": workspace_id,
            "query_preview": question[:240],
            "query_hash": sha1(question.encode("utf-8")).hexdigest(),
            "route": route,
            "intent_id": str(semantic_context.get("intent", {}).get("intent_id", "")).strip(),
            "support_assessment": support_assessment,
            "strategy_decision": strategy_decision,
            "reasoning": semantic_context.get("reasoning", {}),
            "evidence_summary": {
                "grounded_slots": list(evidence_bundle.get("grounded_slots", [])),
                "missing_slots": list(evidence_bundle.get("missing_slots", [])),
                "selected_triple_count": len(evidence_bundle.get("selected_triples", [])),
                "confidence": float(evidence_bundle.get("confidence", 0.0) or 0.0),
            },
            "lpg_record_count": len((lpg_result or {}).get("records", [])),
            "rdf_record_count": len((rdf_result or {}).get("records", [])),
            "response_preview": response[:240],
        }

        try:
            stored = self._save_semantic_run(record)
            recorded = True
        except Exception:
            recorded = False
            stored = {"db_path": self.path}
            logger.warning("Failed to persist semantic run metadata.", exc_info=True)

        return {
            "schema_version": "semantic_run_registry.v1",
            "run_id": run_id,
            "recorded": recorded,
            "registry_path": str(stored.get("db_path", self.path)),
            "timestamp": timestamp,
        }

    def _save_semantic_run(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as conn:
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("run_id", "")).strip(),
                    str(record.get("workspace_id", "")).strip(),
                    str(record.get("timestamp", "")).strip() or _now_iso(),
                    str(record.get("route", "")).strip(),
                    str(record.get("intent_id", "")).strip(),
                    str(record.get("query_preview", "")),
                    str(record.get("query_hash", "")),
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
            "db_path": str(self._resolve_db_path()),
        }

    def _connect(self) -> sqlite3.Connection:
        db_path = self._resolve_db_path()
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

    def _resolve_db_path(self) -> Path:
        configured = str(os.getenv("SEOCHO_SEMANTIC_METADATA_DB", self.path)).strip() or self.path
        base_path = Path(configured)
        if base_path.suffix in {".db", ".sqlite", ".sqlite3"}:
            return base_path
        return base_path / "semantic_runs.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
