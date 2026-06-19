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


# ---------------------------------------------------------------------------
# Answer-accuracy evaluation (ADR-0122/0123): conformance is NOT a safe proxy
# for answer quality — they can move in opposite directions — so the eval
# surface must measure actual answer correctness over a gold QA set, with an
# ontology injected as the extraction guardrail.
# ---------------------------------------------------------------------------

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


@dataclass(slots=True)
class AnswerCase:
    """One gold QA case: a question + expected answer, with the source context to
    extract/answer from and an optional category for per-segment breakdown."""

    question: str
    gold_answer: str
    context: str = ""
    category: str = ""
    case_id: str = ""


def load_answer_cases(path: str) -> List[AnswerCase]:
    """Load gold QA cases from a JSON file (a list of objects). Each object needs
    ``question`` and ``gold_answer``; ``context``/``category``/``case_id`` are
    optional and default to empty. Pure/offline — no backend involved."""
    import json
    from pathlib import Path

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"answer-cases file must be a JSON list, got {type(data).__name__}")
    cases: List[AnswerCase] = []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            raise ValueError(f"answer-case #{i} must be a JSON object, got {type(obj).__name__}")
        cases.append(AnswerCase(
            question=str(obj.get("question", "")),
            gold_answer=str(obj.get("gold_answer", "")),
            context=str(obj.get("context", "")),
            category=str(obj.get("category", "")),
            case_id=str(obj.get("case_id", "")),
        ))
    return cases


@dataclass(slots=True)
class AnswerAccuracyReport:
    n_scored: int
    accuracy: float
    by_category: Dict[str, float] = field(default_factory=dict)
    by_category_n: Dict[str, int] = field(default_factory=dict)
    errors: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_scored": self.n_scored, "accuracy": self.accuracy,
            "by_category": dict(self.by_category), "by_category_n": dict(self.by_category_n),
            "errors": self.errors, "results": list(self.results),
        }


_ANS_SYS = ("You are a financial analyst. Use ONLY the entity/relationship types in the provided "
            "ontology to extract the relevant facts, then answer the question. Return ONLY JSON.")
_JUDGE_SYS = "You grade answers. Return ONLY JSON."
_JUDGE_USER = ('QUESTION: {q}\nGOLD: {gold}\nMODEL ANSWER: {ans}\n'
               'Is the model answer correct vs gold (same entity/number/fact, allowing phrasing/'
               'rounding)? Return JSON {{"correct": true|false}}')


def _ans_user(ontology: "Ontology", context: str, question: str) -> str:
    ctx = ontology.to_extraction_context()
    return (f"ONTOLOGY ENTITY TYPES:\n{ctx.get('entity_types','')}\n\n"
            f"ONTOLOGY RELATIONSHIP TYPES:\n{ctx.get('relationship_types','')}\n\n"
            f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\n"
            'Return JSON: {"facts":[{"label":"...","name":"...","value":"..."}],"answer":"..."}')


def _eval_retry(fn, *, attempts: int = 5, base: float = 2.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e).lower()
            if "429" in msg or "rate limit" in msg or "timeout" in msg or "temporarily" in msg:
                time.sleep(base * (2 ** i))
                continue
            raise
    raise last


def evaluate_answer_accuracy(
    backend: Any,
    ontology: "Ontology",
    cases: Sequence[AnswerCase],
    *,
    judge_backend: Optional[Any] = None,
    model: Optional[str] = None,
    max_chars: int = 3500,
    workers: int = 6,
) -> AnswerAccuracyReport:
    """Measure answer accuracy over a gold QA set with ``ontology`` as the
    extraction guardrail. For each case: extract facts + answer (robustly, via the
    provider-aware structured layer), then LLM-judge the answer vs gold. Returns
    overall + per-category accuracy. ``backend``/``judge_backend`` follow the
    SEOCHO ``LLMBackend`` contract; injected, so this is testable with fakes.

    Concurrency is bounded by ``workers`` with 429-retry (MARA rate-limits at high
    concurrency — see ADR-0122)."""
    from .llm_structured import StructuredOutputError, structured_complete

    judge = judge_backend or backend
    mdl = model or getattr(backend, "model", "")
    done = {"n": 0}
    lock = Lock()

    def run(case: AnswerCase) -> Dict[str, Any]:
        out: Dict[str, Any] = {"case_id": case.case_id, "category": case.category}
        try:
            ex = _eval_retry(lambda: structured_complete(
                backend, system=_ANS_SYS, user=_ans_user(ontology, case.context[:max_chars], case.question),
                model=mdl, task_hint="json_extraction"))
            ans = str(ex.get("answer", "")) if isinstance(ex, dict) else ""
            facts = ex.get("facts", []) if isinstance(ex, dict) else []
            labels = [str(f.get("label", "")) for f in facts if isinstance(f, dict)]
            out["label_conformance"] = (
                round(sum(1 for l in labels if l in ontology.nodes) / len(labels), 4) if labels else 0.0)
            jc = _eval_retry(lambda: structured_complete(
                judge, system=_JUDGE_SYS,
                user=_JUDGE_USER.format(q=case.question, gold=case.gold_answer, ans=ans), model=mdl))
            out["correct"] = bool(jc.get("correct")) if isinstance(jc, dict) else False
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
        return out

    if workers > 1 and len(cases) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, cases))
    else:
        results = [run(c) for c in cases]

    scored = [r for r in results if "correct" in r]
    errors = sum(1 for r in results if "error" in r)
    by_cat: Dict[str, List[int]] = {}
    for r in scored:
        by_cat.setdefault(r.get("category", ""), []).append(1 if r["correct"] else 0)
    return AnswerAccuracyReport(
        n_scored=len(scored),
        accuracy=round(mean([1 if r["correct"] else 0 for r in scored]), 4) if scored else 0.0,
        by_category={c: round(mean(v), 4) for c, v in by_cat.items() if v},
        by_category_n={c: len(v) for c, v in by_cat.items()},
        errors=errors, results=results,
    )


def compare_guardrails_by_answer(
    backend: Any,
    ontologies: Dict[str, "Ontology"],
    cases: Sequence[AnswerCase],
    **kwargs: Any,
) -> Dict[str, AnswerAccuracyReport]:
    """Answer-accuracy per candidate guardrail — the reusable form of the FinDER
    answer matrix (ADR-0122). Pairs with the offline guardrail selector
    (``seocho.guardrail_selector``): the selector picks offline, this validates the
    pick against gold answers."""
    return {name: evaluate_answer_accuracy(backend, onto, cases, **kwargs)
            for name, onto in ontologies.items()}
