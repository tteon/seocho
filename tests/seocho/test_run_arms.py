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
