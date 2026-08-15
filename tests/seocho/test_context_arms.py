"""The arms must differ in form only — never in budget, and never in knowledge.

`ADR-0105` recorded both ways this comparison has already been broken once: a
serializer that withheld text one side held, and a budget fix that handed the
graph lane the whole novel. These pin the invariants that stop either recurring,
plus the length trap the first run of this generator exposed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "make_context_arms", _ROOT / "scripts" / "serve_track" / "make_context_arms.py"
)
arms = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = arms
_spec.loader.exec_module(arms)

_COUNT = len


def _item(**kw):
    base = {
        "id": "x", "question": "q", "answer": "a",
        "corpus": ["Alpha supplies Beta.", "Beta assembles Gamma."],
        "gold_edges": [["Alpha", "supplies", "Beta"], ["Beta", "assembles", "Gamma"]],
    }
    base.update(kw)
    return base


def test_no_arm_exceeds_the_budget():
    out = arms.build_arms(_item(), budget=40, count=_COUNT, unit="chars")
    for name, arm in out["arms"].items():
        assert arm["used"] <= 40, f"{name} used {arm['used']} of a 40 budget"


def test_both_splits_the_budget_rather_than_doubling_it():
    """A `both` win must not be purchased with twice the context."""
    out = arms.build_arms(_item(), budget=500, count=_COUNT, unit="chars")
    single = max(out["arms"]["vector"]["used"], out["arms"]["graph"]["used"])
    assert out["arms"]["both"]["used"] <= 500
    assert out["arms"]["both"]["used"] < single * 2 or single == 0


def test_vector_matched_is_capped_at_the_graph_arms_actual_length():
    """The floor control: same passages, cut to what the graph form actually used."""
    out = arms.build_arms(_item(), budget=500, count=_COUNT, unit="chars")
    assert out["arms"]["vector_matched"]["used"] <= out["arms"]["graph"]["used"]


def test_units_are_never_truncated_mid_fact():
    """A clipped triple or half sentence changes meaning, not just length."""
    out = arms.build_arms(_item(), budget=45, count=_COUNT, unit="chars")
    for name in ("vector", "graph"):
        for line in out["arms"][name]["context"].splitlines():
            if not line:
                continue
            if name == "graph":
                assert line.startswith("(") and line.endswith(")"), line
            else:
                assert line.endswith("."), line


def test_graph_and_vector_carry_the_same_facts():
    """Knowledge parity. Without it a graph win is the vector arm being starved."""
    item = _item()
    out = arms.build_arms(item, budget=10_000, count=_COUNT, unit="chars")
    prose = out["arms"]["vector"]["context"]
    for subject, _rel, obj in item["gold_edges"]:
        assert subject in prose and obj in prose, (
            f"{subject}/{obj} is in the graph form but not readable from the passages"
        )


def test_graph_form_deduplicates_repeated_facts():
    item = _item(gold_edges=[["A", "r", "B"], ["A", "r", "B"], ["B", "r2", "C"]])
    out = arms.build_arms(item, budget=10_000, count=_COUNT, unit="chars")
    assert out["arms"]["graph"]["units"] == 2


def test_an_item_with_no_graph_form_is_marked_not_comparable():
    """Excluded, not silently scored as a vector win."""
    out = arms.build_arms(_item(gold_edges=[]), budget=500, count=_COUNT, unit="chars")
    assert out["comparable"] is False


def test_an_item_with_no_passages_is_marked_not_comparable():
    out = arms.build_arms(_item(corpus=[]), budget=500, count=_COUNT, unit="chars")
    assert out["comparable"] is False


def test_budget_unit_is_recorded_so_tokens_and_chars_never_mix():
    out = arms.build_arms(_item(), budget=500, count=_COUNT, unit="chars")
    assert out["budget_unit"] == "chars"


def test_malformed_edges_are_dropped_not_rendered():
    item = _item(gold_edges=[["A", "r", "B"], ["A", "r"], ["A", "r", "B", "C"]])
    out = arms.build_arms(item, budget=10_000, count=_COUNT, unit="chars")
    assert out["arms"]["graph"]["units"] == 1


def test_strata_survive_into_the_arms_row():
    """Grouping by stratum is the whole analysis; losing it silently is fatal."""
    item = _item(strata={"stratum": "S2_joined", "hops": 2})
    out = arms.build_arms(item, budget=500, count=_COUNT, unit="chars")
    assert out["strata"]["stratum"] == "S2_joined"


def test_length_matched_prose_cannot_also_be_fact_matched():
    """Why `vector_matched` is a diagnostic, not a control.

    Prose stating a fact is inherently longer than the triple stating it, so an
    arm cut to the graph arm's length must drop facts. The first behaviour run
    confirmed it: 8 of 12 items lost the gold string entirely. The arm therefore
    measures information deprivation, not compactness, and a `graph` win over it
    is not evidence that structure helped. Pinned so nobody reads its score as a
    floor. The real structure-at-matched-length control is tracked separately.
    """
    item = _item(corpus=["Northgate Plant assembles Model K1 for the Norland market."],
                 gold_edges=[["Northgate Plant", "assembles", "Model K1"]])
    out = arms.build_arms(item, budget=10_000, count=_COUNT, unit="chars")
    matched = out["arms"]["vector_matched"]
    assert matched["used"] <= out["arms"]["graph"]["used"]
    assert matched["context"] != out["arms"]["vector"]["context"], (
        "if the trim is a no-op the arm is silently a duplicate of `vector`"
    )
