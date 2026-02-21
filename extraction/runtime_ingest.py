"""
Runtime raw-data ingestion service for interactive platform usage.

This module supports ingesting user-provided raw text records through API calls,
running parse -> semantic extraction -> linking -> rule-annotation, and loading
graph data into a target DB.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from config import load_pipeline_runtime_config
from database_manager import DatabaseManager
from exceptions import InvalidDatabaseNameError
from raw_material_parser import MaterialParseError, parse_raw_material_record

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


class RuntimeRawIngestor:
    """Runs extraction/linking pipeline for ad-hoc runtime records."""

    def __init__(self, db_manager: DatabaseManager):
        self._db_manager = db_manager
        self._extractor = None
        self._linker = None
        self._semantic_orchestrator = None
        self._llm_stack_ready = False
        self._relatedness_threshold = _env_float("RUNTIME_LINKING_RELATEDNESS_THRESHOLD", 0.2)

        try:
            from extractor import EntityExtractor
            from linker import EntityLinker
            from prompt_manager import PromptManager
            from semantic_pass_orchestrator import SemanticPassOrchestrator

            cfg = load_pipeline_runtime_config()
            api_key = cfg.openai_api_key
            if not api_key:
                raise ValueError("OPENAI_API_KEY is empty")
            model = cfg.model
            prompt_manager = PromptManager(cfg)
            self._extractor = EntityExtractor(prompt_manager=prompt_manager, api_key=api_key, model=model)
            self._linker = EntityLinker(prompt_manager=prompt_manager, api_key=api_key, model=model)
            self._semantic_orchestrator = SemanticPassOrchestrator(
                api_key=api_key,
                model=model,
                extractor=self._extractor,
            )
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

        for idx, item in enumerate(records):
            source_id = str(item.get("id") or f"raw_{idx}")
            category = str(item.get("category", "general")).strip() or "general"
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

            known_entities.update(candidate_names)
            prepared_graphs.append((source_id, graph_data))

        merged_ontology = self._merge_ontology_candidates(ontology_candidates)
        merged_shacl = self._merge_shacl_candidates(shacl_candidates)
        llm_rule_profile = self._shacl_candidates_to_rule_profile(merged_shacl)

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
            "semantic_artifacts": {
                "ontology_candidate": merged_ontology,
                "shacl_candidate": merged_shacl,
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

    def _extract_graph(self, source_id: str, text: str, category: str) -> Tuple[Dict[str, Any], bool, str]:
        if not self._llm_stack_ready or self._extractor is None:
            return (
                self._fallback_extract(source_id=source_id, text=text, semantic_payload={}),
                True,
                "LLM extraction stack unavailable",
            )

        try:
            semantic_payload: Dict[str, Any] = {}
            if self._semantic_orchestrator is not None:
                pass_result = self._semantic_orchestrator.run_three_pass(text=text, category=category)
                extracted = pass_result.get("entity_graph", {})
                semantic_payload = {
                    "ontology_candidate": pass_result.get("ontology_candidate", {}),
                    "shacl_candidate": pass_result.get("shacl_candidate", {}),
                    "pass_metadata": pass_result.get("metadata", {}),
                }
            else:
                extracted = self._extractor.extract_entities(text=text, category=category)

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
            linked = self._linker.link_entities(extracted_data=input_graph, category=category)
            linked.setdefault("nodes", input_graph["nodes"])
            linked.setdefault("relationships", input_graph["relationships"])
            linked["_semantic"] = graph_data.get("_semantic", {})
            return linked, None
        except Exception as exc:
            return graph_data, f"LLM linking failed: {type(exc).__name__}"

    def _load_existing_entity_names(self, target_database: str, limit: int = 500) -> Set[str]:
        names: Set[str] = set()
        try:
            with self._db_manager.driver.session(database=target_database) as session:
                result = session.run(
                    "MATCH (n) WHERE n.name IS NOT NULL "
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
            props = node.get("properties", {}) if isinstance(node, dict) else {}
            value = str(props.get("name", "")).strip().lower()
            if value:
                names.add(value)
        return names

    def _compute_relatedness(self, candidate_names: Set[str], known_entities: Set[str]) -> Dict[str, Any]:
        if not candidate_names:
            return {"is_related": False, "score": 0.0, "overlap_count": 0, "reason": "no_candidate_entities"}
        if not known_entities:
            return {"is_related": True, "score": 1.0, "overlap_count": 0, "reason": "bootstrap_record"}

        overlap = candidate_names.intersection(known_entities)
        score = len(overlap) / max(len(candidate_names), 1)
        is_related = score >= self._relatedness_threshold or len(overlap) > 0
        return {
            "is_related": is_related,
            "score": round(score, 3),
            "overlap_count": len(overlap),
            "reason": "overlap_detected" if overlap else "below_threshold",
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
                        "properties": [],
                    },
                )
                seen_props = {p["name"] for p in existing["properties"] if "name" in p}
                for prop in cls.get("properties", []):
                    prop_name = str(prop.get("name", "")).strip()
                    if not prop_name or prop_name in seen_props:
                        continue
                    seen_props.add(prop_name)
                    existing["properties"].append(
                        {
                            "name": prop_name,
                            "datatype": str(prop.get("datatype", "string")).strip() or "string",
                        }
                    )

            for rel in item.get("relationships", []):
                rel_type = str(rel.get("type", "")).strip()
                source = str(rel.get("source", "")).strip()
                target = str(rel.get("target", "")).strip()
                if not rel_type:
                    continue
                merged_relationships[(rel_type, source, target)] = {
                    "type": rel_type,
                    "source": source,
                    "target": target,
                    "description": str(rel.get("description", "")).strip(),
                }

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
        return {
            "total_records": total,
            "related_records": linked,
            "unrelated_records": max(total - linked, 0),
            "average_score": round(avg_score, 3),
        }


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
