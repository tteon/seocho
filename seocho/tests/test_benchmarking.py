from seocho.benchmarking import (
    FinDERBenchmarkCase,
    classify_finder_scenario,
    FinanceBenchmarkCase,
    compare_answers,
    filter_finder_cases,
    load_finder_cases,
    normalize_answer,
    run_finder_benchmark,
    run_finance_benchmark,
    split_finder_diagnosis,
    split_finance_diagnosis,
    summarize_finder_contract_findings,
    summarize_finder_records,
    summarize_finance_contract_findings,
    summarize_finance_records,
)
from scripts.benchmarks.run_finder_benchmark import _local_graph_path_for_run


class _FakeMemory:
    def __init__(self, nodes: int, rels: int, *, fallback_used: bool = False, deduplicated: bool = False):
        self.metadata = {
            "nodes_created": nodes,
            "relationships_created": rels,
            "fallback_used": fallback_used,
            "deduplicated": deduplicated,
        }


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


class _FallbackClient(_FakeClient):
    def add(self, text: str, **kwargs):
        return _FakeMemory(nodes=0, rels=0, fallback_used=False, deduplicated=False)


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


def test_run_finder_benchmark_summarizes_latencies_and_matches():
    cases = [
        FinDERBenchmarkCase(
            case_id="finder_001",
            text="PTC text",
            question="What was PTC's revenue growth in fiscal 2023?",
            expected_answer="PTC reported total revenue of $2.1 billion in fiscal 2023, a 10% increase from $1.9 billion in the prior year.",
            category="Financials",
            reasoning_type="Subtraction",
        ),
        FinDERBenchmarkCase(
            case_id="finder_002",
            text="Brown text",
            question="What are Brown & Brown's business segments?",
            expected_answer="Retail, National Programs, Wholesale Brokerage, and Services.",
            category="Company Overview",
            reasoning_type="Qualitative",
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


def test_run_finder_benchmark_preserves_indexing_metadata_flags():
    cases = [
        FinDERBenchmarkCase(
            case_id="finder_010",
            text="Meta text",
            question="What accounting standard does Meta use?",
            expected_answer="ASC 718.",
            category="Accounting",
            reasoning_type="Qualitative",
        )
    ]

    summary = run_finder_benchmark(
        client=_FallbackClient(),
        cases=cases,
        mode="local",
        dataset="finder_sample.json",
        database="neo4j",
    )

    assert summary.records[0].fallback_used is False
    assert summary.records[0].deduplicated is False


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


def test_summarize_finder_records_handles_empty_input():
    summary = summarize_finder_records(mode="local", dataset="finder_sample.json", records=[])
    assert summary.record_count == 0
    assert summary.add_latency_p50_ms == 0.0
    assert summary.ask_latency_p95_ms == 0.0


def test_summarize_finance_records_handles_empty_input():
    summary = summarize_finance_records(mode="local", dataset="tutorial_filings_sample.json", records=[])
    assert summary.record_count == 0
    assert summary.add_latency_p50_ms == 0.0
    assert summary.ask_latency_p95_ms == 0.0


def test_split_finder_diagnosis_separates_indexing_and_query_findings():
    split = split_finder_diagnosis(
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


def test_summarize_finder_contract_findings_counts_records_and_codes():
    summary = summarize_finder_contract_findings(
        [
            {
                "case_id": "finder_001",
                "diagnosis": [
                    "indexing_no_graph_writes",
                    "query_no_graph_records",
                    "query_execution_failed_or_contract_error",
                ],
            },
            {
                "case_id": "finder_002",
                "diagnosis": [
                    "source_text_has_answer_but_graph_projection_lost_it",
                    "answer_quality_or_slot_selection_gap",
                    "custom_follow_up",
                ],
            },
            {"case_id": "finder_003", "diagnosis": []},
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


def test_classify_finder_scenario_splits_beginner_and_advanced_cases():
    beginner = FinDERBenchmarkCase(
        case_id="finder_002",
        text="",
        question="",
        expected_answer="",
        category="Company Overview",
        reasoning_type="Qualitative",
    )
    advanced = FinDERBenchmarkCase(
        case_id="finder_004",
        text="",
        question="",
        expected_answer="",
        category="Financials",
        reasoning_type="Compositional",
    )

    assert classify_finder_scenario(beginner) == "beginner"
    assert classify_finder_scenario(advanced) == "advanced"


def test_filter_finder_cases_returns_scenario_subset():
    cases = [
        FinDERBenchmarkCase("finder_002", "", "", "", "Company Overview", "Qualitative"),
        FinDERBenchmarkCase("finder_004", "", "", "", "Financials", "Compositional"),
        FinDERBenchmarkCase("finder_005", "", "", "", "Legal", "Qualitative"),
    ]

    assert [case.case_id for case in filter_finder_cases(cases, "beginner")] == ["finder_002"]
    assert [case.case_id for case in filter_finder_cases(cases, "advanced")] == ["finder_004", "finder_005"]


def test_load_finder_cases_reads_dataset(tmp_path):
    dataset = tmp_path / "finder.json"
    dataset.write_text(
        """
        [
          {
            "id": "finder_001",
            "text": "text",
            "question": "question",
            "expected_answer": "answer",
            "category": "Financials",
            "reasoning_type": "Subtraction"
          }
        ]
        """.strip()
    )

    cases = load_finder_cases(dataset)

    assert len(cases) == 1
    assert cases[0].case_id == "finder_001"
    assert cases[0].reasoning_type == "Subtraction"


def test_local_graph_path_for_run_isolated_by_workspace(tmp_path):
    path = _local_graph_path_for_run("Finder Local 2026-04-18", base_dir=tmp_path)

    assert path.endswith("finder-local-2026-04-18.lbug")
    assert str(tmp_path) in path


def test_local_graph_path_for_run_removes_stale_file_when_fresh(tmp_path):
    stale_path = tmp_path / "finder-local-2026-04-18.lbug"
    stale_path.write_text("stale")

    path = _local_graph_path_for_run("Finder Local 2026-04-18", base_dir=tmp_path, fresh=True)

    assert path == str(stale_path)
    assert not stale_path.exists()
