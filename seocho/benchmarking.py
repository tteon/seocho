from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence


_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


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
    contains = norm_expected in norm_actual or norm_actual in norm_expected
    return exact, contains


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
