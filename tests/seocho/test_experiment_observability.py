from __future__ import annotations

from seocho.eval.experiment_observability import (
    agents_sdk_runtime_receipt,
    direct_runtime_receipt,
)


def test_direct_runtime_receipt_never_claims_agents_sdk() -> None:
    receipt = direct_runtime_receipt()
    assert receipt["execution_runtime"] == "seocho_direct"
    assert receipt["agents_sdk_version"] is None
    assert receipt["max_turns"] is None


def test_agents_sdk_receipt_records_installed_runtime_version() -> None:
    receipt = agents_sdk_runtime_receipt(max_turns=3, toolset_digest="a" * 64)
    assert receipt["execution_runtime"] == "agents_sdk"
    assert receipt["agents_sdk_version"]
    assert receipt["max_turns"] == 3
    assert receipt["toolset_digest"] == "a" * 64
