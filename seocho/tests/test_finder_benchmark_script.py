from __future__ import annotations

import importlib.util
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
