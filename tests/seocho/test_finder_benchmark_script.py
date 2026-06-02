from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "benchmarks" / "run_finder_benchmark.py"
SPEC = importlib.util.spec_from_file_location("finder_benchmark_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_reasoning_cycle_payload_is_enabled_and_actionable():
    payload = MODULE._default_reasoning_cycle_payload()

    assert payload["enabled"] is True
    assert "unsupported_answer" in payload["anomaly_sources"]
    assert "ontology_mismatch" in payload["anomaly_sources"]
    assert "query_execution_failed_or_contract_error" in payload["anomaly_sources"]


def test_extract_reasoning_cycle_reads_nested_runtime_payload():
    status, sources = MODULE._extract_reasoning_cycle(
        {
            "runtime_payload": {
                "reasoning_cycle": {
                    "status": "anomaly_detected",
                    "observed_anomalies": [
                        {"source": "unsupported_answer"},
                        {"source": "query_execution_failed_or_contract_error"},
                    ],
                }
            }
        }
    )

    assert status == "anomaly_detected"
    assert sources == [
        "unsupported_answer",
        "query_execution_failed_or_contract_error",
    ]


def test_resolve_llm_selection_accepts_explicit_provider_and_model():
    payload = MODULE._resolve_llm_selection("deepseek", "deepseek-chat")

    assert payload == {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "llm": "deepseek/deepseek-chat",
    }


def test_resolve_llm_selection_accepts_provider_model_shorthand():
    payload = MODULE._resolve_llm_selection("", "kimi/kimi-k2.5")

    assert payload == {
        "provider": "kimi",
        "model": "kimi-k2.5",
        "llm": "kimi/kimi-k2.5",
    }


def test_resolve_llm_selection_rejects_conflicting_provider():
    try:
        MODULE._resolve_llm_selection("openai", "deepseek/deepseek-chat")
    except ValueError as exc:
        assert "--provider conflicts" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected conflicting provider/model to fail")


def test_limit_cases_per_category_preserves_order():
    cases = [
        SimpleNamespace(case_id="a1", category="A"),
        SimpleNamespace(case_id="b1", category="B"),
        SimpleNamespace(case_id="a2", category="A"),
        SimpleNamespace(case_id="a3", category="A"),
        SimpleNamespace(case_id="b2", category="B"),
    ]

    selected = MODULE._limit_cases_per_category(cases, 2)

    assert [case.case_id for case in selected] == ["a1", "b1", "a2", "b2"]


def test_extract_query_metadata_reads_generation_and_metric_trace_steps():
    metadata = MODULE._extract_query_metadata(
        {
            "runtime_payload": {
                "support_assessment": {"status": "supported"},
                "evidence_bundle": {"coverage": 1.0, "missing_slots": []},
                "trace_steps": [
                    {
                        "type": "GENERATION",
                        "metadata": {
                            "latency_breakdown_ms": {"retrieval_ms": 10.0, "generation_ms": 2.0},
                            "agent_pattern": {"pattern": "semantic_direct"},
                            "usage_estimate": {"total_tokens_est": 12},
                        },
                    },
                    {
                        "type": "METRIC",
                        "metadata": {
                            "usage": {"source": "provider", "exact": True, "total_tokens": 9},
                        },
                    },
                ],
            }
        }
    )

    assert metadata["support_assessment"]["status"] == "supported"
    assert metadata["evidence_bundle"]["coverage"] == 1.0
    assert metadata["latency_breakdown_ms"]["retrieval_ms"] == 10.0
    assert metadata["agent_pattern"]["pattern"] == "semantic_direct"
    assert metadata["token_usage"]["total_tokens"] == 9
