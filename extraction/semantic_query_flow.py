"""
Semantic query flow for graph QA.

Implements a deterministic 4-agent orchestration:
1) Router agent
2) LPG agent
3) RDF agent
4) Answer generation agent

The semantic layer resolves question entities using fulltext search first,
then falls back to contains-based lookup, and applies lightweight
dedup/disambiguation scoring.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha1
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from config import graph_registry
from ontology_hints import OntologyHintStore
from semantic_artifact_store import (
    DEFAULT_SEMANTIC_ARTIFACT_DIR,
    get_semantic_artifact,
    list_semantic_artifacts,
)
from semantic_context import (
    _merge_ontology_candidates,
    _merge_shacl_candidates,
    _merge_vocabulary_candidates,
)
from semantic_vocabulary import ManagedVocabularyResolver

logger = logging.getLogger(__name__)


RDF_HINTS = {
    "rdf",
    "rdfs",
    "owl",
    "shacl",
    "sparql",
    "triple",
    "ontology",
    "uri",
    "class",
    "instance",
}

LPG_HINTS = {
    "cypher",
    "node",
    "edge",
    "path",
    "neighbor",
    "graph",
    "community",
    "relationship",
}

STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "do",
    "does",
    "did",
    "what",
    "which",
    "who",
    "whom",
    "where",
    "when",
    "why",
    "how",
    "tell",
    "show",
    "about",
    "please",
}

ENTITY_PROPERTIES = ("name", "title", "id", "uri", "code", "symbol", "alias", "content_preview", "content", "memory_id")

COMMON_ALLOWED_PROPERTIES = {
    *ENTITY_PROPERTIES,
    "workspace_id",
    "status",
    "description",
    "summary",
    "type",
    "value",
    "created_at",
    "updated_at",
}

FORBIDDEN_CYPHER_TOKENS = (
    " CREATE ",
    " MERGE ",
    " DELETE ",
    " DETACH ",
    " SET ",
    " REMOVE ",
    " DROP ",
    " LOAD CSV ",
    " CALL DBMS",
    " CALL GDS",
)

QUESTION_LABEL_HINTS = {
    "company": {"company", "organization", "org", "enterprise", "firm"},
    "person": {"person", "human", "individual", "employee", "ceo", "founder"},
    "product": {"product", "service", "offering"},
    "event": {"event", "incident", "meeting"},
    "document": {"document", "section", "chunk"},
    "ontology": {"ontology", "class", "property", "concept"},
}


@dataclass(frozen=True)
class IntentSpec:
    intent_id: str
    required_relations: Tuple[str, ...]
    required_entity_types: Tuple[str, ...]
    focus_slots: Tuple[str, ...]
    trigger_keywords: Tuple[str, ...]


INTENT_CATALOG: Tuple[IntentSpec, ...] = (
    IntentSpec(
        intent_id="relationship_lookup",
        required_relations=("RELATES_TO", "USES", "OWNS", "WORKS_WITH"),
        required_entity_types=("Entity",),
        focus_slots=("source_entity", "target_entity", "relation_paths"),
        trigger_keywords=("relation", "relationship", "related", "connected", "connection", "link", "between"),
    ),
    IntentSpec(
        intent_id="responsibility_lookup",
        required_relations=("MANAGES", "OWNS", "LEADS", "OPERATES"),
        required_entity_types=("Person", "Organization"),
        focus_slots=("owner_or_operator", "target_entity", "supporting_fact"),
        trigger_keywords=("who manages", "manages", "owner", "owns", "owned", "leads", "lead", "responsible", "operates"),
    ),
    IntentSpec(
        intent_id="explanation_lookup",
        required_relations=(),
        required_entity_types=("Entity",),
        focus_slots=("target_entity", "supporting_fact"),
        trigger_keywords=("why", "how", "explain"),
    ),
    IntentSpec(
        intent_id="entity_summary",
        required_relations=(),
        required_entity_types=("Entity",),
        focus_slots=("target_entity", "supporting_fact"),
        trigger_keywords=(),
    ),
)


def infer_question_intent(question: str, entities: Sequence[str]) -> Dict[str, Any]:
    normalized = question.lower()
    best_spec = INTENT_CATALOG[-1]
    best_score = 0
    matched_keywords: List[str] = []

    for spec in INTENT_CATALOG:
        keywords = [keyword for keyword in spec.trigger_keywords if keyword and keyword in normalized]
        score = len(keywords)
        if score > best_score:
            best_spec = spec
            best_score = score
            matched_keywords = keywords

    return {
        "intent_id": best_spec.intent_id,
        "required_relations": list(best_spec.required_relations),
        "required_entity_types": list(best_spec.required_entity_types),
        "focus_slots": list(best_spec.focus_slots),
        "matched_keywords": matched_keywords,
        "candidate_entity_count": len([entity for entity in entities if str(entity).strip()]),
    }


def build_evidence_bundle(
    *,
    question: str,
    semantic_context: Dict[str, Any],
    memory: Optional[Dict[str, Any]] = None,
    matched_entities: Optional[Sequence[str]] = None,
    reasons: Optional[Sequence[str]] = None,
    score: Optional[float] = None,
    support_assessment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = semantic_context.get("intent")
    if not isinstance(intent, dict) or not intent.get("intent_id"):
        intent = infer_question_intent(question, semantic_context.get("entities", []))

    matched_entity_names = [
        str(entity).strip()
        for entity in (matched_entities or [])
        if str(entity).strip()
    ]
    matched_entity_set = {entity.lower() for entity in matched_entity_names}

    candidate_entities: List[Dict[str, Any]] = []
    for question_entity, candidates in semantic_context.get("matches", {}).items():
        if not candidates:
            continue
        best = candidates[0]
        display_name = str(best.get("display_name") or question_entity).strip()
        if matched_entity_set and question_entity.lower() not in matched_entity_set and display_name.lower() not in matched_entity_set:
            continue
        candidate_entities.append(
            {
                "question_entity": question_entity,
                "display_name": display_name,
                "database": str(best.get("database", "")).strip(),
                "node_id": str(best.get("node_id", "")).strip(),
                "labels": list(best.get("labels", [])) if isinstance(best.get("labels"), list) else [],
                "source": str(best.get("source", "")).strip(),
                "confidence": float(best.get("final_score", 0.0) or 0.0),
            }
        )

    memory_payload = memory if isinstance(memory, dict) else {}
    memory_entities = memory_payload.get("entities", []) if isinstance(memory_payload.get("entities"), list) else []
    prioritized_memory_entities = sorted(
        memory_entities,
        key=lambda entity: 0 if _entity_name(entity).lower() in matched_entity_set else 1,
    )

    selected_triples: List[Dict[str, Any]] = []
    for entity in prioritized_memory_entities[:5]:
        entity_name = _entity_name(entity)
        if not entity_name:
            continue
        selected_triples.append(
            {
                "source": str(memory_payload.get("memory_id", "")).strip(),
                "relation": "MENTIONS",
                "target": entity_name,
                "target_labels": list(entity.get("labels", [])) if isinstance(entity.get("labels"), list) else [],
            }
        )

    slot_fills: Dict[str, Any] = {}
    focus_slots = [str(slot).strip() for slot in intent.get("focus_slots", []) if str(slot).strip()]
    if matched_entity_names:
        slot_fills["target_entity"] = matched_entity_names[0]
    elif candidate_entities:
        slot_fills["target_entity"] = candidate_entities[0]["display_name"]

    if len(matched_entity_names) > 1:
        slot_fills["source_entity"] = matched_entity_names[0]
        slot_fills["target_entity"] = matched_entity_names[1]

    if "relation_paths" in focus_slots and selected_triples:
        slot_fills["relation_paths"] = [triple["relation"] for triple in selected_triples]

    labeled_owner = _first_entity_with_labels(prioritized_memory_entities, {"person", "organization", "company"})
    if labeled_owner and "owner_or_operator" in focus_slots:
        slot_fills["owner_or_operator"] = labeled_owner

    preview = str(memory_payload.get("content_preview") or memory_payload.get("content") or "").strip()
    if preview and "supporting_fact" in focus_slots:
        slot_fills["supporting_fact"] = preview

    missing_slots = [slot for slot in focus_slots if slot not in slot_fills]
    grounded_slots = [slot for slot in focus_slots if slot in slot_fills]
    coverage = round(
        len(grounded_slots) / max(1, len(focus_slots)),
        4,
    ) if focus_slots else 1.0

    confidence = 0.0
    if score is not None:
        confidence = float(score or 0.0)
    elif candidate_entities:
        confidence = max(float(entity.get("confidence", 0.0) or 0.0) for entity in candidate_entities)

    provenance: List[Dict[str, Any]] = []
    if memory_payload:
        provenance.append(
            {
                "memory_id": str(memory_payload.get("memory_id", "")).strip(),
                "database": str(memory_payload.get("database", "")).strip(),
                "content_preview": preview,
                "reasons": [str(reason).strip() for reason in (reasons or []) if str(reason).strip()],
            }
        )
    else:
        for entity in candidate_entities[:3]:
            provenance.append(
                {
                    "database": entity["database"],
                    "node_id": entity["node_id"],
                    "display_name": entity["display_name"],
                    "source": entity["source"],
                }
            )

    return {
        "schema_version": "evidence_bundle.v2",
        "intent_id": str(intent.get("intent_id", "")).strip(),
        "required_relations": list(intent.get("required_relations", [])),
        "required_entity_types": list(intent.get("required_entity_types", [])),
        "focus_slots": focus_slots,
        "candidate_entities": candidate_entities,
        "selected_triples": selected_triples,
        "slot_fills": slot_fills,
        "grounded_slots": grounded_slots,
        "missing_slots": missing_slots,
        "provenance": provenance,
        "confidence": round(confidence, 4),
        "coverage": coverage,
        "support_assessment": dict(support_assessment or {}),
    }


def _entity_name(payload: Dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("display_name") or "").strip()


def _first_entity_with_labels(entities: Sequence[Dict[str, Any]], normalized_targets: Set[str]) -> str:
    for entity in entities:
        labels = entity.get("labels", [])
        if not isinstance(labels, list):
            continue
        normalized_labels = {
            re.sub(r"[^a-z0-9]+", "", str(label).lower())
            for label in labels
        }
        if normalized_labels & {re.sub(r"[^a-z0-9]+", "", item.lower()) for item in normalized_targets}:
            entity_name = _entity_name(entity)
            if entity_name:
                return entity_name
    return ""


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _slugify_symbol(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "term"


def _parse_cypher_rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, str) and raw.startswith("Error"):
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


@dataclass(frozen=True)
class CypherPlan:
    database: str
    query: str
    params: Dict[str, Any]
    strategy: str
    anchor_entity: str
    anchor_label: str = ""
    relation_types: Tuple[str, ...] = ()


@dataclass(frozen=True)
class InsufficiencyAssessment:
    sufficient: bool
    reason: str
    missing_slots: Tuple[str, ...]
    row_count: int
    filled_slots: Tuple[str, ...] = ()


class SemanticConstraintSliceBuilder:
    """Build a lightweight semantic-layer slice for deterministic query generation."""

    def __init__(
        self,
        *,
        artifact_base_dir: Optional[str] = None,
        global_workspace_id: Optional[str] = None,
    ) -> None:
        self.artifact_base_dir = artifact_base_dir or os.getenv(
            "SEMANTIC_ARTIFACT_DIR",
            DEFAULT_SEMANTIC_ARTIFACT_DIR,
        )
        self.global_workspace_id = (
            global_workspace_id
            or os.getenv("VOCABULARY_GLOBAL_WORKSPACE_ID", "global").strip()
            or "global"
        )

    def build_for_databases(
        self,
        databases: Sequence[str],
        *,
        workspace_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        return {
            str(database): self.build_for_database(str(database), workspace_id=workspace_id)
            for database in databases
        }

    def build_for_database(self, database: str, *, workspace_id: str) -> Dict[str, Any]:
        graph_target = graph_registry.find_by_database(database)
        graph_id = graph_target.graph_id if graph_target is not None else database
        ontology_id = (
            str(graph_target.ontology_id).strip()
            if graph_target is not None and str(graph_target.ontology_id).strip()
            else database
        )
        vocabulary_profile = (
            str(graph_target.vocabulary_profile).strip()
            if graph_target is not None and str(graph_target.vocabulary_profile).strip()
            else "vocabulary.v2"
        )

        artifact_payloads = self._load_matching_artifacts(
            workspace_id=workspace_id,
            ontology_id=ontology_id,
            graph_id=graph_id,
            database=database,
        )
        ontology_candidate = _merge_ontology_candidates(
            [payload.get("ontology_candidate") for payload in artifact_payloads]
        )
        shacl_candidate = _merge_shacl_candidates(
            [payload.get("shacl_candidate") for payload in artifact_payloads]
        )
        vocabulary_candidate = _merge_vocabulary_candidates(
            [payload.get("vocabulary_candidate") for payload in artifact_payloads]
        )

        allowed_labels = sorted(
            {
                str(item.get("name", "")).strip()
                for item in ontology_candidate.get("classes", [])
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
        )
        allowed_relationship_types = sorted(
            {
                str(item.get("type", "")).strip()
                for item in ontology_candidate.get("relationships", [])
                if isinstance(item, dict) and str(item.get("type", "")).strip()
            }
        )
        allowed_properties = set(COMMON_ALLOWED_PROPERTIES)
        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            for prop in cls.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                prop_name = str(prop.get("name", "")).strip()
                if prop_name:
                    allowed_properties.add(prop_name)
        for shape in shacl_candidate.get("shapes", []):
            if not isinstance(shape, dict):
                continue
            for prop in shape.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("path", "")).strip()
                if path:
                    allowed_properties.add(path)

        return {
            "graph_id": graph_id,
            "database": database,
            "ontology_id": ontology_id,
            "vocabulary_profile": vocabulary_profile,
            "artifact_ids": [
                str(payload.get("artifact_id", "")).strip()
                for payload in artifact_payloads
                if str(payload.get("artifact_id", "")).strip()
            ],
            "ontology_candidate": ontology_candidate,
            "shacl_candidate": shacl_candidate,
            "vocabulary_candidate": vocabulary_candidate,
            "allowed_labels": allowed_labels,
            "allowed_relationship_types": allowed_relationship_types,
            "allowed_properties": sorted(allowed_properties),
            "relation_aliases": self._build_relation_aliases(ontology_candidate),
            "label_aliases": self._build_label_aliases(ontology_candidate, vocabulary_candidate),
            "json_ld_context": self._build_json_ld_context(
                ontology_id=ontology_id,
                ontology_candidate=ontology_candidate,
                vocabulary_candidate=vocabulary_candidate,
            ),
            "constraint_strength": (
                "semantic_layer"
                if artifact_payloads
                else "graph_metadata_only"
            ),
        }

    def _load_matching_artifacts(
        self,
        *,
        workspace_id: str,
        ontology_id: str,
        graph_id: str,
        database: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for current_workspace in {self.global_workspace_id, workspace_id}:
            approved_rows = list_semantic_artifacts(
                workspace_id=current_workspace,
                status="approved",
                base_dir=self.artifact_base_dir,
            )
            for row in approved_rows:
                artifact_id = str(row.get("artifact_id", "")).strip()
                if not artifact_id:
                    continue
                try:
                    payload = get_semantic_artifact(
                        workspace_id=current_workspace,
                        artifact_id=artifact_id,
                        base_dir=self.artifact_base_dir,
                    )
                except FileNotFoundError:
                    continue
                if not self._artifact_matches(
                    payload,
                    ontology_id=ontology_id,
                    graph_id=graph_id,
                    database=database,
                ):
                    continue
                candidates.append(payload)
        candidates.sort(
            key=lambda payload: (
                str(payload.get("approved_at") or ""),
                str(payload.get("created_at") or ""),
                str(payload.get("artifact_id") or ""),
            )
        )
        return candidates

    @staticmethod
    def _artifact_matches(
        payload: Dict[str, Any],
        *,
        ontology_id: str,
        graph_id: str,
        database: str,
    ) -> bool:
        ontology_candidate = payload.get("ontology_candidate", {})
        ontology_name = str(ontology_candidate.get("ontology_name", "")).strip()
        artifact_name = str(payload.get("name", "")).strip()

        normalized_targets = {
            _normalize_symbol(ontology_id),
            _normalize_symbol(graph_id),
            _normalize_symbol(database),
        }
        normalized_targets.discard("")
        normalized_candidates = {
            _normalize_symbol(ontology_name),
            _normalize_symbol(artifact_name),
        }
        normalized_candidates.discard("")
        if not normalized_targets:
            return True
        if normalized_targets & normalized_candidates:
            return True
        return False

    @staticmethod
    def _build_relation_aliases(ontology_candidate: Dict[str, Any]) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        for rel in ontology_candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            relation_type = str(rel.get("type", "")).strip()
            if not relation_type:
                continue
            for candidate in [relation_type, *rel.get("aliases", []), *rel.get("related", [])]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = relation_type
        return aliases

    @staticmethod
    def _build_label_aliases(
        ontology_candidate: Dict[str, Any],
        vocabulary_candidate: Dict[str, Any],
    ) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            canonical = str(cls.get("name", "")).strip()
            if not canonical:
                continue
            for candidate in [canonical, *cls.get("aliases", []), *cls.get("related", [])]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = canonical

        for term in vocabulary_candidate.get("terms", []):
            if not isinstance(term, dict):
                continue
            canonical = str(
                term.get("canonical")
                or term.get("pref_label")
                or term.get("name")
                or ""
            ).strip()
            if not canonical:
                continue
            for candidate in [
                canonical,
                *term.get("aliases", []),
                *term.get("alt_labels", []),
                *term.get("hidden_labels", []),
            ]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = canonical
        return aliases

    @staticmethod
    def _build_json_ld_context(
        *,
        ontology_id: str,
        ontology_candidate: Dict[str, Any],
        vocabulary_candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = f"seocho://semantic/{_slugify_symbol(ontology_id)}/"
        context: Dict[str, Any] = {"@vocab": base}

        def register(term: str, iri: Optional[str] = None) -> None:
            term_text = str(term).strip()
            if not term_text:
                return
            context[term_text] = iri or f"{base}{_slugify_symbol(term_text)}"

        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            register(cls.get("name", ""))
            for alias in cls.get("aliases", []):
                register(alias)
        for rel in ontology_candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            register(rel.get("type", ""))
            for alias in rel.get("aliases", []):
                register(alias)
        for term in vocabulary_candidate.get("terms", []):
            if not isinstance(term, dict):
                continue
            iri = str(term.get("uri") or term.get("id") or "").strip() or None
            register(term.get("pref_label") or term.get("canonical") or term.get("name"), iri)
            for alias in [*term.get("alt_labels", []), *term.get("hidden_labels", []), *term.get("aliases", [])]:
                register(alias, iri)
        return context


class CypherQueryValidator:
    """Validate constrained Cypher plans before execution."""

    def validate(self, plan: CypherPlan, constraint_slice: Dict[str, Any]) -> Dict[str, Any]:
        normalized_query = " " + re.sub(r"\s+", " ", plan.query.upper()) + " "
        violations: List[str] = []
        if "$node_id" not in plan.query:
            violations.append("missing_node_binding")
        if "RETURN" not in normalized_query:
            violations.append("missing_return_clause")
        for token in FORBIDDEN_CYPHER_TOKENS:
            if token in normalized_query:
                violations.append(f"forbidden_token:{token.strip().lower().replace(' ', '_')}")

        labels = {
            match
            for match in re.findall(r"\([^)]+:([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }
        relation_types = {
            match
            for match in re.findall(r"\[[^\]]*:\s*([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }
        properties = {
            match
            for match in re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)", plan.query)
            if match
        }

        allowed_labels = set(constraint_slice.get("allowed_labels", []))
        if allowed_labels and labels - allowed_labels:
            violations.append(
                "unknown_labels:" + ",".join(sorted(labels - allowed_labels))
            )

        allowed_relationship_types = set(constraint_slice.get("allowed_relationship_types", []))
        if allowed_relationship_types and relation_types - allowed_relationship_types:
            violations.append(
                "unknown_relationship_types:" + ",".join(sorted(relation_types - allowed_relationship_types))
            )

        allowed_properties = set(constraint_slice.get("allowed_properties", []))
        if allowed_properties and properties - allowed_properties:
            violations.append(
                "unknown_properties:" + ",".join(sorted(properties - allowed_properties))
            )

        return {
            "ok": not violations,
            "violations": violations,
            "labels": sorted(labels),
            "relation_types": sorted(relation_types),
            "properties": sorted(properties),
        }


class QueryInsufficiencyClassifier:
    """Classify whether executed graph retrieval filled the requested slots."""

    def assess(self, intent: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> InsufficiencyAssessment:
        focus_slots = [
            str(slot).strip()
            for slot in intent.get("focus_slots", [])
            if str(slot).strip()
        ]
        row_count = len(rows)
        if row_count == 0:
            return InsufficiencyAssessment(
                sufficient=False,
                reason="empty_result",
                missing_slots=tuple(focus_slots),
                row_count=0,
            )

        filled_slots: Set[str] = set()
        intent_id = str(intent.get("intent_id", "")).strip()
        for row in rows:
            if row.get("source_entity"):
                filled_slots.add("source_entity")
            if row.get("target_entity"):
                filled_slots.add("target_entity")
            if row.get("relation_type") or row.get("relation_paths"):
                filled_slots.add("relation_paths")
            if row.get("owner_or_operator"):
                filled_slots.add("owner_or_operator")
            if row.get("supporting_fact") or row.get("properties") or row.get("neighbors"):
                filled_slots.add("supporting_fact")

        if intent_id == "relationship_lookup":
            if not any(row.get("relation_type") for row in rows):
                return InsufficiencyAssessment(
                    sufficient=False,
                    reason="missing_relation_path",
                    missing_slots=tuple(slot for slot in focus_slots if slot not in filled_slots or slot == "relation_paths"),
                    row_count=row_count,
                    filled_slots=tuple(sorted(filled_slots)),
                )
        if intent_id == "responsibility_lookup":
            if not any(row.get("owner_or_operator") for row in rows):
                return InsufficiencyAssessment(
                    sufficient=False,
                    reason="missing_owner_or_operator",
                    missing_slots=tuple(slot for slot in focus_slots if slot not in filled_slots or slot == "owner_or_operator"),
                    row_count=row_count,
                    filled_slots=tuple(sorted(filled_slots)),
                )

        missing_slots = tuple(slot for slot in focus_slots if slot not in filled_slots)
        if missing_slots:
            return InsufficiencyAssessment(
                sufficient=False,
                reason="partial_slot_fill",
                missing_slots=missing_slots,
                row_count=row_count,
                filled_slots=tuple(sorted(filled_slots)),
            )
        return InsufficiencyAssessment(
            sufficient=True,
            reason="sufficient",
            missing_slots=(),
            row_count=row_count,
            filled_slots=tuple(sorted(filled_slots)),
        )


class IntentSupportValidator:
    """Estimate whether a graph target can likely satisfy the requested intent."""

    def assess_candidate(
        self,
        *,
        question_entity: str,
        candidate: Dict[str, Any],
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        preview_bundle: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        required_relations = [
            str(item).strip()
            for item in intent.get("required_relations", [])
            if str(item).strip()
        ]
        required_entity_types = [
            str(item).strip()
            for item in intent.get("required_entity_types", [])
            if str(item).strip()
        ]
        focus_slots = [
            str(item).strip()
            for item in intent.get("focus_slots", [])
            if str(item).strip()
        ]
        preview_bundle = preview_bundle if isinstance(preview_bundle, dict) else {}

        candidate_labels = [
            str(label).strip()
            for label in candidate.get("labels", [])
            if str(label).strip()
        ]
        allowed_relationship_types = [
            str(item).strip()
            for item in constraint_slice.get("allowed_relationship_types", [])
            if str(item).strip()
        ]

        matched_relations = self._matched_relations(required_relations, constraint_slice)
        non_generic_entity_types = [
            item for item in required_entity_types
            if _normalize_symbol(item) not in {"", "entity"}
        ]
        matched_entity_types = self._matched_entity_types(non_generic_entity_types, candidate_labels)

        grounded_slots = set()
        if question_entity or candidate.get("display_name"):
            grounded_slots.add("target_entity")
        if len(preview_bundle.get("candidate_entities", [])) > 1:
            grounded_slots.add("source_entity")

        relation_coverage = 1.0
        if required_relations:
            relation_coverage = len(matched_relations) / max(1, len(required_relations))

        entity_type_coverage = 1.0
        if non_generic_entity_types:
            entity_type_coverage = len(matched_entity_types) / max(1, len(non_generic_entity_types))

        slot_coverage = 1.0
        if focus_slots:
            slot_coverage = len(grounded_slots & set(focus_slots)) / max(1, len(focus_slots))

        if required_relations:
            coverage = (0.35 * slot_coverage) + (0.35 * relation_coverage) + (0.30 * entity_type_coverage)
        else:
            coverage = (0.70 * slot_coverage) + (0.30 * entity_type_coverage)
        coverage = round(min(1.0, coverage), 4)

        reason = "supported"
        status = "supported"
        if not candidate.get("node_id"):
            reason = "no_candidate_node"
            status = "unsupported"
        elif required_relations and not matched_relations and constraint_slice.get("constraint_strength") == "semantic_layer":
            reason = "missing_required_relation_support"
            status = "partial"
        elif non_generic_entity_types and not matched_entity_types:
            reason = "entity_type_mismatch"
            status = "partial"
        elif coverage < 0.45:
            reason = "low_support_coverage"
            status = "partial"

        supported = status == "supported"
        missing_slots = [slot for slot in focus_slots if slot not in grounded_slots]
        return {
            "schema_version": "intent_support.v1",
            "intent_id": str(intent.get("intent_id", "")).strip(),
            "question_entity": question_entity,
            "display_name": str(candidate.get("display_name") or question_entity).strip(),
            "graph_id": str(constraint_slice.get("graph_id", "")).strip(),
            "database": str(candidate.get("database") or constraint_slice.get("database") or "").strip(),
            "constraint_strength": str(constraint_slice.get("constraint_strength", "")).strip(),
            "supported": supported,
            "status": status,
            "reason": reason,
            "coverage": coverage,
            "confidence": round(float(candidate.get("final_score", 0.0) or 0.0), 4),
            "required_relations": required_relations,
            "matched_relations": matched_relations,
            "required_entity_types": required_entity_types,
            "matched_entity_types": matched_entity_types,
            "focus_slots": focus_slots,
            "grounded_slots": sorted(grounded_slots & set(focus_slots)),
            "missing_slots": missing_slots,
        }

    def finalize_runtime_support(
        self,
        *,
        preflight: Optional[Dict[str, Any]],
        intent: Dict[str, Any],
        bundle: Dict[str, Any],
        assessment: InsufficiencyAssessment,
        plan: CypherPlan,
        constraint_slice: Dict[str, Any],
    ) -> Dict[str, Any]:
        focus_slots = [
            str(item).strip()
            for item in intent.get("focus_slots", [])
            if str(item).strip()
        ]
        grounded_slots = {
            str(item).strip()
            for item in bundle.get("grounded_slots", [])
            if str(item).strip()
        }
        selected_triples = bundle.get("selected_triples", [])
        matched_relations = []
        for triple in selected_triples:
            if not isinstance(triple, dict):
                continue
            relation = str(triple.get("relation", "")).strip()
            if relation and relation not in matched_relations:
                matched_relations.append(relation)

        coverage = round(
            len(grounded_slots & set(focus_slots)) / max(1, len(focus_slots)),
            4,
        ) if focus_slots else 1.0

        support = dict(preflight or {})
        support.update(
            {
                "schema_version": "intent_support.v1",
                "intent_id": str(intent.get("intent_id", "")).strip(),
                "graph_id": str(constraint_slice.get("graph_id", "")).strip(),
                "database": plan.database,
                "supported": assessment.sufficient,
                "status": "supported" if assessment.sufficient else ("partial" if grounded_slots else "unsupported"),
                "reason": assessment.reason,
                "coverage": coverage,
                "matched_relations": matched_relations,
                "focus_slots": focus_slots,
                "grounded_slots": sorted(grounded_slots & set(focus_slots)),
                "missing_slots": list(assessment.missing_slots),
                "row_count": assessment.row_count,
                "selected_triple_count": len(selected_triples),
            }
        )
        return support

    @staticmethod
    def empty_assessment(intent: Dict[str, Any]) -> Dict[str, Any]:
        focus_slots = [
            str(item).strip()
            for item in intent.get("focus_slots", [])
            if str(item).strip()
        ]
        return {
            "schema_version": "intent_support.v1",
            "intent_id": str(intent.get("intent_id", "")).strip(),
            "supported": False,
            "status": "unsupported",
            "reason": "no_entity_match",
            "coverage": 0.0,
            "confidence": 0.0,
            "required_relations": [
                str(item).strip()
                for item in intent.get("required_relations", [])
                if str(item).strip()
            ],
            "matched_relations": [],
            "required_entity_types": [
                str(item).strip()
                for item in intent.get("required_entity_types", [])
                if str(item).strip()
            ],
            "matched_entity_types": [],
            "focus_slots": focus_slots,
            "grounded_slots": [],
            "missing_slots": focus_slots,
        }

    @staticmethod
    def _matched_relations(required_relations: Sequence[str], constraint_slice: Dict[str, Any]) -> List[str]:
        allowed_lookup = {
            _normalize_symbol(item): str(item).strip()
            for item in constraint_slice.get("allowed_relationship_types", [])
            if str(item).strip()
        }
        matched: List[str] = []
        for relation in required_relations:
            normalized = _normalize_symbol(relation)
            if normalized and normalized in allowed_lookup:
                matched.append(allowed_lookup[normalized])
        return matched

    @staticmethod
    def _matched_entity_types(required_entity_types: Sequence[str], candidate_labels: Sequence[str]) -> List[str]:
        label_lookup = {
            _normalize_symbol(item): str(item).strip()
            for item in candidate_labels
            if str(item).strip()
        }
        matched: List[str] = []
        for entity_type in required_entity_types:
            normalized = _normalize_symbol(entity_type)
            if normalized and normalized in label_lookup:
                matched.append(label_lookup[normalized])
        return matched


class ExecutionStrategyChooser:
    """Choose and summarize semantic execution strategy."""

    def choose_initial(
        self,
        *,
        route: str,
        reasoning_mode: bool,
        repair_budget: int,
        support_assessment: Dict[str, Any],
        graph_count: int,
    ) -> Dict[str, Any]:
        support_status = str(support_assessment.get("status", "unsupported")).strip() or "unsupported"
        if route == "rdf":
            initial_mode = "rdf"
            reason = "question matched RDF-oriented cues"
        elif reasoning_mode or repair_budget > 0:
            initial_mode = "semantic_repair"
            reason = "bounded repair was explicitly requested"
        elif support_status == "supported":
            initial_mode = "semantic_direct"
            reason = "intent support is available for the selected graph scope"
        elif graph_count > 1:
            initial_mode = "semantic_direct"
            reason = "starting with the cheapest grounded path before recommending advanced review"
        else:
            initial_mode = "semantic_direct"
            reason = "starting with a lightweight semantic pass"

        return {
            "schema_version": "strategy_decision.v1",
            "requested_mode": "semantic",
            "initial_mode": initial_mode,
            "executed_mode": initial_mode,
            "reasoning_mode_requested": bool(reasoning_mode),
            "repair_budget": max(0, int(repair_budget or 0)),
            "support_status": support_status,
            "reason": reason,
            "advanced_debate_recommended": False,
            "self_reflection_used": False,
            "next_mode_hint": None,
            "sdk_hint": None,
        }

    def finalize(
        self,
        *,
        initial_decision: Dict[str, Any],
        route: str,
        graph_count: int,
        support_assessment: Dict[str, Any],
        reasoning: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        decision = dict(initial_decision or {})
        reasoning = reasoning if isinstance(reasoning, dict) else {}
        support_status = str(support_assessment.get("status", "unsupported")).strip() or "unsupported"
        self_reflection_used = bool(reasoning.get("self_reflection_used", False))

        if route == "rdf":
            executed_mode = "rdf"
        elif route == "hybrid":
            executed_mode = "hybrid"
        elif self_reflection_used:
            executed_mode = "semantic_self_reflect"
        elif reasoning.get("requested"):
            executed_mode = "semantic_repair"
        else:
            executed_mode = "semantic_direct"

        next_mode_hint = None
        sdk_hint = None
        advanced_debate_recommended = False
        if not support_assessment.get("supported", False):
            if graph_count > 1:
                advanced_debate_recommended = True
                next_mode_hint = "advanced"
                sdk_hint = "Use client.plan(...).advanced().run() for an explicit cross-graph debate."
            elif not reasoning.get("requested"):
                next_mode_hint = "reasoning_mode"
                sdk_hint = "Use client.plan(...).with_repair_budget(2).run() to allow bounded repair."
            else:
                next_mode_hint = "entity_override_or_semantic_artifact"
                sdk_hint = "Add entity overrides or improve approved semantic artifacts for this graph."

        decision.update(
            {
                "executed_mode": executed_mode,
                "support_status": support_status,
                "advanced_debate_recommended": advanced_debate_recommended,
                "self_reflection_used": self_reflection_used,
                "next_mode_hint": next_mode_hint,
                "sdk_hint": sdk_hint,
            }
        )
        return decision


class RunMetadataRegistry:
    """Persist a lightweight semantic execution registry outside the graph store."""

    def __init__(self, path: Optional[str] = None) -> None:
        default_path = os.getenv(
            "SEOCHO_RUN_METADATA_PATH",
            "/tmp/seocho/semantic_run_registry.jsonl",
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
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            recorded = True
        except Exception:
            recorded = False
            logger.warning("Failed to persist semantic run metadata.", exc_info=True)

        return {
            "schema_version": "semantic_run_registry.v1",
            "run_id": run_id,
            "recorded": recorded,
            "registry_path": self.path,
            "timestamp": timestamp,
        }


class SemanticEntityResolver:
    """Resolve question entities against graph entities."""

    def __init__(
        self,
        connector: Any,
        fulltext_index_hint: str = "entity_fulltext",
        candidate_limit: int = 5,
        ontology_hint_store: Optional[OntologyHintStore] = None,
        vocabulary_resolver: Optional[ManagedVocabularyResolver] = None,
    ):
        self.connector = connector
        self.fulltext_index_hint = fulltext_index_hint
        self.candidate_limit = candidate_limit
        self.ontology_hint_store = ontology_hint_store or OntologyHintStore(
            path=os.getenv("ONTOLOGY_HINTS_PATH", "output/ontology_hints.json")
        )
        self.vocabulary_resolver = vocabulary_resolver or ManagedVocabularyResolver()

    def extract_question_entities(self, question: str) -> List[str]:
        """Extract candidate entity spans from user question."""
        quoted = [m.group(1).strip() for m in re.finditer(r'"([^"]+)"', question)]
        single_quoted = [m.group(1).strip() for m in re.finditer(r"'([^']+)'", question)]
        caps = [
            m.group(0).strip()
            for m in re.finditer(
                r"\b(?:[A-Z][a-zA-Z0-9&.-]{1,}|[A-Z]{2,})(?:\s+[A-Z][a-zA-Z0-9&.-]{1,})*\b",
                question,
            )
        ]

        entities: List[str] = []
        seen: Set[str] = set()
        for value in quoted + single_quoted + caps:
            cleaned = self._clean_span(value)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen or key in STOPWORDS:
                continue
            seen.add(key)
            entities.append(cleaned)

        # Fallback: use long tokens when no span was detected.
        if not entities:
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9&._-]{2,}", question):
                key = token.lower()
                if key in STOPWORDS or key.isdigit():
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entities.append(token)
                if len(entities) >= 3:
                    break
        return entities

    def resolve(
        self,
        question: str,
        databases: Sequence[str],
        workspace_id: str = "default",
    ) -> Dict[str, Any]:
        """Resolve entities for a question across one or more databases."""
        entities = self.extract_question_entities(question)
        label_hints = self._infer_label_hints(question)
        label_hints.update(self.ontology_hint_store.infer_label_hints(question))
        fulltext_indexes = self._discover_fulltext_indexes(databases)

        matches: Dict[str, List[Dict[str, Any]]] = {}
        unresolved: List[str] = []
        vocabulary_resolved: Dict[str, str] = {}
        alias_resolved: Dict[str, str] = {}

        for entity in entities:
            vocabulary_text = self.vocabulary_resolver.resolve_alias(entity, workspace_id=workspace_id)
            resolved_text = self.ontology_hint_store.resolve_alias(vocabulary_text)
            vocabulary_resolved[entity] = vocabulary_text
            alias_resolved[entity] = resolved_text
            candidates: List[Dict[str, Any]] = []
            for db_name in databases:
                db_candidates = self._query_fulltext_candidates(
                    db_name=db_name,
                    entity_text=resolved_text,
                    indexes=fulltext_indexes.get(db_name, []),
                    workspace_id=workspace_id,
                )
                if not db_candidates:
                    db_candidates = self._query_contains_candidates(
                        db_name=db_name,
                        entity_text=resolved_text,
                        workspace_id=workspace_id,
                    )
                candidates.extend(db_candidates)

            ranked = self._rank_and_dedup(
                entity_text=entity,
                resolved_text=resolved_text,
                candidates=candidates,
                label_hints=label_hints,
            )
            if ranked:
                matches[entity] = ranked
            else:
                unresolved.append(entity)

        intent = infer_question_intent(question, entities)
        semantic_context = {
            "entities": entities,
            "label_hints": sorted(label_hints),
            "vocabulary_resolved": vocabulary_resolved,
            "alias_resolved": alias_resolved,
            "matches": matches,
            "unresolved_entities": unresolved,
            "vocabulary_hints": self.vocabulary_resolver.to_summary(workspace_id),
            "ontology_hints": self.ontology_hint_store.to_summary(),
            "intent": intent,
        }
        semantic_context["evidence_bundle_preview"] = build_evidence_bundle(
            question=question,
            semantic_context=semantic_context,
            matched_entities=entities,
        )
        return semantic_context

    def _discover_fulltext_indexes(self, databases: Sequence[str]) -> Dict[str, List[str]]:
        by_db: Dict[str, List[str]] = {}
        for db_name in databases:
            indexes: List[str] = []
            candidates = [
                "SHOW FULLTEXT INDEXES YIELD name, state WHERE state = 'ONLINE' RETURN name",
                "SHOW INDEXES YIELD name, type, state WHERE type = 'FULLTEXT' AND state = 'ONLINE' RETURN name",
            ]
            for query in candidates:
                rows = self._run_query(db_name, query, params=None)
                if rows:
                    indexes = [str(row.get("name", "")).strip() for row in rows if row.get("name")]
                    if indexes:
                        break
            if self.fulltext_index_hint and self.fulltext_index_hint not in indexes:
                indexes.insert(0, self.fulltext_index_hint)
            by_db[db_name] = [idx for idx in indexes if idx]
        return by_db

    def _query_fulltext_candidates(
        self,
        db_name: str,
        entity_text: str,
        indexes: Sequence[str],
        workspace_id: str,
    ) -> List[Dict[str, Any]]:
        query = """
        CALL db.index.fulltext.queryNodes($index_name, $query)
        YIELD node, score
        WITH node, score
        WHERE coalesce(node.workspace_id, $workspace_id) = $workspace_id
          AND coalesce(node.status, 'active') <> 'archived'
        RETURN elementId(node) AS node_id,
               labels(node) AS labels,
               coalesce(node.name, node.title, node.id, node.uri, elementId(node)) AS display_name,
               coalesce(node.source_id, '') AS source_id,
               coalesce(node.memory_id, node.source_id, '') AS memory_id,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        for index_name in indexes:
            rows = self._run_query(
                db_name,
                query,
                params={
                    "index_name": index_name,
                    "query": entity_text,
                    "limit": self.candidate_limit,
                    "workspace_id": workspace_id,
                },
            )
            if not rows:
                continue

            candidates: List[Dict[str, Any]] = []
            for row in rows:
                candidates.append(
                    {
                        "database": db_name,
                        "entity_text": entity_text,
                        "node_id": row.get("node_id"),
                        "labels": row.get("labels", []),
                        "display_name": row.get("display_name", ""),
                        "source_id": row.get("source_id", ""),
                        "memory_id": row.get("memory_id", ""),
                        "base_score": float(row.get("score", 0.0) or 0.0),
                        "source": "fulltext",
                        "index_name": index_name,
                    }
                )
            if candidates:
                return candidates
        return []

    def _query_contains_candidates(self, db_name: str, entity_text: str, workspace_id: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE coalesce(n.workspace_id, $workspace_id) = $workspace_id
          AND coalesce(n.status, 'active') <> 'archived'
          AND any(key IN $properties
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN elementId(n) AS node_id,
               labels(n) AS labels,
               coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS display_name,
               coalesce(n.source_id, '') AS source_id,
               coalesce(n.memory_id, n.source_id, '') AS memory_id
        LIMIT $limit
        """
        rows = self._run_query(
            db_name,
            query,
            params={
                "properties": list(ENTITY_PROPERTIES),
                "query": entity_text,
                "limit": self.candidate_limit,
                "workspace_id": workspace_id,
            },
        )
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            display_name = str(row.get("display_name", ""))
            lexical = self._lexical_similarity(entity_text, display_name)
            candidates.append(
                {
                    "database": db_name,
                    "entity_text": entity_text,
                    "node_id": row.get("node_id"),
                    "labels": row.get("labels", []),
                    "display_name": display_name,
                    "source_id": row.get("source_id", ""),
                    "memory_id": row.get("memory_id", ""),
                    "base_score": lexical,
                    "source": "contains",
                    "index_name": None,
                }
            )
        return candidates

    def _rank_and_dedup(
        self,
        entity_text: str,
        resolved_text: str,
        candidates: Sequence[Dict[str, Any]],
        label_hints: Set[str],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, Any]] = set()

        normalized_entity = self._normalize(entity_text)
        normalized_resolved = self._normalize(resolved_text)
        for candidate in candidates:
            db_name = str(candidate.get("database", ""))
            node_id = candidate.get("node_id")
            dedup_key = (db_name, node_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            display_name = str(candidate.get("display_name", ""))
            normalized_display = self._normalize(display_name)
            lexical = self._lexical_similarity(normalized_entity, normalized_display)
            base_score = float(candidate.get("base_score", 0.0))
            label_boost = self._label_boost(candidate.get("labels", []), label_hints)
            exact_boost = 0.2 if normalized_entity == normalized_display else 0.0
            alias_boost = 0.12 if normalized_resolved == normalized_display else 0.0
            final_score = base_score + lexical + label_boost + exact_boost + alias_boost

            ranked.append(
                {
                    **candidate,
                    "lexical_score": round(lexical, 4),
                    "label_boost": round(label_boost, 4),
                    "alias_boost": round(alias_boost, 4),
                    "final_score": round(final_score, 4),
                }
            )

        ranked.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
        top_candidates = ranked[: self.candidate_limit]
        
        # UI Signal: If confidence gap > 0.15, mark as safe to auto-pin
        if len(top_candidates) > 0:
            best_score = top_candidates[0].get("final_score", 0.0)
            if len(top_candidates) > 1:
                runner_up = top_candidates[1].get("final_score", 0.0)
                gap = best_score - runner_up
                top_candidates[0]["is_confident"] = (gap > 0.15)
            else:
                top_candidates[0]["is_confident"] = True
                
        return top_candidates

    def _run_query(
        self,
        db_name: str,
        query: str,
        params: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raw = self.connector.run_cypher(query=query, database=db_name, params=params)
        if isinstance(raw, str) and raw.startswith("Error"):
            logger.debug("Cypher error [%s]: %s", db_name, raw)
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            logger.debug("Non-JSON query output [%s]: %s", db_name, str(raw)[:160])
            return []
        if not isinstance(parsed, list):
            return []
        return parsed

    @staticmethod
    def _clean_span(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        cleaned = cleaned.strip(".,:;!?()[]{}")
        return cleaned

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @staticmethod
    def _lexical_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _infer_label_hints(question: str) -> Set[str]:
        q = question.lower()
        hints: Set[str] = set()
        for _, tokens in QUESTION_LABEL_HINTS.items():
            if any(token in q for token in tokens):
                hints.update(tokens)
        return hints

    @staticmethod
    def _label_boost(labels: Sequence[str], label_hints: Set[str]) -> float:
        if not labels or not label_hints:
            return 0.0
        normalized_labels = {re.sub(r"[^a-z0-9]+", "", str(label).lower()) for label in labels}
        normalized_hints = {re.sub(r"[^a-z0-9]+", "", hint.lower()) for hint in label_hints}
        if normalized_labels & normalized_hints:
            return 0.15
        return 0.0


class QueryRouterAgent:
    """Route question to LPG, RDF, or hybrid path."""

    def route(self, question: str) -> str:
        q = question.lower()
        has_rdf = any(token in q for token in RDF_HINTS)
        has_lpg = any(token in q for token in LPG_HINTS)
        if has_rdf and has_lpg:
            return "hybrid"
        if has_rdf:
            return "rdf"
        return "lpg"


class LPGAgent:
    """LPG query agent with semantic-layer-constrained Cypher planning."""

    def __init__(self, connector: Any, result_limit: int = 20):
        self.connector = connector
        self.result_limit = result_limit
        self.constraint_builder = SemanticConstraintSliceBuilder()
        self.validator = CypherQueryValidator()
        self.classifier = QueryInsufficiencyClassifier()
        self.support_validator = IntentSupportValidator()

    def preview_support(
        self,
        semantic_context: Dict[str, Any],
        constraint_slices: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        intent = semantic_context.get("intent", {})
        preview_bundle = semantic_context.get("evidence_bundle_preview", {})
        ranked_matches: List[Dict[str, Any]] = []
        support_candidates: List[Dict[str, Any]] = []

        for question_entity, candidates in semantic_context.get("matches", {}).items():
            if not isinstance(candidates, list):
                continue
            for candidate in candidates[:2]:
                if not isinstance(candidate, dict):
                    continue
                db_name = str(candidate.get("database", "")).strip()
                if not db_name:
                    continue
                constraint_slice = constraint_slices.get(db_name, {})
                support = self.support_validator.assess_candidate(
                    question_entity=question_entity,
                    candidate=candidate,
                    intent=intent,
                    constraint_slice=constraint_slice,
                    preview_bundle=preview_bundle,
                )
                annotated = dict(candidate)
                annotated["question_entity"] = question_entity
                annotated["support_assessment"] = support
                ranked_matches.append(annotated)
                support_candidates.append(support)

        ranked_matches.sort(
            key=lambda item: (
                bool(item.get("support_assessment", {}).get("supported")),
                float(item.get("support_assessment", {}).get("coverage", 0.0) or 0.0),
                float(item.get("final_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        support_candidates.sort(
            key=lambda item: (
                bool(item.get("supported")),
                float(item.get("coverage", 0.0) or 0.0),
                float(item.get("confidence", 0.0) or 0.0),
            ),
            reverse=True,
        )

        semantic_context["support_candidates"] = support_candidates[:6]
        semantic_context["preflight_support_assessment"] = (
            support_candidates[0]
            if support_candidates
            else self.support_validator.empty_assessment(intent)
        )
        semantic_context.setdefault(
            "support_assessment",
            semantic_context["preflight_support_assessment"],
        )
        return ranked_matches[:6]

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
        *,
        workspace_id: str = "default",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
        constraint_slices: Optional[Dict[str, Dict[str, Any]]] = None,
        ranked_matches: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        top_matches = list(ranked_matches) if ranked_matches is not None else self._top_entity_matches(
            semantic_context,
            constraint_slices or {},
        )
        if not top_matches:
            return {
                "mode": "lpg",
                "summary": "No resolved entity. Returned graph label distribution.",
                "records": self._label_distribution(databases),
                "reasoning": {
                    "requested": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "attempt_count": 0,
                    "repair_trace": [],
                    "self_reflection_used": False,
                },
                "support_assessment": semantic_context.get("support_assessment", {}),
                "execution_strategy": semantic_context.get("strategy_decision", {}),
            }

        constraint_slices = constraint_slices or self.constraint_builder.build_for_databases(
            databases,
            workspace_id=workspace_id,
        )
        records: List[Dict[str, Any]] = []
        repair_trace: List[Dict[str, Any]] = []
        best_assessment: Optional[InsufficiencyAssessment] = None
        best_bundle: Optional[Dict[str, Any]] = None
        best_support_assessment: Optional[Dict[str, Any]] = None
        selected_constraint_slice: Optional[Dict[str, Any]] = None
        attempt_limit = 1 + max(0, int(repair_budget or 0)) if reasoning_mode else 1

        for item in top_matches:
            db_name = str(item.get("database", "")).strip()
            node_id = item.get("node_id")
            if not db_name or node_id is None:
                continue
            constraint_slice = constraint_slices.get(db_name) or self.constraint_builder.build_for_database(
                db_name,
                workspace_id=workspace_id,
            )
            execution = self._execute_with_repair(
                question=question,
                semantic_context=semantic_context,
                anchor_match=item,
                constraint_slice=constraint_slice,
                attempt_limit=attempt_limit,
            )
            repair_trace.extend(execution["repair_trace"])
            if execution["records"]:
                records.extend(execution["records"])
            if best_assessment is None or (
                execution["assessment"].sufficient and not best_assessment.sufficient
            ) or execution["assessment"].row_count > best_assessment.row_count:
                best_assessment = execution["assessment"]
                best_bundle = execution["evidence_bundle"]
                best_support_assessment = execution["support_assessment"]
                selected_constraint_slice = constraint_slice
            if execution["assessment"].sufficient:
                break

        unique_anchors = {
            (
                str(item.get("database", "")).strip(),
                str(item.get("anchor_entity", "")).strip(),
            )
            for item in repair_trace
            if str(item.get("anchor_entity", "")).strip()
        }
        self_reflection_used = len(unique_anchors) > 1

        if not records:
            summary = "No grounded LPG result satisfied the semantic-layer constraints. Returned label distribution."
            return {
                "mode": "lpg",
                "summary": summary,
                "records": self._label_distribution(databases),
                "reasoning": {
                    "requested": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "attempt_count": len(repair_trace),
                    "repair_trace": repair_trace,
                    "constraint_slice": self._summarize_constraint_slice(selected_constraint_slice),
                    "self_reflection_used": self_reflection_used,
                    "insufficiency": (
                        {
                            "reason": best_assessment.reason,
                            "missing_slots": list(best_assessment.missing_slots),
                            "row_count": best_assessment.row_count,
                        }
                        if best_assessment is not None
                        else {"reason": "no_executable_plan", "missing_slots": [], "row_count": 0}
                    ),
                },
                "evidence_bundle": best_bundle or semantic_context.get("evidence_bundle_preview", {}),
                "support_assessment": best_support_assessment or semantic_context.get("support_assessment", {}),
                "execution_strategy": semantic_context.get("strategy_decision", {}),
            }

        best_reason = best_assessment.reason if best_assessment is not None else "sufficient"
        if reasoning_mode and len(repair_trace) > len(top_matches):
            summary = "Reasoning mode repaired an initially insufficient constrained Cypher plan."
        else:
            summary = "Resolved entities were expanded through semantic-layer-constrained LPG queries."
        return {
            "mode": "lpg",
            "summary": summary,
            "records": records,
            "reasoning": {
                "requested": reasoning_mode,
                "repair_budget": max(0, int(repair_budget or 0)),
                "attempt_count": len(repair_trace),
                "repair_trace": repair_trace,
                "constraint_slice": self._summarize_constraint_slice(selected_constraint_slice),
                "terminal_reason": best_reason,
                "self_reflection_used": self_reflection_used,
            },
            "evidence_bundle": best_bundle or semantic_context.get("evidence_bundle_preview", {}),
            "support_assessment": best_support_assessment or semantic_context.get("support_assessment", {}),
            "execution_strategy": semantic_context.get("strategy_decision", {}),
        }

    def _top_entity_matches(
        self,
        semantic_context: Dict[str, Any],
        constraint_slices: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ranked_matches = self.preview_support(semantic_context, constraint_slices)
        return ranked_matches[:3]

    def _execute_with_repair(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        attempt_limit: int,
    ) -> Dict[str, Any]:
        intent = semantic_context.get("intent", {})
        records: List[Dict[str, Any]] = []
        repair_trace: List[Dict[str, Any]] = []
        last_assessment = InsufficiencyAssessment(
            sufficient=False,
            reason="no_attempts",
            missing_slots=tuple(intent.get("focus_slots", [])),
            row_count=0,
        )
        last_bundle = semantic_context.get("evidence_bundle_preview", {})

        for strategy in self._strategy_sequence(attempt_limit):
            plan = self._build_plan(
                question=question,
                semantic_context=semantic_context,
                anchor_match=anchor_match,
                intent=intent,
                constraint_slice=constraint_slice,
                strategy=strategy,
            )
            validation = self.validator.validate(plan, constraint_slice)
            if not validation["ok"]:
                repair_trace.append(
                    {
                        "strategy": strategy,
                        "database": plan.database,
                        "anchor_entity": plan.anchor_entity,
                        "status": "invalid",
                        "violations": validation["violations"],
                    }
                )
                last_assessment = InsufficiencyAssessment(
                    sufficient=False,
                    reason="validation_failed",
                    missing_slots=tuple(intent.get("focus_slots", [])),
                    row_count=0,
                )
                continue

            raw = self.connector.run_cypher(
                query=plan.query,
                database=plan.database,
                params=plan.params,
            )
            rows = _parse_cypher_rows(raw)
            annotated_rows = [
                {
                    "database": plan.database,
                    "graph_id": constraint_slice.get("graph_id"),
                    "strategy": strategy,
                    **row,
                }
                for row in rows
                if isinstance(row, dict)
            ]
            assessment = self.classifier.assess(intent, annotated_rows)
            evidence_bundle = self._build_runtime_evidence_bundle(
                question=question,
                semantic_context=semantic_context,
                intent=intent,
                rows=annotated_rows,
                plan=plan,
                assessment=assessment,
                constraint_slice=constraint_slice,
            )
            support_assessment = self.support_validator.finalize_runtime_support(
                preflight=anchor_match.get("support_assessment"),
                intent=intent,
                bundle=evidence_bundle,
                assessment=assessment,
                plan=plan,
                constraint_slice=constraint_slice,
            )
            evidence_bundle["support_assessment"] = support_assessment
            repair_trace.append(
                {
                    "strategy": strategy,
                    "database": plan.database,
                    "graph_id": constraint_slice.get("graph_id"),
                    "anchor_entity": plan.anchor_entity,
                    "relation_types": list(plan.relation_types),
                    "row_count": assessment.row_count,
                    "sufficient": assessment.sufficient,
                    "reason": assessment.reason,
                    "missing_slots": list(assessment.missing_slots),
                    "support_status": support_assessment.get("status"),
                    "coverage": support_assessment.get("coverage"),
                    "validation": {
                        "labels": validation["labels"],
                        "relation_types": validation["relation_types"],
                    },
                }
            )
            if annotated_rows:
                records = annotated_rows
            last_assessment = assessment
            last_bundle = evidence_bundle
            if assessment.sufficient:
                break

        return {
            "records": records,
            "repair_trace": repair_trace,
            "assessment": last_assessment,
            "evidence_bundle": last_bundle,
            "support_assessment": last_bundle.get("support_assessment", anchor_match.get("support_assessment", {})),
        }

    @staticmethod
    def _strategy_sequence(attempt_limit: int) -> List[str]:
        ordered = ["strict", "ontology_relaxed", "graph_broad"]
        return ordered[: max(1, attempt_limit)]

    def _build_plan(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
    ) -> CypherPlan:
        database = str(anchor_match.get("database", "")).strip()
        node_id = anchor_match.get("node_id")
        anchor_entity = str(anchor_match.get("display_name") or anchor_match.get("question_entity") or "").strip()
        anchor_label = self._pick_anchor_label(anchor_match, constraint_slice, strategy)
        relation_types = self._resolve_relation_types(question, intent, constraint_slice, strategy)
        target_hint = self._secondary_entity_hint(semantic_context, anchor_match) if strategy == "strict" else ""
        intent_id = str(intent.get("intent_id", "")).strip()

        if intent_id == "relationship_lookup":
            query = self._relationship_query(anchor_label=anchor_label, relation_types=relation_types)
            params = {
                "node_id": node_id,
                "limit": self.result_limit,
                "target_hint": target_hint,
            }
        elif intent_id == "responsibility_lookup":
            query = self._responsibility_query(anchor_label=anchor_label, relation_types=relation_types)
            params = {"node_id": node_id, "limit": self.result_limit}
        else:
            query = self._entity_summary_query(anchor_label=anchor_label)
            params = {"node_id": node_id, "limit": self.result_limit}

        return CypherPlan(
            database=database,
            query=query,
            params=params,
            strategy=strategy,
            anchor_entity=anchor_entity,
            anchor_label=anchor_label,
            relation_types=tuple(relation_types),
        )

    def _resolve_relation_types(
        self,
        question: str,
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
    ) -> List[str]:
        allowed_relationship_types = [
            str(item).strip()
            for item in constraint_slice.get("allowed_relationship_types", [])
            if str(item).strip()
        ]
        allowed_lookup = {_normalize_symbol(item): item for item in allowed_relationship_types}
        alias_lookup = {
            _normalize_symbol(alias): relation_type
            for alias, relation_type in constraint_slice.get("relation_aliases", {}).items()
        }
        normalized_question = _normalize_symbol(question)
        matched_relations = [
            relation_type
            for alias, relation_type in alias_lookup.items()
            if alias and alias in normalized_question
        ]
        required_relations = [
            str(item).strip()
            for item in intent.get("required_relations", [])
            if str(item).strip()
        ]

        if strategy == "graph_broad":
            return []

        candidates = matched_relations + required_relations
        normalized_candidates = {_normalize_symbol(item): item for item in candidates if item}
        if strategy == "ontology_relaxed" and allowed_relationship_types:
            return allowed_relationship_types[:8]
        if not allowed_relationship_types:
            return list(dict.fromkeys(normalized_candidates.values()))

        constrained = [
            allowed_lookup[key]
            for key in normalized_candidates
            if key in allowed_lookup
        ]
        return list(dict.fromkeys(constrained))

    @staticmethod
    def _pick_anchor_label(
        anchor_match: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
    ) -> str:
        if strategy == "graph_broad":
            return ""
        allowed_labels = set(constraint_slice.get("allowed_labels", []))
        labels = [
            str(label).strip()
            for label in anchor_match.get("labels", [])
            if str(label).strip()
        ]
        if not labels:
            return ""
        if not allowed_labels:
            return labels[0]
        for label in labels:
            if label in allowed_labels:
                return label
        return ""

    @staticmethod
    def _secondary_entity_hint(
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
    ) -> str:
        anchor_name = str(anchor_match.get("display_name") or anchor_match.get("question_entity") or "").strip()
        for entity, candidates in semantic_context.get("matches", {}).items():
            if not candidates:
                continue
            display_name = str(candidates[0].get("display_name") or entity).strip()
            if display_name and display_name != anchor_name:
                return display_name
        return ""

    @staticmethod
    def _relationship_query(*, anchor_label: str, relation_types: Sequence[str]) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        relation_clause = (
            ":" + "|".join(relation_types)
            if relation_types
            else ""
        )
        return f"""
        MATCH (n{label_clause})
        WHERE elementId(n) = toString($node_id)
        OPTIONAL MATCH (n)-[r{relation_clause}]-(m)
        WHERE $target_hint = ''
           OR toLower(coalesce(m.name, m.title, m.id, m.uri, elementId(m))) CONTAINS toLower($target_hint)
        RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS source_entity,
               type(r) AS relation_type,
               coalesce(m.name, m.title, m.id, m.uri, elementId(m)) AS target_entity,
               labels(m) AS target_labels,
               coalesce(m.content_preview, m.description, '') AS supporting_fact
        ORDER BY target_entity
        LIMIT $limit
        """

    @staticmethod
    def _responsibility_query(*, anchor_label: str, relation_types: Sequence[str]) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        relation_clause = (
            ":" + "|".join(relation_types)
            if relation_types
            else ""
        )
        return f"""
        MATCH (target{label_clause})
        WHERE elementId(target) = toString($node_id)
        OPTIONAL MATCH (counterparty)-[r{relation_clause}]-(target)
        RETURN coalesce(counterparty.name, counterparty.title, counterparty.id, counterparty.uri, elementId(counterparty)) AS owner_or_operator,
               type(r) AS relation_type,
               coalesce(target.name, target.title, target.id, target.uri, elementId(target)) AS target_entity,
               labels(counterparty) AS owner_labels,
               labels(target) AS target_labels,
               coalesce(counterparty.content_preview, counterparty.description, '') AS supporting_fact
        ORDER BY owner_or_operator
        LIMIT $limit
        """

    @staticmethod
    def _entity_summary_query(*, anchor_label: str) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        return f"""
        MATCH (n{label_clause})
        WHERE elementId(n) = toString($node_id)
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS target_entity,
               properties(n) AS properties,
               collect(
                 DISTINCT {{
                   relation: type(r),
                   target: coalesce(m.name, m.title, m.id, m.uri, elementId(m)),
                   target_labels: labels(m)
                 }}
               )[0..$limit] AS neighbors,
               coalesce(n.content_preview, n.description, n.content, '') AS supporting_fact
        LIMIT 1
        """

    def _build_runtime_evidence_bundle(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        intent: Dict[str, Any],
        rows: Sequence[Dict[str, Any]],
        plan: CypherPlan,
        assessment: InsufficiencyAssessment,
        constraint_slice: Dict[str, Any],
    ) -> Dict[str, Any]:
        bundle = build_evidence_bundle(
            question=question,
            semantic_context=semantic_context,
            matched_entities=semantic_context.get("entities", []),
        )
        slot_fills = dict(bundle.get("slot_fills", {}))
        selected_triples: List[Dict[str, Any]] = []

        for row in rows[: self.result_limit]:
            relation_type = str(row.get("relation_type", "")).strip()
            if row.get("source_entity") and row.get("target_entity") and relation_type:
                slot_fills["source_entity"] = row.get("source_entity")
                slot_fills["target_entity"] = row.get("target_entity")
                slot_fills["relation_paths"] = [relation_type]
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                selected_triples.append(
                    {
                        "source": row.get("source_entity"),
                        "relation": relation_type,
                        "target": row.get("target_entity"),
                        "target_labels": row.get("target_labels", []),
                    }
                )
            if row.get("owner_or_operator"):
                slot_fills["owner_or_operator"] = row.get("owner_or_operator")
                slot_fills["target_entity"] = row.get("target_entity")
                if relation_type:
                    slot_fills["relation_paths"] = [relation_type]
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                selected_triples.append(
                    {
                        "source": row.get("owner_or_operator"),
                        "relation": relation_type or "RELATED_TO",
                        "target": row.get("target_entity"),
                        "target_labels": row.get("target_labels", []),
                    }
                )
            if row.get("target_entity") and (row.get("properties") or row.get("neighbors")):
                slot_fills["target_entity"] = row.get("target_entity")
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                for neighbor in row.get("neighbors", [])[:5] if isinstance(row.get("neighbors"), list) else []:
                    if not isinstance(neighbor, dict):
                        continue
                    relation = str(neighbor.get("relation", "")).strip()
                    target = str(neighbor.get("target", "")).strip()
                    if relation and target:
                        selected_triples.append(
                            {
                                "source": row.get("target_entity"),
                                "relation": relation,
                                "target": target,
                                "target_labels": neighbor.get("target_labels", []),
                            }
                        )

        focus_slots = [str(slot).strip() for slot in intent.get("focus_slots", []) if str(slot).strip()]
        bundle["slot_fills"] = slot_fills
        bundle["selected_triples"] = selected_triples[:10]
        bundle["grounded_slots"] = [slot for slot in focus_slots if slot in slot_fills]
        bundle["missing_slots"] = [slot for slot in focus_slots if slot not in slot_fills]
        bundle["coverage"] = round(
            len(bundle["grounded_slots"]) / max(1, len(focus_slots)),
            4,
        ) if focus_slots else 1.0
        bundle["database"] = plan.database
        bundle["graph_id"] = constraint_slice.get("graph_id")
        bundle["reasoning"] = {
            "strategy": plan.strategy,
            "anchor_entity": plan.anchor_entity,
            "anchor_label": plan.anchor_label,
            "relation_types": list(plan.relation_types),
            "assessment": {
                "sufficient": assessment.sufficient,
                "reason": assessment.reason,
                "missing_slots": list(assessment.missing_slots),
                "row_count": assessment.row_count,
            },
        }
        return bundle

    @staticmethod
    def _summarize_constraint_slice(constraint_slice: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(constraint_slice, dict):
            return {}
        return {
            "graph_id": constraint_slice.get("graph_id"),
            "database": constraint_slice.get("database"),
            "ontology_id": constraint_slice.get("ontology_id"),
            "constraint_strength": constraint_slice.get("constraint_strength"),
            "artifact_ids": list(constraint_slice.get("artifact_ids", [])),
            "label_count": len(constraint_slice.get("allowed_labels", [])),
            "relationship_count": len(constraint_slice.get("allowed_relationship_types", [])),
            "property_count": len(constraint_slice.get("allowed_properties", [])),
        }

    def _label_distribution(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class RDFAgent:
    """RDF-oriented query agent."""

    def __init__(self, connector: Any, result_limit: int = 20):
        self.connector = connector
        self.result_limit = result_limit

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        entities = semantic_context.get("entities", [])
        if entities:
            rows = self._resource_matches(databases, entities[0])
            if rows:
                return {
                    "mode": "rdf",
                    "summary": "Matched RDF-like resources using URI/name signals.",
                    "records": rows,
                }

        return {
            "mode": "rdf",
            "summary": "No RDF resource match found. Returned RDF label overview.",
            "records": self._rdf_label_overview(databases),
        }

    def _resource_matches(self, databases: Sequence[str], entity_text: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE (
            any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
            OR n.uri IS NOT NULL
        )
          AND any(key IN ['uri', 'name', 'title', 'id']
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN labels(n) AS labels,
               coalesce(n.uri, n.name, n.title, n.id, elementId(n)) AS resource,
               n.name AS name
        LIMIT $limit
        """
        all_rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(
                query=query,
                database=db_name,
                params={"query": entity_text, "limit": self.result_limit},
            )
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    all_rows.append({"database": db_name, **row})
        return all_rows

    def _rdf_label_overview(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
           OR n.uri IS NOT NULL
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class AnswerGenerationAgent:
    """Generate final response payload from routed agent outputs."""

    def synthesize(
        self,
        question: str,
        route: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
    ) -> str:
        entities = semantic_context.get("entities", [])
        unresolved = semantic_context.get("unresolved_entities", [])
        intent = semantic_context.get("intent", {})
        evidence_bundle = semantic_context.get("evidence_bundle_preview", {})
        support_assessment = semantic_context.get("support_assessment", {})
        strategy_decision = semantic_context.get("strategy_decision", {})

        lines = [f"Route selected: {route.upper()}."]
        if intent.get("intent_id"):
            lines.append(f"Intent: {intent['intent_id']}.")
        if entities:
            lines.append(f"Extracted entities: {', '.join(entities)}.")
        if unresolved:
            lines.append(f"Unresolved entities: {', '.join(unresolved)}.")
        if support_assessment.get("status"):
            lines.append(
                f"Support status: {support_assessment['status']} ({support_assessment.get('reason', 'unspecified')})."
            )
        if evidence_bundle.get("grounded_slots"):
            lines.append(f"Grounded slots: {', '.join(evidence_bundle['grounded_slots'])}.")
        if evidence_bundle.get("missing_slots"):
            lines.append(f"Missing slots: {', '.join(evidence_bundle['missing_slots'])}.")
        reasoning = semantic_context.get("reasoning", {})
        if reasoning.get("requested"):
            lines.append(
                f"Reasoning mode used {int(reasoning.get('attempt_count', 0))} retrieval attempt(s)."
            )
            if reasoning.get("terminal_reason"):
                lines.append(f"Reasoning terminal state: {reasoning['terminal_reason']}.")
        if strategy_decision.get("advanced_debate_recommended"):
            lines.append("Advanced debate is recommended if you want a cross-graph comparison.")
        elif strategy_decision.get("next_mode_hint") == "reasoning_mode":
            lines.append("A bounded repair retry is recommended for a stronger retrieval attempt.")

        if lpg_result and lpg_result.get("records"):
            lines.append(f"LPG records: {len(lpg_result['records'])}.")
        if rdf_result and rdf_result.get("records"):
            lines.append(f"RDF records: {len(rdf_result['records'])}.")
        if not ((lpg_result and lpg_result.get("records")) or (rdf_result and rdf_result.get("records"))):
            lines.append("No matching graph records were found for this question.")

        return " ".join(lines)


class SemanticAgentFlow:
    """Orchestrate semantic layer + router + specialist agents + answer agent."""

    def __init__(self, connector: Any):
        self.resolver = SemanticEntityResolver(connector)
        self.router = QueryRouterAgent()
        self.lpg_agent = LPGAgent(connector)
        self.rdf_agent = RDFAgent(connector)
        self.answer_agent = AnswerGenerationAgent()
        self.constraint_builder = SemanticConstraintSliceBuilder()
        self.strategy_chooser = ExecutionStrategyChooser()
        self.run_registry = RunMetadataRegistry()

    def run(
        self,
        question: str,
        databases: Sequence[str],
        entity_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        workspace_id: str = "default",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
    ) -> Dict[str, Any]:
        trace_steps: List[Dict[str, Any]] = []

        semantic_context = self.resolver.resolve(question, databases, workspace_id=workspace_id)
        constraint_slices = self.constraint_builder.build_for_databases(
            databases,
            workspace_id=workspace_id,
        )
        semantic_context["semantic_layer"] = {
            "databases": {
                database: LPGAgent._summarize_constraint_slice(constraint_slice)
                for database, constraint_slice in constraint_slices.items()
            }
        }
        self._apply_entity_overrides(semantic_context, entity_overrides or {})
        support_ranked_matches = self.lpg_agent.preview_support(semantic_context, constraint_slices)
        trace_steps.append(
            {
                "id": "0",
                "type": "SEMANTIC",
                "agent": "SemanticLayer",
                "content": "Entity extraction and disambiguation completed.",
                "metadata": {
                    "entities": semantic_context.get("entities", []),
                    "unresolved_entities": semantic_context.get("unresolved_entities", []),
                    "overrides_applied": sorted(
                        list(semantic_context.get("overrides_applied", {}).keys())
                    ),
                    "reasoning_mode": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "support_status": semantic_context.get("preflight_support_assessment", {}).get("status"),
                },
            }
        )

        route = self.router.route(question)
        semantic_context["strategy_decision"] = self.strategy_chooser.choose_initial(
            route=route,
            reasoning_mode=reasoning_mode,
            repair_budget=repair_budget,
            support_assessment=semantic_context.get("preflight_support_assessment", {}),
            graph_count=len(databases),
        )
        trace_steps.append(
            {
                "id": "1",
                "type": "ROUTER",
                "agent": "RouterAgent",
                "content": f"Question routed to {route}.",
                "metadata": {
                    "route": route,
                    "initial_mode": semantic_context["strategy_decision"].get("initial_mode"),
                },
            }
        )
        trace_steps.append(
            {
                "id": "2",
                "type": "STRATEGY",
                "agent": "StrategyChooser",
                "content": semantic_context["strategy_decision"].get("reason", ""),
                "metadata": semantic_context["strategy_decision"],
            }
        )

        lpg_result: Optional[Dict[str, Any]] = None
        rdf_result: Optional[Dict[str, Any]] = None

        if route in {"lpg", "hybrid"}:
            lpg_result = self.lpg_agent.run(
                question,
                databases,
                semantic_context,
                workspace_id=workspace_id,
                reasoning_mode=reasoning_mode,
                repair_budget=repair_budget,
                constraint_slices=constraint_slices,
                ranked_matches=support_ranked_matches,
            )
            if isinstance(lpg_result.get("evidence_bundle"), dict):
                semantic_context["evidence_bundle_preview"] = lpg_result["evidence_bundle"]
            if isinstance(lpg_result.get("reasoning"), dict):
                semantic_context["reasoning"] = lpg_result["reasoning"]
            if isinstance(lpg_result.get("support_assessment"), dict):
                semantic_context["support_assessment"] = lpg_result["support_assessment"]
            trace_steps.append(
                {
                    "id": "3",
                    "type": "SPECIALIST",
                    "agent": "LPGAgent",
                    "content": lpg_result.get("summary", ""),
                    "metadata": {
                        "records": len(lpg_result.get("records", [])),
                        "reasoning_attempts": int(
                            lpg_result.get("reasoning", {}).get("attempt_count", 0)
                        ),
                        "terminal_reason": lpg_result.get("reasoning", {}).get("terminal_reason"),
                        "support_status": lpg_result.get("support_assessment", {}).get("status"),
                    },
                }
            )

        if route in {"rdf", "hybrid"}:
            rdf_result = self.rdf_agent.run(question, databases, semantic_context)
            trace_steps.append(
                {
                    "id": "4",
                    "type": "SPECIALIST",
                    "agent": "RDFAgent",
                    "content": rdf_result.get("summary", ""),
                    "metadata": {"records": len(rdf_result.get("records", []))},
                }
            )

        semantic_context["strategy_decision"] = self.strategy_chooser.finalize(
            initial_decision=semantic_context.get("strategy_decision", {}),
            route=route,
            graph_count=len(databases),
            support_assessment=semantic_context.get("support_assessment", {}),
            reasoning=semantic_context.get("reasoning"),
        )

        response = self.answer_agent.synthesize(
            question=question,
            route=route,
            semantic_context=semantic_context,
            lpg_result=lpg_result,
            rdf_result=rdf_result,
        )
        semantic_context["run_metadata"] = self.run_registry.record_run(
            question=question,
            workspace_id=workspace_id,
            route=route,
            semantic_context=semantic_context,
            lpg_result=lpg_result,
            rdf_result=rdf_result,
            response=response,
        )
        trace_steps.append(
            {
                "id": "5",
                "type": "GENERATION",
                "agent": "AnswerGenerationAgent",
                "content": response,
                "metadata": {
                    "support_status": semantic_context.get("support_assessment", {}).get("status"),
                    "next_mode_hint": semantic_context.get("strategy_decision", {}).get("next_mode_hint"),
                },
            }
        )

        return {
            "response": response,
            "trace_steps": trace_steps,
            "route": route,
            "semantic_context": semantic_context,
            "lpg_result": lpg_result,
            "rdf_result": rdf_result,
            "support_assessment": semantic_context.get("support_assessment", {}),
            "strategy_decision": semantic_context.get("strategy_decision", {}),
            "run_metadata": semantic_context.get("run_metadata", {}),
            "evidence_bundle": semantic_context.get("evidence_bundle_preview", {}),
        }

    @staticmethod
    def _apply_entity_overrides(
        semantic_context: Dict[str, Any],
        entity_overrides: Dict[str, Dict[str, Any]],
    ) -> None:
        if not entity_overrides:
            return

        matches = semantic_context.setdefault("matches", {})
        unresolved = set(semantic_context.get("unresolved_entities", []))
        applied: Dict[str, Dict[str, Any]] = {}

        for question_entity, override in entity_overrides.items():
            if not question_entity:
                continue

            db_name = override.get("database")
            node_id = override.get("node_id")
            if db_name is None or node_id is None:
                continue

            candidate = {
                "database": str(db_name),
                "entity_text": question_entity,
                "node_id": node_id,
                "labels": override.get("labels", []),
                "display_name": override.get("display_name", question_entity),
                "base_score": 1.0,
                "source": "override",
                "index_name": None,
                "lexical_score": 1.0,
                "label_boost": 0.0,
                "alias_boost": 0.0,
                "final_score": 10.0,
            }

            existing = matches.get(question_entity, [])
            matches[question_entity] = [candidate] + [
                row for row in existing
                if not (row.get("database") == candidate["database"] and row.get("node_id") == candidate["node_id"])
            ]
            unresolved.discard(question_entity)
            applied[question_entity] = {
                "database": candidate["database"],
                "node_id": candidate["node_id"],
                "display_name": candidate["display_name"],
            }

        semantic_context["unresolved_entities"] = sorted(unresolved)
        if applied:
            semantic_context["overrides_applied"] = applied
            semantic_context["evidence_bundle_preview"] = build_evidence_bundle(
                question="",
                semantic_context=semantic_context,
                matched_entities=semantic_context.get("entities", []),
            )
