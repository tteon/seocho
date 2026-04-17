from seocho.benchmarking import (
    FinanceBenchmarkCase,
    compare_answers,
    normalize_answer,
    run_finance_benchmark,
    split_finance_diagnosis,
    summarize_finance_contract_findings,
    summarize_finance_records,
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


def test_compare_answers_supports_slot_contains_match():
    exact, contains = compare_answers(
        "PTC reported total revenue of $2.1 billion in fiscal 2023, a 10% increase from $1.9 billion in the prior year.",
        (
            "PTC Inc. reported total revenue of 2.1 billion for fiscal year 2023, "
            "representing a 10 increase from 1.9 billion in the prior year. "
            "Route selected: LPG."
        ),
    )
    assert exact is False
    assert contains is True


def test_compare_answers_rejects_slot_match_when_numbers_differ():
    exact, contains = compare_answers(
        "PTC reported total revenue of $2.1 billion in fiscal 2023, a 10% increase from $1.9 billion in the prior year.",
        "PTC reported total revenue of 3.4 billion in fiscal 2023.",
    )
    assert exact is False
    assert contains is False


def test_compare_answers_treats_scaled_numeric_units_as_equivalent():
    exact, contains = compare_answers(
        "Tesla delivered 1.31 million vehicles in 2022 compared to 936,000 in 2021.",
        "In 2022, Tesla delivered 1,310,000 vehicles. In 2021, Tesla delivered 936,000 vehicles.",
    )
    assert exact is False
    assert contains is True


def test_run_finance_benchmark_summarizes_latencies_and_matches():
    cases = [
        FinanceBenchmarkCase(
            case_id="case_001",
            text="PTC text",
            question="What was PTC's revenue growth in fiscal 2023?",
            expected_answer="PTC reported total revenue of $2.1 billion in fiscal 2023, a 10% increase from $1.9 billion in the prior year.",
            category="Financials",
        ),
        FinanceBenchmarkCase(
            case_id="case_002",
            text="Brown text",
            question="What are Brown & Brown's business segments?",
            expected_answer="Retail, National Programs, Wholesale Brokerage, and Services.",
            category="Company Overview",
        ),
    ]

    summary = run_finance_benchmark(
        client=_FakeClient(),
        cases=cases,
        mode="local",
        dataset="tutorial_filings_sample.json",
        database="neo4j",
    )

    assert summary.mode == "local"
    assert summary.record_count == 2
    assert summary.failure_count == 0
    assert summary.contains_match_rate == 1.0
    assert summary.avg_nodes_created == 2.0
    assert summary.avg_relationships_created == 1.0


def test_run_finance_benchmark_records_failures():
    cases = [
        FinanceBenchmarkCase(
            case_id="case_003",
            text="Broken text",
            question="Broken question",
            expected_answer="Broken answer",
            category="General",
        )
    ]

    summary = run_finance_benchmark(
        client=_FailingClient(),
        cases=cases,
        mode="local",
        dataset="tutorial_filings_sample.json",
        database="neo4j",
    )

    assert summary.failure_count == 1
    assert summary.records[0].error == "boom"


def test_summarize_finance_records_handles_empty_input():
    summary = summarize_finance_records(mode="local", dataset="tutorial_filings_sample.json", records=[])
    assert summary.record_count == 0
    assert summary.add_latency_p50_ms == 0.0
    assert summary.ask_latency_p95_ms == 0.0


def test_split_finance_diagnosis_separates_indexing_and_query_findings():
    split = split_finance_diagnosis(
        [
            "indexing_no_graph_writes",
            "query_no_graph_records",
            "query_execution_failed_or_contract_error",
            "answer_quality_or_slot_selection_gap",
            "custom_follow_up",
            "query_no_graph_records",
        ]
    )

    assert split["indexing"] == ["indexing_no_graph_writes"]
    assert split["query"] == [
        "query_no_graph_records",
        "query_execution_failed_or_contract_error",
        "answer_quality_or_slot_selection_gap",
    ]
    assert split["shared"] == ["custom_follow_up"]


def test_summarize_finance_contract_findings_counts_records_and_codes():
    summary = summarize_finance_contract_findings(
        [
            {
                "case_id": "case_001",
                "diagnosis": [
                    "indexing_no_graph_writes",
                    "query_no_graph_records",
                    "query_execution_failed_or_contract_error",
                ],
            },
            {
                "case_id": "case_002",
                "diagnosis": [
                    "source_text_has_answer_but_graph_projection_lost_it",
                    "answer_quality_or_slot_selection_gap",
                    "custom_follow_up",
                ],
            },
            {"case_id": "case_003", "diagnosis": []},
            {"case_id": "case_003", "diagnosis": []},
        ]
    )

    assert summary["indexing"]["record_count"] == 2
    assert summary["indexing"]["finding_counts"]["indexing_no_graph_writes"] == 1
    assert summary["indexing"]["finding_counts"]["source_text_has_answer_but_graph_projection_lost_it"] == 1
    assert summary["query"]["record_count"] == 2
    assert summary["query"]["finding_counts"]["query_no_graph_records"] == 1
    assert summary["query"]["finding_counts"]["query_execution_failed_or_contract_error"] == 1
    assert summary["query"]["finding_counts"]["answer_quality_or_slot_selection_gap"] == 1
    assert summary["shared"]["record_count"] == 1
    assert summary["shared"]["finding_counts"]["custom_follow_up"] == 1
