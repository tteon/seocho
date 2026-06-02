from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .curation_design import CurationDesignSpec
from .qualification import (
    CanonicalEntityRecord,
    CanonicalRelationRecord,
    CurationDecisionResult,
    CurationPreview,
    GraphProjectionSnapshot,
    QualificationCase,
    QualificationRunResult,
)
from .store.llm import complete_with_task_hints

logger = logging.getLogger(__name__)

_SYSTEM_LABELS = {"Document", "DocumentVersion", "Section", "Chunk"}
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: Any, *, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _NORMALIZE_RE.sub(" ", text).strip()


def _slug(value: Any) -> str:
    normalized = _NORMALIZE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "unknown"


def _qualifier_hash(properties: Mapping[str, Any]) -> str:
    filtered = {
        str(key): value
        for key, value in dict(properties).items()
        if key not in {"workspace_id", "memory_id", "source_id"}
    }
    payload = _json_dumps(filtered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class QualificationStore:
    """SQLite-default embedded store for qualification and curation artifacts.

    SQLite is the default mutable curation store because the workload is
    case/decision oriented rather than analytical. DuckDB remains available as
    an optional backend when callers want larger offline batch analysis against
    the same tabular contract.
    """

    def __init__(self, path: str, *, backend: str = "sqlite") -> None:
        self.path = path
        self.backend = backend
        self._lock = threading.RLock()
        self._conn, self.backend_name = self._connect(path, backend=backend)
        self.ensure_schema()

    @staticmethod
    def _connect(path: str, *, backend: str) -> tuple[Any, str]:
        if backend not in {"auto", "duckdb", "sqlite"}:
            raise ValueError("backend must be one of: auto, duckdb, sqlite")

        if backend in {"auto", "duckdb"}:
            try:
                import duckdb

                if path != ":memory:":
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                return duckdb.connect(path), "duckdb"
            except Exception:
                if backend == "duckdb":
                    raise

        sqlite_path = ":memory:" if path == ":memory:" else path
        if sqlite_path != ":memory:":
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        return cursor

    def _executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        cursor = self._conn.cursor()
        cursor.executemany(sql, [tuple(row) for row in rows])

    def _commit(self) -> None:
        try:
            self._conn.commit()
        except Exception:
            # duckdb autocommit path is fine.
            pass

    def _rows(self, cursor: Any) -> List[Dict[str, Any]]:
        fetched = cursor.fetchall()
        if not fetched:
            return []
        first = fetched[0]
        if isinstance(first, sqlite3.Row):
            return [dict(item) for item in fetched]
        description = cursor.description or []
        columns = [str(item[0]) for item in description]
        return [dict(zip(columns, row)) for row in fetched]

    def ensure_schema(self) -> None:
        with self._lock:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS documents (
                    source_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version_id TEXT,
                    content TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version_id TEXT,
                    ordinal INTEGER,
                    text TEXT,
                    section_path TEXT,
                    section_title TEXT,
                    section_level INTEGER,
                    entity_ids_json TEXT,
                    metadata_json TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS observed_entities (
                    observed_entity_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    name TEXT,
                    normalized_name TEXT,
                    attrs_json TEXT,
                    chunk_ids_json TEXT,
                    document_id TEXT,
                    version_id TEXT,
                    confidence REAL,
                    ontology_context_hash TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS observed_relations (
                    observed_relation_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    source_observed_entity_id TEXT NOT NULL,
                    target_observed_entity_id TEXT NOT NULL,
                    attrs_json TEXT,
                    qualifier_hash TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS entity_pair_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    left_id TEXT NOT NULL,
                    right_id TEXT NOT NULL,
                    text_score REAL,
                    graph_score REAL,
                    llm_score REAL,
                    total_score REAL,
                    blocked_reason TEXT,
                    recommended_action TEXT,
                    rationale_json TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS qualification_runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    curation_design_name TEXT NOT NULL,
                    curation_design_hash TEXT NOT NULL,
                    curation_design_json TEXT NOT NULL,
                    modes_json TEXT,
                    scope_json TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    summary_json TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS qualification_cases (
                    case_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    case_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    candidate_ids_json TEXT NOT NULL,
                    scores_json TEXT NOT NULL,
                    rationale_json TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    blocked_reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS curation_decisions (
                    decision_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    canonical_entity_id TEXT,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS canonical_entities (
                    entity_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    identity_key TEXT,
                    attrs_json TEXT NOT NULL,
                    support_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS entity_memberships (
                    observed_entity_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (observed_entity_id, entity_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS canonical_relations (
                    relation_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    source_entity_id TEXT NOT NULL,
                    target_entity_id TEXT NOT NULL,
                    qualifier_key TEXT NOT NULL,
                    attrs_json TEXT NOT NULL,
                    support_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS relation_support (
                    observed_relation_id TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    PRIMARY KEY (observed_relation_id, relation_id, snapshot_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS promotion_events (
                    snapshot_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    run_id TEXT,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
            ]
            for statement in statements:
                self._execute(statement)
            self._commit()

    def record_indexing_result(
        self,
        *,
        result: Any,
        workspace_id: str,
        graph_id: str,
        database: str,
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        with self._lock:
            observed_nodes = list(getattr(result, "observed_nodes", None) or getattr(result, "nodes", []) or [])
            observed_relationships = list(
                getattr(result, "observed_relationships", None) or getattr(result, "relationships", []) or []
            )
            chunk_records = [dict(item) for item in (getattr(result, "chunk_records", None) or []) if isinstance(item, dict)]
            layered_summary = dict(getattr(result, "layered_graph_summary", None) or {})
            ontology_context = dict(getattr(result, "ontology_context", None) or {})
            source_id = str(getattr(result, "source_id", "") or "")
            document_id = str(layered_summary.get("document_id") or f"{source_id}_doc")
            version_id = str(layered_summary.get("version_id") or "")
            now = _utc_now_iso()
            metadata_json = _json_dumps(metadata or {})

            self._execute("DELETE FROM documents WHERE source_id = ?", (source_id,))
            self._execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
            self._execute("DELETE FROM observed_entities WHERE source_id = ?", (source_id,))
            self._execute("DELETE FROM observed_relations WHERE source_id = ?", (source_id,))

            self._execute(
                """
                INSERT INTO documents (
                    source_id, workspace_id, graph_id, database_name, memory_id,
                    document_id, version_id, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    workspace_id,
                    graph_id,
                    database,
                    source_id,
                    document_id,
                    version_id,
                    content,
                    metadata_json,
                    now,
                ),
            )

            for record in chunk_records:
                self._execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, source_id, workspace_id, graph_id, database_name,
                        document_id, version_id, ordinal, text, section_path,
                        section_title, section_level, entity_ids_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.get("chunk_id") or ""),
                        source_id,
                        workspace_id,
                        graph_id,
                        database,
                        str(record.get("document_id") or document_id),
                        str(record.get("version_id") or version_id),
                        int(record.get("ordinal", 0) or 0),
                        str(record.get("text") or ""),
                        str(record.get("section_path") or ""),
                        str(record.get("section_title") or ""),
                        (
                            int(record["section_level"])
                            if record.get("section_level") not in (None, "")
                            else None
                        ),
                        _json_dumps(list(record.get("entity_ids") or [])),
                        _json_dumps(record),
                    ),
                )

            chunk_ids_by_entity: Dict[str, List[str]] = {}
            for record in chunk_records:
                chunk_id = str(record.get("chunk_id") or "").strip()
                if not chunk_id:
                    continue
                for entity_id in record.get("entity_ids", []) or []:
                    canonical = str(entity_id).strip()
                    if not canonical:
                        continue
                    chunk_ids_by_entity.setdefault(canonical, []).append(chunk_id)

            observed_entity_ids: set[str] = set()
            for node in observed_nodes:
                if not isinstance(node, Mapping):
                    continue
                label = str(node.get("label", "")).strip()
                if label in _SYSTEM_LABELS:
                    continue
                observed_entity_id = str(node.get("id", "")).strip()
                if not observed_entity_id:
                    continue
                observed_entity_ids.add(observed_entity_id)
                properties = dict(node.get("properties", {})) if isinstance(node.get("properties"), Mapping) else {}
                name = str(properties.get("name") or properties.get("title") or observed_entity_id)
                self._execute(
                    """
                    INSERT INTO observed_entities (
                        observed_entity_id, source_id, workspace_id, graph_id, database_name,
                        label, name, normalized_name, attrs_json, chunk_ids_json,
                        document_id, version_id, confidence, ontology_context_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observed_entity_id,
                        source_id,
                        workspace_id,
                        graph_id,
                        database,
                        label,
                        name,
                        _normalize_name(name),
                        _json_dumps(properties),
                        _json_dumps(chunk_ids_by_entity.get(observed_entity_id, [])),
                        document_id,
                        version_id,
                        float(properties.get("confidence", 0.0) or 0.0),
                        str(ontology_context.get("context_hash", "") or ""),
                    ),
                )

            for index, rel in enumerate(observed_relationships):
                if not isinstance(rel, Mapping):
                    continue
                source = str(rel.get("source", "")).strip()
                target = str(rel.get("target", "")).strip()
                if source not in observed_entity_ids or target not in observed_entity_ids:
                    continue
                rel_type = str(rel.get("type", "")).strip() or "RELATED_TO"
                properties = dict(rel.get("properties", {})) if isinstance(rel.get("properties"), Mapping) else {}
                observed_relation_id = f"{source_id}_rel_{index:04d}"
                self._execute(
                    """
                    INSERT INTO observed_relations (
                        observed_relation_id, source_id, workspace_id, graph_id, database_name,
                        rel_type, source_observed_entity_id, target_observed_entity_id,
                        attrs_json, qualifier_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observed_relation_id,
                        source_id,
                        workspace_id,
                        graph_id,
                        database,
                        rel_type,
                        source,
                        target,
                        _json_dumps(properties),
                        _qualifier_hash(properties),
                    ),
                )

            self._commit()
            return {
                "documents_recorded": 1,
                "chunks_recorded": len(chunk_records),
                "observed_entities_recorded": len(observed_entity_ids),
                "observed_relations_recorded": len(
                    [rel for rel in observed_relationships if str(rel.get("source", "")).strip() in observed_entity_ids]
                ),
                "store_backend": self.backend_name,
            }

    def qualify_graph(
        self,
        *,
        workspace_id: str,
        graph_id: str,
        database: str,
        ontology: Any,
        curation_design: CurationDesignSpec,
        llm: Any = None,
        modes: Sequence[str] = ("text", "graph", "llm"),
        scope: Optional[Dict[str, Any]] = None,
    ) -> QualificationRunResult:
        with self._lock:
            run_id = f"qual_{uuid.uuid4().hex[:12]}"
            started_at = _utc_now_iso()
            normalized_modes = [str(item).strip().lower() for item in modes if str(item).strip()]
            scope = dict(scope or {})
            self._execute(
                """
                INSERT INTO qualification_runs (
                    run_id, workspace_id, graph_id, database_name,
                    curation_design_name, curation_design_hash, curation_design_json,
                    modes_json, scope_json, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workspace_id,
                    graph_id,
                    database,
                    curation_design.name,
                    curation_design.design_hash,
                    _json_dumps(curation_design.to_dict()),
                    _json_dumps(normalized_modes),
                    _json_dumps(scope),
                    "running",
                    started_at,
                ),
            )

            entities = self._load_observed_entities(
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
                scope=scope,
            )
            relations = self._load_observed_relations(
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
            )
            neighbor_signatures = self._build_neighbor_signatures(entities, relations)
            candidate_pairs = self._build_entity_candidate_pairs(
                entities,
                curation_design=curation_design,
                ontology=ontology,
            )

            auto_promotable = 0
            case_count = 0
            now = _utc_now_iso()
            for left, right in candidate_pairs:
                left_label = str(left["label"])
                policy = curation_design.get_entity_policy(left_label, ontology=ontology)
                blocked_reasons = self._entity_blocked_reasons(left, right, policy)
                text_score = self._text_score(left, right) if "text" in normalized_modes else None
                graph_score = (
                    self._graph_score(
                        str(left["observed_entity_id"]),
                        str(right["observed_entity_id"]),
                        neighbor_signatures,
                    )
                    if "graph" in normalized_modes
                    else None
                )
                llm_score = None
                llm_rationale = ""
                if "llm" in normalized_modes:
                    llm_score, llm_rationale = self._llm_score(left, right, llm=llm)
                total_score = self._average_score(text_score, graph_score, llm_score)
                if not blocked_reasons and total_score < min(policy.auto_merge_threshold, 0.75):
                    continue
                recommended_action = (
                    "keep_separate"
                    if blocked_reasons
                    else ("merge" if total_score >= policy.auto_merge_threshold else "review_merge")
                )
                if recommended_action == "merge":
                    auto_promotable += 1
                case_id = f"case_{uuid.uuid4().hex[:12]}"
                candidate_id = f"cand_{uuid.uuid4().hex[:12]}"
                rationale = {
                    "left": left,
                    "right": right,
                    "policy": asdict(policy),
                    "llm_rationale": llm_rationale,
                }
                scores = {
                    "text": text_score,
                    "graph": graph_score,
                    "llm": llm_score,
                    "total": total_score,
                }
                blocked_reason = "; ".join(blocked_reasons)
                self._execute(
                    """
                    INSERT INTO entity_pair_candidates (
                        candidate_id, run_id, left_id, right_id,
                        text_score, graph_score, llm_score, total_score,
                        blocked_reason, recommended_action, rationale_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        run_id,
                        str(left["observed_entity_id"]),
                        str(right["observed_entity_id"]),
                        text_score,
                        graph_score,
                        llm_score,
                        total_score,
                        blocked_reason,
                        recommended_action,
                        _json_dumps(rationale),
                    ),
                )
                self._execute(
                    """
                    INSERT INTO qualification_cases (
                        case_id, run_id, case_type, status,
                        candidate_ids_json, scores_json, rationale_json,
                        recommended_action, blocked_reasons_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        run_id,
                        "possible_duplicate_entity",
                        "open",
                        _json_dumps([left["observed_entity_id"], right["observed_entity_id"]]),
                        _json_dumps(scores),
                        _json_dumps(rationale),
                        recommended_action,
                        _json_dumps(blocked_reasons),
                        now,
                        now,
                    ),
                )
                case_count += 1

            summary = {
                "observed_entity_count": len(entities),
                "observed_relation_count": len(relations),
                "case_count": case_count,
                "auto_promotable_cases": auto_promotable,
            }
            completed_at = _utc_now_iso()
            self._execute(
                """
                UPDATE qualification_runs
                SET status = ?, completed_at = ?, summary_json = ?
                WHERE run_id = ?
                """,
                ("completed", completed_at, _json_dumps(summary), run_id),
            )
            self._commit()
            return QualificationRunResult(
                run_id=run_id,
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
                store_backend=self.backend_name,
                curation_design_name=curation_design.name,
                modes=normalized_modes,
                observed_entity_count=len(entities),
                observed_relation_count=len(relations),
                case_count=case_count,
                auto_promotable_cases=auto_promotable,
                status="completed",
                summary=summary,
            )

    def list_cases(
        self,
        *,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
        case_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[QualificationCase]:
        with self._lock:
            clauses: List[str] = []
            params: List[Any] = []
            if run_id:
                clauses.append("run_id = ?")
                params.append(run_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            if case_type:
                clauses.append("case_type = ?")
                params.append(case_type)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = self._execute(
                f"""
                SELECT *
                FROM qualification_cases
                {where}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                [*params, int(limit)],
            )
            rows = self._rows(cursor)
            return [self._row_to_case(row) for row in rows]

    def preview_decision(
        self,
        case_id: str,
        *,
        action: str,
        chosen_canonical_id: Optional[str] = None,
        property_resolution: Optional[Dict[str, Any]] = None,
    ) -> CurationPreview:
        with self._lock:
            case = self._load_case(case_id)
            run = self._load_run(case.run_id)
            design = CurationDesignSpec.from_dict(_json_loads(run["curation_design_json"], default={}))
            entities = [self._load_observed_entity(entity_id) for entity_id in case.candidate_ids]
            if action != "merge":
                return CurationPreview(
                    case_id=case_id,
                    action=action,
                    candidate_ids=list(case.candidate_ids),
                    blocked_reasons=list(case.blocked_reasons),
                )

            label = str(entities[0]["label"]) if entities else "Entity"
            policy = design.get_entity_policy(label)
            merged_properties, blocked_reasons = self._merge_entity_properties(
                entities,
                policy=policy,
                property_resolution=property_resolution,
            )
            blocked_reasons = list(dict.fromkeys([*case.blocked_reasons, *blocked_reasons]))
            canonical_name = str(
                merged_properties.get("name")
                or merged_properties.get("canonical_name")
                or entities[0].get("name")
                or entities[0]["observed_entity_id"]
            )
            canonical_entity_id = chosen_canonical_id or self._canonical_entity_id(label, canonical_name)
            return CurationPreview(
                case_id=case_id,
                action=action,
                canonical_entity_id=canonical_entity_id,
                candidate_ids=list(case.candidate_ids),
                merged_properties=merged_properties,
                property_diff={
                    "candidates": entities,
                    "resolved_properties": merged_properties,
                },
                blocked_reasons=blocked_reasons,
            )

    def apply_decision(
        self,
        case_id: str,
        *,
        action: str,
        actor_id: str = "local-user",
        actor_type: str = "user",
        chosen_canonical_id: Optional[str] = None,
        property_resolution: Optional[Dict[str, Any]] = None,
    ) -> CurationDecisionResult:
        with self._lock:
            preview = self.preview_decision(
                case_id,
                action=action,
                chosen_canonical_id=chosen_canonical_id,
                property_resolution=property_resolution,
            )
            if action == "merge" and preview.blocked_reasons:
                raise ValueError(
                    "Cannot apply merge decision while blocked reasons remain: "
                    + "; ".join(preview.blocked_reasons)
                )

            case = self._load_case(case_id)
            run = self._load_run(case.run_id)
            decision_id = f"decision_{uuid.uuid4().hex[:12]}"
            now = _utc_now_iso()
            decision_payload = {
                "preview": preview.to_dict(),
                "property_resolution": property_resolution or {},
            }
            self._execute(
                """
                INSERT INTO curation_decisions (
                    decision_id, case_id, action, actor_type, actor_id,
                    canonical_entity_id, decision_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    case_id,
                    action,
                    actor_type,
                    actor_id,
                    preview.canonical_entity_id,
                    _json_dumps(decision_payload),
                    now,
                ),
            )
            self._execute(
                "UPDATE qualification_cases SET status = ?, updated_at = ? WHERE case_id = ?",
                ("resolved", now, case_id),
            )

            if action == "merge" and preview.canonical_entity_id:
                merged_properties = dict(preview.merged_properties)
                canonical_name = str(
                    merged_properties.get("name")
                    or merged_properties.get("canonical_name")
                    or preview.canonical_entity_id
                )
                self._execute(
                    """
                    INSERT OR REPLACE INTO canonical_entities (
                        entity_id, workspace_id, graph_id, database_name,
                        entity_type, canonical_name, identity_key,
                        attrs_json, support_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preview.canonical_entity_id,
                        str(run["workspace_id"]),
                        str(run["graph_id"]),
                        str(run["database_name"]),
                        str(merged_properties.get("entity_type") or case.rationale.get("left", {}).get("label") or "Entity"),
                        canonical_name,
                        _normalize_name(canonical_name),
                        _json_dumps(merged_properties),
                        len(preview.candidate_ids),
                        now,
                    ),
                )
                for observed_entity_id in preview.candidate_ids:
                    self._execute(
                        """
                        INSERT OR REPLACE INTO entity_memberships (
                            observed_entity_id, entity_id, decision_id, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            observed_entity_id,
                            preview.canonical_entity_id,
                            decision_id,
                            now,
                        ),
                    )
            self._commit()
            return CurationDecisionResult(
                decision_id=decision_id,
                case_id=case_id,
                action=action,
                status="applied",
                canonical_entity_id=preview.canonical_entity_id,
                summary=decision_payload,
            )

    def build_projection_snapshot(
        self,
        *,
        workspace_id: str,
        graph_id: str,
        database: str,
        run_id: Optional[str] = None,
    ) -> GraphProjectionSnapshot:
        with self._lock:
            entities = self._load_observed_entities(
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
                scope=None,
            )
            relations = self._load_observed_relations(
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
            )
            memberships = self._load_entity_memberships()
            canonical_entities = self._load_canonical_entities(
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
            )

            grouped_entities: Dict[str, List[Dict[str, Any]]] = {}
            for entity in entities:
                observed_entity_id = str(entity["observed_entity_id"])
                canonical_entity_id = memberships.get(observed_entity_id)
                if canonical_entity_id is None:
                    canonical_entity_id = self._singleton_entity_id(entity)
                grouped_entities.setdefault(canonical_entity_id, []).append(entity)

            entity_records: List[CanonicalEntityRecord] = []
            for entity_id, members in grouped_entities.items():
                persisted = canonical_entities.get(entity_id)
                if persisted is not None:
                    properties = dict(persisted["properties"])
                    entity_type = str(persisted["entity_type"])
                    canonical_name = str(persisted["canonical_name"])
                else:
                    primary = members[0]
                    properties = dict(primary["properties"])
                    entity_type = str(primary["label"])
                    canonical_name = str(primary.get("name") or primary["observed_entity_id"])
                    properties.setdefault("name", canonical_name)
                properties.setdefault("entity_type", entity_type)
                entity_records.append(
                    CanonicalEntityRecord(
                        entity_id=entity_id,
                        entity_type=entity_type,
                        canonical_name=canonical_name,
                        properties=properties,
                        support_count=len(members),
                    )
                )

            relation_groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
            for relation in relations:
                source_entity_id = memberships.get(
                    str(relation["source_observed_entity_id"]),
                    self._singleton_entity_id_from_observed_id(str(relation["source_observed_entity_id"])),
                )
                target_entity_id = memberships.get(
                    str(relation["target_observed_entity_id"]),
                    self._singleton_entity_id_from_observed_id(str(relation["target_observed_entity_id"])),
                )
                key = (
                    str(relation["rel_type"]),
                    source_entity_id,
                    target_entity_id,
                    str(relation["qualifier_hash"]),
                )
                relation_groups.setdefault(key, []).append(relation)

            snapshot_id = f"snapshot_{uuid.uuid4().hex[:12]}"
            relation_records: List[CanonicalRelationRecord] = []
            self._execute(
                "DELETE FROM canonical_relations WHERE workspace_id = ? AND graph_id = ? AND database_name = ?",
                (workspace_id, graph_id, database),
            )
            self._execute("DELETE FROM relation_support WHERE snapshot_id = ?", (snapshot_id,))
            for rel_type, source_entity_id, target_entity_id, qualifier_key in relation_groups:
                grouped = relation_groups[(rel_type, source_entity_id, target_entity_id, qualifier_key)]
                relation_id = self._canonical_relation_id(
                    rel_type=rel_type,
                    source_entity_id=source_entity_id,
                    target_entity_id=target_entity_id,
                    qualifier_key=qualifier_key,
                )
                properties = dict(grouped[0]["properties"])
                properties.setdefault("qualifier_hash", qualifier_key)
                relation_records.append(
                    CanonicalRelationRecord(
                        relation_id=relation_id,
                        rel_type=rel_type,
                        source_entity_id=source_entity_id,
                        target_entity_id=target_entity_id,
                        properties=properties,
                        support_count=len(grouped),
                    )
                )
                self._execute(
                    """
                    INSERT OR REPLACE INTO canonical_relations (
                        relation_id, workspace_id, graph_id, database_name,
                        rel_type, source_entity_id, target_entity_id,
                        qualifier_key, attrs_json, support_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation_id,
                        workspace_id,
                        graph_id,
                        database,
                        rel_type,
                        source_entity_id,
                        target_entity_id,
                        qualifier_key,
                        _json_dumps(properties),
                        len(grouped),
                        _utc_now_iso(),
                    ),
                )
                for observed_relation in grouped:
                    self._execute(
                        """
                        INSERT OR REPLACE INTO relation_support (
                            observed_relation_id, relation_id, snapshot_id
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            str(observed_relation["observed_relation_id"]),
                            relation_id,
                            snapshot_id,
                        ),
                    )

            summary = {
                "entity_count": len(entity_records),
                "relationship_count": len(relation_records),
                "run_id": run_id,
            }
            self._execute(
                """
                INSERT INTO promotion_events (
                    snapshot_id, workspace_id, graph_id, database_name,
                    run_id, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    workspace_id,
                    graph_id,
                    database,
                    run_id,
                    _json_dumps(summary),
                    _utc_now_iso(),
                ),
            )
            self._commit()
            return GraphProjectionSnapshot(
                snapshot_id=snapshot_id,
                workspace_id=workspace_id,
                graph_id=graph_id,
                database=database,
                entities=entity_records,
                relationships=relation_records,
                summary=summary,
            )

    def _load_observed_entities(
        self,
        *,
        workspace_id: str,
        graph_id: str,
        database: str,
        scope: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        clauses = [
            "workspace_id = ?",
            "graph_id = ?",
            "database_name = ?",
        ]
        params: List[Any] = [workspace_id, graph_id, database]
        entity_types = list((scope or {}).get("entity_types") or [])
        if entity_types:
            placeholders = ", ".join(["?"] * len(entity_types))
            clauses.append(f"label IN ({placeholders})")
            params.extend(entity_types)
        cursor = self._execute(
            f"""
            SELECT *
            FROM observed_entities
            WHERE {' AND '.join(clauses)}
            ORDER BY label, normalized_name, observed_entity_id
            """,
            params,
        )
        rows = self._rows(cursor)
        loaded: List[Dict[str, Any]] = []
        for row in rows:
            properties = _json_loads(row.get("attrs_json"), default={})
            loaded.append(
                {
                    **row,
                    "properties": properties,
                    "chunk_ids": _json_loads(row.get("chunk_ids_json"), default=[]),
                }
            )
        return loaded

    def _load_observed_relations(
        self,
        *,
        workspace_id: str,
        graph_id: str,
        database: str,
    ) -> List[Dict[str, Any]]:
        cursor = self._execute(
            """
            SELECT *
            FROM observed_relations
            WHERE workspace_id = ? AND graph_id = ? AND database_name = ?
            ORDER BY observed_relation_id
            """,
            (workspace_id, graph_id, database),
        )
        rows = self._rows(cursor)
        loaded: List[Dict[str, Any]] = []
        for row in rows:
            loaded.append(
                {
                    **row,
                    "properties": _json_loads(row.get("attrs_json"), default={}),
                }
            )
        return loaded

    def _load_observed_entity(self, observed_entity_id: str) -> Dict[str, Any]:
        cursor = self._execute(
            "SELECT * FROM observed_entities WHERE observed_entity_id = ?",
            (observed_entity_id,),
        )
        rows = self._rows(cursor)
        if not rows:
            raise KeyError(f"Unknown observed entity id: {observed_entity_id}")
        row = rows[0]
        return {
            **row,
            "properties": _json_loads(row.get("attrs_json"), default={}),
            "chunk_ids": _json_loads(row.get("chunk_ids_json"), default=[]),
        }

    def _load_case(self, case_id: str) -> QualificationCase:
        cursor = self._execute("SELECT * FROM qualification_cases WHERE case_id = ?", (case_id,))
        rows = self._rows(cursor)
        if not rows:
            raise KeyError(f"Unknown curation case: {case_id}")
        return self._row_to_case(rows[0])

    def _load_run(self, run_id: str) -> Dict[str, Any]:
        cursor = self._execute("SELECT * FROM qualification_runs WHERE run_id = ?", (run_id,))
        rows = self._rows(cursor)
        if not rows:
            raise KeyError(f"Unknown qualification run: {run_id}")
        return rows[0]

    def _load_entity_memberships(self) -> Dict[str, str]:
        cursor = self._execute("SELECT observed_entity_id, entity_id FROM entity_memberships")
        rows = self._rows(cursor)
        return {
            str(row["observed_entity_id"]): str(row["entity_id"])
            for row in rows
        }

    def _load_canonical_entities(
        self,
        *,
        workspace_id: str,
        graph_id: str,
        database: str,
    ) -> Dict[str, Dict[str, Any]]:
        cursor = self._execute(
            """
            SELECT *
            FROM canonical_entities
            WHERE workspace_id = ? AND graph_id = ? AND database_name = ?
            """,
            (workspace_id, graph_id, database),
        )
        rows = self._rows(cursor)
        loaded: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            loaded[str(row["entity_id"])] = {
                "entity_id": str(row["entity_id"]),
                "entity_type": str(row["entity_type"]),
                "canonical_name": str(row["canonical_name"]),
                "properties": _json_loads(row.get("attrs_json"), default={}),
            }
        return loaded

    def _build_neighbor_signatures(
        self,
        entities: Sequence[Dict[str, Any]],
        relations: Sequence[Dict[str, Any]],
    ) -> Dict[str, set[str]]:
        names = {
            str(entity["observed_entity_id"]): _normalize_name(entity.get("name"))
            for entity in entities
        }
        signatures: Dict[str, set[str]] = {str(entity["observed_entity_id"]): set() for entity in entities}
        for relation in relations:
            source = str(relation["source_observed_entity_id"])
            target = str(relation["target_observed_entity_id"])
            rel_type = str(relation["rel_type"])
            signatures.setdefault(source, set()).add(f"out:{rel_type}:{names.get(target, target)}")
            signatures.setdefault(target, set()).add(f"in:{rel_type}:{names.get(source, source)}")
        return signatures

    def _build_entity_candidate_pairs(
        self,
        entities: Sequence[Dict[str, Any]],
        *,
        curation_design: CurationDesignSpec,
        ontology: Any,
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for entity in entities:
            by_label.setdefault(str(entity["label"]), []).append(entity)

        pairs: Dict[Tuple[str, str], Tuple[Dict[str, Any], Dict[str, Any]]] = {}
        for label, group in by_label.items():
            policy = curation_design.get_entity_policy(label, ontology=ontology)
            for items in self._group_entity_candidates(group, policy):
                for left, right in combinations(items, 2):
                    pair_key = tuple(sorted([str(left["observed_entity_id"]), str(right["observed_entity_id"])]))
                    pairs[pair_key] = (left, right)
        return list(pairs.values())

    def _group_entity_candidates(
        self,
        entities: Sequence[Dict[str, Any]],
        policy: Any,
    ) -> List[List[Dict[str, Any]]]:
        buckets: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
        for entity in entities:
            observed_entity_id = str(entity["observed_entity_id"])
            normalized_name = _normalize_name(entity.get("name"))
            if normalized_name:
                buckets.setdefault(("name", normalized_name), {})[observed_entity_id] = entity
            properties = dict(entity.get("properties", {}))
            for key in [*policy.identity_keys, *policy.fallback_identity_keys]:
                value = _normalize_name(properties.get(key))
                if value:
                    buckets.setdefault((str(key), value), {})[observed_entity_id] = entity
        return [list(items.values()) for items in buckets.values() if len(items) > 1]

    def _entity_blocked_reasons(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        policy: Any,
    ) -> List[str]:
        reasons: List[str] = []
        if str(left["label"]) != str(right["label"]):
            reasons.append("ontology_label_mismatch")
        left_props = dict(left.get("properties", {}))
        right_props = dict(right.get("properties", {}))
        for key in policy.identity_keys:
            left_value = _normalize_name(left_props.get(key))
            right_value = _normalize_name(right_props.get(key))
            if left_value and right_value and left_value != right_value:
                reasons.append(f"identity_conflict:{key}")
        return reasons

    @staticmethod
    def _text_score(left: Dict[str, Any], right: Dict[str, Any]) -> float:
        left_name = str(left.get("name") or left["observed_entity_id"])
        right_name = str(right.get("name") or right["observed_entity_id"])
        left_normalized = _normalize_name(left_name)
        right_normalized = _normalize_name(right_name)
        if left_normalized and left_normalized == right_normalized:
            return 1.0
        return float(SequenceMatcher(None, left_normalized, right_normalized).ratio())

    @staticmethod
    def _graph_score(
        left_id: str,
        right_id: str,
        signatures: Mapping[str, set[str]],
    ) -> float:
        left_neighbors = set(signatures.get(left_id, set()))
        right_neighbors = set(signatures.get(right_id, set()))
        if not left_neighbors and not right_neighbors:
            return 0.5
        union = left_neighbors | right_neighbors
        if not union:
            return 0.0
        return float(len(left_neighbors & right_neighbors) / len(union))

    def _llm_score(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        *,
        llm: Any,
    ) -> tuple[Optional[float], str]:
        if llm is None or not hasattr(llm, "complete"):
            return None, "llm mode requested but backend has no complete() method"
        system = (
            "You judge whether two observed graph entities refer to the same canonical entity. "
            "Return JSON with keys same_entity (bool), confidence (0..1), rationale (string)."
        )
        user = _json_dumps(
            {
                "left": {
                    "label": left["label"],
                    "name": left.get("name"),
                    "properties": left.get("properties", {}),
                },
                "right": {
                    "label": right["label"],
                    "name": right.get("name"),
                    "properties": right.get("properties", {}),
                },
            }
        )
        try:
            response = complete_with_task_hints(
                llm,
                system=system,
                user=user,
                temperature=0.0,
                response_format={"type": "json_object"},
                reasoning_mode=False,
                task_hint="entity_resolution_scoring",
            )
            payload = response.json() if hasattr(response, "json") else {}
            confidence = payload.get("confidence")
            if confidence is None:
                return None, str(payload.get("rationale", ""))
            same_entity = bool(payload.get("same_entity", False))
            score = float(confidence)
            return (score if same_entity else 1.0 - score), str(payload.get("rationale", ""))
        except Exception as exc:
            logger.warning("LLM qualification scoring skipped: %s", exc)
            return None, f"llm scoring failed: {type(exc).__name__}"

    @staticmethod
    def _average_score(*values: Optional[float]) -> float:
        filtered = [float(value) for value in values if value is not None]
        if not filtered:
            return 0.0
        return sum(filtered) / len(filtered)

    def _merge_entity_properties(
        self,
        entities: Sequence[Dict[str, Any]],
        *,
        policy: Any,
        property_resolution: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], List[str]]:
        blocked_reasons: List[str] = []
        property_resolution = dict(property_resolution or {})
        merged: Dict[str, Any] = {}
        all_keys = {
            str(key)
            for entity in entities
            for key in dict(entity.get("properties", {})).keys()
        }
        all_keys.update(property_resolution.keys())
        merged["entity_type"] = str(entities[0]["label"]) if entities else "Entity"

        ranked_entities = sorted(
            entities,
            key=lambda entity: len([value for value in dict(entity.get("properties", {})).values() if value not in (None, "", [])]),
            reverse=True,
        )

        for key in all_keys:
            strategy = str(policy.property_merge.get(key, "prefer_non_empty"))
            values = [
                dict(entity.get("properties", {})).get(key)
                for entity in ranked_entities
                if dict(entity.get("properties", {})).get(key) not in (None, "", [])
            ]
            if key in property_resolution:
                merged[key] = property_resolution[key]
                continue
            if not values:
                continue
            if strategy == "set_union":
                union: List[Any] = []
                seen: set[str] = set()
                for value in values:
                    items = value if isinstance(value, list) else [value]
                    for item in items:
                        fingerprint = _json_dumps(item)
                        if fingerprint in seen:
                            continue
                        seen.add(fingerprint)
                        union.append(item)
                merged[key] = union
                continue
            if strategy == "review_if_conflict":
                distinct = {_json_dumps(value) for value in values}
                if len(distinct) > 1:
                    blocked_reasons.append(f"property_conflict:{key}")
                merged[key] = values[0]
                continue
            if strategy == "prefer_latest":
                merged[key] = values[-1]
                continue
            merged[key] = values[0]
        if "name" not in merged and entities:
            merged["name"] = str(entities[0].get("name") or entities[0]["observed_entity_id"])
        return merged, blocked_reasons

    @staticmethod
    def _canonical_entity_id(label: str, canonical_name: str) -> str:
        return f"canonical_{_slug(label)}_{_slug(canonical_name)}"

    @staticmethod
    def _singleton_entity_id(entity: Dict[str, Any]) -> str:
        return f"canonical_{_slug(entity['label'])}_{_slug(entity.get('name') or entity['observed_entity_id'])}_{_slug(entity['observed_entity_id'])}"

    @staticmethod
    def _singleton_entity_id_from_observed_id(observed_entity_id: str) -> str:
        return f"canonical_observed_{_slug(observed_entity_id)}"

    @staticmethod
    def _canonical_relation_id(
        *,
        rel_type: str,
        source_entity_id: str,
        target_entity_id: str,
        qualifier_key: str,
    ) -> str:
        raw = f"{rel_type}|{source_entity_id}|{target_entity_id}|{qualifier_key}"
        return f"relation_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _row_to_case(row: Mapping[str, Any]) -> QualificationCase:
        return QualificationCase(
            case_id=str(row["case_id"]),
            run_id=str(row["run_id"]),
            case_type=str(row["case_type"]),
            status=str(row["status"]),
            candidate_ids=[str(item) for item in _json_loads(row.get("candidate_ids_json"), default=[])],
            recommended_action=str(row["recommended_action"]),
            scores=dict(_json_loads(row.get("scores_json"), default={})),
            rationale=dict(_json_loads(row.get("rationale_json"), default={})),
            blocked_reasons=[str(item) for item in _json_loads(row.get("blocked_reasons_json"), default=[])],
            created_at=str(row.get("created_at", "")),
            updated_at=str(row.get("updated_at", "")),
        )
