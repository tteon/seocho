from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set

from .contracts import IntentSpec


INTENT_CATALOG: tuple[IntentSpec, ...] = (
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
    coverage = round(len(grounded_slots) / max(1, len(focus_slots)), 4) if focus_slots else 1.0

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
    normalized_target_keys = {re.sub(r"[^a-z0-9]+", "", item.lower()) for item in normalized_targets}
    for entity in entities:
        labels = entity.get("labels", [])
        if not isinstance(labels, list):
            continue
        normalized_labels = {
            re.sub(r"[^a-z0-9]+", "", str(label).lower())
            for label in labels
        }
        if normalized_labels & normalized_target_keys:
            entity_name = _entity_name(entity)
            if entity_name:
                return entity_name
    return ""
