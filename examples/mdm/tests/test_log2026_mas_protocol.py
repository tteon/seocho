from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "22_log2026_mas_protocol.py"
SPEC = importlib.util.spec_from_file_location("log2026_mas_protocol", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


def _row(case_id: str, category: str, lane: str, score: float) -> dict:
    return {
        "case_id": case_id,
        "category": category,
        "lane": lane,
        "evaluation": {"token_f1": score},
    }


def _aggregate() -> dict:
    return {
        "run_prefix": "test-run",
        "n_cases": 4,
        "records": [
            _row("a1", "A", "silo-one", 0.8),
            _row("a2", "A", "silo-one", 0.2),
            _row("b1", "B", "silo-one", 0.7),
            _row("b2", "B", "silo-one", 0.3),
            _row("a1", "A", "silo-two", 0.4),
            _row("a2", "A", "silo-two", 0.9),
            _row("b1", "B", "silo-two", 0.5),
            _row("b2", "B", "silo-two", 0.8),
        ],
    }


def test_necessity_analysis_exposes_case_level_complementarity() -> None:
    result = protocol.necessity_analysis(_aggregate())

    assert result["n_cases"] == 4
    assert result["best_fixed_provider"] == "silo-two"
    assert result["per_case_oracle_mean"] > result["best_fixed_mean"]
    assert result["oracle_gain"] == pytest.approx(0.15)
    assert result["cases_with_oracle_gain"] == 2
    assert set(result["winner_counts_with_ties"]) == {"silo-one", "silo-two"}


def test_protocol_split_and_interventions_are_deterministic_and_disjoint() -> None:
    first = protocol.build_protocol(_aggregate(), seed=7, test_fraction=0.5)
    second = protocol.build_protocol(_aggregate(), seed=7, test_fraction=0.5)

    assert first == second
    assert set(first["split"]["development"]).isdisjoint(first["split"]["test"])
    assert len(first["split"]["development"]) == 2
    assert len(first["split"]["test"]) == 2
    assert len(first["interventions"]) == 4
    assert {row["kind"] for row in first["interventions"]} == {
        "one_view_numeric_poison",
        "protected_field_injection",
    }


def test_stratified_split_rejects_invalid_fraction() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        protocol.stratified_split([], test_fraction=1.0)
