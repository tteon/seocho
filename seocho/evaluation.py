from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from .models import DebateRunResponse, SearchResponse, SemanticRunResponse


@dataclass(slots=True)
class ManualGoldCase:
    case_id: str
    question: str
    graph_ids: List[str] = field(default_factory=list)
    databases: List[str] = field(default_factory=list)
    expected_intent_id: str = ""
    required_slots: Dict[str, Any] = field(default_factory=dict)
    preferred_relations: List[str] = field(default_factory=list)
    repair_budget: int = 2
    include_advanced: bool = False


@dataclass(slots=True)
class EvaluationBaselineResult:
    baseline: str
    response: str
    route: str = ""
    intent_id: str = ""
    support_status: str = ""
    intent_match: float = 0.0
    support_rate: float = 0.0
    required_answer_slot_coverage_manual: float = 0.0
    preferred_evidence_hit_rate: float = 0.0
    grounded_slots: List[str] = field(default_factory=list)
    missing_slots: List[str] = field(default_factory=list)
    selected_relations: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationCaseResult:
    case_id: str
    question: str
    baselines: List[EvaluationBaselineResult] = field(default_factory=list)

    def by_baseline(self) -> Dict[str, EvaluationBaselineResult]:
        return {item.baseline: item for item in self.baselines}


@dataclass(slots=True)
class EvaluationMatrixSummary:
    cases: List[EvaluationCaseResult] = field(default_factory=list)
    aggregate_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)


