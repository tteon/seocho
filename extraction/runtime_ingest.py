"""
Runtime raw-data ingestion service for interactive platform usage.

This module supports ingesting user-provided raw text records through API calls,
running parse -> semantic extraction -> linking -> rule-annotation, and loading
graph data into a target DB.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from config import graph_registry, load_pipeline_runtime_config
from database_manager import DatabaseManager
from exceptions import InvalidDatabaseNameError
from raw_material_parser import MaterialParseError, parse_raw_material_record
from semantic_context import build_dynamic_prompt_context
from seocho.index import CanonicalExtractionEngine
from seocho.store.llm import create_llm_backend

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


class _CanonicalRuntimeExtractor:
    """Compatibility adapter exposing the legacy extractor interface."""

    def __init__(self, engine: CanonicalExtractionEngine):
        self._engine = engine

    def extract_entities(
        self,
        text: str,
        category: str = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._engine.extract(
            text,
            category=category,
            extra_context=extra_context,
        )


class _CanonicalRuntimeLinker:
    """Compatibility adapter exposing the legacy linker interface."""

    def __init__(self, engine: CanonicalExtractionEngine):
        self._engine = engine

    def link_entities(
        self,
        extracted_data: Dict[str, Any],
        category: str = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._engine.link(
            extracted_data,
            category=category,
            extra_context=extra_context,
        )


class RuntimeRawIngestor:
    """Runs extraction/linking pipeline for ad-hoc runtime records."""

    def __init__(self, db_manager: DatabaseManager):
        self._db_manager = db_manager
        self._extractor = None
        self._linker = None
        self._semantic_orchestrator = None
        self._llm_stack_ready = False
        self._relatedness_threshold = _env_float("RUNTIME_LINKING_RELATEDNESS_THRESHOLD", 0.2)
        self._embedding_relatedness_threshold = _env_float("RUNTIME_LINKING_EMBED_THRESHOLD", 0.72)
        self._embedding_enabled = _env_bool("RUNTIME_LINKING_USE_EMBEDDING", True)
        self._embedding_model = os.getenv("RUNTIME_LINKING_EMBED_MODEL", "text-embedding-3-small")
        self._embedding_client = None
        self._embedding_cache_max_size = int(os.getenv("RUNTIME_EMBEDDING_CACHE_MAX_SIZE", "4096"))
        self._embedding_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._embedding_cache_lock = threading.Lock()

        try:
            from semantic_pass_orchestrator import SemanticPassOrchestrator

            cfg = load_pipeline_runtime_config()
            api_key = cfg.openai_api_key
            if not api_key:
                raise ValueError("OPENAI_API_KEY is empty")
            model = cfg.model
            llm = create_llm_backend(
                provider="openai",
                model=model,
                api_key=api_key,
            )
            shared_engine = CanonicalExtractionEngine(
                ontology=None,
                llm=llm,
                custom_prompts={
                    "system": cfg.prompts.system,
                    "user": cfg.prompts.user,
                },
                linking_prompt=cfg.linking_prompt.linking,
            )
            self._extractor = _CanonicalRuntimeExtractor(shared_engine)
            self._linker = _CanonicalRuntimeLinker(shared_engine)
            self._semantic_orchestrator = SemanticPassOrchestrator(
                api_key=api_key,
                model=model,
                extractor=self._extractor,
            )
            if self._embedding_enabled:
                from openai import OpenAI
                from tracing import wrap_openai_client

                self._embedding_client = wrap_openai_client(OpenAI(api_key=api_key))
            self._llm_stack_ready = True
        except Exception as exc:
            logger.warning("LLM extraction stack unavailable; fallback mode only: %s", exc)

    def ingest_records(
        self,
        records: List[Dict[str, Any]],
        target_database: str,
        workspace_id: str = "default",
        enable_rule_constraints: bool = True,
        create_database_if_missing: bool = True,
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ingest raw records through the full extraction pipeline.

        Runs parse → semantic extraction → entity linking → rule annotation →
        graph load for each record.

        Args:
            records: Raw record dicts, each with at least ``content`` and
                optionally ``id``, ``category``, ``source_type``, ``metadata``.
            target_database: DozerDB database to load extracted graphs into.
            workspace_id: Workspace scope propagated to all graph properties.
            enable_rule_constraints: Infer and apply SHACL-like rules to nodes.
            create_database_if_missing: Provision the DB if it does not exist.
            semantic_artifact_policy: ``'auto'`` (apply drafts), ``'draft_only'``
                (defer approval), or ``'approved_only'`` (require approved artifacts).
            approved_artifacts: Pre-approved ontology/SHACL payload to use.

        Returns:
            Summary dict with ``records_processed``, ``records_failed``,
            ``rule_profile``, ``semantic_artifacts``, ``errors``, ``warnings``,
            and ``status``.

        Raises:
            InvalidDatabaseNameError: If *target_database* fails name validation.
        """
        from rule_constraints import RuleSet, apply_rules_to_graph, infer_rules_from_graph

        if not _DB_NAME_RE.match(target_database):
            raise InvalidDatabaseNameError(
                f"Invalid DB name '{target_database}': must be alphanumeric and start with a letter"
            )

        if create_database_if_missing:
            self._db_manager.provision_database(target_database)

        processed = 0
        failed = 0
        total_nodes = 0
        total_relationships = 0
        fallback_records = 0
        errors: List[Dict[str, str]] = []
        warnings: List[Dict[str, str]] = []
        prepared_graphs: List[Tuple[str, Dict[str, Any]]] = []
        ontology_candidates: List[Dict[str, Any]] = []
        shacl_candidates: List[Dict[str, Any]] = []
        relatedness_records: List[Dict[str, Any]] = []
        known_entities = self._load_existing_entity_names(target_database)
        artifact_policy = semantic_artifact_policy if semantic_artifact_policy in {
            "auto",
            "draft_only",
            "approved_only",
        } else "auto"
        if artifact_policy != semantic_artifact_policy:
            warnings.append(
                {
                    "record_id": "_batch",
                    "warning_type": "ArtifactPolicyFallback",
                    "message": f"Unknown semantic_artifact_policy '{semantic_artifact_policy}', using 'auto'.",
                }
            )

        # --- Phase A: Parse all records (sequential, fast) ---
        parsed_items: List[Tuple[str, str, str, Any, Dict[str, Any]]] = []
        for idx, item in enumerate(records):
            source_id = str(item.get("id") or f"raw_{idx}")
            category = str(item.get("category", "general")).strip() or "general"
            raw_metadata = item.get("metadata", {})
            user_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            try:
                parsed = parse_raw_material_record(item)
            except MaterialParseError as exc:
                failed += 1
                errors.append(
                    {
                        "record_id": source_id,
                        "error_type": "MaterialParseError",
                        "message": str(exc),
                    }
                )
                continue

            text = parsed.text.strip()
            if not text:
                failed += 1
                errors.append(
                    {
                        "record_id": source_id,
                        "error_type": "ValidationError",
                        "message": "content is empty",
                    }
                )
                continue

            for warning in parsed.warnings:
                warnings.append(
                    {
                        "record_id": source_id,
                        "warning_type": "MaterialParseWarning",
                        "message": warning,
                    }
                )

            record_metadata = self._build_record_metadata(
                source_id=source_id,
                category=category,
                source_type=parsed.source_type,
                content_encoding=str(item.get("content_encoding", "plain")).strip().lower() or "plain",
                parser_metadata=parsed.metadata,
                user_metadata=user_metadata,
            )
            parsed_items.append((source_id, category, text, parsed, record_metadata))

        # --- Phase B: Extract graphs in parallel (I/O-bound LLM calls) ---
        max_workers = min(
            max(len(parsed_items), 1),
            int(os.getenv("RUNTIME_EXTRACT_MAX_WORKERS", "6")),
        )
        extraction_results: List[Optional[Tuple[Dict[str, Any], bool, str]]] = [None] * len(parsed_items)
        extraction_errors: List[Optional[Exception]] = [None] * len(parsed_items)

        def _extract_one(pi_idx: int) -> None:
            src_id, cat, txt, prs, rec_meta = parsed_items[pi_idx]
            try:
                extraction_results[pi_idx] = self._extract_graph(
                    source_id=src_id,
                    text=txt,
                    category=cat,
                    target_database=target_database,
                    record_metadata=rec_meta,
                    source_type=prs.source_type,
                    approved_artifacts=approved_artifacts,
                )
            except Exception as exc:
                extraction_errors[pi_idx] = exc

        if len(parsed_items) <= 1:
            for i in range(len(parsed_items)):
                _extract_one(i)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_extract_one, i): i for i in range(len(parsed_items))}
                concurrent.futures.wait(futures)

        # --- Phase C: Relatedness, linking, memory graph (sequential) ---
        for pi_idx, (source_id, category, text, parsed, record_metadata) in enumerate(parsed_items):
            if extraction_errors[pi_idx] is not None:
                exc = extraction_errors[pi_idx]
                logger.error("Raw ingest failed for record '%s': %s", source_id, exc)
                failed += 1
                errors.append(
                    {
                        "record_id": source_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue

            graph_data, used_fallback, fallback_reason = extraction_results[pi_idx]

            if used_fallback:
                fallback_records += 1
                warnings.append(
                    {
                        "record_id": source_id,
                        "warning_type": "FallbackExtraction",
                        "message": fallback_reason,
                    }
                )

            semantic_payload = graph_data.get("_semantic", {})
            if semantic_payload.get("ontology_candidate"):
                ontology_candidates.append(semantic_payload["ontology_candidate"])
            if semantic_payload.get("shacl_candidate"):
                shacl_candidates.append(semantic_payload["shacl_candidate"])

            candidate_names = self._collect_entity_names(graph_data)
            relatedness = self._compute_relatedness(candidate_names, known_entities)
            relatedness_records.append(relatedness)
            graph_data.setdefault("_semantic", {})["relatedness"] = relatedness

            if self._should_run_linker(known_entities, relatedness):
                linked_graph, warning = self._link_graph(graph_data, category)
                graph_data = linked_graph
                if warning:
                    warnings.append(
                        {
                            "record_id": source_id,
                            "warning_type": "LinkingWarning",
                            "message": warning,
                        }
                    )
            elif self._llm_stack_ready and self._linker is not None and candidate_names:
                warnings.append(
                    {
                        "record_id": source_id,
                        "warning_type": "LinkingSkipped",
                        "message": "relatedness below threshold; skipped cross-record linking",
                    }
                )

            graph_data = self._ensure_memory_graph(
                graph_data=graph_data,
                source_id=source_id,
                workspace_id=workspace_id,
                text=text,
                category=category,
                source_type=parsed.source_type,
                record_metadata=record_metadata,
            )
            known_entities.update(candidate_names)
            prepared_graphs.append((source_id, graph_data))

        merged_ontology = self._merge_ontology_candidates(ontology_candidates)
        merged_shacl = self._merge_shacl_candidates(shacl_candidates)
        active_artifacts, artifact_decision = self._resolve_semantic_artifacts(
            policy=artifact_policy,
            draft_ontology=merged_ontology,
            draft_shacl=merged_shacl,
            approved_artifacts=approved_artifacts or {},
        )
        if artifact_decision.get("warning"):
            warnings.append(
                {
                    "record_id": "_batch",
                    "warning_type": "ArtifactApprovalWarning",
                    "message": str(artifact_decision["warning"]),
                }
            )
        llm_rule_profile = self._shacl_candidates_to_rule_profile(active_artifacts["shacl_candidate"])
        draft_vocabulary_candidate = self._build_vocabulary_candidate(
            merged_ontology,
            merged_shacl,
            prepared_graphs=[graph_data for _, graph_data in prepared_graphs],
        )
        active_vocabulary_candidate = self._build_vocabulary_candidate(
            active_artifacts["ontology_candidate"],
            active_artifacts["shacl_candidate"],
            prepared_graphs=[graph_data for _, graph_data in prepared_graphs],
        )
        approved_vocab = (approved_artifacts or {}).get("vocabulary_candidate")
        if (
            artifact_decision.get("applied") == "approved"
            and isinstance(approved_vocab, dict)
            and isinstance(approved_vocab.get("terms"), list)
        ):
            active_vocabulary_candidate = approved_vocab

        ruleset = None
        if enable_rule_constraints and prepared_graphs:
            try:
                merged_graph = {"nodes": [], "relationships": []}
                for _, graph_data in prepared_graphs:
                    merged_graph["nodes"].extend(graph_data.get("nodes", []))
                    merged_graph["relationships"].extend(graph_data.get("relationships", []))
                inferred_rules = infer_rules_from_graph(merged_graph)
                merged_profile = self._merge_rule_profiles(inferred_rules.to_dict(), llm_rule_profile)
                ruleset = RuleSet.from_dict(merged_profile)
            except Exception as exc:
                logger.warning("Rule inference skipped for runtime ingest batch: %s", exc)
                warnings.append(
                    {
                        "record_id": "_batch",
                        "warning_type": "RuleInferenceSkipped",
                        "message": str(exc),
                    }
                )

        for source_id, graph_data in prepared_graphs:
            graph_for_load = graph_data
            if ruleset is not None:
                try:
                    graph_for_load = apply_rules_to_graph(graph_data, ruleset)
                except Exception as exc:
                    logger.warning("Rule annotation skipped for record '%s': %s", source_id, exc)
                    warnings.append(
                        {
                            "record_id": source_id,
                            "warning_type": "RuleAnnotationSkipped",
                            "message": str(exc),
                        }
                    )
                    graph_for_load = graph_data

            try:
                self._db_manager.load_data(
                    target_database,
                    graph_for_load,
                    source_id=source_id,
                    workspace_id=workspace_id,
                )
                processed += 1
                total_nodes += len(graph_for_load.get("nodes", []))
                total_relationships += len(graph_for_load.get("relationships", []))
            except Exception as exc:
                logger.error("Raw ingest load failed for record '%s': %s", source_id, exc)
                failed += 1
                errors.append(
                    {
                        "record_id": source_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )

        return {
            "target_database": target_database,
            "records_received": len(records),
            "records_processed": processed,
            "records_failed": failed,
            "total_nodes": total_nodes,
            "total_relationships": total_relationships,
            "fallback_records": fallback_records,
            "rule_profile": ruleset.to_dict() if ruleset is not None else None,
            "semantic_artifacts": {
                "ontology_candidate": active_artifacts["ontology_candidate"],
                "shacl_candidate": active_artifacts["shacl_candidate"],
                "vocabulary_candidate": active_vocabulary_candidate,
                "draft_ontology_candidate": merged_ontology,
                "draft_shacl_candidate": merged_shacl,
                "draft_vocabulary_candidate": draft_vocabulary_candidate,
                "artifact_decision": artifact_decision,
                "relatedness_summary": self._summarize_relatedness(relatedness_records),
            },
            "errors": errors,
            "warnings": warnings,
            "status": (
                "success_with_fallback"
                if failed == 0 and fallback_records > 0
                else ("success" if failed == 0 else ("partial_success" if processed > 0 else "failed"))
            ),
        }

    def _extract_graph(
        self,
        source_id: str,
        text: str,
        category: str,
        target_database: str,
        record_metadata: Optional[Dict[str, Any]] = None,
        source_type: str = "text",
        approved_artifacts: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool, str]:
        if not self._llm_stack_ready or self._extractor is None:
            return (
                self._fallback_extract(source_id=source_id, text=text, semantic_payload={}),
                True,
                "LLM extraction stack unavailable",
            )

        try:
            semantic_payload: Dict[str, Any] = {}
            graph_metadata = self._build_graph_prompt_metadata(target_database)
            if self._semantic_orchestrator is not None:
                pass_result = self._semantic_orchestrator.run_three_pass(
                    text=text,
                    category=category,
                    record_metadata=record_metadata,
                    source_type=source_type,
                    approved_artifacts=approved_artifacts,
                    graph_metadata=graph_metadata,
                )
                extracted = pass_result.get("entity_graph", {})
                semantic_payload = {
                    "ontology_candidate": pass_result.get("ontology_candidate", {}),
                    "shacl_candidate": pass_result.get("shacl_candidate", {}),
                    "pass_metadata": pass_result.get("metadata", {}),
                    "prompt_context": pass_result.get("prompt_context", {}),
                    "record_context": record_metadata or {},
                    "graph_metadata": graph_metadata,
                }
            else:
                prompt_context = build_dynamic_prompt_context(
                    category=category,
                    source_type=source_type,
                    approved_artifacts=approved_artifacts,
                    record_metadata=record_metadata,
                    graph_metadata=graph_metadata,
                )
                extracted = self._extractor.extract_entities(
                    text=text,
                    category=category,
                    extra_context=prompt_context,
                )
                semantic_payload = {
                    "prompt_context": prompt_context,
                    "record_context": record_metadata or {},
                    "graph_metadata": graph_metadata,
                }

            nodes = extracted.get("nodes", []) if isinstance(extracted, dict) else []
            relationships = extracted.get("relationships", []) if isinstance(extracted, dict) else []
            if not nodes:
                raise ValueError("entity extraction returned no nodes")

            graph_data = {"nodes": nodes, "relationships": relationships}
            graph_data["_semantic"] = semantic_payload
            return graph_data, False, ""
        except Exception as exc:
            logger.warning("LLM extraction failed for '%s'; falling back to rule-based extraction: %s", source_id, exc)
            fallback_graph = self._fallback_extract(source_id=source_id, text=text, semantic_payload={})
            reason = f"LLM semantic extraction failed: {type(exc).__name__}"
            return fallback_graph, True, reason

    @staticmethod
    def _fallback_extract(source_id: str, text: str, semantic_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Deterministic fallback for local verification when LLM path is unavailable.
        tokens = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
        unique_tokens: List[str] = []
        seen = set()
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_tokens.append(token)
            if len(unique_tokens) >= 12:
                break

        doc_id = f"{source_id}_doc"
        nodes: List[Dict[str, Any]] = [
            {
                "id": doc_id,
                "label": "Document",
                "properties": {
                    "name": text[:80],
                    "source_id": source_id,
                },
            }
        ]
        relationships: List[Dict[str, Any]] = []

        for idx, name in enumerate(unique_tokens):
            ent_id = f"{source_id}_ent_{idx}"
            nodes.append(
                {
                    "id": ent_id,
                    "label": "Entity",
                    "properties": {
                        "name": name,
                    },
                }
            )
            relationships.append(
                {
                    "source": doc_id,
                    "target": ent_id,
                    "type": "MENTIONS",
                    "properties": {},
                }
            )

        return {
            "nodes": nodes,
            "relationships": relationships,
            "_semantic": semantic_payload or {},
        }

    def _link_graph(self, graph_data: Dict[str, Any], category: str) -> Tuple[Dict[str, Any], Optional[str]]:
        if self._linker is None:
            return graph_data, "entity linker unavailable"
        try:
            input_graph = {
                "nodes": graph_data.get("nodes", []),
                "relationships": graph_data.get("relationships", []),
            }
            linked = self._linker.link_entities(
                extracted_data=input_graph,
                category=category,
                extra_context=graph_data.get("_semantic", {}).get("prompt_context"),
            )
            linked.setdefault("nodes", input_graph["nodes"])
            linked.setdefault("relationships", input_graph["relationships"])
            linked["_semantic"] = graph_data.get("_semantic", {})
            return linked, None
        except Exception as exc:
            return graph_data, f"LLM linking failed: {type(exc).__name__}"

    @staticmethod
    def _build_graph_prompt_metadata(target_database: str) -> Dict[str, Any]:
        target = graph_registry.find_by_database(target_database)
        if target is None:
            return {"database": target_database}
        return {
            "graph_id": target.graph_id,
            "database": target.database,
            "ontology_id": target.ontology_id,
            "vocabulary_profile": target.vocabulary_profile,
            "description": target.description,
            "workspace_scope": target.workspace_scope,
        }

    def _load_existing_entity_names(self, target_database: str, limit: int = 500) -> Set[str]:
        names: Set[str] = set()
        try:
            with self._db_manager.driver.session(database=target_database) as session:
                result = session.run(
                    "MATCH (n) WHERE n.name IS NOT NULL AND NOT 'Document' IN labels(n) "
                    "RETURN toLower(trim(toString(n.name))) AS name LIMIT $limit",
                    limit=limit,
                )
                for row in result:
                    value = str(row.get("name", "")).strip().lower()
                    if value:
                        names.add(value)
        except Exception:
            return set()
        return names

    @staticmethod
    def _collect_entity_names(graph_data: Dict[str, Any]) -> Set[str]:
        names: Set[str] = set()
        for node in graph_data.get("nodes", []):
            label = str(node.get("label", "")).strip()
            if label == "Document":
                continue
            props = node.get("properties", {}) if isinstance(node, dict) else {}
            value = str(props.get("name", "")).strip().lower()
            if value:
                names.add(value)
        return names

    @staticmethod
    def _build_record_metadata(
        source_id: str,
        category: str,
        source_type: str,
        content_encoding: str,
        parser_metadata: Optional[Dict[str, Any]],
        user_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        if isinstance(parser_metadata, dict):
            metadata.update(parser_metadata)
        if isinstance(user_metadata, dict):
            metadata.update(user_metadata)
        metadata.setdefault("source_id", source_id)
        metadata.setdefault("category", category)
        metadata.setdefault("source_type", source_type)
        metadata.setdefault("content_encoding", content_encoding)
        return metadata

    def _ensure_memory_graph(
        self,
        *,
        graph_data: Dict[str, Any],
        source_id: str,
        workspace_id: str,
        text: str,
        category: str,
        source_type: str,
        record_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        document_id = f"{source_id}_doc"
        preview = text[:280]
        metadata_json = json.dumps(record_metadata, ensure_ascii=False, sort_keys=True)
        timestamp = str(
            record_metadata.get("updated_at")
            or record_metadata.get("created_at")
            or _utc_now_iso()
        )
        node_map: Dict[str, Dict[str, Any]] = {}
        normalized_relationships: List[Dict[str, Any]] = []
        relationship_seen: Set[Tuple[str, str, str]] = set()

        for raw_node in graph_data.get("nodes", []):
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get("id", "")).strip()
            if not node_id:
                continue
            label = str(raw_node.get("label", "Entity")).strip() or "Entity"
            raw_props = raw_node.get("properties", {})
            properties = dict(raw_props) if isinstance(raw_props, dict) else {}
            properties.setdefault("source_id", source_id)
            properties.setdefault("memory_id", source_id)
            properties.setdefault("workspace_id", workspace_id)
            properties.setdefault("status", "active")
            properties.setdefault("category", category)
            properties.setdefault("source_type", source_type)
            properties.setdefault("updated_at", timestamp)
            if label == "Document":
                properties.setdefault("name", preview[:80] or source_id)
                properties.setdefault("title", preview[:120] or source_id)
                properties.setdefault("content", text)
                properties.setdefault("content_preview", preview)
                properties.setdefault("metadata_json", metadata_json)
                properties.setdefault("created_at", timestamp)
                self._copy_scope_properties(properties, record_metadata)
            else:
                properties.setdefault("content_preview", preview)
            node_map[node_id] = {"id": node_id, "label": label, "properties": properties}

        document_node = node_map.get(document_id, {"id": document_id, "label": "Document", "properties": {}})
        document_props = dict(document_node.get("properties", {}))
        document_props.update(
            {
                "name": document_props.get("name") or preview[:80] or source_id,
                "title": document_props.get("title") or preview[:120] or source_id,
                "content": text,
                "content_preview": preview,
                "source_id": source_id,
                "memory_id": source_id,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "category": category,
                "status": document_props.get("status") or "active",
                "metadata_json": metadata_json,
                "updated_at": document_props.get("updated_at") or timestamp,
                "created_at": document_props.get("created_at") or timestamp,
            }
        )
        self._copy_scope_properties(document_props, record_metadata)
        node_map[document_id] = {"id": document_id, "label": "Document", "properties": document_props}

        for raw_rel in graph_data.get("relationships", []):
            if not isinstance(raw_rel, dict):
                continue
            source = str(raw_rel.get("source", "")).strip()
            target = str(raw_rel.get("target", "")).strip()
            rel_type = str(raw_rel.get("type", "RELATED_TO")).strip() or "RELATED_TO"
            if not source or not target:
                continue
            raw_props = raw_rel.get("properties", {})
            properties = dict(raw_props) if isinstance(raw_props, dict) else {}
            properties.setdefault("source_id", source_id)
            properties.setdefault("memory_id", source_id)
            properties.setdefault("workspace_id", workspace_id)
            key = (source, target, rel_type)
            if key in relationship_seen:
                continue
            relationship_seen.add(key)
            normalized_relationships.append(
                {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "properties": properties,
                }
            )

        for node_id in list(node_map.keys()):
            if node_id == document_id:
                continue
            key = (document_id, node_id, "MENTIONS")
            if key in relationship_seen:
                continue
            relationship_seen.add(key)
            normalized_relationships.append(
                {
                    "source": document_id,
                    "target": node_id,
                    "type": "MENTIONS",
                    "properties": {
                        "source_id": source_id,
                        "memory_id": source_id,
                        "workspace_id": workspace_id,
                    },
                }
            )

        semantic_payload = dict(graph_data.get("_semantic", {}))
        semantic_payload["record_context"] = record_metadata
        return {
            "nodes": list(node_map.values()),
            "relationships": normalized_relationships,
            "_semantic": semantic_payload,
        }

    @staticmethod
    def _copy_scope_properties(properties: Dict[str, Any], record_metadata: Dict[str, Any]) -> None:
        for key in ("user_id", "agent_id", "session_id", "created_at", "updated_at"):
            value = record_metadata.get(key)
            if value not in (None, ""):
                properties.setdefault(key, value)

    def _cache_get(self, key: str) -> Optional[List[float]]:
        with self._embedding_cache_lock:
            vec = self._embedding_cache.get(key)
            if vec is not None:
                self._embedding_cache.move_to_end(key)
            return vec

    def _cache_put(self, key: str, vec: List[float]) -> None:
        with self._embedding_cache_lock:
            if key in self._embedding_cache:
                self._embedding_cache.move_to_end(key)
            else:
                if len(self._embedding_cache) >= self._embedding_cache_max_size:
                    self._embedding_cache.popitem(last=False)
            self._embedding_cache[key] = vec

    def _compute_relatedness(self, candidate_names: Set[str], known_entities: Set[str]) -> Dict[str, Any]:
        if not candidate_names:
            return {
                "is_related": False,
                "score": 0.0,
                "lexical_score": 0.0,
                "embedding_score": None,
                "overlap_count": 0,
                "reason": "no_candidate_entities",
            }
        if not known_entities:
            return {
                "is_related": True,
                "score": 1.0,
                "lexical_score": 1.0,
                "embedding_score": None,
                "overlap_count": 0,
                "reason": "bootstrap_record",
            }

        overlap = candidate_names.intersection(known_entities)
        lexical_score = len(overlap) / max(len(candidate_names), 1)
        embedding_score = self._compute_embedding_relatedness(candidate_names, known_entities)
        score = max(lexical_score, embedding_score or 0.0)
        if len(overlap) > 0:
            reason = "overlap_detected"
            is_related = True
        elif embedding_score is not None and embedding_score >= self._embedding_relatedness_threshold:
            reason = "embedding_match"
            is_related = True
        elif lexical_score >= self._relatedness_threshold:
            reason = "lexical_threshold"
            is_related = True
        else:
            reason = "below_threshold"
            is_related = False
        return {
            "is_related": is_related,
            "score": round(score, 3),
            "lexical_score": round(lexical_score, 3),
            "embedding_score": round(embedding_score, 3) if embedding_score is not None else None,
            "overlap_count": len(overlap),
            "reason": reason,
        }

    def _should_run_linker(self, known_entities: Set[str], relatedness: Dict[str, Any]) -> bool:
        if not self._llm_stack_ready or self._linker is None:
            return False
        if not known_entities:
            return True
        return bool(relatedness.get("is_related"))

    @staticmethod
    def _merge_ontology_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged_classes: Dict[str, Dict[str, Any]] = {}
        merged_relationships: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        ontology_names: List[str] = []

        for item in candidates:
            name = str(item.get("ontology_name", "")).strip()
            if name:
                ontology_names.append(name)
            for cls in item.get("classes", []):
                cls_name = str(cls.get("name", "")).strip()
                if not cls_name:
                    continue
                existing = merged_classes.setdefault(
                    cls_name,
                    {
                        "name": cls_name,
                        "description": str(cls.get("description", "")).strip(),
                        "aliases": [],
                        "broader": [],
                        "related": [],
                        "properties": [],
                    },
                )
                if not existing.get("description"):
                    existing["description"] = str(cls.get("description", "")).strip()
                existing["aliases"] = RuntimeRawIngestor._merge_string_lists(
                    existing.get("aliases", []),
                    cls.get("aliases", []),
                )
                existing["broader"] = RuntimeRawIngestor._merge_string_lists(
                    existing.get("broader", []),
                    cls.get("broader", []),
                )
                existing["related"] = RuntimeRawIngestor._merge_string_lists(
                    existing.get("related", []),
                    cls.get("related", []),
                )
                existing_props = {
                    str(prop.get("name", "")).strip(): prop
                    for prop in existing["properties"]
                    if isinstance(prop, dict) and str(prop.get("name", "")).strip()
                }
                for prop in cls.get("properties", []):
                    prop_name = str(prop.get("name", "")).strip()
                    if not prop_name:
                        continue
                    existing_prop = existing_props.get(prop_name)
                    if existing_prop is None:
                        existing_prop = {
                            "name": prop_name,
                            "datatype": str(prop.get("datatype", "string")).strip() or "string",
                            "description": str(prop.get("description", "")).strip(),
                            "aliases": RuntimeRawIngestor._clean_string_list(prop.get("aliases", [])),
                        }
                        existing["properties"].append(existing_prop)
                        existing_props[prop_name] = existing_prop
                        continue
                    if not existing_prop.get("description"):
                        existing_prop["description"] = str(prop.get("description", "")).strip()
                    if not existing_prop.get("datatype"):
                        existing_prop["datatype"] = str(prop.get("datatype", "string")).strip() or "string"
                    existing_prop["aliases"] = RuntimeRawIngestor._merge_string_lists(
                        existing_prop.get("aliases", []),
                        prop.get("aliases", []),
                    )

            for rel in item.get("relationships", []):
                rel_type = str(rel.get("type", "")).strip()
                source = str(rel.get("source", "")).strip()
                target = str(rel.get("target", "")).strip()
                if not rel_type:
                    continue
                rel_key = (rel_type, source, target)
                existing_rel = merged_relationships.setdefault(
                    rel_key,
                    {
                        "type": rel_type,
                        "source": source,
                        "target": target,
                        "description": "",
                        "aliases": [],
                        "related": [],
                    },
                )
                if not existing_rel.get("description"):
                    existing_rel["description"] = str(rel.get("description", "")).strip()
                existing_rel["aliases"] = RuntimeRawIngestor._merge_string_lists(
                    existing_rel.get("aliases", []),
                    rel.get("aliases", []),
                )
                existing_rel["related"] = RuntimeRawIngestor._merge_string_lists(
                    existing_rel.get("related", []),
                    rel.get("related", []),
                )

        ontology_name = ontology_names[0] if ontology_names else "runtime_candidate_merged"
        return {
            "ontology_name": ontology_name,
            "classes": list(merged_classes.values()),
            "relationships": list(merged_relationships.values()),
        }

    @staticmethod
    def _merge_shacl_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        shape_map: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            for shape in item.get("shapes", []):
                target = str(shape.get("target_class", "")).strip()
                if not target:
                    continue
                existing = shape_map.setdefault(target, {"target_class": target, "properties": []})
                seen_keys = {
                    (
                        p.get("path"),
                        p.get("constraint"),
                        json.dumps(p.get("params", {}), sort_keys=True),
                    )
                    for p in existing["properties"]
                }
                for prop in shape.get("properties", []):
                    key = (
                        prop.get("path"),
                        prop.get("constraint"),
                        json.dumps(prop.get("params", {}), sort_keys=True),
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    existing["properties"].append(
                        {
                            "path": str(prop.get("path", "")).strip(),
                            "constraint": str(prop.get("constraint", "")).strip(),
                            "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                        }
                    )
        return {"shapes": list(shape_map.values())}

    @staticmethod
    def _build_vocabulary_candidate(
        ontology_candidate: Dict[str, Any],
        shacl_candidate: Dict[str, Any],
        prepared_graphs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        term_map: Dict[str, Dict[str, Any]] = {}

        def upsert_term(
            canonical: str,
            *,
            source: str,
            alt_labels: Optional[List[Any]] = None,
            hidden_labels: Optional[List[Any]] = None,
            broader: Optional[List[Any]] = None,
            related: Optional[List[Any]] = None,
            definition: str = "",
            examples: Optional[List[Any]] = None,
        ) -> None:
            canonical_text = str(canonical).strip()
            if not canonical_text:
                return
            key = canonical_text.lower()
            existing = term_map.setdefault(
                key,
                {
                    "pref_label": canonical_text,
                    "alt_labels": set(),
                    "hidden_labels": set(),
                    "broader": set(),
                    "related": set(),
                    "definition": "",
                    "sources": set(),
                    "examples": set(),
                },
            )
            existing["sources"].add(source)
            if definition and not existing["definition"]:
                existing["definition"] = definition
            for alias in alt_labels or []:
                alias_text = str(alias).strip()
                if alias_text and alias_text.lower() != key:
                    existing["alt_labels"].add(alias_text)
            for alias in hidden_labels or []:
                alias_text = str(alias).strip()
                if alias_text and alias_text.lower() != key:
                    existing["hidden_labels"].add(alias_text)
            for value in broader or []:
                broader_text = str(value).strip()
                if broader_text:
                    existing["broader"].add(broader_text)
            for value in related or []:
                related_text = str(value).strip()
                if related_text:
                    existing["related"].add(related_text)
            for value in examples or []:
                example_text = str(value).strip()
                if example_text:
                    existing["examples"].add(example_text)

        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            class_name = str(cls.get("name", "")).strip()
            upsert_term(
                class_name,
                source="ontology.class",
                alt_labels=RuntimeRawIngestor._clean_string_list(cls.get("aliases", [])),
                broader=RuntimeRawIngestor._clean_string_list(cls.get("broader", [])),
                related=RuntimeRawIngestor._clean_string_list(cls.get("related", [])),
                definition=str(cls.get("description", "")).strip(),
            )
            for prop in cls.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                prop_name = str(prop.get("name", "")).strip()
                if not prop_name:
                    continue
                qualified_name = f"{class_name}.{prop_name}" if class_name else prop_name
                upsert_term(
                    qualified_name,
                    source="ontology.property",
                    alt_labels=RuntimeRawIngestor._clean_string_list(prop.get("aliases", [])),
                    definition=str(prop.get("description", "")).strip(),
                )

        for rel in ontology_candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            upsert_term(
                str(rel.get("type", "")).strip(),
                source="ontology.relationship",
                alt_labels=RuntimeRawIngestor._clean_string_list(rel.get("aliases", [])),
                related=RuntimeRawIngestor._clean_string_list(rel.get("related", [])),
                definition=str(rel.get("description", "")).strip(),
            )

        for shape in shacl_candidate.get("shapes", []):
            if not isinstance(shape, dict):
                continue
            target_class = str(shape.get("target_class", "")).strip()
            upsert_term(target_class, source="shacl.target_class")
            for prop in shape.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("path", "")).strip()
                constraint = str(prop.get("constraint", "")).strip()
                if not path:
                    continue
                params = prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {}
                definition = constraint
                if params:
                    definition = f"{constraint} {json.dumps(params, sort_keys=True)}"
                upsert_term(
                    f"{target_class}.{path}" if target_class else path,
                    source="shacl.property",
                    definition=definition,
                )

        for graph_data in prepared_graphs or []:
            for node in graph_data.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                label = str(node.get("label", "")).strip()
                if not label or label == "Document":
                    continue
                props = node.get("properties", {})
                properties = props if isinstance(props, dict) else {}
                upsert_term(
                    label,
                    source="entity.label_observation",
                    examples=[
                        RuntimeRawIngestor._node_display_name(
                            properties,
                            node_id=str(node.get("id", "")).strip(),
                        )
                    ],
                )
            for rel in graph_data.get("relationships", []):
                if not isinstance(rel, dict):
                    continue
                rel_type = str(rel.get("type", "")).strip()
                if rel_type and rel_type != "MENTIONS":
                    upsert_term(rel_type, source="entity.relationship_observation")

        terms: List[Dict[str, Any]] = []
        for value in sorted(term_map.values(), key=lambda item: str(item.get("pref_label", "")).lower()):
            terms.append(
                {
                    "pref_label": value["pref_label"],
                    "alt_labels": sorted(value["alt_labels"], key=lambda alias: alias.lower()),
                    "hidden_labels": sorted(value["hidden_labels"], key=lambda alias: alias.lower()),
                    "broader": sorted(value["broader"], key=lambda alias: alias.lower()),
                    "related": sorted(value["related"], key=lambda alias: alias.lower()),
                    "definition": value.get("definition", ""),
                    "sources": sorted(value["sources"]),
                    "examples": sorted(value["examples"]),
                }
            )

        return {
            "schema_version": "vocabulary.v2",
            "profile": "skos",
            "terms": terms,
        }

    @staticmethod
    def _merge_string_lists(existing: Any, incoming: Any) -> List[str]:
        seen: Set[str] = set()
        merged: List[str] = []
        for value in RuntimeRawIngestor._clean_string_list(existing) + RuntimeRawIngestor._clean_string_list(incoming):
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(value)
        return merged

    @staticmethod
    def _clean_string_list(values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        cleaned: List[str] = []
        seen: Set[str] = set()
        for value in values:
            text = str(value).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        return cleaned

    @staticmethod
    def _node_display_name(properties: Dict[str, Any], node_id: str = "") -> str:
        return str(
            properties.get("name")
            or properties.get("title")
            or properties.get("id")
            or properties.get("uri")
            or node_id
        ).strip()

    @staticmethod
    def _shacl_candidates_to_rule_profile(shacl_candidate: Dict[str, Any]) -> Dict[str, Any]:
        supported = {"required", "datatype", "enum", "range"}
        rules: List[Dict[str, Any]] = []
        for shape in shacl_candidate.get("shapes", []):
            label = str(shape.get("target_class", "")).strip()
            if not label:
                continue
            for prop in shape.get("properties", []):
                kind = str(prop.get("constraint", "")).strip()
                path = str(prop.get("path", "")).strip()
                if not path or kind not in supported:
                    continue
                rules.append(
                    {
                        "label": label,
                        "property_name": path,
                        "kind": kind,
                        "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                    }
                )
        return {"schema_version": "rules.v1", "rules": rules}

    @staticmethod
    def _merge_rule_profiles(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for profile in [primary, secondary]:
            for rule in profile.get("rules", []):
                key = (
                    str(rule.get("label", "")),
                    str(rule.get("property_name", "")),
                    str(rule.get("kind", "")),
                    json.dumps(rule.get("params", {}), sort_keys=True),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "label": key[0],
                        "property_name": key[1],
                        "kind": key[2],
                        "params": rule.get("params", {}) if isinstance(rule.get("params", {}), dict) else {},
                    }
                )
        return {"schema_version": "rules.v1", "rules": merged}

    @staticmethod
    def _summarize_relatedness(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(records)
        linked = sum(1 for item in records if item.get("is_related"))
        avg_score = 0.0 if total == 0 else sum(float(item.get("score", 0.0)) for item in records) / total
        embed_available = sum(1 for item in records if item.get("embedding_score") is not None)
        return {
            "total_records": total,
            "related_records": linked,
            "unrelated_records": max(total - linked, 0),
            "average_score": round(avg_score, 3),
            "embedding_evaluated_records": embed_available,
        }

    def _compute_embedding_relatedness(self, candidate_names: Set[str], known_entities: Set[str]) -> Optional[float]:
        if not self._embedding_enabled or self._embedding_client is None:
            return None
        candidate_text = " | ".join(sorted(candidate_names)[:40]).strip()
        known_text = " | ".join(sorted(known_entities)[:120]).strip()
        if not candidate_text or not known_text:
            return None
        candidate_vec = self._embed_text(candidate_text)
        known_vec = self._embed_text(known_text)
        if candidate_vec is None or known_vec is None:
            return None
        return _cosine_similarity(candidate_vec, known_vec)

    def _embed_text(self, text: str) -> Optional[List[float]]:
        key = text.strip().lower()
        if not key:
            return None
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if self._embedding_client is None:
            return None
        try:
            response = self._embedding_client.embeddings.create(
                input=[text],
                model=self._embedding_model,
            )
            vec = response.data[0].embedding
            self._cache_put(key, vec)
            return vec
        except Exception as exc:
            logger.warning("Embedding relatedness skipped due to embedding error: %s", exc)
            return None

    @staticmethod
    def _resolve_semantic_artifacts(
        policy: str,
        draft_ontology: Dict[str, Any],
        draft_shacl: Dict[str, Any],
        approved_artifacts: Dict[str, Any],
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        approved_ontology = approved_artifacts.get("ontology_candidate")
        approved_shacl = approved_artifacts.get("shacl_candidate")

        if policy == "draft_only":
            return (
                {"ontology_candidate": {"ontology_name": "", "classes": [], "relationships": []}, "shacl_candidate": {"shapes": []}},
                {
                    "policy": policy,
                    "applied": "none",
                    "status": "draft_pending_review",
                    "warning": None,
                },
            )

        if policy == "approved_only":
            if isinstance(approved_ontology, dict) and isinstance(approved_shacl, dict):
                return (
                    {"ontology_candidate": approved_ontology, "shacl_candidate": approved_shacl},
                    {
                        "policy": policy,
                        "applied": "approved",
                        "status": "approved_applied",
                        "warning": None,
                    },
                )
            return (
                {"ontology_candidate": {"ontology_name": "", "classes": [], "relationships": []}, "shacl_candidate": {"shapes": []}},
                {
                    "policy": policy,
                    "applied": "none",
                    "status": "approval_required",
                    "warning": "approved_only policy requires approved_artifacts with ontology_candidate and shacl_candidate.",
                },
            )

        return (
            {"ontology_candidate": draft_ontology, "shacl_candidate": draft_shacl},
            {
                "policy": "auto",
                "applied": "draft",
                "status": "auto_applied",
                "warning": None,
            },
        )


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


from seocho.index.linker import _cosine_similarity  # canonical impl (Rust native + Python fallback)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
