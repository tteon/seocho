from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .contracts import IntentSpec


LIMITATION_TEXT_HINTS = (
    "limitation",
    "limitations",
    "constraint",
    "constraints",
    "bottleneck",
    "bottlenecks",
    "drawback",
    "drawbacks",
    "downside",
    "downsides",
    "single-thread",
    "single thread",
    "serial",
    "serialized",
    "blocked",
    "blocking",
    "limited by",
    "constrained by",
    "due to",
    "because of",
    "gil",
    "global interpreter lock",
)

ALTERNATIVE_TEXT_HINTS = (
    "alternative",
    "alternatives",
    "instead",
    "parallel",
    "parallelism",
    "parallelize",
    "parallelized",
    "workaround",
    "workarounds",
    "bypass",
    "avoid",
    "multiprocessing",
    "multi-processing",
    "process pool",
    "worker pool",
    "ray",
    "dask",
    "celery",
    "asyncio",
    "joblib",
    "subprocess",
)

LIMITATION_RELATION_HINTS = {
    "limitedby",
    "constrainedby",
    "blockedby",
    "bottleneckedby",
    "haslimitation",
    "hasconstraint",
    "hasdrawback",
    "prevents",
    "restricts",
}

ALTERNATIVE_RELATION_HINTS = {
    "alternativeto",
    "usesalternative",
    "parallelizedwith",
    "parallelwith",
    "workaroundwith",
    "bypasswith",
    "recommendedwith",
    "replacedby",
    "scalewith",
}

LIMITATION_LABEL_HINTS = {
    "limitation",
    "constraint",
    "bottleneck",
    "drawback",
}

ALTERNATIVE_LABEL_HINTS = {
    "alternative",
    "workaround",
    "paralleltool",
    "parallelstrategy",
    "solution",
}

COMMON_ALTERNATIVE_TECHNIQUE_HINTS = (
    "multiprocessing",
    "multi processing",
    "ray",
    "dask",
    "celery",
    "joblib",
    "asyncio",
    "subprocess",
    "process pool",
    "thread pool",
    "worker pool",
)

DETERMINISTIC_QUESTION_HINTS = (
    "how many",
    "count",
    "total",
    "sum",
    "average",
    "avg",
    "mean",
    "highest",
    "lowest",
    "top",
    "bottom",
    "list",
    "return",
    "show",
    "sorted",
    "sort",
    "ratio",
    "percentage",
    "percent",
    "difference",
    "growth",
    "delta",
)

