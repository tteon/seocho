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
    add_latency_ms: float
    ask_latency_ms: float
    answer: str
    expected_answer: str
    exact_match: bool
    contains_match: bool
    nodes_created: int = 0
    relationships_created: int = 0
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


def _slot_contains_match(expected: str, actual: str) -> bool:
    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return False

    expected_numbers = _numeric_slots(expected)
    actual_numbers = _numeric_slots(actual)
    if expected_numbers and not expected_numbers.issubset(actual_numbers):
        return False

    overlap = expected_tokens & actual_tokens
    recall = len(overlap) / len(expected_tokens)
    return recall >= 0.72


def _meaningful_tokens(text: str) -> set[str]:
    tokens = set(normalize_answer(text).split())
    return {
        token
        for token in tokens
        if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    }


def _numeric_slots(text: str) -> set[str]:
    normalized = str(text).lower().replace(",", "").replace("$", "")
    slots: set[str] = set()
    for match in _NUMBER_WITH_UNIT_RE.finditer(normalized):
        raw_number = match.group("number")
        unit = match.group("unit")
        if unit and not match.group("percent"):
            scaled = Decimal(raw_number) * _UNIT_MULTIPLIERS[unit]
            slots.add(str(int(scaled)) if scaled == scaled.to_integral_value() else str(scaled.normalize()))
        else:
            slots.add(raw_number)
    return slots


def _percentile_ms(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return round(float(ordered[index]), 2)


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
    count = len(records)

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
        records=list(records),
    )


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
        try:
            memory = client.add(case.text, database=database, category=case.category)
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            metadata = dict(getattr(memory, "metadata", {}) or {})
            nodes_created = int(metadata.get("nodes_created", 0) or 0)
            relationships_created = int(metadata.get("relationships_created", 0) or 0)

            ask_started = time.perf_counter()
            answer = str(client.ask(case.question, database=database))
            ask_latency_ms = (time.perf_counter() - ask_started) * 1000.0
            exact, contains = compare_answers(case.expected_answer, answer)
        except Exception as exc:  # pragma: no cover
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            ask_latency_ms = 0.0
            error = str(exc)

        records.append(
            FinDERBenchmarkRecord(
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
        try:
            memory = client.add(case.text, database=database, category=case.category)
            add_latency_ms = (time.perf_counter() - add_started) * 1000.0
            metadata = dict(getattr(memory, "metadata", {}) or {})
            nodes_created = int(metadata.get("nodes_created", 0) or 0)
            relationships_created = int(metadata.get("relationships_created", 0) or 0)

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
                error=error,
            )
        )

    return summarize_finance_records(mode=mode, dataset=dataset, records=records)
