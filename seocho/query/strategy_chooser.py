from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from .contracts import CypherPlan, InsufficiencyAssessment


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


class IntentSupportValidator:
    """Estimate whether a graph target can likely satisfy the requested intent."""

    def assess_candidate(
        self,
        *,
        question_entity: str,
        candidate: Dict[str, Any],
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        preview_bundle: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        required_relations = [str(item).strip() for item in intent.get("required_relations", []) if str(item).strip()]
        required_entity_types = [str(item).strip() for item in intent.get("required_entity_types", []) if str(item).strip()]
        focus_slots = [str(item).strip() for item in intent.get("focus_slots", []) if str(item).strip()]
        preview_bundle = preview_bundle if isinstance(preview_bundle, dict) else {}

        candidate_labels = [str(label).strip() for label in candidate.get("labels", []) if str(label).strip()]
        matched_relations = self._matched_relations(required_relations, constraint_slice)
        non_generic_entity_types = [item for item in required_entity_types if _normalize_symbol(item) not in {"", "entity"}]
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
        preflight: Dict[str, Any] | None,
        intent: Dict[str, Any],
        bundle: Dict[str, Any],
        assessment: InsufficiencyAssessment,
        plan: CypherPlan,
        constraint_slice: Dict[str, Any],
    ) -> Dict[str, Any]:
        focus_slots = [str(item).strip() for item in intent.get("focus_slots", []) if str(item).strip()]
        grounded_slots = {str(item).strip() for item in bundle.get("grounded_slots", []) if str(item).strip()}
        selected_triples = bundle.get("selected_triples", [])
        matched_relations = []
        for triple in selected_triples:
            if not isinstance(triple, dict):
                continue
            relation = str(triple.get("relation", "")).strip()
            if relation and relation not in matched_relations:
                matched_relations.append(relation)

        coverage = round(len(grounded_slots & set(focus_slots)) / max(1, len(focus_slots)), 4) if focus_slots else 1.0

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
        focus_slots = [str(item).strip() for item in intent.get("focus_slots", []) if str(item).strip()]
        return {
            "schema_version": "intent_support.v1",
            "intent_id": str(intent.get("intent_id", "")).strip(),
            "supported": False,
            "status": "unsupported",
            "reason": "no_entity_match",
            "coverage": 0.0,
            "confidence": 0.0,
            "required_relations": [str(item).strip() for item in intent.get("required_relations", []) if str(item).strip()],
            "matched_relations": [],
            "required_entity_types": [str(item).strip() for item in intent.get("required_entity_types", []) if str(item).strip()],
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
        cross_graph_analysis: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        support_status = str(support_assessment.get("status", "unsupported")).strip() or "unsupported"
        cross_graph_analysis = cross_graph_analysis if isinstance(cross_graph_analysis, dict) else {}
        if route == "rdf":
            initial_mode = "rdf"
            reason = "question matched RDF-oriented cues"
        elif reasoning_mode or repair_budget > 0:
            initial_mode = "semantic_repair"
            reason = "bounded repair was explicitly requested"
        elif support_status == "supported":
            initial_mode = "semantic_direct"
            reason = "intent support is available for the selected graph scope"
        elif graph_count > 1 and cross_graph_analysis.get("recommended_advanced"):
            initial_mode = "semantic_direct"
            reason = "cross-graph disagreement is detectable, but semantic mode stays the first pass"
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
            "cross_graph_analysis": cross_graph_analysis,
        }

    def finalize(
        self,
        *,
        initial_decision: Dict[str, Any],
        route: str,
        graph_count: int,
        support_assessment: Dict[str, Any],
        reasoning: Dict[str, Any] | None,
        cross_graph_analysis: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        decision = dict(initial_decision or {})
        reasoning = reasoning if isinstance(reasoning, dict) else {}
        cross_graph_analysis = cross_graph_analysis if isinstance(cross_graph_analysis, dict) else {}
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
        if cross_graph_analysis.get("recommended_advanced"):
            advanced_debate_recommended = True
            next_mode_hint = "advanced"
            sdk_hint = "Use client.plan(...).advanced().run() when graph scopes disagree materially."
        elif not support_assessment.get("supported", False):
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
                "cross_graph_analysis": cross_graph_analysis,
            }
        )
        return decision