NONDETERMINISTIC_QUESTION_HINTS = (
    "why",
    "explain",
    "describe",
    "summarize",
    "summary",
    "interpret",
    "discuss",
    "insight",
    "reason",
    "cause",
    "recommend",
    "suggest",
    "risk",
    "tradeoff",
)


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
        intent_id="engineering_tradeoff_lookup",
        required_relations=(),
        required_entity_types=("Entity",),
        focus_slots=("target_entity", "limitation_points", "alternative_points", "supporting_fact"),
        trigger_keywords=(
            "limitation",
            "limitations",
            "constraint",
            "constraints",
            "bottleneck",
            "tradeoff",
            "tradeoffs",
            "alternative",
            "alternatives",
            "parallel",
            "parallelism",
            "gil",
        ),
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
    databases: List[str] = []
    graph_ids: List[str] = []
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
        database = str(best.get("database", "")).strip()
        graph_id = str(best.get("graph_id", "")).strip()
        if database and database not in databases:
            databases.append(database)
        if graph_id and graph_id not in graph_ids:
            graph_ids.append(graph_id)

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

    if "source_entity" in focus_slots and len(matched_entity_names) > 1:
        slot_fills["source_entity"] = matched_entity_names[0]
        slot_fills["target_entity"] = matched_entity_names[1]

    if "relation_paths" in focus_slots and selected_triples:
        slot_fills["relation_paths"] = [triple["relation"] for triple in selected_triples]

    labeled_owner = _first_entity_with_labels(prioritized_memory_entities, {"person", "organization", "company"})
    if labeled_owner and "owner_or_operator" in focus_slots:
        slot_fills["owner_or_operator"] = labeled_owner

    preview = str(memory_payload.get("content_preview") or memory_payload.get("content") or "").strip()
    relation_triples = _relation_triples_from_text(
        preview,
        target_entity=str(slot_fills.get("target_entity", "") or ""),
        owner_or_operator=str(slot_fills.get("owner_or_operator", "") or ""),
    )
    required_relations = [
        str(relation).strip()
        for relation in intent.get("required_relations", [])
        if str(relation).strip()
    ]
    if relation_triples:
        selected_triples = [*relation_triples, *selected_triples]
        if "relation_paths" in focus_slots or required_relations:
            slot_fills["relation_paths"] = [
                triple["relation"]
                for triple in relation_triples
                if str(triple.get("relation", "")).strip()
            ]
    if memory_payload:
        database = str(memory_payload.get("database", "")).strip()
        graph_id = str(memory_payload.get("graph_id", "")).strip()
        if database and database not in databases:
            databases.append(database)
        if graph_id and graph_id not in graph_ids:
            graph_ids.append(graph_id)

    tradeoff_from_entities = _tradeoff_points_from_entities(prioritized_memory_entities)
    tradeoff_from_preview = extract_tradeoff_points_from_text(preview)
    limitation_points = _dedupe_points(
        [
            *tradeoff_from_entities["limitation_points"],
            *tradeoff_from_preview["limitation_points"],
        ]
    )
    alternative_points = _dedupe_points(
        [
            *tradeoff_from_entities["alternative_points"],
            *tradeoff_from_preview["alternative_points"],
        ]
    )
    if limitation_points and "limitation_points" in focus_slots:
        slot_fills["limitation_points"] = limitation_points
    if alternative_points and "alternative_points" in focus_slots:
        slot_fills["alternative_points"] = alternative_points
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

    support_payload = dict(support_assessment or {})
    support_status = str(
        support_payload.get("status")
        or ("supported" if not missing_slots and grounded_slots else "partial" if grounded_slots else "unsupported")
    ).strip()
    support_reason = str(
        support_payload.get("reason")
        or ("sufficient" if support_status == "supported" else "partial_slot_fill" if support_status == "partial" else "no_grounded_slots")
    ).strip()
    route_profile = _build_route_profile(
        question=question,
        intent=intent,
        semantic_context=semantic_context,
        memory_payload=memory_payload,
        candidate_entities=candidate_entities,
        selected_triples=selected_triples,
        grounded_slots=grounded_slots,
        missing_slots=missing_slots,
    )
    answer_shape = _infer_answer_shape(
        question=question,
        intent=intent,
        route_profile=route_profile,
        missing_slots=missing_slots,
    )

    return {
        "schema_version": "evidence_bundle.v2",
        "intent_id": str(intent.get("intent_id", "")).strip(),
        "route_profile": route_profile,
        "answer_shape": answer_shape["shape"],
        "answer_shape_profile": answer_shape,
        "database": databases[0] if databases else "",
        "databases": databases,
        "graph_id": graph_ids[0] if graph_ids else "",
        "graph_ids": graph_ids,
        "required_relations": required_relations,
        "required_entity_types": list(intent.get("required_entity_types", [])),
        "focus_slots": focus_slots,
        "candidate_entities": candidate_entities,
        "selected_triples": _dedupe_triples(selected_triples),
        "slot_fills": slot_fills,
        "grounded_slots": grounded_slots,
        "missing_slots": missing_slots,
        "provenance": provenance,
        "confidence": round(confidence, 4),
        "coverage": coverage,
        "support_status": support_status,
        "support_reason": support_reason,
        "support_assessment": {
            **support_payload,
            "status": support_status,
            "reason": support_reason,
            "missing_slots": list(support_payload.get("missing_slots", missing_slots) or []),
            "grounded_slots": list(support_payload.get("grounded_slots", grounded_slots) or []),
            "route_class": route_profile["route_class"],
            "question_determinism": route_profile["question_determinism"],
            "answer_shape": answer_shape["shape"],
        },
    }


