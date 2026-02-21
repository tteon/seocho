"""
Runtime raw-data ingestion service for interactive platform usage.

This module supports ingesting user-provided raw text records through API calls,
running extraction/linking/rule-annotation, and loading graph data into a target DB.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Tuple

from config import load_pipeline_runtime_config
from database_manager import DatabaseManager
from exceptions import InvalidDatabaseNameError

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


class RuntimeRawIngestor:
    """Runs extraction/linking pipeline for ad-hoc runtime records."""

    def __init__(self, db_manager: DatabaseManager):
        self._db_manager = db_manager
        self._extractor = None
        self._linker = None
        self._llm_stack_ready = False

        try:
            from extractor import EntityExtractor
            from linker import EntityLinker
            from prompt_manager import PromptManager

            cfg = load_pipeline_runtime_config()
            api_key = cfg.openai_api_key
            model = cfg.model
            prompt_manager = PromptManager(cfg)
            self._extractor = EntityExtractor(prompt_manager=prompt_manager, api_key=api_key, model=model)
            self._linker = EntityLinker(prompt_manager=prompt_manager, api_key=api_key, model=model)
            self._llm_stack_ready = True
        except Exception as exc:
            logger.warning("LLM extraction stack unavailable; fallback mode only: %s", exc)

    def ingest_records(
        self,
        records: List[Dict[str, Any]],
        target_database: str,
        enable_rule_constraints: bool = True,
        create_database_if_missing: bool = True,
    ) -> Dict[str, Any]:
        from rule_constraints import apply_rules_to_graph, infer_rules_from_graph

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

        for idx, item in enumerate(records):
            source_id = str(item.get("id") or f"raw_{idx}")
            text = str(item.get("content", "")).strip()
            category = str(item.get("category", "general")).strip() or "general"
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

            try:
                graph_data, used_fallback, fallback_reason = self._extract_graph(source_id, text, category)
            except Exception as exc:
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

            if used_fallback:
                fallback_records += 1
                warnings.append(
                    {
                        "record_id": source_id,
                        "warning_type": "FallbackExtraction",
                        "message": fallback_reason,
                    }
                )
            prepared_graphs.append((source_id, graph_data))

        ruleset = None
        if enable_rule_constraints and prepared_graphs:
            try:
                merged_graph = {"nodes": [], "relationships": []}
                for _, graph_data in prepared_graphs:
                    merged_graph["nodes"].extend(graph_data.get("nodes", []))
                    merged_graph["relationships"].extend(graph_data.get("relationships", []))
                ruleset = infer_rules_from_graph(merged_graph)
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
                self._db_manager.load_data(target_database, graph_for_load, source_id=source_id)
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
            "errors": errors,
            "warnings": warnings,
            "status": (
                "success_with_fallback"
                if failed == 0 and fallback_records > 0
                else ("success" if failed == 0 else ("partial_success" if processed > 0 else "failed"))
            ),
        }

    def _extract_graph(self, source_id: str, text: str, category: str) -> Tuple[Dict[str, Any], bool, str]:
        if not self._llm_stack_ready or self._extractor is None or self._linker is None:
            return (
                self._fallback_extract(source_id=source_id, text=text),
                True,
                "LLM extraction stack unavailable",
            )
        try:
            extracted = self._extractor.extract_entities(text=text, category=category)
            linked = self._linker.link_entities(extracted_data=extracted, category=category)
            return linked, False, ""
        except Exception as exc:
            logger.warning("LLM extraction failed for '%s'; falling back to rule-based extraction: %s", source_id, exc)
            fallback_graph = self._fallback_extract(source_id=source_id, text=text)
            reason = f"LLM extraction/linking failed: {type(exc).__name__}"
            return fallback_graph, True, reason

    @staticmethod
    def _fallback_extract(source_id: str, text: str) -> Dict[str, Any]:
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
        }
