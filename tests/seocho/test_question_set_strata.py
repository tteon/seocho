"""The stratified question set must be correct by construction, not by hand.

A gold answer typed by a human is exactly the kind of thing that is quietly
wrong and then silently decides an experiment. These tests re-derive every
answer from the declared gold edges and check that each stratum actually has the
property it claims to isolate — otherwise a "graph wins on multi-hop" result
could be a set of mislabelled one-hop questions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "make_question_set", _ROOT / "scripts" / "serve_track" / "make_question_set.py"
)
qs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = qs
_spec.loader.exec_module(qs)

ITEMS = qs.build()


def test_every_gold_edge_is_present_in_the_corpus():
    """A path the graph form states must also be readable from the passages.

    If it is not, the arms are not given the same knowledge and any graph win is
    an artefact of the vector arm being starved.
    """
    for item in ITEMS:
        joined = " ".join(item.corpus)
        for subject, _relation, obj in item.gold_edges:
            assert subject in joined, f"{item.stratum}: {subject!r} missing from corpus"
            assert obj in joined, f"{item.stratum}: {obj!r} missing from corpus"


def test_hop_count_matches_the_declared_chain():
    """`hops` must equal the length of the gold path, not a hopeful label."""
    for item in ITEMS:
        if item.answer_type != "joined":
            continue
        assert len(item.gold_edges) == item.hops, (
            f"{item.stratum}: declares hops={item.hops} but the gold path has "
            f"{len(item.gold_edges)} edges"
        )


def test_joined_strata_really_span_multiple_passages():
    """S2/S3 exist to force composition; a single-passage item there is mislabelled."""
    for item in ITEMS:
        if item.answer_type == "joined":
            assert item.dispersion >= 2, f"{item.stratum}: dispersion={item.dispersion}"
            assert item.hops >= 2, f"{item.stratum}: hops={item.hops}"


def test_extractive_strata_are_single_hop():
    for item in ITEMS:
        if item.stratum.startswith(("S1", "S7")):
            assert item.hops == 1 and item.dispersion == 1


def test_chained_answers_are_derivable_from_the_edges():
    """Re-derive each joined answer by walking its own gold path."""
    for item in ITEMS:
        if item.answer_type != "joined":
            continue
        node = item.gold_edges[0][0]
        for subject, _relation, obj in item.gold_edges:
            assert subject == node, (
                f"{item.stratum}: gold path is not connected at {subject!r}"
            )
            node = obj
        assert node == item.answer, (
            f"{item.stratum}: walking the gold path yields {node!r}, "
            f"but the declared answer is {item.answer!r}"
        )


def test_aggregate_answers_match_the_edge_count():
    for item in ITEMS:
        if item.answer_type == "aggregate":
            assert item.answer == str(len(item.gold_edges)), (
                f"{item.stratum}: answer {item.answer!r} != {len(item.gold_edges)} gold edges"
            )


def test_distractor_stratum_actually_has_distractors():
    """S7 is the control for 'graph wins because retrieval is noisy'."""
    s7 = [i for i in ITEMS if i.stratum.startswith("S7")]
    assert s7, "S7 missing — without it a graph win cannot be separated from retrieval precision"
    plain = {len(i.corpus) for i in ITEMS if i.stratum.startswith("S1")}
    for item in s7:
        assert len(item.corpus) > max(plain), "S7 corpus is not larger than the plain one"


def test_ambiguous_stratum_has_two_entities_sharing_a_surface_name():
    amb = [i for i in ITEMS if i.stratum.startswith("S6")]
    assert amb
    for item in amb:
        joined = " ".join(item.corpus)
        assert joined.count("Aurora Metals") >= 3, (
            "S6 needs the bare name plus both qualified entities present"
        )


def test_every_stratum_states_a_prediction_in_advance():
    """A stratum without a prediction cannot produce a negative result."""
    for item in ITEMS:
        assert item.prediction.strip(), f"{item.stratum} has no prediction"
        assert len(item.prediction) > 20, f"{item.stratum} prediction is too vague"


@pytest.mark.parametrize("stratum", [
    "S1_extractive", "S2_joined", "S3_deep_join",
    "S4_aggregation", "S5_absence", "S6_ambiguous", "S7_distractor",
])
def test_all_seven_strata_are_populated(stratum):
    assert any(i.stratum == stratum for i in ITEMS), f"{stratum} has no items"
