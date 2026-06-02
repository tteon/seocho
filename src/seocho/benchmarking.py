from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Sequence


_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)(?P<percent>%?)\s*"
    r"(?P<unit>thousand|million|billion|trillion)?"
)
_UNIT_MULTIPLIERS = {
    "thousand": Decimal("1000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "trillion": Decimal("1000000000000"),
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "through",
    "to",
    "was",
    "were",
    "what",
    "which",
    "with",
}
_FINDER_INDEXING_FINDINGS = {
    "indexing_no_graph_writes",
    "source_text_has_answer_but_graph_projection_lost_it",
}
_FINDER_QUERY_FINDINGS = {
    "query_no_graph_records",
    "query_execution_failed_or_contract_error",
    "vector_substrate_not_in_local_answer_path",
    "fulltext_substrate_unavailable_or_unchecked",
    "answer_quality_or_slot_selection_gap",
    "support_claim_answer_mismatch",
}
_FINDER_BEGINNER_CATEGORIES = {
    "Accounting",
    "Company Overview",
    "Governance",
    "Risk",
    "Shareholder Return",
}
_FINDER_ADVANCED_REASONING_TYPES = {"Compositional", "Subtraction"}
_FINANCE_BENCHMARK_INDEXING_FINDINGS = {
    "indexing_no_graph_writes",
    "source_text_has_answer_but_graph_projection_lost_it",
}
_FINANCE_BENCHMARK_QUERY_FINDINGS = {
    "query_no_graph_records",
    "query_execution_failed_or_contract_error",
    "vector_substrate_not_in_local_answer_path",
    "fulltext_substrate_unavailable_or_unchecked",
    "answer_quality_or_slot_selection_gap",
}


@dataclass(slots=True)
class FinanceBenchmarkCase:
    case_id: str
    text: str
    question: str
    expected_answer: str
    category: str
    reasoning_type: str = ""


@dataclass(slots=True)
class FinDERBenchmarkCase:
    case_id: str
    text: str
    question: str
    expected_answer: str
    category: str
    reasoning_type: str = ""


@dataclass(slots=True)
class FinDERBenchmarkRecord:
    case_id: str
    category: str
    question: str
    add_latency_ms: float
    ask_latency_ms: float
    answer: str
    expected_answer: str
    exact_match: bool
    contains_match: bool
    nodes_created: int = 0
    relationships_created: int = 0
    fallback_used: bool = False
    deduplicated: bool = False
    reasoning_cycle_status: str = ""
    reasoning_cycle_sources: List[str] = field(default_factory=list)
    route: str = ""
    support_status: str = ""
    support_coverage: float = 0.0
    missing_slots: List[str] = field(default_factory=list)
    evidence_bundle_size: int = 0
    trace_step_count: int = 0
    tool_call_count: int = 0
    reasoning_attempt_count: int = 0
    semantic_reused: bool = False
    debate_state: str = ""
    token_usage: Dict[str, Any] = field(default_factory=dict)
    support_answer_gap: bool = False
    diagnosis: List[str] = field(default_factory=list)
    latency_breakdown_ms: Dict[str, float] = field(default_factory=dict)
    retrieval_latency_ms: float = 0.0
    generation_latency_ms: float = 0.0
    evidence_coverage: float = 0.0
    slot_metrics: Dict[str, Any] = field(default_factory=dict)
    agent_pattern: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class FinDERBenchmarkSummary:
    mode: str
    dataset: str
    record_count: int
    add_latency_p50_ms: float
    add_latency_p95_ms: float
    ask_latency_p50_ms: float
    ask_latency_p95_ms: float
    exact_match_rate: float
    contains_match_rate: float
    avg_nodes_created: float
    avg_relationships_created: float
    failure_count: int
    retrieval_latency_p50_ms: float = 0.0
    retrieval_latency_p95_ms: float = 0.0
    generation_latency_p50_ms: float = 0.0
    generation_latency_p95_ms: float = 0.0
    avg_evidence_coverage: float = 0.0
    agent_pattern_counts: Dict[str, int] = field(default_factory=dict)
    reasoning_cycle_status_counts: Dict[str, int] = field(default_factory=dict)
    reasoning_cycle_source_counts: Dict[str, int] = field(default_factory=dict)
    route_counts: Dict[str, int] = field(default_factory=dict)
    support_status_counts: Dict[str, int] = field(default_factory=dict)
    debate_state_counts: Dict[str, int] = field(default_factory=dict)
    missing_slot_counts: Dict[str, int] = field(default_factory=dict)
    semantic_reuse_count: int = 0
    support_answer_gap_count: int = 0
    support_answer_gap_rate: float = 0.0
    diagnosis_counts: Dict[str, int] = field(default_factory=dict)
    avg_trace_step_count: float = 0.0
    avg_tool_call_count: float = 0.0
    avg_reasoning_attempt_count: float = 0.0
    avg_evidence_bundle_size: float = 0.0
    avg_total_tokens_est: float = 0.0
    records: List[FinDERBenchmarkRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["records"] = [asdict(record) for record in self.records]
        return payload


@dataclass(slots=True)
class FinanceBenchmarkRecord:
    case_id: str
    category: str
    add_latency_ms: float
    ask_latency_ms: float
    answer: str
    expected_answer: str
    exact_match: bool
    contains_match: bool
    nodes_created: int = 0
    relationships_created: int = 0
    fallback_used: bool = False
    deduplicated: bool = False
    error: str = ""


@dataclass(slots=True)
class FinanceBenchmarkSummary:
    mode: str
    dataset: str
    record_count: int
    add_latency_p50_ms: float
    add_latency_p95_ms: float
    ask_latency_p50_ms: float
    ask_latency_p95_ms: float
    exact_match_rate: float
    contains_match_rate: float
    avg_nodes_created: float
    avg_relationships_created: float
    failure_count: int
    records: List[FinanceBenchmarkRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["records"] = [asdict(record) for record in self.records]
        return payload


def split_finder_diagnosis(findings: Sequence[str]) -> Dict[str, List[str]]:
    """Split FinDER diagnosis codes into indexing vs query contracts."""

    contracts = {"indexing": [], "query": [], "shared": []}
    seen: set[str] = set()
    for raw in findings:
        finding = str(raw or "").strip()
        if not finding or finding in seen:
            continue
        seen.add(finding)
        if finding in _FINDER_INDEXING_FINDINGS:
            contracts["indexing"].append(finding)
        elif finding in _FINDER_QUERY_FINDINGS:
            contracts["query"].append(finding)
        else:
            contracts["shared"].append(finding)
    return contracts


def summarize_finder_contract_findings(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Aggregate split FinDER findings across records."""

    summary: Dict[str, Dict[str, Any]] = {
        "indexing": {"record_count": 0, "finding_counts": {}},
        "query": {"record_count": 0, "finding_counts": {}},
        "shared": {"record_count": 0, "finding_counts": {}},
    }
    for record in records:
        split = split_finder_diagnosis(record.get("diagnosis", []))
        for contract, findings in split.items():
            if findings:
                summary[contract]["record_count"] += 1
            for finding in findings:
                counts = summary[contract]["finding_counts"]
                counts[finding] = int(counts.get(finding, 0)) + 1
    return summary


def classify_finder_scenario(case: FinDERBenchmarkCase) -> str:
    """Map FinDER cases onto beginner vs advanced demo slices.

    Beginner: mostly single-hop qualitative lookups.
    Advanced: compositional, subtraction, legal synthesis, or finance-slot cases.
    """

    category = str(case.category or "").strip()
    reasoning_type = str(case.reasoning_type or "").strip()
    if reasoning_type in _FINDER_ADVANCED_REASONING_TYPES:
        return "advanced"
    if category == "Legal":
        return "advanced"
    if category == "Financials":
        return "advanced"
    if category in _FINDER_BEGINNER_CATEGORIES:
        return "beginner"
    return "advanced"


def filter_finder_cases(
    cases: Sequence[FinDERBenchmarkCase],
    scenario: str = "all",
) -> List[FinDERBenchmarkCase]:
    """Return a deterministic FinDER subset for demo and regression runs."""

    normalized = str(scenario or "all").strip().lower()
    if normalized in {"", "all"}:
        return list(cases)
    if normalized not in {"beginner", "advanced"}:
        raise ValueError(f"Unknown FinDER scenario '{scenario}'.")
    return [case for case in cases if classify_finder_scenario(case) == normalized]


def load_finder_cases(path: str | Path) -> List[FinDERBenchmarkCase]:
    raw = json.loads(Path(path).read_text())
    return [
        FinDERBenchmarkCase(
            case_id=str(item["id"]),
            text=str(item["text"]),
            question=str(item["question"]),
            expected_answer=str(item.get("expected_answer", "")),
            category=str(item.get("category", "general")),
            reasoning_type=str(item.get("reasoning_type", "")),
        )
        for item in raw
    ]


def split_finance_diagnosis(findings: Sequence[str]) -> Dict[str, List[str]]:
    """Split finance benchmark diagnosis codes into indexing vs query contracts."""

    contracts = {"indexing": [], "query": [], "shared": []}
    seen: set[str] = set()
    for raw in findings:
        finding = str(raw or "").strip()
        if not finding or finding in seen:
            continue
        seen.add(finding)
        if finding in _FINANCE_BENCHMARK_INDEXING_FINDINGS:
            contracts["indexing"].append(finding)
        elif finding in _FINANCE_BENCHMARK_QUERY_FINDINGS:
            contracts["query"].append(finding)
        else:
            contracts["shared"].append(finding)
    return contracts


def summarize_finance_contract_findings(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Aggregate split finance benchmark findings across records.

    Each input record is expected to expose ``case_id`` and ``diagnosis``.
    Unknown finding codes remain visible under the ``shared`` contract bucket.
    """

    summary: Dict[str, Dict[str, Any]] = {
        "indexing": {"record_count": 0, "finding_counts": {}},
        "query": {"record_count": 0, "finding_counts": {}},
        "shared": {"record_count": 0, "finding_counts": {}},
    }
    for record in records:
        split = split_finance_diagnosis(record.get("diagnosis", []))
        for contract, findings in split.items():
            if findings:
                summary[contract]["record_count"] += 1
            for finding in findings:
                counts = summary[contract]["finding_counts"]
                counts[finding] = int(counts.get(finding, 0)) + 1
    return summary


def load_finance_cases(path: str | Path) -> List[FinanceBenchmarkCase]:
    raw = json.loads(Path(path).read_text())
    return [
        FinanceBenchmarkCase(
            case_id=str(item["id"]),
            text=str(item["text"]),
            question=str(item["question"]),
            expected_answer=str(item.get("expected_answer", "")),
            category=str(item.get("category", "general")),
            reasoning_type=str(item.get("reasoning_type", "")),
        )
        for item in raw
    ]


def normalize_answer(text: str) -> str:
    lowered = text.lower().strip()
    lowered = _NON_ALNUM_RE.sub(" ", lowered)
    lowered = _SPACE_RE.sub(" ", lowered)
    return lowered.strip()


def compare_answers(expected: str, actual: str) -> tuple[bool, bool]:
    norm_expected = normalize_answer(expected)
    norm_actual = normalize_answer(actual)
    if not norm_expected or not norm_actual:
        return False, False
    exact = norm_expected == norm_actual
    contains = (
        norm_expected in norm_actual
        or norm_actual in norm_expected
        or _slot_contains_match(expected, actual)
    )
    return exact, contains


def score_answer_slots(expected: str, actual: str) -> Dict[str, Any]:
    """Return slot-level answer diagnostics for finance-style QA.

    Exact/contains match hides the failure mode we care about in FinDER:
    whether the model retrieved and preserved the right numbers, periods, and
    domain tokens. This helper is intentionally heuristic and deterministic so
    local benchmark artifacts can be compared without an LLM judge.
    """

    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    expected_years = set(_FOUR_DIGIT_YEAR_RE.findall(str(expected)))
    actual_years = set(_FOUR_DIGIT_YEAR_RE.findall(str(actual)))
    expected_numbers = _numeric_slots(expected) - expected_years
    actual_numbers = _numeric_slots(actual) - actual_years
    token_overlap = expected_tokens & actual_tokens
    token_recall = round(len(token_overlap) / max(1, len(expected_tokens)), 4)
    numeric_recall = round(
        len(expected_numbers & actual_numbers) / max(1, len(expected_numbers)),
        4,
    ) if expected_numbers else 1.0
    period_recall = round(
        len(expected_years & actual_years) / max(1, len(expected_years)),
        4,
    ) if expected_years else 1.0
    return {
        "token_recall": token_recall,
        "numeric_recall": numeric_recall,
        "period_recall": period_recall,
        "numeric_slots_match": expected_numbers.issubset(actual_numbers) if expected_numbers else True,
        "period_slots_match": expected_years.issubset(actual_years) if expected_years else True,
        "expected_numeric_slots": sorted(expected_numbers),
        "actual_numeric_slots": sorted(actual_numbers),
        "expected_period_slots": sorted(expected_years),
        "actual_period_slots": sorted(actual_years),
    }


def _slot_contains_match(expected: str, actual: str) -> bool:
    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return False

    actual_numbers = _numeric_slots(actual)
    expected_number_groups = _numeric_slot_groups(expected)
    if expected_number_groups and not all(
        group.intersection(actual_numbers) for group in expected_number_groups
    ):
        return False

    overlap = expected_tokens & actual_tokens
    recall = len(overlap) / len(expected_tokens)
    if expected_number_groups and len(expected_tokens) <= 12:
        return recall >= 0.52
    return recall >= 0.72


def _meaningful_tokens(text: str) -> set[str]:
    tokens = set(normalize_answer(text).split())
    return {
        token
        for token in tokens
        if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    }


def _numeric_slots(text: str) -> set[str]:
    return {slot for group in _numeric_slot_groups(text) for slot in group}


def _numeric_slot_groups(text: str) -> List[set[str]]:
    normalized = str(text).lower().replace(",", "").replace("$", "")
    groups: List[set[str]] = []
    for match in _NUMBER_WITH_UNIT_RE.finditer(normalized):
        raw_number = match.group("number")
        unit = match.group("unit")
        slots = {raw_number}
        if unit and not match.group("percent"):
            scaled = Decimal(raw_number) * _UNIT_MULTIPLIERS[unit]
            slots.add(str(int(scaled)) if scaled == scaled.to_integral_value() else str(scaled.normalize()))
        groups.append(slots)
    return groups


def _percentile_ms(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return round(float(ordered[index]), 2)


def diagnose_finder_query_contract(
    *,
    error: str = "",
    contains_match: bool = False,
    support_status: str = "",
    missing_slots: Sequence[str] = (),
    evidence_bundle_size: int = 0,
    trace_step_count: int = 0,
) -> List[str]:
    """Classify FinDER query failures into stable engineering diagnosis codes."""

    findings: List[str] = []
    if str(error or "").strip():
        findings.append("query_execution_failed_or_contract_error")
        return findings

    normalized_support = str(support_status or "").strip().lower()
    if normalized_support == "supported" and not contains_match:
        findings.append("support_claim_answer_mismatch")
        findings.append("answer_quality_or_slot_selection_gap")
    elif not contains_match:
        findings.append("answer_quality_or_slot_selection_gap")

    if normalized_support in {"partial", "unsupported"} and missing_slots:
        findings.append("answer_quality_or_slot_selection_gap")
    if evidence_bundle_size == 0 and trace_step_count > 0:
        findings.append("query_no_graph_records")

    deduped: List[str] = []
    for finding in findings:
        if finding not in deduped:
            deduped.append(finding)
    return deduped


def summarize_finder_records(
    *,
    mode: str,
    dataset: str,
    records: Sequence[FinDERBenchmarkRecord],
) -> FinDERBenchmarkSummary:
    add_latencies = [record.add_latency_ms for record in records]
    ask_latencies = [record.ask_latency_ms for record in records]
    exact_hits = sum(1 for record in records if record.exact_match)
    contains_hits = sum(1 for record in records if record.contains_match)
    nodes = [record.nodes_created for record in records]
    rels = [record.relationships_created for record in records]
    failures = sum(1 for record in records if record.error)
    retrieval_latencies = [record.retrieval_latency_ms for record in records if record.retrieval_latency_ms > 0]
    generation_latencies = [record.generation_latency_ms for record in records if record.generation_latency_ms > 0]
    coverages = [record.evidence_coverage for record in records if record.evidence_coverage > 0]
    support_status_counts: Dict[str, int] = {}
    missing_slot_counts: Dict[str, int] = {}
    agent_pattern_counts: Dict[str, int] = {}
    reasoning_status_counts: Dict[str, int] = {}
    reasoning_source_counts: Dict[str, int] = {}
    route_counts: Dict[str, int] = {}
    debate_state_counts: Dict[str, int] = {}
    diagnosis_counts: Dict[str, int] = {}
    trace_steps = [record.trace_step_count for record in records]
    tool_calls = [record.tool_call_count for record in records]
    reasoning_attempts = [record.reasoning_attempt_count for record in records]
    evidence_sizes = [record.evidence_bundle_size for record in records]
    token_totals = [
        int(record.token_usage.get("total_tokens_est", 0) or record.token_usage.get("total_tokens", 0) or 0)
        for record in records
    ]
    for record in records:
        support_status = str(record.support_status or "").strip()
        if support_status:
            support_status_counts[support_status] = int(support_status_counts.get(support_status, 0)) + 1
        for slot in record.missing_slots:
            normalized_slot = str(slot or "").strip()
            if normalized_slot:
                missing_slot_counts[normalized_slot] = int(missing_slot_counts.get(normalized_slot, 0)) + 1
        pattern = str(record.agent_pattern.get("pattern", "") if isinstance(record.agent_pattern, dict) else "").strip()
        if pattern:
            agent_pattern_counts[pattern] = int(agent_pattern_counts.get(pattern, 0)) + 1
        status = str(record.reasoning_cycle_status or "").strip()
        if status:
            reasoning_status_counts[status] = int(reasoning_status_counts.get(status, 0)) + 1
        for source in record.reasoning_cycle_sources:
            normalized = str(source or "").strip()
            if normalized:
                reasoning_source_counts[normalized] = int(
                    reasoning_source_counts.get(normalized, 0)
                ) + 1
        route = str(record.route or "").strip()
        if route:
            route_counts[route] = int(route_counts.get(route, 0)) + 1
        debate_state = str(record.debate_state or "").strip()
        if debate_state:
            debate_state_counts[debate_state] = int(debate_state_counts.get(debate_state, 0)) + 1
        for finding in record.diagnosis:
            normalized_finding = str(finding or "").strip()
            if normalized_finding:
                diagnosis_counts[normalized_finding] = int(
                    diagnosis_counts.get(normalized_finding, 0)
                ) + 1
    count = len(records)
    support_answer_gap_count = sum(1 for record in records if record.support_answer_gap)

    return FinDERBenchmarkSummary(
        mode=mode,
        dataset=dataset,
        record_count=count,
        add_latency_p50_ms=round(float(median(add_latencies)), 2) if add_latencies else 0.0,
        add_latency_p95_ms=_percentile_ms(add_latencies, 0.95),
        ask_latency_p50_ms=round(float(median(ask_latencies)), 2) if ask_latencies else 0.0,
        ask_latency_p95_ms=_percentile_ms(ask_latencies, 0.95),
        exact_match_rate=round(exact_hits / count, 4) if count else 0.0,
        contains_match_rate=round(contains_hits / count, 4) if count else 0.0,
        avg_nodes_created=round(sum(nodes) / count, 2) if count else 0.0,
        avg_relationships_created=round(sum(rels) / count, 2) if count else 0.0,
        failure_count=failures,
        retrieval_latency_p50_ms=round(float(median(retrieval_latencies)), 2) if retrieval_latencies else 0.0,
        retrieval_latency_p95_ms=_percentile_ms(retrieval_latencies, 0.95),
        generation_latency_p50_ms=round(float(median(generation_latencies)), 2) if generation_latencies else 0.0,
        generation_latency_p95_ms=_percentile_ms(generation_latencies, 0.95),
        avg_evidence_coverage=round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
        support_status_counts=support_status_counts,
        missing_slot_counts=missing_slot_counts,
        agent_pattern_counts=agent_pattern_counts,
        reasoning_cycle_status_counts=reasoning_status_counts,
        reasoning_cycle_source_counts=reasoning_source_counts,
        route_counts=route_counts,
        debate_state_counts=debate_state_counts,
        semantic_reuse_count=sum(1 for record in records if record.semantic_reused),
        support_answer_gap_count=support_answer_gap_count,
        support_answer_gap_rate=round(support_answer_gap_count / count, 4) if count else 0.0,
        diagnosis_counts=diagnosis_counts,
        avg_trace_step_count=round(sum(trace_steps) / count, 2) if count else 0.0,
        avg_tool_call_count=round(sum(tool_calls) / count, 2) if count else 0.0,
        avg_reasoning_attempt_count=round(sum(reasoning_attempts) / count, 2) if count else 0.0,
        avg_evidence_bundle_size=round(sum(evidence_sizes) / count, 2) if count else 0.0,
        avg_total_tokens_est=round(sum(token_totals) / count, 2) if count else 0.0,
        records=list(records),
    )


def extract_query_metadata(client: Any) -> Dict[str, Any]:
    """Best-effort query metadata extractor for SDK and test clients."""

    direct = getattr(client, "last_query_metadata", None)
    if isinstance(direct, dict):
        return dict(direct)
    engine = getattr(client, "_engine", None)
    metadata = getattr(engine, "_last_query_metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    metadata = getattr(client, "_last_query_metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def finder_record_observability(
    *,
    expected_answer: str,
    actual_answer: str,
    query_metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    """Normalize local/runtime query metadata into benchmark record fields."""

    envelope = query_metadata.get("answer_envelope")
    if not isinstance(envelope, Mapping):
        envelope = {}
    evidence_bundle = query_metadata.get("evidence_bundle")
    if not isinstance(evidence_bundle, Mapping):
        evidence_bundle = envelope.get("evidence_bundle", {})
    if not isinstance(evidence_bundle, Mapping):
        evidence_bundle = {}
    support_assessment = query_metadata.get("support_assessment")
    if not isinstance(support_assessment, Mapping):
        support_assessment = envelope.get("support_assessment", {})
    if not isinstance(support_assessment, Mapping):
        support_assessment = {}
    latency_breakdown = query_metadata.get("latency_breakdown_ms")
    if not isinstance(latency_breakdown, Mapping):
        latency_breakdown = envelope.get("latency_breakdown_ms", {})
    if not isinstance(latency_breakdown, Mapping):
        latency_breakdown = {}
    agent_pattern = query_metadata.get("agent_pattern")
    if not isinstance(agent_pattern, Mapping):
        agent_pattern = envelope.get("agent_pattern", {})
    if not isinstance(agent_pattern, Mapping):
        agent_pattern = {}
    token_usage = query_metadata.get("token_usage")
    if not isinstance(token_usage, Mapping):
        token_usage = envelope.get("token_usage", {})
    if not isinstance(token_usage, Mapping):
        token_usage = {}

    def _float_field(key: str) -> float:
        try:
            return round(float(latency_breakdown.get(key, 0.0) or 0.0), 2)
        except (TypeError, ValueError):
            return 0.0

    missing_slots = [
        str(slot).strip()
        for slot in evidence_bundle.get("missing_slots", [])
        if str(slot).strip()
    ] if isinstance(evidence_bundle.get("missing_slots", []), list) else []

    try:
        evidence_coverage = round(float(evidence_bundle.get("coverage", 0.0) or 0.0), 4)
    except (TypeError, ValueError):
        evidence_coverage = 0.0

    return {
        "latency_breakdown_ms": {
            str(key): round(float(value), 2)
            for key, value in latency_breakdown.items()
            if isinstance(value, (int, float))
        },
        "retrieval_latency_ms": _float_field("retrieval_ms"),
        "generation_latency_ms": _float_field("generation_ms"),
        "support_status": str(support_assessment.get("status", "") or "").strip(),
        "evidence_coverage": evidence_coverage,
        "missing_slots": missing_slots,
        "slot_metrics": score_answer_slots(expected_answer, actual_answer),
        "token_usage": dict(token_usage),
        "agent_pattern": dict(agent_pattern),
    }


def summarize_finance_records(
    *,
    mode: str,
    dataset: str,
    records: Sequence[FinanceBenchmarkRecord],
) -> FinanceBenchmarkSummary:
    add_latencies = [record.add_latency_ms for record in records]
    ask_latencies = [record.ask_latency_ms for record in records]
    exact_hits = sum(1 for record in records if record.exact_match)
    contains_hits = sum(1 for record in records if record.contains_match)
    nodes = [record.nodes_created for record in records]
    rels = [record.relationships_created for record in records]
    failures = sum(1 for record in records if record.error)
    count = len(records)

    return FinanceBenchmarkSummary(
        mode=mode,
        dataset=dataset,
        record_count=count,
        add_latency_p50_ms=round(float(median(add_latencies)), 2) if add_latencies else 0.0,
        add_latency_p95_ms=_percentile_ms(add_latencies, 0.95),
        ask_latency_p50_ms=round(float(median(ask_latencies)), 2) if ask_latencies else 0.0,
        ask_latency_p95_ms=_percentile_ms(ask_latencies, 0.95),
        exact_match_rate=round(exact_hits / count, 4) if count else 0.0,
        contains_match_rate=round(contains_hits / count, 4) if count else 0.0,
        avg_nodes_created=round(sum(nodes) / count, 2) if count else 0.0,
        avg_relationships_created=round(sum(rels) / count, 2) if count else 0.0,
        failure_count=failures,
        records=list(records),
    )


def run_finder_benchmark(
    *,
    client: Any,
    cases: Iterable[FinDERBenchmarkCase],
    mode: str,
    dataset: str,
    database: str = "neo4j",
) -> FinDERBenchmarkSummary:
    records: List[FinDERBenchmarkRecord] = []
    for case in cases:
        add_started = time.perf_counter()
        answer = ""
        error = ""
        exact = False
        contains = False
        nodes_created = 0
        relationships_created = 0
        fallback_used = False
        deduplicated = False
        reasoning_cycle_status = ""
        reasoning_cycle_sources: List[str] = []
        observability: Dict[str, Any] = {}
        try:
            memory = client.add(case.text, database=database, category=case.category)
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            metadata = dict(getattr(memory, "metadata", {}) or {})
            nodes_created = int(metadata.get("nodes_created", 0) or 0)
            relationships_created = int(metadata.get("relationships_created", 0) or 0)
            fallback_used = bool(metadata.get("fallback_used", False))
            deduplicated = bool(metadata.get("deduplicated", False))
            reasoning_cycle = metadata.get("reasoning_cycle")
            if isinstance(reasoning_cycle, Mapping):
                reasoning_cycle_status = str(reasoning_cycle.get("status", "")).strip()
                reasoning_cycle_sources = [
                    str(item.get("source", "")).strip()
                    for item in reasoning_cycle.get("observed_anomalies", [])
                    if isinstance(item, Mapping) and str(item.get("source", "")).strip()
                ]

            ask_started = time.perf_counter()
            answer = str(client.ask(case.question, database=database))
            ask_latency_ms = (time.perf_counter() - ask_started) * 1000.0
            exact, contains = compare_answers(case.expected_answer, answer)
            observability = finder_record_observability(
                expected_answer=case.expected_answer,
                actual_answer=answer,
                query_metadata=extract_query_metadata(client),
            )
        except Exception as exc:  # pragma: no cover
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            ask_latency_ms = 0.0
            error = str(exc)
            observability = {
                "slot_metrics": score_answer_slots(case.expected_answer, answer),
            }

        diagnosis = diagnose_finder_query_contract(
            error=error,
            contains_match=contains,
        )

        records.append(
            FinDERBenchmarkRecord(
                case_id=case.case_id,
                category=case.category,
                question=case.question,
                add_latency_ms=round(add_latency_ms, 2),
                ask_latency_ms=round(ask_latency_ms, 2),
                answer=answer,
                expected_answer=case.expected_answer,
                exact_match=exact,
                contains_match=contains,
                nodes_created=nodes_created,
                relationships_created=relationships_created,
                fallback_used=fallback_used,
                deduplicated=deduplicated,
                reasoning_cycle_status=reasoning_cycle_status,
                reasoning_cycle_sources=reasoning_cycle_sources,
                diagnosis=diagnosis,
                latency_breakdown_ms=dict(observability.get("latency_breakdown_ms", {})),
                retrieval_latency_ms=float(observability.get("retrieval_latency_ms", 0.0) or 0.0),
                generation_latency_ms=float(observability.get("generation_latency_ms", 0.0) or 0.0),
                support_status=str(observability.get("support_status", "") or ""),
                evidence_coverage=float(observability.get("evidence_coverage", 0.0) or 0.0),
                missing_slots=list(observability.get("missing_slots", [])),
                slot_metrics=dict(observability.get("slot_metrics", {})),
                token_usage=dict(observability.get("token_usage", {})),
                agent_pattern=dict(observability.get("agent_pattern", {})),
                error=error,
            )
        )

    return summarize_finder_records(mode=mode, dataset=dataset, records=records)


def run_finance_benchmark(
    *,
    client: Any,
    cases: Iterable[FinanceBenchmarkCase],
    mode: str,
    dataset: str,
    database: str = "neo4j",
) -> FinanceBenchmarkSummary:
    records: List[FinanceBenchmarkRecord] = []
    for case in cases:
        add_started = time.perf_counter()
        answer = ""
        error = ""
        exact = False
        contains = False
        nodes_created = 0
        relationships_created = 0
        fallback_used = False
        deduplicated = False
        try:
            memory = client.add(case.text, database=database, category=case.category)
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            metadata = dict(getattr(memory, "metadata", {}) or {})
            nodes_created = int(metadata.get("nodes_created", 0) or 0)
            relationships_created = int(metadata.get("relationships_created", 0) or 0)
            fallback_used = bool(metadata.get("fallback_used", False))
            deduplicated = bool(metadata.get("deduplicated", False))

            ask_started = time.perf_counter()
            answer = str(client.ask(case.question, database=database))
            ask_latency_ms = (time.perf_counter() - ask_started) * 1000.0
            exact, contains = compare_answers(case.expected_answer, answer)
        except Exception as exc:  # pragma: no cover
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            ask_latency_ms = 0.0
            error = str(exc)

        records.append(
            FinanceBenchmarkRecord(
                case_id=case.case_id,
                category=case.category,
                add_latency_ms=round(add_latency_ms, 2),
                ask_latency_ms=round(ask_latency_ms, 2),
                answer=answer,
                expected_answer=case.expected_answer,
                exact_match=exact,
                contains_match=contains,
                nodes_created=nodes_created,
                relationships_created=relationships_created,
                fallback_used=fallback_used,
                deduplicated=deduplicated,
                error=error,
            )
        )

    return summarize_finance_records(mode=mode, dataset=dataset, records=records)
