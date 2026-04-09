"""
Three-pass semantic extraction orchestrator.

Pass 1: ontology candidate extraction
Pass 2: SHACL-like candidate extraction
Pass 3: entity graph extraction (ontology/shacl context injected)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from retry_utils import openai_retry
from semantic_context import build_dynamic_prompt_context
from tracing import track, wrap_openai_client

logger = logging.getLogger(__name__)


class SemanticPassOrchestrator:
    """Runs ontology -> SHACL -> entity extraction using LLM-driven passes."""

    def __init__(
        self,
        api_key: str,
        model: str,
        extractor: Any,
        json_runner: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ):
        self.model = model
        self.extractor = extractor
        self._json_runner = json_runner
        self._client = None
        if json_runner is None:
            from openai import OpenAI

            self._client = wrap_openai_client(OpenAI(api_key=api_key))

    @track("semantic_pass_orchestrator.run")
    def run_three_pass(
        self,
        text: str,
        category: str,
        record_metadata: Optional[Dict[str, Any]] = None,
        source_type: str = "text",
        approved_artifacts: Optional[Dict[str, Any]] = None,
        graph_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ontology_payload: Dict[str, Any] = {}
        shacl_payload: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {"ontology_pass": "skipped", "shacl_pass": "skipped"}

        try:
            ontology_payload = self._extract_ontology_candidate(
                text=text,
                category=category,
                record_metadata=record_metadata,
                source_type=source_type,
                approved_artifacts=approved_artifacts,
                graph_metadata=graph_metadata,
            )
            metadata["ontology_pass"] = "ok"
        except Exception as exc:
            metadata["ontology_pass"] = f"error:{type(exc).__name__}"
            logger.warning("Ontology candidate pass failed: %s", exc)

        try:
            shacl_payload = self._extract_shacl_candidate(
                text=text,
                category=category,
                ontology_payload=ontology_payload,
                record_metadata=record_metadata,
                source_type=source_type,
                approved_artifacts=approved_artifacts,
                graph_metadata=graph_metadata,
            )
            metadata["shacl_pass"] = "ok"
        except Exception as exc:
            metadata["shacl_pass"] = f"error:{type(exc).__name__}"
            logger.warning("SHACL candidate pass failed: %s", exc)

        entity_context = self._build_entity_context(
            ontology_payload=ontology_payload,
            shacl_payload=shacl_payload,
            category=category,
            source_type=source_type,
            record_metadata=record_metadata,
            approved_artifacts=approved_artifacts,
            graph_metadata=graph_metadata,
        )
        entity_graph = self.extractor.extract_entities(text=text, category=category, extra_context=entity_context)

        return {
            "ontology_candidate": ontology_payload,
            "shacl_candidate": shacl_payload,
            "entity_graph": entity_graph,
            "metadata": metadata,
            "prompt_context": entity_context,
        }

    def _extract_ontology_candidate(
        self,
        text: str,
        category: str,
        record_metadata: Optional[Dict[str, Any]],
        source_type: str,
        approved_artifacts: Optional[Dict[str, Any]],
        graph_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You extract ontology candidates from domain text. "
            "Return strict JSON only."
        )
        prompt_context = build_dynamic_prompt_context(
            category=category,
            source_type=source_type,
            approved_artifacts=approved_artifacts,
            record_metadata=record_metadata,
            graph_metadata=graph_metadata,
        )
        sections = [
            "Analyze the input text and return ontology candidate JSON with keys: ontology_name, classes, relationships.",
            "Each class item: {name, description, aliases, broader, related, properties:[{name, datatype, description, aliases}]}.",
            "Each relationship item: {type, source, target, description, aliases, related}.",
            "Use aliases and SKOS-compatible vocabulary hints when visible in the text or metadata.",
            f"Category: {category}",
            f"Source type: {source_type}",
        ]
        self._append_section(sections, "Graph target metadata", prompt_context.get("graph_context", ""))
        self._append_section(sections, "Developer instructions", prompt_context.get("developer_instructions", ""))
        self._append_section(sections, "Ontology hints", prompt_context.get("entity_types", ""))
        self._append_section(sections, "Relationship hints", prompt_context.get("relationship_types", ""))
        self._append_section(sections, "Vocabulary hints", prompt_context.get("vocabulary_terms", ""))
        self._append_section(sections, "Record metadata", prompt_context.get("record_metadata_json", ""))
        self._append_section(sections, "Prompt guidance", prompt_context.get("ontology_context_notes", ""))
        sections.append(f"Text:\n{text[:12000]}")
        user_prompt = "\n\n".join(sections)
        payload = self._run_json(system_prompt, user_prompt)
        return self._normalize_ontology_payload(payload)

    def _extract_shacl_candidate(
        self,
        text: str,
        category: str,
        ontology_payload: Dict[str, Any],
        record_metadata: Optional[Dict[str, Any]],
        source_type: str,
        approved_artifacts: Optional[Dict[str, Any]],
        graph_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You extract SHACL-like constraints from text and ontology hints. "
            "Return strict JSON only."
        )
        prompt_context = build_dynamic_prompt_context(
            category=category,
            source_type=source_type,
            ontology_candidate=ontology_payload,
            approved_artifacts=approved_artifacts,
            record_metadata=record_metadata,
            graph_metadata=graph_metadata,
        )
        sections = [
            "Return JSON with key 'shapes'.",
            "Each shape: {target_class, properties:[{path, constraint, params}]}.",
            "constraint must be one of required, datatype, enum, range.",
            f"Category: {category}",
            f"Source type: {source_type}",
        ]
        self._append_section(sections, "Graph target metadata", prompt_context.get("graph_context", ""))
        self._append_section(sections, "Developer instructions", prompt_context.get("developer_instructions", ""))
        self._append_section(sections, "Ontology hints", prompt_context.get("entity_types", ""))
        self._append_section(sections, "Relationship hints", prompt_context.get("relationship_types", ""))
        self._append_section(sections, "Vocabulary hints", prompt_context.get("vocabulary_terms", ""))
        self._append_section(sections, "Record metadata", prompt_context.get("record_metadata_json", ""))
        sections.append(f"Text:\n{text[:12000]}")
        user_prompt = "\n\n".join(sections)
        payload = self._run_json(system_prompt, user_prompt)
        return self._normalize_shacl_payload(payload)

    def _build_entity_context(
        self,
        ontology_payload: Dict[str, Any],
        shacl_payload: Dict[str, Any],
        category: str,
        source_type: str,
        record_metadata: Optional[Dict[str, Any]],
        approved_artifacts: Optional[Dict[str, Any]],
        graph_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return build_dynamic_prompt_context(
            category=category,
            source_type=source_type,
            ontology_candidate=ontology_payload,
            shacl_candidate=shacl_payload,
            approved_artifacts=approved_artifacts,
            record_metadata=record_metadata,
            graph_metadata=graph_metadata,
        )

    @openai_retry
    def _run_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        if self._json_runner is not None:
            return self._json_runner(system_prompt, user_prompt)
        if self._client is None:
            return {}
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            return {}
        try:
            payload = json.loads(content)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _normalize_ontology_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        ontology_name = str(payload.get("ontology_name", "runtime_candidate")).strip() or "runtime_candidate"
        classes = payload.get("classes", [])
        relationships = payload.get("relationships", [])
        norm_classes = []
        for cls in classes if isinstance(classes, list) else []:
            name = str(cls.get("name", "")).strip()
            if not name:
                continue
            props = cls.get("properties", [])
            norm_props = []
            for prop in props if isinstance(props, list) else []:
                prop_name = str(prop.get("name", "")).strip()
                if not prop_name:
                    continue
                norm_props.append(
                    {
                        "name": prop_name,
                        "datatype": str(prop.get("datatype", "string")).strip() or "string",
                        "description": str(prop.get("description", "")).strip(),
                        "aliases": SemanticPassOrchestrator._clean_string_list(prop.get("aliases", [])),
                    }
                )
            norm_classes.append(
                {
                    "name": name,
                    "description": str(cls.get("description", "")).strip(),
                    "aliases": SemanticPassOrchestrator._clean_string_list(cls.get("aliases", [])),
                    "broader": SemanticPassOrchestrator._clean_string_list(cls.get("broader", [])),
                    "related": SemanticPassOrchestrator._clean_string_list(cls.get("related", [])),
                    "properties": norm_props,
                }
            )

        norm_relationships = []
        for rel in relationships if isinstance(relationships, list) else []:
            rel_type = str(rel.get("type", "")).strip()
            if not rel_type:
                continue
            norm_relationships.append(
                {
                    "type": rel_type,
                    "source": str(rel.get("source", "")).strip(),
                    "target": str(rel.get("target", "")).strip(),
                    "description": str(rel.get("description", "")).strip(),
                    "aliases": SemanticPassOrchestrator._clean_string_list(rel.get("aliases", [])),
                    "related": SemanticPassOrchestrator._clean_string_list(rel.get("related", [])),
                }
            )

        return {
            "ontology_name": ontology_name,
            "classes": norm_classes,
            "relationships": norm_relationships,
        }

    @staticmethod
    def _normalize_shacl_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        shapes = payload.get("shapes", [])
        normalized_shapes = []
        for shape in shapes if isinstance(shapes, list) else []:
            target_class = str(shape.get("target_class", "")).strip()
            if not target_class:
                continue
            properties = shape.get("properties", [])
            normalized_props = []
            for prop in properties if isinstance(properties, list) else []:
                path = str(prop.get("path", "")).strip()
                constraint = str(prop.get("constraint", "")).strip()
                if not path or not constraint:
                    continue
                normalized_props.append(
                    {
                        "path": path,
                        "constraint": constraint,
                        "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                    }
                )
            normalized_shapes.append(
                {
                    "target_class": target_class,
                    "properties": normalized_props,
                }
            )
        return {"shapes": normalized_shapes}

    @staticmethod
    def _clean_string_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        cleaned: list[str] = []
        seen = set()
        for value in values:
            text = str(value).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        return cleaned

    @staticmethod
    def _append_section(lines: list[str], title: str, content: str) -> None:
        text = str(content or "").strip()
        if text:
            lines.append(f"{title}:\n{text}")
