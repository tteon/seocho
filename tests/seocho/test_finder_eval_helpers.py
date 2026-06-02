"""Regression tests for the finder eval helpers (NaN / non-string safety).

pandas reads empty CSV answers as NaN floats; the number-aware metric and
token-F1 must not crash on NaN/None/non-string gold or candidate answers.
Pure functions — no external services. The benchmark scripts are loaded via
importlib (same pattern as test_finder_benchmark_script.py).
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parents[2] / "scripts" / "benchmarks"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _BENCH / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ARM = _load("finder_4arm_sample", "finder_4arm_sample.py")
JUDGE = _load("finder_judge", "finder_judge.py")


@pytest.mark.parametrize("bad", [float("nan"), None, 12345, 3.14])
def test_safe_str_coerces_nonstring(bad):
    out = ARM._safe_str(bad)
    assert isinstance(out, str)
    if isinstance(bad, float) and math.isnan(bad):
        assert out == ""
    if bad is None:
        assert out == ""


def test_nums_handles_nan_without_crash():
    assert ARM._nums(float("nan")) == set()
    assert ARM._nums(None) == set()
    assert "5" in ARM._nums("revenue was 5")
    assert "5 million" in ARM._nums("revenue was 5 million")  # unit captured with number


@pytest.mark.parametrize("gold,actual", [
    (float("nan"), "Revenue grew 12%"),
    (None, None),
    ("Net income 1,270", float("nan")),
    (42, "the answer is 42"),
])
def test_evaluate_answer_never_crashes_on_nonstring(gold, actual):
    res = ARM.evaluate_answer(gold, actual)
    assert set(res) >= {"contains_match", "number_overlap_ratio", "shared_numbers"}
    assert 0.0 <= res["number_overlap_ratio"] <= 1.0


def test_token_f1_nan_and_identity():
    assert JUDGE.token_f1(float("nan"), "x") == 0.0
    assert JUDGE.token_f1("revenue grew 5", float("nan")) == 0.0
    assert JUDGE.token_f1("revenue grew 5", "revenue grew 5") == pytest.approx(1.0)
    assert 0.0 < JUDGE.token_f1("revenue grew 5 percent", "revenue grew 5") < 1.0
