from seocho.benchmarking import (
    FinDERBenchmarkCase,
    compare_answers,
    normalize_answer,
    run_finder_benchmark,
    summarize_finder_records,
)


class _FakeMemory:
    def __init__(self, nodes: int, rels: int):
        self.metadata = {"nodes_created": nodes, "relationships_created": rels}


class _FakeClient:
    def __init__(self):
        self._answers = {
            "What was PTC's revenue growth in fiscal 2023?": "PTC reported total revenue of 2.1 billion in fiscal 2023, a 10 increase from 1.9 billion in the prior year.",
            "What are Brown & Brown's business segments?": "Brown & Brown operates through Retail, National Programs, Wholesale Brokerage, and Services.",
        }

    def add(self, text: str, **kwargs):
        return _FakeMemory(nodes=2, rels=1)

    def ask(self, question: str, **kwargs):
        return self._answers[question]


class _FailingClient(_FakeClient):
    def add(self, text: str, **kwargs):
        raise RuntimeError("boom")


def test_normalize_answer_collapses_case_and_punctuation():
    assert normalize_answer("PTC, Inc. reported  $2.1 billion!") == "ptc inc reported 2 1 billion"


def test_compare_answers_supports_contains_match():
    exact, contains = compare_answers(
        "Retail, National Programs, Wholesale Brokerage, and Services.",
        "Brown & Brown operates through Retail, National Programs, Wholesale Brokerage, and Services.",
    )
    assert exact is False
    assert contains is True


def test_run_finder_benchmark_summarizes_latencies_and_matches():
    cases = [
        FinDERBenchmarkCase(
            case_id="finder_001",
            text="PTC text",
            question="What was PTC's revenue growth in fiscal 2023?",
            expected_answer="PTC reported total revenue of $2.1 billion in fiscal 2023, a 10% increase from $1.9 billion in the prior year.",
            category="Financials",
        ),
        FinDERBenchmarkCase(
            case_id="finder_002",
            text="Brown text",
            question="What are Brown & Brown's business segments?",
            expected_answer="Retail, National Programs, Wholesale Brokerage, and Services.",
            category="Company Overview",
        ),
    ]

    summary = run_finder_benchmark(
        client=_FakeClient(),
        cases=cases,
        mode="local",
        dataset="finder_sample.json",
        database="neo4j",
    )

    assert summary.mode == "local"
    assert summary.record_count == 2
    assert summary.failure_count == 0
    assert summary.contains_match_rate == 1.0
    assert summary.avg_nodes_created == 2.0
    assert summary.avg_relationships_created == 1.0


def test_run_finder_benchmark_records_failures():
    cases = [
        FinDERBenchmarkCase(
            case_id="finder_003",
            text="Broken text",
            question="Broken question",
            expected_answer="Broken answer",
            category="General",
        )
    ]

    summary = run_finder_benchmark(
        client=_FailingClient(),
        cases=cases,
        mode="local",
        dataset="finder_sample.json",
        database="neo4j",
    )

    assert summary.failure_count == 1
    assert summary.records[0].error == "boom"


def test_summarize_finder_records_handles_empty_input():
    summary = summarize_finder_records(mode="local", dataset="finder_sample.json", records=[])
    assert summary.record_count == 0
    assert summary.add_latency_p50_ms == 0.0
    assert summary.ask_latency_p95_ms == 0.0
