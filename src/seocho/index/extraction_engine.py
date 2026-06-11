"""Shared extraction and linking engine for canonical graph construction.

This module centralizes prompt rendering, LLM calls, and payload normalization
so both the public SDK indexing path and extraction-side compatibility paths can
reuse the same graph-construction contract.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Optional

from jinja2 import Template

from seocho.query.strategy import ExtractionStrategy, LinkingStrategy
from seocho.store.llm import complete_with_task_hints

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ONTOLOGY_RELAXED_RETRY_GUIDANCE = (
    "Previous ontology-guided extraction returned an empty graph. "
    "Retry once in relaxed mode. Prefer ontology labels and relationships when "
    "they are clearly grounded in the text, but do not return an empty graph "
    "just because the text does not fit the ontology perfectly. If needed, use "
    "the closest supported label or a generic Entity node. When the text names "
    "concrete entities, emit at least one useful node."
)


# seocho-snt: appended verbatim in strict mode. MUST stay constant and
# ontology-independent — the extraction firewall (FinDER r=-0.76) forbids
# ontology-derived richness from entering the extraction prompt surface,
# and a constant line keeps the KV-cache prefix stable per mode.
_CLOSED_VOCABULARY_PROMPT_LINE = (
    "Closed vocabulary: use only the provided node labels and relationship "
    "types, spelled exactly as given. If an entity or relationship does not "
    "fit any provided type, omit it entirely — never invent labels, "
    "relationship types, or generic placeholders."
)


class CanonicalExtractionEngine:
    """Shared extraction/linking engine for ontology-first graph construction."""

    def __init__(
        self,
        *,
        ontology: Any | None,
        llm: Any,
        extraction_prompt: Optional[Any] = None,
        custom_prompts: Optional[Dict[str, str]] = None,
        linking_prompt: Optional[str] = None,
        enforcement: Any = None,
    ) -> None:
        from .enforcement import resolve_enforcement

        self.ontology = ontology
        self.llm = llm
        self.custom_prompts = dict(custom_prompts or {})
        self.linking_prompt = linking_prompt
        self.enforcement = resolve_enforcement(enforcement)
        self._extraction = (
            ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)
            if ontology is not None
            else None
        )
        self._linking = LinkingStrategy(ontology) if ontology is not None else None

    def extract(
        self,
        text: str,
        *,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run one extraction pass and normalize the returned graph payload."""

        system, user = self._render_extraction_prompts(
            text=text,
            category=category,
            metadata=metadata,
            extra_context=extra_context,
        )
        if self.enforcement.closed_vocab_prompt_line:
            system = f"{system}\n\n{_CLOSED_VOCABULARY_PROMPT_LINE}"
        response = complete_with_task_hints(
            self.llm,
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="json_extraction",
        )
        normalized = self.normalize_payload(response.json())
        if not self._should_retry_relaxed_extraction(normalized, extra_context):
            return normalized

        retry_metadata = {
            "attempted": True,
            "mode": "ontology_relaxed",
            "succeeded": False,
        }
        retry_system, retry_user = self._build_relaxed_retry_prompts(
            system=system,
            user=user,
            extra_context=extra_context,
        )
        try:
            retry_response = complete_with_task_hints(
                self.llm,
                system=retry_system,
                user=retry_user,
                temperature=0.15,
                response_format={"type": "json_object"},
                reasoning_mode=False,
                task_hint="json_extraction_retry",
            )
            retried = self.normalize_payload(retry_response.json())
            if retried.get("nodes") or retried.get("relationships"):
                retry_metadata["succeeded"] = True
                retried["_retry"] = retry_metadata
                return retried
        except Exception as exc:
            retry_metadata["error"] = type(exc).__name__

        normalized["_retry"] = retry_metadata
        return normalized

    def link(
        self,
        extracted: Dict[str, Any],
        *,
        category: str = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run entity linking and preserve the graph-write contract."""

        nodes = extracted.get("nodes", []) or []
        if not nodes:
            return extracted

        entities_json = json.dumps(
            {
                "nodes": nodes,
                "relationships": extracted.get("relationships", []) or [],
            },
            default=str,
        )
        system, user = self._render_linking_prompts(
            entities_json=entities_json,
            category=category,
            extra_context=extra_context,
        )
        response = complete_with_task_hints(
            self.llm,
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="entity_linking",
        )
        linked = self.normalize_payload(response.json())
        if "relationships" not in linked or not linked["relationships"]:
            linked["relationships"] = extracted.get("relationships", []) or []
        return linked

    def _should_retry_relaxed_extraction(
        self,
        normalized: Dict[str, Any],
        extra_context: Optional[Dict[str, Any]],
    ) -> bool:
        # seocho-snt: the relaxed retry explicitly invites near-miss labels
        # and generic Entity nodes — out-of-vocabulary by definition, so
        # strict mode never takes it. An empty graph is the correct strict
        # outcome for text the ontology does not cover.
        if not self.enforcement.allow_relaxed_retry:
            return False
        if normalized.get("nodes") or normalized.get("relationships"):
            return False
        if self.ontology is not None:
            return True
        return self._has_relaxable_context(extra_context)

    @staticmethod
    def _has_relaxable_context(extra_context: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(extra_context, dict):
            return False
        for key in (
            "ontology_name",
            "entity_types",
            "relationship_types",
            "shacl_constraints",
            "vocabulary_terms",
            "entity_guidance",
            "developer_instructions",
        ):
            value = extra_context.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _build_relaxed_retry_prompts(
        self,
        *,
        system: str,
        user: str,
        extra_context: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        retry_system = f"{system}\n\n{_ONTOLOGY_RELAXED_RETRY_GUIDANCE}"
        retry_user = user
        hint_block = self._render_relaxation_hints(extra_context)
        if hint_block:
            retry_user = f"{retry_user}\n\nRelaxed retry hints:\n{hint_block}"
        return retry_system, retry_user

    @staticmethod
    def _render_relaxation_hints(extra_context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(extra_context, dict):
            return ""
        lines = []
        for label, key in (
            ("Ontology", "ontology_name"),
            ("Entity hints", "entity_types"),
            ("Relationship hints", "relationship_types"),
            ("SHACL hints", "shacl_constraints"),
            ("Vocabulary hints", "vocabulary_terms"),
            ("Known entities", "entity_guidance"),
            ("Developer instructions", "developer_instructions"),
        ):
            value = extra_context.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def normalize_payload(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize arbitrary LLM payloads into the graph write contract."""

        raw_nodes = list(extracted.get("nodes", []) or [])
        raw_relationships = list(extracted.get("relationships", []) or [])
        raw_triples = list(extracted.get("triples", []) or [])

        nodes = []
        node_lookup: Dict[str, str] = {}
        for index, raw_node in enumerate(raw_nodes):
            normalized = self._normalize_node(raw_node, index)
            if not normalized:
                continue
            nodes.append(normalized)
            props = normalized.get("properties", {})
            for key in (
                str(normalized.get("id", "")),
                str(props.get("name", "")),
                str(props.get("uri", "")),
            ):
                if key:
                    node_lookup[key] = str(normalized["id"])

        relationships = []
        for raw_rel in (raw_relationships or raw_triples):
            normalized = self._normalize_relationship(raw_rel, node_lookup)
            if normalized:
                relationships.append(normalized)

        return {"nodes": nodes, "relationships": relationships}

    def _render_extraction_prompts(
        self,
        *,
        text: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
        extra_context: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        if self.custom_prompts:
            context = self._prompt_context(
                text=text,
                category=category,
                metadata=metadata,
                extra_context=extra_context,
            )
            system_template = self.custom_prompts.get("system", "")
            user_template = self.custom_prompts.get("user", "{{text}}")
            return (
                Template(system_template).render(**context),
                Template(user_template).render(**context),
            )

        if self._extraction is not None:
            self._extraction.category = category
            return self._extraction.render(text, metadata=metadata)

        system = (
            "You are an expert entity extraction system.\n"
            'Return JSON with "nodes" and "relationships" keys.\n'
            'Nodes: {"id": "unique_id", "label": "EntityType", "properties": {...}}\n'
            'Relationships: {"source": "source_id", "target": "target_id", "type": "TYPE", "properties": {...}}'
        )
        return system, f"Text to extract:\n{text}"

    def _render_linking_prompts(
        self,
        *,
        entities_json: str,
        category: str,
        extra_context: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        if self.linking_prompt:
            context = {
                "category": category,
                "entities": entities_json,
                "entities_json": entities_json,
            }
            if isinstance(extra_context, dict):
                context.update(extra_context)
            return (
                "You are an entity linking assistant.",
                Template(self.linking_prompt).render(**context),
            )

        if self._linking is not None:
            self._linking.category = category
            return self._linking.render(entities_json)

        system = (
            "You are an entity linking assistant. Identify duplicates and return "
            "JSON with the same nodes and relationships structure."
        )
        return system, f"Input Entities:\n{entities_json}"

    def _prompt_context(
        self,
        *,
        text: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
        extra_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "text": text,
            "category": category,
            "metadata": metadata or {},
        }
        if self.ontology is not None:
            context.update(self.ontology.to_extraction_context())
        else:
            context.setdefault("ontology_name", "")
            context.setdefault("entity_types", "")
            context.setdefault("relationship_types", "")
            context.setdefault("constraints_summary", "")

        # T2.3 — hierarchical-extraction guidance. When the ontology exposes
        # 10+ classes (e.g. FIBO be+sec+ind+dbt = 11), Kimi's tendency to
        # fall back to a generic ``Entity`` label dominates. Surface the
        # explicit "prefer most-specific subclass" rule alongside the
        # entity_types listing so the rule travels with every extraction
        # prompt that consumes ``{{ontology_guidance}}`` (templates that
        # don't reference it stay unchanged).
        guidance = self._build_ontology_guidance()
        context.setdefault("ontology_guidance", guidance)

        if isinstance(extra_context, dict):
            context.update(extra_context)
        return context

    def _build_ontology_guidance(self) -> str:
        """Class-count-aware hierarchy hint for the extraction prompt."""
        if self.ontology is None:
            return ""
        node_count = len(getattr(self.ontology, "nodes", {}) or {})
        base = (
            "Label selection rules:\n"
            "  1. Use the most-specific class that matches the entity. "
            "If both an abstract base (e.g. FinancialMetric) and a concrete "
            "subclass (Revenue, OperatingIncome, NetIncome, EPS, GrossProfit, "
            "OperatingMargin) are listed, pick the subclass.\n"
            "  2. Only fall back to a generic label when no domain-specific "
            "class clearly applies. Never default to 'Entity' when the "
            "ontology offers a domain label that matches."
        )
        if node_count >= 10:
            base += (
                "\n  3. (This ontology has many classes — read the entity_types "
                "list carefully before defaulting to abstract labels.)"
            )
        return base

    def _normalize_node(self, raw_node: Any, index: int) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_node, dict):
            return None

        if isinstance(raw_node.get("properties"), dict):
            props = dict(raw_node.get("properties", {}))
        else:
            props = {
                key: value
                for key, value in raw_node.items()
                if key
                not in {
                    "id",
                    "label",
                    "properties",
                    "from",
                    "to",
                    "source",
                    "target",
                    "type",
                    "predicate",
                }
            }

        label = str(raw_node.get("label") or "").strip() or self._infer_node_label(props)
        if not label:
            return None

        raw_id = raw_node.get("id", "")
        is_sequential_id = str(raw_id).strip().isdigit()
        if is_sequential_id and props.get("name"):
            node_id = props["name"]
        else:
            node_id = (
                raw_id
                or props.get("id")
                or props.get("uri")
                or props.get("name")
                or f"{label}_{index + 1}"
            )

        normalized_id = self._normalize_node_id(str(node_id), label)
        clean_props = {
            key: value
            for key, value in props.items()
            if value not in (None, "") and key != "id"
        }
        if "name" not in clean_props and raw_node.get("name"):
            clean_props["name"] = raw_node["name"]

        return {"id": normalized_id, "label": label, "properties": clean_props}

    def _normalize_relationship(
        self,
        raw_rel: Any,
        node_lookup: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_rel, dict):
            return None

        raw_source = str(
            raw_rel.get("source")
            or raw_rel.get("from")
            or raw_rel.get("subject")
            or ""
        ).strip()
        raw_target = str(
            raw_rel.get("target")
            or raw_rel.get("to")
            or raw_rel.get("object")
            or ""
        ).strip()
        raw_type = str(
            raw_rel.get("type")
            or raw_rel.get("predicate")
            or raw_rel.get("relationship")
            or ""
        ).strip()
        if not raw_source or not raw_target or not raw_type:
            return None

        rel_type = self._normalize_relationship_type(raw_type)
        if not rel_type:
            return None

        source_id = node_lookup.get(raw_source, raw_source)
        target_id = node_lookup.get(raw_target, raw_target)
        if not source_id or not target_id:
            return None

        properties = {}
        nested_properties = raw_rel.get("properties")
        if isinstance(nested_properties, dict):
            properties.update(
                {
                    key: value
                    for key, value in nested_properties.items()
                    if value not in (None, "")
                }
            )

        properties.update(
            {
                key: value
                for key, value in raw_rel.items()
                if key
                not in {
                    "source",
                    "target",
                    "from",
                    "to",
                    "subject",
                    "object",
                    "type",
                    "predicate",
                    "relationship",
                    "properties",
                }
                and value not in (None, "")
            }
        )
        return {
            "source": source_id,
            "target": target_id,
            "type": rel_type,
            "properties": properties,
        }

    def _infer_node_label(self, props: Dict[str, Any]) -> str:
        if self.ontology is None:
            return ""

        prop_keys = {key for key, value in props.items() if value not in (None, "")}
        best_label = ""
        best_score = -1
        for label, node_def in self.ontology.nodes.items():
            schema_keys = set(node_def.properties.keys())
            score = len(prop_keys & schema_keys)
            if "name" in prop_keys and "name" in schema_keys:
                score += 1
            if score > best_score:
                best_label = label
                best_score = score
        return best_label

    def _normalize_node_id(self, raw_id: str, label: str) -> str:
        if "://" in raw_id or raw_id.startswith("urn:"):
            return raw_id
        slug = _SLUG_RE.sub("_", raw_id.lower()).strip("_")
        return slug or f"{label.lower()}_{uuid.uuid4().hex[:8]}"

    def _normalize_relationship_type(self, raw_type: str) -> str:
        if raw_type == "rdf:type":
            return ""

        direct = str(raw_type).strip()
        if self.ontology is None:
            return direct

        if direct in self.ontology.relationships:
            return direct

        direct_lower = direct.lower()
        tail = direct_lower.split(":")[-1].split("/")[-1].split("__")[-1]
        for rel_name, rel_def in self.ontology.relationships.items():
            candidates = {rel_name.lower(), *(alias.lower() for alias in rel_def.aliases)}
            if rel_def.same_as:
                same_as = rel_def.same_as.lower()
                candidates.add(same_as)
                candidates.add(same_as.split(":")[-1].split("/")[-1])
            if direct_lower in candidates or tail in candidates:
                return rel_name
        return direct
