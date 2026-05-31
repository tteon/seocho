"""Regression tests for the LLM-as-judge scoring pass (finder_judge.py).

The judge LLM is ALWAYS mocked here — tests must never call grok. They lock the
parser robustness (fenced/garbage JSON), verdict->score mapping, the panel
aggregation, and the same-case paired analysis. Loaded via importlib.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_BENCH = Path(__file__).resolve().parents[2] / "scripts" / "benchmarks"
_spec = importlib.util.spec_from_file_location("finder_judge", _BENCH / "finder_judge.py")
assert _spec and _spec.loader
JUDGE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(JUDGE)


# ---- parser robustness -----------------------------------------------------

def test_parse_judge_strips_markdown_fence():
    out = JUDGE._parse_judge('```json\n{"verdict":"correct","score":1.0}\n```')
    assert out["verdict"] == "correct" and out["score"] == 1.0
    assert out["parse_error"] is False


def test_parse_judge_garbage_defaults_incorrect():
    out = JUDGE._parse_judge("the model rambled with no json")
    assert out["verdict"] == "incorrect" and out["score"] == 0.0
    assert out["parse_error"] is True


def test_parse_judge_verdict_without_score_uses_map():
    out = JUDGE._parse_judge('{"verdict":"partial"}')
    assert out["score"] == 0.5
    out2 = JUDGE._parse_judge('{"verdict":"bogus"}')
    assert out2["verdict"] == "incorrect" and out2["score"] == 0.0


# ---- judge_one wiring (mocked LLM, temperature=0) --------------------------

class _FakeJudgeLLM:
    def __init__(self, text):
        self.text_payload = text
        self.calls = []

    def complete(self, *, system, user, temperature=None):
        self.calls.append({"temperature": temperature})
        return type("R", (), {"text": self.text_payload})()


def test_judge_one_mocks_llm_and_uses_temp_zero():
    llm = _FakeJudgeLLM('{"verdict":"correct","score":1.0}')
    out = JUDGE.judge_one(llm, "Q", "gold", "candidate")
    assert out["verdict"] == "correct"
    assert llm.calls and llm.calls[0]["temperature"] == 0.0


def test_judge_one_falls_back_when_no_temperature_kwarg():
    class _NoTemp:
        def complete(self, *, system, user):
            return type("R", (), {"text": '{"verdict":"partial","score":0.5}'})()
    out = JUDGE.judge_one(_NoTemp(), "Q", "gold", "cand")
    assert out["verdict"] == "partial"


# ---- panel + agreement -----------------------------------------------------

def test_panel_majority_and_disagreement():
    per = {"grok": {"verdict": "correct", "score": 1.0},
           "gpt": {"verdict": "incorrect", "score": 0.0}}
    p = JUDGE._panel(per)
    assert p["panel_score"] == 0.5
    assert p["disagreement"] is True
    # tie -> stricter verdict wins
    assert p["panel_verdict"] in {"incorrect", "correct"}


def test_panel_unanimous_no_disagreement():
    per = {"a": {"verdict": "correct", "score": 1.0},
           "b": {"verdict": "correct", "score": 1.0}}
    p = JUDGE._panel(per)
    assert p["panel_score"] == 1.0 and p["disagreement"] is False


def test_cohen_kappa_perfect_and_none():
    assert JUDGE._cohen_kappa(["c", "i", "c"], ["c", "i", "c"]) == 1.0
    assert JUDGE._cohen_kappa([], []) == 0.0


# ---- paired analysis -------------------------------------------------------

def test_paired_analysis_pairs_by_case():
    judged = [
        {"case_id": "x1", "slice": "S1", "retrieval": "vector", "panel_score": 0.5},
        {"case_id": "x1", "slice": "S1", "retrieval": "graph", "arm": "medium", "panel_score": 1.0},
        {"case_id": "x2", "slice": "S1", "retrieval": "vector", "panel_score": 1.0},
        {"case_id": "x2", "slice": "S1", "retrieval": "graph", "arm": "medium", "panel_score": 0.0},
    ]
    out = JUDGE._paired_analysis(judged)
    key = "graph|medium vs vector"
    assert key in out
    rec = out[key]
    assert rec["n_paired"] == 2
    assert rec["lane_wins"] == 1 and rec["vector_wins"] == 1
    # x1: +0.5 (graph 1.0 - vector 0.5), x2: -1.0 (graph 0.0 - vector 1.0) -> mean -0.25
    assert rec["mean_delta"] == -0.25
