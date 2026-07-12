from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/benchmarks/okx_release_gate.py"
SPEC = importlib.util.spec_from_file_location("okx_release_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_evaluate_requires_every_live_plane() -> None:
    vertical = {
        "memory": {"projection_current": True},
        "query": {"plans": 6},
        "guardrail": {"raw_address_in_cases": False},
        "llm": {
            "error_count": 0,
            "disposition_accuracy": 1.0,
            "provenance_coverage": 1.0,
            "leakage_cases": 0,
        },
    }
    gates = MODULE.evaluate(
        vertical,
        {"passed": True},
        {"prometheus": True, "tempo": True, "grafana": True},
    )
    assert all(gates.values())
    vertical["llm"]["error_count"] = 1
    assert MODULE.evaluate(vertical, {"passed": True}, {"prometheus": True})[
        "answer_generation"
    ] is False
