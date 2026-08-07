from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "21_log2026_paper_analysis.py"
SPEC = importlib.util.spec_from_file_location("log2026_paper_analysis", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def _row(case_id: str, score: float, *, lane: str, category: str = "A") -> dict:
    return {
        "case_id": case_id,
        "lane": lane,
        "category": category,
        "evaluation": {"token_f1": score},
    }


def test_compare_paired_reports_case_level_outcomes() -> None:
    candidate = {
        "a": _row("a", 0.6, lane="category-federation"),
        "b": _row("b", 0.2, lane="category-federation"),
        "c": _row("c", 0.4, lane="category-federation"),
    }
    baseline = {
        "a": _row("a", 0.5, lane="federation"),
        "b": _row("b", 0.3, lane="federation"),
        "c": _row("c", 0.4, lane="federation"),
    }

    result = analysis.compare_paired(candidate, baseline, bootstrap_samples=100, seed=7)

    assert result["n"] == 3
    assert result["mean_delta"] == pytest.approx(0.0)
    assert (result["wins"], result["ties"], result["losses"]) == (1, 1, 1)


def test_build_analysis_uses_best_fixed_silo_not_case_oracle() -> None:
    baseline = {
        "lanes": {
            "silo-one": {"token_f1": 0.4},
            "silo-two": {"token_f1": 0.3},
            "federation": {"token_f1": 0.35},
        },
        "records": [
            _row("a", 0.2, lane="silo-one"),
            _row("b", 0.6, lane="silo-one"),
            _row("a", 0.8, lane="silo-two"),
            _row("b", 0.0, lane="silo-two"),
            _row("a", 0.3, lane="federation"),
            _row("b", 0.4, lane="federation"),
        ],
    }
    category = {
        "records": [
            _row("a", 0.5, lane="category-federation"),
            _row("b", 0.5, lane="category-federation"),
        ]
    }

    result = analysis.build_analysis(baseline, category, bootstrap_samples=100)

    assert result["status"] == "preliminary_non_causal"
    assert result["best_fixed_silo_lane"] == "silo-one"
    assert result["category_vs_best_fixed_silo"]["mean_delta"] == pytest.approx(0.1)


def test_paired_bootstrap_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        analysis.paired_bootstrap_ci([])