def _build_route_profile(
    *,
    question: str,
    intent: Dict[str, Any],
    semantic_context: Dict[str, Any],
    memory_payload: Dict[str, Any],
    candidate_entities: Sequence[Dict[str, Any]],
    selected_triples: Sequence[Dict[str, Any]],
    grounded_slots: Sequence[str],
    missing_slots: Sequence[str],
) -> Dict[str, Any]:
    intent_id = str(intent.get("intent_id", "")).strip()
    focus_slots = [str(slot) for slot in intent.get("focus_slots", []) if str(slot).strip()]
    source_types = _source_types_for_route(
        semantic_context=semantic_context,
        memory_payload=memory_payload,
        candidate_entities=candidate_entities,
        selected_triples=selected_triples,
    )
    question_determinism, determinism_reasons = _question_determinism(question, intent_id)

    if intent_id in {"relationship_lookup", "responsibility_lookup"}:
        route_class = "R4_GRAPH_JOIN"
    elif intent_id == "engineering_tradeoff_lookup":
        route_class = "R5_LONG_CONTEXT_REASONING"
    elif intent_id == "explanation_lookup":
        route_class = "R5_LONG_CONTEXT_REASONING" if "text" in source_types else "R1_LOOKUP"
    elif "supporting_fact" in focus_slots and "supporting_fact" in missing_slots:
        route_class = "R5_LONG_CONTEXT_REASONING"
    else:
        route_class = "R1_LOOKUP"

    if question_determinism == "deterministic":
        tool_policy = "verified_query_first" if route_class in {"R4_GRAPH_JOIN", "R5_LONG_CONTEXT_REASONING"} else "lookup_first"
    elif question_determinism == "non_deterministic":
        tool_policy = "evidence_bundle_then_synthesis"
    else:
        tool_policy = "retrieve_verify_then_synthesis"

    recommended_tools = _recommended_tools_for_route(route_class, question_determinism)
    rationale = [
        f"intent_id={intent_id or 'unknown'}",
        f"source_types={','.join(source_types) or 'unknown'}",
        f"grounded_slots={','.join(grounded_slots) or 'none'}",
        f"missing_slots={','.join(missing_slots) or 'none'}",
        *determinism_reasons[:2],
    ]

    return {
        "schema_version": "route_profile.v1",
        "route_class": route_class,
        "question_determinism": question_determinism,
        "tool_policy": tool_policy,
        "recommended_tools": recommended_tools,
        "rationale": rationale,
    }


def _source_types_for_route(
    *,
    semantic_context: Dict[str, Any],
    memory_payload: Dict[str, Any],
    candidate_entities: Sequence[Dict[str, Any]],
    selected_triples: Sequence[Dict[str, Any]],
) -> List[str]:
    source_types: List[str] = []
    metadata = memory_payload.get("metadata", {}) if isinstance(memory_payload.get("metadata"), dict) else {}
    for value in (
        memory_payload.get("source_type"),
        memory_payload.get("category"),
        metadata.get("source_type"),
        metadata.get("category"),
    ):
        cleaned = str(value or "").strip().lower()
        if cleaned and cleaned not in source_types:
            source_types.append(cleaned)
    if memory_payload and "text" not in source_types:
        source_types.append("text")
    if candidate_entities and "graph" not in source_types:
        source_types.append("graph")
    if selected_triples and "relation" not in source_types:
        source_types.append("relation")
    semantic_layer = semantic_context.get("semantic_layer")
    if isinstance(semantic_layer, dict) and "semantic_layer" not in source_types:
        source_types.append("semantic_layer")
    return source_types


