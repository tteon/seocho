from seocho.benchmarking import (
    FinDERBenchmarkCase,
    classify_finder_scenario,
    FinanceBenchmarkCase,
    compare_answers,
    diagnose_finder_query_contract,
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
from scripts.benchmarks.run_finder_benchmark import (
    _benchmark_setup_payload,
    _extract_agent_metrics,
    _local_graph_path_for_run,
)


class _FakeMemory:
    def __init__(
        self,
        nodes: int,
        rels: int,
        *,
        fallback_used: bool = False,
        deduplicated: bool = False,
        reasoning_cycle: dict | None = None,
    ):
        self.metadata = {
            "nodes_created": nodes,
            "relationships_created": rels,
            "fallback_used": fallback_used,
            "deduplicated": deduplicated,
        }
        if reasoning_cycle is not None:
            self.metadata["reasoning_cycle"] = reasoning_cycle


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


class _ReasoningCycleClient(_FakeClient):
    def add(self, text: str, **kwargs):
        return _FakeMemory(
            nodes=1,
            rels=0,
            reasoning_cycle={
                "enabled": True,
                "status": "anomaly_detected",
                "observed_anomalies": [{"source": "shacl_violation"}],
            },
        )


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


def test_compare_answers_tolerates_omitted_financial_unit_when_slots_match():
    exact, contains = compare_answers(
        (
            "The Data and Access Solutions revenue increased by $111.5 million "
            "from 2021 to 2023, calculated as 539.2 million minus 427.7 million."
        ),
        (
            "For Cboe Global Markets, Inc., Data and Access Solutions revenue "
            "increased by $111.5 from 2021 to 2023, calculated as $539.2 minus $427.7."
        ),
    )
    assert exact is False
    assert contains is True


def test_compare_answers_accepts_short_standard_answer_with_key_slot():
    exact, contains = compare_answers(
        "Meta uses the fair value method for stock-based compensation under ASC 718.",
        "Meta follows ASC 718 for stock-based compensation.",
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
    assert summary.records[0].question == "What was PTC's revenue growth in fiscal 2023?"
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


def test_run_finder_benchmark_aggregates_reasoning_cycle_findings():
    cases = [
        FinDERBenchmarkCase(
            case_id="finder_011",
            text="Reasoning text",
            question="What are Brown & Brown's business segments?",
            expected_answer="Retail, National Programs, Wholesale Brokerage, and Services.",
            category="Company Overview",
            reasoning_type="Qualitative",
        )
    ]

    summary = run_finder_benchmark(
        client=_ReasoningCycleClient(),
        cases=cases,
        mode="local",
        dataset="finder_sample.json",
        database="neo4j",
    )

    assert summary.records[0].reasoning_cycle_status == "anomaly_detected"
    assert summary.records[0].reasoning_cycle_sources == ["shacl_violation"]
    assert summary.reasoning_cycle_status_counts["anomaly_detected"] == 1
    assert summary.reasoning_cycle_source_counts["shacl_violation"] == 1


def test_summarize_finder_records_aggregates_agent_metrics():
    from seocho.benchmarking import FinDERBenchmarkRecord

    summary = summarize_finder_records(
        mode="remote-semantic",
        dataset="finder_sample.json",
        records=[
            FinDERBenchmarkRecord(
                case_id="finder_012",
                category="Financials",
                question="What changed?",
                add_latency_ms=10.0,
                ask_latency_ms=20.0,
                answer="answer",
                expected_answer="answer",
                exact_match=True,
                contains_match=True,
                route="lpg",
                support_status="partial",
                missing_slots=["period", "metric"],
                evidence_bundle_size=4,
                trace_step_count=6,
                tool_call_count=2,
                reasoning_attempt_count=1,
                semantic_reused=True,
                debate_state="ready",
                token_usage={"total_tokens_est": 120},
                support_answer_gap=True,
                diagnosis=["support_claim_answer_mismatch"],
            )
        ],
    )

    assert summary.route_counts == {"lpg": 1}
    assert summary.support_status_counts == {"partial": 1}
    assert summary.debate_state_counts == {"ready": 1}
    assert summary.missing_slot_counts == {"period": 1, "metric": 1}
    assert summary.semantic_reuse_count == 1
    assert summary.support_answer_gap_count == 1
    assert summary.support_answer_gap_rate == 1.0
    assert summary.diagnosis_counts == {"support_claim_answer_mismatch": 1}
    assert summary.avg_trace_step_count == 6.0
    assert summary.avg_tool_call_count == 2.0
    assert summary.avg_reasoning_attempt_count == 1.0
    assert summary.avg_evidence_bundle_size == 4.0
    assert summary.avg_total_tokens_est == 120.0


def test_diagnose_finder_query_contract_flags_supported_but_wrong_answer():
    diagnosis = diagnose_finder_query_contract(
        contains_match=False,
        support_status="supported",
        evidence_bundle_size=3,
        trace_step_count=5,
    )

    assert diagnosis == [
        "support_claim_answer_mismatch",
        "answer_quality_or_slot_selection_gap",
    ]


def test_diagnose_finder_query_contract_flags_empty_evidence_after_trace():
    diagnosis = diagnose_finder_query_contract(
        contains_match=True,
        support_status="supported",
        evidence_bundle_size=0,
        trace_step_count=4,
    )

    assert diagnosis == ["query_no_graph_records"]


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
            "support_claim_answer_mismatch",
            "custom_follow_up",
            "query_no_graph_records",
        ]
    )

    assert split["indexing"] == ["indexing_no_graph_writes"]
    assert split["query"] == [
        "query_no_graph_records",
        "query_execution_failed_or_contract_error",
        "answer_quality_or_slot_selection_gap",
        "support_claim_answer_mismatch",
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
                    "support_claim_answer_mismatch",
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
    assert summary["query"]["finding_counts"]["support_claim_answer_mismatch"] == 1
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


def test_finder_benchmark_setup_payload_records_model_and_trace_env(monkeypatch):
    class _Args:
        model = "gpt-4o-mini"

    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "opik")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "seocho-e2e")
    monkeypatch.setenv("OPIK_WORKSPACE", "tteon")

    payload = _benchmark_setup_payload(_Args(), tracing_configured=True)

    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["trace_backend_env"] == "opik"
    assert payload["tracing_configured"] is True
    assert payload["opik_project"] == "seocho-e2e"
    assert payload["opik_workspace"] == "tteon"


