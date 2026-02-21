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
    def run_three_pass(self, text: str, category: str) -> Dict[str, Any]:
        ontology_payload: Dict[str, Any] = {}
        shacl_payload: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {"ontology_pass": "skipped", "shacl_pass": "skipped"}

        try:
            ontology_payload = self._extract_ontology_candidate(text=text, category=category)
            metadata["ontology_pass"] = "ok"
        except Exception as exc:
            metadata["ontology_pass"] = f"error:{type(exc).__name__}"
            logger.warning("Ontology candidate pass failed: %s", exc)

        try:
            shacl_payload = self._extract_shacl_candidate(text=text, category=category, ontology_payload=ontology_payload)
            metadata["shacl_pass"] = "ok"
        except Exception as exc:
            metadata["shacl_pass"] = f"error:{type(exc).__name__}"
            logger.warning("SHACL candidate pass failed: %s", exc)

        entity_context = self._build_entity_context(ontology_payload=ontology_payload, shacl_payload=shacl_payload)
        entity_graph = self.extractor.extract_entities(text=text, category=category, extra_context=entity_context)

        return {
            "ontology_candidate": ontology_payload,
            "shacl_candidate": shacl_payload,
            "entity_graph": entity_graph,
            "metadata": metadata,
        }

    def _extract_ontology_candidate(self, text: str, category: str) -> Dict[str, Any]:
        system_prompt = (
            "You extract ontology candidates from domain text. "
            "Return strict JSON only."
        )
        user_prompt = (
            "Analyze the input text and return ontology candidate JSON with keys: "
            "ontology_name, classes, relationships. "
            "Each class item: {name, description, properties:[{name, datatype}]}. "
            "Each relationship item: {type, source, target, description}. "
            f"Category: {category}\n\nText:\n{text[:12000]}"
        )
        payload = self._run_json(system_prompt, user_prompt)
        return self._normalize_ontology_payload(payload)

    def _extract_shacl_candidate(self, text: str, category: str, ontology_payload: Dict[str, Any]) -> Dict[str, Any]:
        system_prompt = (
            "You extract SHACL-like constraints from text and ontology hints. "
            "Return strict JSON only."
        )
        user_prompt = (
            "Return JSON with key 'shapes'. "
            "Each shape: {target_class, properties:[{path, constraint, params}]}. "
            "constraint must be one of required, datatype, enum, range. "
            f"Category: {category}\n"
            f"Ontology hints:\n{json.dumps(ontology_payload, ensure_ascii=False)}\n\n"
            f"Text:\n{text[:12000]}"
        )
        payload = self._run_json(system_prompt, user_prompt)
        return self._normalize_shacl_payload(payload)

    def _build_entity_context(self, ontology_payload: Dict[str, Any], shacl_payload: Dict[str, Any]) -> Dict[str, Any]:
        classes = ontology_payload.get("classes", [])
        rels = ontology_payload.get("relationships", [])
        shapes = shacl_payload.get("shapes", [])

        entity_lines = []
        for item in classes:
            name = str(item.get("name", "")).strip() or "Entity"
            description = str(item.get("description", "")).strip()
            properties = item.get("properties", [])
            prop_desc = ", ".join(str(prop.get("name", "")).strip() for prop in properties if prop.get("name"))
            text = f"- {name}"
            if description:
                text += f": {description}"
            if prop_desc:
                text += f" (properties: {prop_desc})"
            entity_lines.append(text)

        relation_lines = []
        for rel in rels:
            r_type = str(rel.get("type", "")).strip() or "RELATED_TO"
            source = str(rel.get("source", "")).strip() or "Entity"
            target = str(rel.get("target", "")).strip() or "Entity"
            desc = str(rel.get("description", "")).strip()
            rel_line = f"- {r_type}: {source} -> {target}"
            if desc:
                rel_line += f" ({desc})"
            relation_lines.append(rel_line)

        shacl_lines = []
        for shape in shapes:
            target_class = str(shape.get("target_class", "")).strip()
            for prop in shape.get("properties", []):
                path = str(prop.get("path", "")).strip()
                constraint = str(prop.get("constraint", "")).strip()
                if target_class and path and constraint:
                    shacl_lines.append(f"- {target_class}.{path}: {constraint}")

        return {
            "ontology_name": str(ontology_payload.get("ontology_name", "runtime_candidate")),
            "entity_types": "\n".join(entity_lines),
            "relationship_types": "\n".join(relation_lines),
            "shacl_constraints": "\n".join(shacl_lines),
        }

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
                    }
                )
            norm_classes.append(
                {
                    "name": name,
                    "description": str(cls.get("description", "")).strip(),
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
