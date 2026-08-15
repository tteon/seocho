"""The scorer must not lose a correct answer to invisible characters.

The first behaviour run marked a correct `Model K1` wrong because MiniMax emitted
U+202F between the words. A scorer that is literal about invisible characters
under-counts, and the under-count attaches to whichever arm formats more
prettily — which is the arm identity the experiment is trying to measure.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_arms", _ROOT / "scripts" / "serve_track" / "run_arms.py"
)
runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)


def test_narrow_no_break_space_does_not_fail_a_correct_answer():
    assert runner.check_deterministic("Model K1", "Model K1") is True


def test_case_and_surrounding_prose_do_not_matter():
    assert runner.check_deterministic("Model K1", "The answer is model k1.") is True


def test_a_wrong_answer_is_still_wrong():
    assert runner.check_deterministic("Model K1", "Model L9") is False


def test_prose_gold_defers_to_the_judge():
    """Containment over a paragraph passes on coincidence, so it must not run."""
    gold = "Basal cell carcinoma is the most common type of skin cancer worldwide."
    assert runner.check_deterministic(gold, "anything") is None


def test_absence_gold_accepts_a_refusal():
    assert runner.check_deterministic("none", "NOT STATED") is True
    assert runner.check_deterministic("none", "Model K2") is False


def test_empty_answer_is_not_silently_correct():
    assert runner.check_deterministic("Model K1", "") is False


def test_a_list_gold_defers_to_the_judge():
    """Containment cannot score a set, in either direction.

    It marks a correct answer wrong when the order differs or an "and" appears,
    and it marks a wrong answer right when the reply names every gold item plus
    two that do not belong. Both happened on the negation stratum.
    """
    gold = "Model K2, Model M3, Model R4"
    assert runner.check_deterministic(gold, "Model K2, Model M3, and Model R4") is None
    assert runner.check_deterministic(gold, "anything at all") is None


def test_a_single_value_gold_still_scores_deterministically():
    assert runner.check_deterministic("Eastfield Plant", "Eastfield Plant") is True


_NEG_GOLD = "Model K2, Model M3, Model R4"
_NEG_EXCLUDED = ["Model K1", "Model L9", "Model P7"]


def test_set_scoring_accepts_reordering_and_supporting_detail():
    """The exact shape the model produced, which containment scored wrong."""
    answer = ("Model K2, Model M3, and Model R4 are not sold in Norland.\n"
              "- Model K2 is sold in Sudmark\n- Model M3 is sold in Verrat")
    assert runner.check_set(_NEG_GOLD, _NEG_EXCLUDED, answer) is True


def test_set_scoring_rejects_an_omission():
    assert runner.check_set(_NEG_GOLD, _NEG_EXCLUDED, "Model K2 and Model M3") is False


def test_set_scoring_rejects_an_item_that_does_not_belong():
    """The error an LLM judge and a containment check both miss."""
    answer = "Model K2, Model M3, Model R4, and Model K1"
    assert runner.check_set(_NEG_GOLD, _NEG_EXCLUDED, answer) is False


def test_set_scoring_declines_when_there_is_no_complement():
    """Without the excluded set the check cannot see additions, so it must abstain."""
    assert runner.check_set(_NEG_GOLD, [], "anything") is None
    assert runner.check_set("single value", _NEG_EXCLUDED, "single value") is None


def test_refusal_detection_covers_paraphrase_not_just_one_wording():
    """The bug this replaced: a string list caught 'does not contain' and missed
    'does not include', scoring four correct refusals as inventions."""
    for reply in (
        "The provided context does not include information about HTTP 409.",
        "The provided context does not contain that detail.",
        "Based on the provided context, I cannot answer this question.",
        "This cannot be answered from the documents.",
        "That value is not specified anywhere in the context.",
        "NOT STATED",
        "There is no information about the metering coefficients.",
        "The answer is not fully answerable from the provided documents.",
    ):
        assert runner.check_refusal(reply) is True, reply


def test_an_asserted_answer_is_not_a_refusal():
    for reply in (
        "The Slack channel is #obs-alerts.",
        "The default limit is 10 MiB per file.",
        "Northgate Plant assembles Model K1.",
    ):
        assert runner.check_refusal(reply) is False, reply