def test_extract_agent_metrics_from_semantic_runtime_payload():
    metrics = _extract_agent_metrics(
        {
            "route": "lpg",
            "support_assessment": {"status": "partial", "coverage": 0.5},
            "evidence_bundle": {
                "selected_triples": [{"source": "A", "relation": "R", "target": "B"}],
                "slot_fills": {"target_entity": "B"},
                "grounded_slots": ["target_entity"],
                "missing_slots": ["period"],
            },
            "lpg_result": {"reasoning": {"attempt_count": 2}},
            "trace_steps": [
                {"type": "SEMANTIC", "metadata": {}},
                {"type": "SPECIALIST", "metadata": {"tool_calls": [{"query": "MATCH"}]}},
                {
                    "type": "METRIC",
                    "metadata": {
                        "usage": {
                            "source": "estimated_char_count",
                            "total_tokens_est": 42,
                        }
                    },
                },
            ],
        }
    )

    assert metrics["route"] == "lpg"
    assert metrics["support_status"] == "partial"
    assert metrics["support_coverage"] == 0.5
    assert metrics["missing_slots"] == ["period"]
    assert metrics["evidence_bundle_size"] == 3
    assert metrics["trace_step_count"] == 3
    assert metrics["tool_call_count"] == 1
    assert metrics["reasoning_attempt_count"] == 2
    assert metrics["token_usage"]["total_tokens_est"] == 42


def test_extract_agent_metrics_detects_debate_semantic_reuse():
    metrics = _extract_agent_metrics(
        {
            "runtime_payload": {
                "debate_state": "ready",
                "debate_results": [{"semantic_reused": True}],
            },
            "trace_steps": [
                {"type": "FANOUT", "metadata": {}},
                {"type": "DETERMINISTIC_PREFLIGHT", "metadata": {"tool_names": ["semantic_agent_flow"]}},
                {"type": "SYNTHESIS_BYPASSED", "metadata": {"bypass_reason": "single_supported_semantic_reuse"}},
            ],
        }
    )

    assert metrics["debate_state"] == "ready"
    assert metrics["semantic_reused"] is True
    assert metrics["tool_call_count"] == 1
    assert metrics["trace_step_count"] == 3
