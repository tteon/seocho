from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from config import db_registry, graph_registry
from runtime.runtime_ingest import RuntimeRawIngestor
from semantic_query_flow import SemanticAgentFlow
from seocho.ontology_context import (
    _clean_distinct_strings,
    assess_graph_ontology_context_status,
)
from seocho.query.answering import build_evidence_bundle


class GraphMemoryService:
    """Memory-first facade over runtime ingest and semantic graph search."""

    def __init__(
        self,
        *,
        db_manager: Any,
        runtime_raw_ingestor: RuntimeRawIngestor,
        semantic_agent_flow: SemanticAgentFlow,
        default_database: Optional[str] = None,
    ) -> None:
        self.db_manager = db_manager
        self.runtime_raw_ingestor = runtime_raw_ingestor
        self.semantic_agent_flow = semantic_agent_flow
        self.default_database = default_database or os.getenv("PUBLIC_MEMORY_DATABASE", "kgnormal")

    def create_memory(
        self,
        *,
        workspace_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a single memory record, extracting entities into the graph.

        Args:
            workspace_id: Workspace scope for tenant isolation.
            content: Raw text content of the memory.
            metadata: Arbitrary key-value metadata to attach.
            memory_id: Explicit ID; auto-generated (``mem_<hex>``) if omitted.
            user_id: Originating user identity.
            agent_id: Originating agent identity.
            session_id: Session context for grouping related memories.
            database: Target DozerDB database; falls back to ``PUBLIC_MEMORY_DATABASE``.
            category: Data category for prompt routing (default ``'memory'``).
            source_type: Content format (``'text'``, ``'pdf'``, ``'csv'``).
            semantic_artifact_policy: Artifact promotion policy.
            approved_artifacts: Pre-approved ontology/SHACL payload.

        Returns:
            Dict with ``memory`` (stored record metadata) and ``ingest_summary``
            (entity/relation counts, warnings).

        Raises:
            ValueError: If the underlying ingest pipeline processes zero records.
        """
        created_at = _utc_now_iso()
        resolved_memory_id = str(memory_id or f"mem_{uuid4().hex}")
        record_metadata = self._build_record_metadata(
            metadata=metadata,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            created_at=created_at,
            updated_at=created_at,
        )
        target_database = database or self.default_database
        ingest_result = self.runtime_raw_ingestor.ingest_records(
            records=[
                {
                    "id": resolved_memory_id,
                    "content": content,
                    "category": category,
                    "source_type": source_type,
                    "metadata": record_metadata,
                }
            ],
            target_database=target_database,
            workspace_id=workspace_id,
            semantic_artifact_policy=semantic_artifact_policy,
            approved_artifacts=approved_artifacts,
        )
        if ingest_result.get("records_processed", 0) < 1:
            raise ValueError("memory ingest failed")
        return {
            "memory": {
                "memory_id": resolved_memory_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "content": content,
                "metadata": metadata or {},
                "status": "stored",
                "created_at": created_at,
                "updated_at": created_at,
                "database": target_database,
            },
            "ingest_summary": {
                "database": target_database,
                "entities_detected": max(int(ingest_result.get("total_nodes", 0)) - 1, 0),
                "relations_detected": int(ingest_result.get("total_relationships", 0)),
                "records_processed": ingest_result.get("records_processed", 0),
                "records_failed": ingest_result.get("records_failed", 0),
                "warnings": ingest_result.get("warnings", []),
                "semantic_artifacts": ingest_result.get("semantic_artifacts"),
            },
        }

    def create_memories(
        self,
        *,
        workspace_id: str,
        items: Sequence[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Batch-store multiple memory records in a single ingestion call.

        Each item in *items* should contain ``content`` and optionally
        ``memory_id``, ``category``, ``source_type``, and ``metadata``.

        Returns:
            Dict with ``memories`` list and ``ingest_summary``.
        """
        target_database = database or self.default_database
        created_at = _utc_now_iso()
        records: List[Dict[str, Any]] = []
        memory_rows: List[Dict[str, Any]] = []
        for item in items:
            resolved_memory_id = str(item.get("memory_id") or f"mem_{uuid4().hex}")
            content = str(item.get("content", ""))
            metadata = item.get("metadata", {})
            record_metadata = self._build_record_metadata(
                metadata=metadata if isinstance(metadata, dict) else {},
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                created_at=created_at,
                updated_at=created_at,
            )
            category = str(item.get("category", "memory")).strip() or "memory"
            source_type = str(item.get("source_type", "text")).strip() or "text"
            records.append(
                {
                    "id": resolved_memory_id,
                    "content": content,
                    "category": category,
                    "source_type": source_type,
                    "metadata": record_metadata,
                }
            )
            memory_rows.append(
                {
                    "memory_id": resolved_memory_id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "content": content,
                    "metadata": metadata if isinstance(metadata, dict) else {},
                    "status": "stored",
                    "created_at": created_at,
                    "updated_at": created_at,
                    "database": target_database,
                }
            )

        ingest_result = self.runtime_raw_ingestor.ingest_records(
            records=records,
            target_database=target_database,
            workspace_id=workspace_id,
            semantic_artifact_policy=semantic_artifact_policy,
            approved_artifacts=approved_artifacts,
        )
        failed_ids = {str(item.get("record_id", "")).strip() for item in ingest_result.get("errors", [])}
        memories = [item for item in memory_rows if item["memory_id"] not in failed_ids]
        return {
            "memories": memories,
            "ingest_summary": {
                "database": target_database,
                "records_processed": ingest_result.get("records_processed", 0),
                "records_failed": ingest_result.get("records_failed", 0),
                "warnings": ingest_result.get("warnings", []),
                "semantic_artifacts": ingest_result.get("semantic_artifacts"),
            },
        }

    def get_memory(
        self,
        *,
        memory_id: str,
        workspace_id: str,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single memory by ID, searching across candidate databases.

        Returns ``None`` if the memory is not found.
        """
        for db_name in self._candidate_databases(database):
            row = self._get_memory_row(db_name, memory_id, workspace_id)
            if row is None:
                continue
            return self._row_to_memory(row, db_name)
        return None

    def search_memories(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Search memories using the semantic entity-resolution flow.

        Performs fulltext + semantic entity lookup across candidate databases,
        then enriches results with full memory content.

        Returns:
            Dict with ``memories`` (ranked results) and ``semantic_context``
            (entity resolution metadata).
        """
        candidate_dbs = self._candidate_databases_from_list(databases)
        ontology_context_mismatch = self.ontology_context_mismatch(
            workspace_id=workspace_id,
            databases=candidate_dbs,
        )
        semantic_context = self.semantic_agent_flow.resolver.resolve(
            question=query,
            databases=candidate_dbs,
            workspace_id=workspace_id,
        )
        semantic_context.setdefault("ontology_context_mismatch", ontology_context_mismatch)
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for entity, candidates in semantic_context.get("matches", {}).items():
            for candidate in candidates:
                db_name = str(candidate.get("database", "")).strip()
                memory_id = str(candidate.get("memory_id") or candidate.get("source_id") or "").strip()
                if not db_name or not memory_id:
                    continue
                key = (db_name, memory_id)
                entry = grouped.setdefault(
                    key,
                    {
                        "database": db_name,
                        "memory_id": memory_id,
                        "score": 0.0,
                        "reasons": set(),
                        "entities": set(),
                    },
                )
                entry["score"] = max(entry["score"], float(candidate.get("final_score", 0.0) or 0.0))
                entry["reasons"].add("entity_match")
                source = str(candidate.get("source", "")).strip()
                if source:
                    entry["reasons"].add(source)
                if entity:
                    entry["entities"].add(entity)

        results: List[Dict[str, Any]] = []
        seen_keys: set[Tuple[str, str]] = set()
        ranked_candidates = sorted(grouped.values(), key=lambda item: item["score"], reverse=True)
        for item in ranked_candidates:
            key = (item["database"], item["memory_id"])
            if key in seen_keys:
                continue
            memory = self.get_memory(
                memory_id=item["memory_id"],
                workspace_id=workspace_id,
                database=item["database"],
            )
            if memory is None or not self._matches_scope(memory, user_id, agent_id, session_id):
                continue
            seen_keys.add(key)
            results.append(
                {
                    "memory_id": memory["memory_id"],
                    "content": memory["content"],
                    "content_preview": memory["content_preview"],
                    "metadata": memory["metadata"],
                    "score": round(item["score"], 4),
                    "reasons": sorted(item["reasons"]),
                    "matched_entities": sorted(item["entities"]),
                    "database": item["database"],
                    "status": memory["status"],
                    "evidence_bundle": build_evidence_bundle(
                        question=query,
                        semantic_context=semantic_context,
                        memory=memory,
                        matched_entities=sorted(item["entities"]),
                        reasons=sorted(item["reasons"]),
                        score=item["score"],
                    ),
                }
            )
            if len(results) >= limit:
                break

        if len(results) < limit:
            for item in self._search_document_fallback(
                query=query,
                workspace_id=workspace_id,
                databases=candidate_dbs,
                semantic_context=semantic_context,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            ):
                key = (item["database"], item["memory_id"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append(item)
                if len(results) >= limit:
                    break

        return {
            "results": results[:limit],
            "semantic_context": semantic_context,
            "ontology_context_mismatch": ontology_context_mismatch,
        }

    def archive_memory(
        self,
        *,
        memory_id: str,
        workspace_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Soft-archive a memory by setting its status to ``'archived'``.

        Raises:
            FileNotFoundError: If the memory is not found in any candidate database.
        """
        archived_at = _utc_now_iso()
        for db_name in self._candidate_databases(database):
            count = self._archive_memory_in_db(db_name, memory_id, workspace_id, archived_at)
            if count > 0:
                return {
                    "memory_id": memory_id,
                    "workspace_id": workspace_id,
                    "database": db_name,
                    "status": "archived",
                    "archived_at": archived_at,
                    "archived_nodes": count,
                }
        raise FileNotFoundError(f"memory not found: {memory_id}")

    def chat_from_memories(
        self,
        *,
        workspace_id: str,
        message: str,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Search memories and synthesize a natural-language answer.

        Combines :meth:`search_memories` with LLM-based answer generation,
        returning the assistant response alongside the evidence used.
        """
        search_payload = self.search_memories(
            workspace_id=workspace_id,
            query=message,
            limit=limit,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            databases=databases,
        )
        hits = search_payload["results"]
        top_bundle = {}
        if hits:
            top_bundle = dict(hits[0].get("evidence_bundle", {}))
        if not top_bundle:
            top_bundle = build_evidence_bundle(
                question=message,
                semantic_context=search_payload.get("semantic_context", {}),
                memory=hits[0] if hits else None,
                matched_entities=hits[0].get("matched_entities", []) if hits else None,
                reasons=hits[0].get("reasons", []) if hits else None,
                score=hits[0].get("score") if hits else None,
            )
        return {
            "assistant_message": self._synthesize_answer(message, hits),
            "memory_hits": [
                {
                    "memory_id": item["memory_id"],
                    "score": item["score"],
                    "database": item["database"],
                }
                for item in hits
            ],
            "search_results": hits,
            "semantic_context": search_payload["semantic_context"],
            "evidence_bundle": top_bundle,
            "ontology_context_mismatch": search_payload.get("ontology_context_mismatch", {}),
        }

    def ontology_context_mismatch(
        self,
        *,
        workspace_id: str,
        databases: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Return ontology-context provenance status for runtime query responses."""

        statuses: List[Dict[str, Any]] = []
        for database in self._candidate_databases_from_list(databases):
            target = graph_registry.find_by_database(database)
            rows = self._run_query(
                database,
                """
                MATCH (n)
                WHERE coalesce(n._workspace_id, n.workspace_id, $workspace_id) = $workspace_id
                RETURN collect(DISTINCT coalesce(n._ontology_context_hash, '')) AS raw_context_hashes,
                       collect(DISTINCT coalesce(n._ontology_id, '')) AS raw_ontology_ids,
                       collect(DISTINCT coalesce(n._ontology_profile, '')) AS raw_profiles,
                       count(n) AS scoped_nodes,
                       sum(CASE WHEN coalesce(n._ontology_context_hash, '') = '' AND coalesce(n._ontology_id, '') = '' THEN 1 ELSE 0 END) AS missing_context_nodes,
                       sum(CASE WHEN coalesce(n._ontology_context_hash, '') = '' THEN 1 ELSE 0 END) AS missing_context_hash_nodes
                """,
                {"workspace_id": workspace_id},
            )
            row = rows[0] if rows else {}
            status = assess_graph_ontology_context_status(
                database=database,
                workspace_id=workspace_id,
                indexed_context_hashes=_clean_distinct_strings(
                    (
                        row.get("raw_context_hashes")
                        if isinstance(row, dict) and row.get("raw_context_hashes") is not None
                        else row.get("indexed_context_hashes", [])
                        if isinstance(row, dict)
                        else []
                    )
                ),
                indexed_ontology_ids=_clean_distinct_strings(
                    (
                        row.get("raw_ontology_ids")
                        if isinstance(row, dict) and row.get("raw_ontology_ids") is not None
                        else row.get("indexed_ontology_ids", [])
                        if isinstance(row, dict)
                        else []
                    )
                ),
                indexed_profiles=_clean_distinct_strings(
                    (
                        row.get("raw_profiles")
                        if isinstance(row, dict) and row.get("raw_profiles") is not None
                        else row.get("indexed_profiles", [])
                        if isinstance(row, dict)
                        else []
                    )
                ),
                expected_ontology_id=str(getattr(target, "ontology_id", "") or ""),
                missing_context_nodes=(
                    int(row.get("missing_context_nodes", 0) or 0)
                    if isinstance(row, dict)
                    else 0
                ),
                missing_context_hash_nodes=(
                    int(row.get("missing_context_hash_nodes", 0) or 0)
                    if isinstance(row, dict)
                    else 0
                ),
                scoped_nodes=(
                    int(row.get("scoped_nodes", 0) or 0)
                    if isinstance(row, dict)
                    else 0
                ),
            )
            status["graph_id"] = str(getattr(target, "graph_id", "") or "")
            status["expected_vocabulary_profile"] = str(
                getattr(target, "vocabulary_profile", "") or ""
            )
            statuses.append(status)

        mismatch = any(item.get("mismatch") for item in statuses)
        missing_context = any(
            int(item.get("missing_context_nodes", 0) or 0) > 0
            or int(item.get("missing_context_hash_nodes", 0) or 0) > 0
            for item in statuses
        )
        warning = ""
        if mismatch:
            warning = (
                "At least one runtime graph has indexed ontology metadata that differs "
                "from the active graph target."
            )
        elif missing_context:
            warning = (
                "At least one runtime graph has incomplete ontology context metadata; "
                "re-index to make context parity fully auditable."
            )

        return {
            "mismatch": mismatch,
            "missing_context": missing_context,
            "databases": statuses,
            "warning": warning,
        }

    def _candidate_databases(self, database: Optional[str] = None) -> List[str]:
        if database:
            return [database]
        databases = db_registry.list_databases()
        if self.default_database not in databases:
            databases = [self.default_database, *databases]
        return [db_name for db_name in databases if db_name]

    def _candidate_databases_from_list(self, databases: Optional[Sequence[str]]) -> List[str]:
        if databases:
            return [str(db_name).strip() for db_name in databases if str(db_name).strip()]
        return self._candidate_databases()

    def _get_memory_row(self, database: str, memory_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
        query = """
        MATCH (m:Document)
        WHERE coalesce(m.memory_id, m.source_id) = $memory_id
          AND coalesce(m.workspace_id, $workspace_id) = $workspace_id
        OPTIONAL MATCH (m)-[:MENTIONS]->(e)
        WITH m, collect(DISTINCT {
          id: coalesce(e.id, elementId(e)),
          labels: labels(e),
          name: coalesce(e.name, e.title, e.id, e.uri, '')
        })[0..25] AS entities
        RETURN coalesce(m.memory_id, m.source_id, $memory_id) AS memory_id,
               coalesce(m.workspace_id, $workspace_id) AS workspace_id,
               coalesce(m.content, '') AS content,
               coalesce(m.content_preview, '') AS content_preview,
               coalesce(m.status, 'active') AS status,
               coalesce(m.source_type, '') AS source_type,
               coalesce(m.category, '') AS category,
               coalesce(m.user_id, '') AS user_id,
               coalesce(m.agent_id, '') AS agent_id,
               coalesce(m.session_id, '') AS session_id,
               coalesce(m.created_at, '') AS created_at,
               coalesce(m.updated_at, '') AS updated_at,
               coalesce(m.metadata_json, '{}') AS metadata_json,
               entities
        LIMIT 1
        """
        rows = self._run_query(database, query, {"memory_id": memory_id, "workspace_id": workspace_id})
        if not rows:
            return None
        return rows[0]

    def _archive_memory_in_db(self, database: str, memory_id: str, workspace_id: str, archived_at: str) -> int:
        query = """
        MATCH (n)
        WHERE coalesce(n.memory_id, n.source_id) = $memory_id
          AND coalesce(n.workspace_id, $workspace_id) = $workspace_id
        SET n.status = 'archived',
            n.archived_at = $archived_at,
            n.updated_at = $archived_at
        RETURN count(n) AS archived_count
        """
        rows = self._run_query(
            database,
            query,
            {"memory_id": memory_id, "workspace_id": workspace_id, "archived_at": archived_at},
        )
        if not rows:
            return 0
        return int(rows[0].get("archived_count", 0) or 0)

    def _search_document_fallback(
        self,
        *,
        query: str,
        workspace_id: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
        user_id: Optional[str],
        agent_id: Optional[str],
        session_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        search_query = """
        MATCH (m:Document)
        WHERE coalesce(m.workspace_id, $workspace_id) = $workspace_id
          AND coalesce(m.status, 'active') <> 'archived'
          AND (
            toLower(coalesce(m.content, '')) CONTAINS toLower($query)
            OR toLower(coalesce(m.content_preview, '')) CONTAINS toLower($query)
            OR toLower(coalesce(m.name, '')) CONTAINS toLower($query)
          )
        RETURN coalesce(m.memory_id, m.source_id, '') AS memory_id,
               coalesce(m.content, '') AS content,
               coalesce(m.content_preview, '') AS content_preview,
               coalesce(m.metadata_json, '{}') AS metadata_json,
               coalesce(m.status, 'active') AS status,
               coalesce(m.user_id, '') AS user_id,
               coalesce(m.agent_id, '') AS agent_id,
               coalesce(m.session_id, '') AS session_id
        LIMIT 10
        """
        results: List[Dict[str, Any]] = []
        for database in databases:
            rows = self._run_query(database, search_query, {"workspace_id": workspace_id, "query": query})
            for row in rows:
                payload = self._row_to_memory(row, database)
                if not self._matches_scope(payload, user_id, agent_id, session_id):
                    continue
                score = _overlap_score(query, payload["content"] or payload["content_preview"])
                results.append(
                    {
                        "memory_id": payload["memory_id"],
                        "content": payload["content"],
                        "content_preview": payload["content_preview"],
                        "metadata": payload["metadata"],
                        "score": round(score, 4),
                        "reasons": ["document_text_match"],
                        "matched_entities": [],
                        "database": database,
                        "status": payload["status"],
                        "evidence_bundle": build_evidence_bundle(
                            question=query,
                            semantic_context=semantic_context,
                            memory=payload,
                            reasons=["document_text_match"],
                            score=score,
                        ),
                    }
                )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results

    def _row_to_memory(self, row: Dict[str, Any], database: str) -> Dict[str, Any]:
        metadata = row.get("metadata")
        if metadata is None:
            metadata = _parse_json_dict(str(row.get("metadata_json", "{}")))
        return {
            "memory_id": str(row.get("memory_id", "")).strip(),
            "workspace_id": str(row.get("workspace_id", "")).strip(),
            "content": str(row.get("content", "") or ""),
            "content_preview": str(row.get("content_preview", "") or ""),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "status": str(row.get("status", "active") or "active"),
            "source_type": str(row.get("source_type", "") or ""),
            "category": str(row.get("category", "") or ""),
            "user_id": str(row.get("user_id", "") or ""),
            "agent_id": str(row.get("agent_id", "") or ""),
            "session_id": str(row.get("session_id", "") or ""),
            "created_at": str(row.get("created_at", "") or ""),
            "updated_at": str(row.get("updated_at", "") or ""),
            "entities": row.get("entities", []) if isinstance(row.get("entities"), list) else [],
            "database": database,
        }

    def _matches_scope(
        self,
        memory: Dict[str, Any],
        user_id: Optional[str],
        agent_id: Optional[str],
        session_id: Optional[str],
    ) -> bool:
        if not self._scope_matches(memory.get("user_id"), user_id):
            return False
        if not self._scope_matches(memory.get("agent_id"), agent_id):
            return False
        if not self._scope_matches(memory.get("session_id"), session_id):
            return False
        return True

    @staticmethod
    def _scope_matches(memory_value: Any, requested_value: Optional[str]) -> bool:
        if not requested_value:
            return True
        stored = str(memory_value or "").strip()
        # Empty scope means workspace-level memory; it remains visible to scoped
        # user/agent/session queries inside the already-enforced workspace.
        return not stored or stored == str(requested_value).strip()

    def _run_query(self, database: str, query: str, params: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            with self.db_manager.driver.session(database=database) as session:
                result = session.run(query, params or {})
                return [record.data() if hasattr(record, "data") else dict(record) for record in result]
        except Exception:
            return []

    @staticmethod
    def _build_record_metadata(
        *,
        metadata: Optional[Dict[str, Any]],
        user_id: Optional[str],
        agent_id: Optional[str],
        session_id: Optional[str],
        created_at: str,
        updated_at: str,
    ) -> Dict[str, Any]:
        record_metadata = dict(metadata or {})
        if user_id:
            record_metadata["user_id"] = user_id
        if agent_id:
            record_metadata["agent_id"] = agent_id
        if session_id:
            record_metadata["session_id"] = session_id
        record_metadata.setdefault("created_at", created_at)
        record_metadata.setdefault("updated_at", updated_at)
        return record_metadata

    @staticmethod
    def _synthesize_answer(message: str, hits: Sequence[Dict[str, Any]]) -> str:
        if not hits:
            return "No matching memory was found."
        if len(hits) == 1:
            return str(hits[0].get("content") or hits[0].get("content_preview") or "").strip()
        fragments: List[str] = []
        for item in hits[:3]:
            text = str(item.get("content_preview") or item.get("content") or "").strip()
            if text:
                fragments.append(text)
        if not fragments:
            return f"I found {len(hits)} related memories for: {message}"
        return "\n".join(fragments)


def _parse_json_dict(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _overlap_score(query: str, content: str) -> float:
    q = _normalize_text(query)
    c = _normalize_text(content)
    if not q or not c:
        return 0.0
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)
    lexical = SequenceMatcher(None, q, c[: max(len(q), 1) * 4]).ratio()
    return max(overlap, lexical)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