def _question_determinism(question: str, intent_id: str) -> Tuple[str, List[str]]:
    normalized = question.lower()
    deterministic_hits = [hint for hint in DETERMINISTIC_QUESTION_HINTS if hint in normalized]
    nondeterministic_hits = [hint for hint in NONDETERMINISTIC_QUESTION_HINTS if hint in normalized]
    deterministic_score = min(len(deterministic_hits), 3)
    nondeterministic_score = min(len(nondeterministic_hits), 3)
    reasons: List[str] = []

    if intent_id in {"relationship_lookup", "responsibility_lookup"}:
        deterministic_score += 1
        reasons.append("relation intent can be checked against explicit graph slots")
    if intent_id in {"engineering_tradeoff_lookup", "explanation_lookup"}:
        nondeterministic_score += 1
        reasons.append("intent needs supporting evidence before synthesis")
    if any(term in normalized for term in ("how many", "count", "average", "sum", "highest", "lowest")):
        deterministic_score += 2
        reasons.append("question contains aggregation or ranking cues")
    if any(term in normalized for term in ("why", "explain", "describe", "recommend")):
        nondeterministic_score += 2
        reasons.append("question asks for explanation or judgment")

    if deterministic_score >= nondeterministic_score + 2:
        label = "deterministic"
    elif nondeterministic_score >= deterministic_score + 2:
        label = "non_deterministic"
    else:
        label = "hybrid"
    if not reasons:
        reasons.append("no dominant determinism cue")
    return label, reasons


def _recommended_tools_for_route(route_class: str, question_determinism: str) -> List[str]:
    if route_class == "R4_GRAPH_JOIN":
        tools = ["resolve_entities", "select_relation_paths", "query_graph", "verify_slot_fill"]
    elif route_class == "R5_LONG_CONTEXT_REASONING":
        tools = ["resolve_entities", "retrieve_evidence_bundle", "expand_text_evidence", "verify_slot_fill"]
    else:
        tools = ["resolve_entities", "retrieve_evidence_bundle"]
    if question_determinism == "deterministic" and "verified_answer_shape" not in tools:
        tools.append("verified_answer_shape")
    if question_determinism != "deterministic" and "grounded_synthesis" not in tools:
        tools.append("grounded_synthesis")
    return tools


def _infer_answer_shape(
    *,
    question: str,
    intent: Dict[str, Any],
    route_profile: Dict[str, Any],
    missing_slots: Sequence[str],
) -> Dict[str, Any]:
    normalized = question.lower()
    intent_id = str(intent.get("intent_id", "")).strip()
    rationale: List[str] = []
    if missing_slots:
        rationale.append("missing slots must remain visible in the final answer")
    if any(term in normalized for term in ("how many", "number of", "count of")):
        shape = "count_scalar"
        rationale.append("count cue detected")
    elif any(term in normalized for term in ("highest", "lowest", "top", "bottom", "most", "least")):
        shape = "ranked_projection"
        rationale.append("ranking cue detected")
    elif any(term in normalized for term in ("average", "avg", "sum", "total", "ratio", "percentage", "percent", "difference", "delta")):
        shape = "scalar_metric"
        rationale.append("metric cue detected")
    elif intent_id in {"relationship_lookup", "responsibility_lookup"}:
        shape = "relationship_summary"
        rationale.append("relationship intent detected")
    elif intent_id in {"engineering_tradeoff_lookup", "explanation_lookup"}:
        shape = "evidence_summary"
        rationale.append("explanatory intent detected")
    else:
        shape = "entity_summary"
        rationale.append("default entity/evidence summary")
    if missing_slots and shape not in {"count_scalar", "scalar_metric", "ranked_projection"}:
        shape = "partial_evidence_summary"
    return {
        "schema_version": "answer_shape.v1",
        "shape": shape,
        "route_class": str(route_profile.get("route_class", "")),
        "question_determinism": str(route_profile.get("question_determinism", "")),
        "rationale": rationale,
    }


