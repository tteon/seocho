import pytest

from seocho.eval.critical_scenarios import (
    CRITICAL_SCENARIO_IDS,
    CriticalScenarioResult,
    assert_live_evidence,
)


def _result(**overrides):
    values = {
        "scenario_id": "S1",
        "dataset_manifest": "okx-agent-transaction.v1",
        "service_versions": {"postgresql": "18.4", "dozerdb": "5.26.3.0"},
        "concurrency": 8,
        "memory_sequence": 635,
        "projection_watermark": 635,
        "support_status": "supported",
        "required_slots": ("state", "provenance"),
        "missing_slots": (),
        "provenance_coverage": 1.0,
        "disclosure_violations": 0,
        "latency_ms": {"p95": 10.9},
        "trace_id": "trace-1",
        "live_services": ("postgresql", "dozerdb", "tempo"),
    }
    values.update(overrides)
    return CriticalScenarioResult(**values)


def test_catalog_has_ten_critical_scenarios() -> None:
    assert CRITICAL_SCENARIO_IDS == tuple(f"S{i}" for i in range(1, 11))


def test_common_gate_requires_no_silent_stale_or_skipped_live_gate() -> None:
    assert _result().passed_common_gates
    assert not _result(silent_stale_answers=1).passed_common_gates
    assert not _result(skipped_gates=("tls_rotation",)).passed_common_gates


def test_live_evidence_cannot_be_replaced_by_mock() -> None:
    with pytest.raises(ValueError, match="etcd"):
        assert_live_evidence(
            _result(live_services=("postgresql", "dozerdb")),
            required_services=("postgresql", "dozerdb", "etcd"),
        )