class SemanticEvaluationHarness:
    """Evaluate SEOCHO retrieval modes using manual gold support metrics."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def run_case(
        self,
        case: ManualGoldCase,
        *,
        include_advanced: Optional[bool] = None,
    ) -> EvaluationCaseResult:
        results: List[EvaluationBaselineResult] = []
        run_advanced = case.include_advanced if include_advanced is None else include_advanced

        results.append(self._question_only_baseline(case))
        results.append(self._reference_only_baseline(case))

        semantic_direct = self.client.semantic(
            case.question,
            graph_ids=case.graph_ids or None,
            databases=case.databases or None,
            reasoning_mode=False,
            repair_budget=0,
        )
        results.append(self._semantic_baseline("semantic_direct", case, semantic_direct))

        semantic_repair = self.client.semantic(
            case.question,
            graph_ids=case.graph_ids or None,
            databases=case.databases or None,
            reasoning_mode=True,
            repair_budget=max(0, int(case.repair_budget or 0)),
        )
        results.append(self._semantic_baseline("semantic_repair", case, semantic_repair))

        if run_advanced:
            debate_result = self.client.advanced(
                case.question,
                graph_ids=case.graph_ids or None,
            )
            results.append(self._advanced_baseline(case, debate_result))

        return EvaluationCaseResult(case_id=case.case_id, question=case.question, baselines=results)

    def run_matrix(
        self,
        cases: Sequence[ManualGoldCase],
        *,
        include_advanced: bool = False,
    ) -> EvaluationMatrixSummary:
        case_results = [self.run_case(case, include_advanced=include_advanced) for case in cases]
        aggregates: Dict[str, Dict[str, List[float]]] = {}

        for case_result in case_results:
            for baseline_result in case_result.baselines:
                bucket = aggregates.setdefault(
                    baseline_result.baseline,
                    {
                        "intent_match_rate": [],
                        "support_rate": [],
                        "required_answer_slot_coverage_manual": [],
                        "preferred_evidence_hit_rate": [],
                    },
                )
                bucket["intent_match_rate"].append(baseline_result.intent_match)
                bucket["support_rate"].append(baseline_result.support_rate)
                bucket["required_answer_slot_coverage_manual"].append(
                    baseline_result.required_answer_slot_coverage_manual
                )
                bucket["preferred_evidence_hit_rate"].append(
                    baseline_result.preferred_evidence_hit_rate
                )

        return EvaluationMatrixSummary(
            cases=case_results,
            aggregate_metrics={
                baseline: {
                    metric_name: round(mean(values), 4) if values else 0.0
                    for metric_name, values in metric_map.items()
                }
                for baseline, metric_map in aggregates.items()
            },
        )

    @staticmethod
    def _question_only_baseline(case: ManualGoldCase) -> EvaluationBaselineResult:
        return EvaluationBaselineResult(
            baseline="question_only_baseline",
            response=case.question,
            route="question_only",
            intent_id="",
            support_status="unsupported",
            intent_match=0.0,
            support_rate=0.0,
            required_answer_slot_coverage_manual=0.0,
            preferred_evidence_hit_rate=0.0,
            metadata={"mode": "question_only"},
        )

    def _reference_only_baseline(self, case: ManualGoldCase) -> EvaluationBaselineResult:
        response: SearchResponse = self.client.search_with_context(
            case.question,
            graph_ids=case.graph_ids or None,
            databases=case.databases or None,
        )
        top_result = response.results[0] if response.results else None
        evidence_bundle = top_result.evidence_bundle if top_result else {}
        return self._build_baseline_result(
            baseline="reference_only_baseline",
            case=case,
            response_text=top_result.content_preview if top_result else "",
            route="reference_only",
            intent_id=str(evidence_bundle.get("intent_id", "")),
            support_status=str(evidence_bundle.get("support_assessment", {}).get("status", "")),
            evidence_bundle=evidence_bundle,
            metadata={
                "result_count": len(response.results),
                "semantic_entities": list(response.semantic_context.get("entities", [])),
            },
        )

    def _semantic_baseline(
        self,
        baseline: str,
        case: ManualGoldCase,
        result: SemanticRunResponse,
    ) -> EvaluationBaselineResult:
        return self._build_baseline_result(
            baseline=baseline,
            case=case,
            response_text=result.response,
            route=result.route,
            intent_id=str(result.support.intent_id or result.semantic_context.get("intent", {}).get("intent_id", "")),
            support_status=result.support.status,
            evidence_bundle=result.evidence.to_dict(),
            metadata={
                "next_mode_hint": result.strategy.next_mode_hint,
                "advanced_debate_recommended": result.strategy.advanced_debate_recommended,
                "run_id": result.run_record.run_id,
            },
        )

    def _advanced_baseline(
        self,
        case: ManualGoldCase,
        result: DebateRunResponse,
    ) -> EvaluationBaselineResult:
        return EvaluationBaselineResult(
            baseline="advanced_debate",
            response=result.response,
            route="debate",
            intent_id=case.expected_intent_id,
            support_status="review_required",
            intent_match=1.0 if case.expected_intent_id else 0.0,
            support_rate=0.0,
            required_answer_slot_coverage_manual=0.0,
            preferred_evidence_hit_rate=0.0,
            metadata={
                "debate_state": result.debate_state,
                "degraded": result.degraded,
                "agent_count": len(result.agent_statuses),
            },
        )

    def _build_baseline_result(
        self,
        *,
        baseline: str,
        case: ManualGoldCase,
        response_text: str,
        route: str,
        intent_id: str,
        support_status: str,
        evidence_bundle: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> EvaluationBaselineResult:
        slot_fills = dict(evidence_bundle.get("slot_fills", {}))
        grounded_slots = [str(item) for item in evidence_bundle.get("grounded_slots", [])]
        missing_slots = [str(item) for item in evidence_bundle.get("missing_slots", [])]
        selected_relations = self._extract_relations(evidence_bundle)
        return EvaluationBaselineResult(
            baseline=baseline,
            response=response_text,
            route=route,
            intent_id=intent_id,
            support_status=support_status,
            intent_match=1.0 if case.expected_intent_id and intent_id == case.expected_intent_id else 0.0,
            support_rate=1.0 if support_status == "supported" else 0.0,
            required_answer_slot_coverage_manual=self._required_slot_coverage(case.required_slots, slot_fills),
            preferred_evidence_hit_rate=self._preferred_evidence_hit_rate(case.preferred_relations, selected_relations),
            grounded_slots=grounded_slots,
            missing_slots=missing_slots,
            selected_relations=selected_relations,
            metadata=metadata,
        )

    @staticmethod
    def _required_slot_coverage(required_slots: Dict[str, Any], slot_fills: Dict[str, Any]) -> float:
        if not required_slots:
            return 0.0
        hits = 0
        total = 0
        for slot_name, expected_value in required_slots.items():
            total += 1
            actual_value = slot_fills.get(slot_name)
            if _slot_matches(expected_value, actual_value):
                hits += 1
        return round(hits / max(1, total), 4)

    @staticmethod
    def _preferred_evidence_hit_rate(
        preferred_relations: Sequence[str],
        selected_relations: Sequence[str],
    ) -> float:
        normalized_expected = {_normalize_token(item) for item in preferred_relations if _normalize_token(item)}
        if not normalized_expected:
            return 0.0
        normalized_actual = {_normalize_token(item) for item in selected_relations if _normalize_token(item)}
        hits = len(normalized_expected & normalized_actual)
        return round(hits / max(1, len(normalized_expected)), 4)

    @staticmethod
    def _extract_relations(evidence_bundle: Dict[str, Any]) -> List[str]:
        relations = [
            str(item).strip()
            for item in evidence_bundle.get("slot_fills", {}).get("relation_paths", [])
            if str(item).strip()
        ]
        for triple in evidence_bundle.get("selected_triples", []):
            if not isinstance(triple, dict):
                continue
            relation = str(triple.get("relation", "")).strip()
            if relation:
                relations.append(relation)
        deduped: List[str] = []
        seen = set()
        for relation in relations:
            normalized = _normalize_token(relation)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(relation)
        return deduped


def _slot_matches(expected_value: Any, actual_value: Any) -> bool:
    if expected_value in (None, True):
        return bool(_flatten_values(actual_value))
    expected_tokens = _flatten_values(expected_value)
    actual_tokens = _flatten_values(actual_value)
    if not expected_tokens:
        return bool(actual_tokens)
    if not actual_tokens:
        return False
    for expected in expected_tokens:
        if not any(expected in actual or actual in expected for actual in actual_tokens):
            return False
    return True


def _flatten_values(value: Any) -> List[str]:
    flattened: List[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            flattened.extend(_flatten_values(key))
            flattened.extend(_flatten_values(nested))
        return flattened
    if isinstance(value, (list, tuple, set)):
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    normalized = _normalize_token(value)
    if normalized:
        flattened.append(normalized)
    return flattened


def _normalize_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.split())