def _entity_name(payload: Dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("display_name") or "").strip()


def extract_tradeoff_points_from_text(text: str) -> Dict[str, List[str]]:
    if not text:
        return {"limitation_points": [], "alternative_points": []}

    limitation_points: List[str] = []
    alternative_points: List[str] = []
    normalized_text = text.lower()

    if "gil" in normalized_text or "global interpreter lock" in normalized_text:
        limitation_points.append("GIL")

    limitation_patterns = (
        r"(?:limited by|constrained by|blocked by|bottlenecked by)\s+([^.;\n]+)",
        r"(?:limitation|constraint|bottleneck|drawback|downside)\s+(?:is|comes from|comes down to)\s+([^.;\n]+)",
        r"(?:because of|due to)\s+([^.;\n]+)",
    )
    for pattern in limitation_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            limitation_points.extend(_split_compact_points(match))

    alternative_patterns = (
        r"(?:alternatives include|alternative approaches include|consider|use|try|prefer)\s+([^.;\n]+?)(?:\s+instead)?(?:[.;\n]|$)",
        r"(?:instead use|instead try)\s+([^.;\n]+?)(?:[.;\n]|$)",
    )
    for pattern in alternative_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            alternative_points.extend(_split_compact_points(match))

    for hint in COMMON_ALTERNATIVE_TECHNIQUE_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", normalized_text):
            alternative_points.append(_canonicalize_point_text(hint))

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]
    if not limitation_points:
        for sentence in sentences:
            if any(hint in sentence.lower() for hint in LIMITATION_TEXT_HINTS):
                limitation_points.append(_compact_sentence(sentence))
                break
    if not alternative_points:
        for sentence in sentences:
            if any(hint in sentence.lower() for hint in ALTERNATIVE_TEXT_HINTS):
                alternative_points.append(_compact_sentence(sentence))
                break

    return {
        "limitation_points": _dedupe_points(limitation_points),
        "alternative_points": _dedupe_points(alternative_points),
    }


def _relation_triples_from_text(
    text: str,
    *,
    target_entity: str,
    owner_or_operator: str,
) -> List[Dict[str, Any]]:
    if not text or not target_entity or not owner_or_operator:
        return []
    normalized_text = text.lower()
    if not any(token in normalized_text for token in ("manages", "managed", "owns", "owned", "leads", "operates")):
        return []
    relation = "RELATED_TO"
    if "manages" in normalized_text or "managed" in normalized_text:
        relation = "MANAGES"
    elif "owns" in normalized_text or "owned" in normalized_text:
        relation = "OWNS"
    elif "leads" in normalized_text:
        relation = "LEADS"
    elif "operates" in normalized_text:
        relation = "OPERATES"
    return [
        {
            "source": owner_or_operator,
            "relation": relation,
            "target": target_entity,
            "target_labels": [],
            "supporting_fact": _compact_sentence(text),
        }
    ]


def extract_tradeoff_points_from_triples(
    *,
    question: str = "",
    selected_triples: Sequence[Dict[str, Any]],
) -> Dict[str, List[str]]:
    limitation_points: List[str] = []
    alternative_points: List[str] = []
    normalized_question = _normalize_symbol(question)
    prefers_parallel_alternatives = any(
        hint in normalized_question
        for hint in ("parallel", "parallelism", "workaround", "alternative", "alternatives")
    )

    for triple in selected_triples:
        if not isinstance(triple, dict):
            continue
        target = str(triple.get("target") or "").strip()
        if not target:
            continue
        relation = _normalize_symbol(str(triple.get("relation") or ""))
        raw_labels = triple.get("target_labels", [])
        labels = {
            _normalize_symbol(str(label))
            for label in raw_labels
            if str(label).strip()
        } if isinstance(raw_labels, list) else set()
        normalized_target = _normalize_symbol(target)

        if (
            relation in LIMITATION_RELATION_HINTS
            or labels & LIMITATION_LABEL_HINTS
            or any(hint in normalized_target for hint in (_normalize_symbol(item) for item in LIMITATION_TEXT_HINTS))
        ):
            limitation_points.append(target)
            continue

        if (
            relation in ALTERNATIVE_RELATION_HINTS
            or labels & ALTERNATIVE_LABEL_HINTS
            or any(hint in normalized_target for hint in (_normalize_symbol(item) for item in COMMON_ALTERNATIVE_TECHNIQUE_HINTS))
            or (prefers_parallel_alternatives and relation in {"uses", "workswith", "runson"})
        ):
            alternative_points.append(target)

    return {
        "limitation_points": _dedupe_points(limitation_points),
        "alternative_points": _dedupe_points(alternative_points),
    }


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


def _tradeoff_points_from_entities(entities: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    limitation_points: List[str] = []
    alternative_points: List[str] = []
    for entity in entities:
        entity_name = _entity_name(entity)
        if not entity_name:
            continue
        labels = entity.get("labels", [])
        normalized_labels = {
            _normalize_symbol(str(label))
            for label in labels
            if str(label).strip()
        } if isinstance(labels, list) else set()
        normalized_name = _normalize_symbol(entity_name)
        if normalized_labels & LIMITATION_LABEL_HINTS or any(
            hint in normalized_name for hint in (_normalize_symbol(item) for item in LIMITATION_TEXT_HINTS)
        ):
            limitation_points.append(entity_name)
            continue
        if normalized_labels & ALTERNATIVE_LABEL_HINTS or any(
            hint in normalized_name for hint in (_normalize_symbol(item) for item in COMMON_ALTERNATIVE_TECHNIQUE_HINTS)
        ):
            alternative_points.append(entity_name)
    return {
        "limitation_points": _dedupe_points(limitation_points),
        "alternative_points": _dedupe_points(alternative_points),
    }


def _split_compact_points(raw: str) -> List[str]:
    cleaned = re.sub(r"\s+", " ", str(raw).strip(" .,:;")).strip()
    if not cleaned:
        return []
    parts = re.split(r",|\bor\b|\band\b|/|;", cleaned, flags=re.IGNORECASE)
    return [
        _canonicalize_point_text(part)
        for part in parts
        if _canonicalize_point_text(part)
    ]


def _compact_sentence(sentence: str) -> str:
    compact = re.sub(r"\s+", " ", sentence.strip())
    if len(compact) <= 160:
        return compact
    return compact[:157].rsplit(" ", 1)[0].rstrip() + "..."


def _canonicalize_point_text(value: str) -> str:
    compact = re.sub(r"\s+", " ", str(value).strip(" .,:;")).strip()
    if not compact:
        return ""
    if compact.lower() in {"gil", "the gil", "global interpreter lock"}:
        return "GIL"
    if compact.lower() == "ray":
        return "Ray"
    return compact


def _dedupe_points(values: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    results: List[str] = []
    for value in values:
        canonical = _canonicalize_point_text(value)
        key = _normalize_symbol(canonical)
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(canonical)
    return results[:5]


def _dedupe_triples(triples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[tuple[str, str, str]] = set()
    results: List[Dict[str, Any]] = []
    for triple in triples:
        if not isinstance(triple, dict):
            continue
        source = str(triple.get("source", "") or "").strip()
        relation = str(triple.get("relation", "") or "").strip()
        target = str(triple.get("target", "") or "").strip()
        if not source or not relation or not target:
            continue
        key = (source.lower(), relation.upper(), target.lower())
        if key in seen:
            continue
        seen.add(key)
        results.append(dict(triple))
    return results[:10]


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())
