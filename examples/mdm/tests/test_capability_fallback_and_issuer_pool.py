from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MDM = Path(__file__).resolve().parents[1]


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, MDM / stem)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FALLBACK = _load("73_sdcr_capability_fallback.py", "sdcr_fallback73")
ISSUER = _load("74_validated_issuer_pool.py", "validated_issuer74")


def test_clustered_bootstrap_is_seed_deterministic() -> None:
    """Reported intervals must reproduce exactly on a re-run."""
    pairs = [("AAA", 0.10), ("AAA", 0.12), ("BBB", -0.02), ("CCC", 0.30), ("DDD", 0.0)]
    first = FALLBACK.clustered_bootstrap(pairs, iterations=500, seed=42)
    second = FALLBACK.clustered_bootstrap(pairs, iterations=500, seed=42)
    assert first == second
    lo, hi = first
    assert lo <= hi


def test_clustered_bootstrap_refuses_a_single_cluster() -> None:
    """One issuer cannot support a clustered interval; return NaN rather than a fake one."""
    lo, hi = FALLBACK.clustered_bootstrap([("AAA", 0.1), ("AAA", 0.2)], iterations=50)
    assert lo != lo and hi != hi  # NaN


def test_fallback_policy_constants_match_the_measured_arms() -> None:
    assert FALLBACK.PRIMARY == "sdcr"
    assert FALLBACK.FALLBACK == "slot_only"  # frozen TF-IDF top-2 capability team


def test_validated_issuer_requires_exactly_one_accepted_ticker() -> None:
    tickers = {"UAL", "CTAS", "CSCO"}
    assert ISSUER.validated_issuer("Buyback suspension at UAL hurts EPS.", tickers) == "UAL"
    # Two accepted tickers in one question is a cannot-link, not a coin flip.
    assert ISSUER.validated_issuer("UAL versus CTAS dividend payout.", tickers) == ""
    # An accounting acronym is not an issuer.
    assert ISSUER.validated_issuer("Diluted EPS grew this year.", tickers) == ""


def test_legacy_extractor_reproduces_the_reported_defect() -> None:
    """The heuristic picks the trailing acronym over the named company.

    This is the mechanism behind the merged-issuer candidates, so it is pinned:
    if the legacy behaviour ever changes, the paper's disclosure must change too.
    """
    question = "Keysight's (KEYS) IP litigation risk may impact earnings & valuation."
    assert ISSUER.legacy_issuer(question) == "IP"
    assert ISSUER.validated_issuer(question, {"KEYS"}) == "KEYS"


def test_decision_axes_are_shared_between_extractors() -> None:
    """Only issuer identification may differ between the frozen and corrected pools."""
    axes = ISSUER.decision_axes("Liquidity and buyback policy under board oversight.")
    assert "liquidity_capital_allocation" in axes
    assert "governance_audit" in axes


def test_stable_fraction_is_deterministic_across_processes() -> None:
    """The dev/held-out split must not depend on hash randomization."""
    assert ISSUER.stable_fraction("CTAS") == ISSUER.stable_fraction("CTAS")
    assert 0.0 <= ISSUER.stable_fraction("CTAS") <= 1.0


def test_paid_fallback_eval_reuses_the_frozen_harness() -> None:
    """The answer arm must not re-implement prompt or scoring.

    Re-implementing either would void the fair-comparison requirement, so the
    paid script imports them from 64_revised_answer_eval.py and this pins it.
    """
    source = (MDM / "75_fallback_answer_eval.py").read_text()
    assert "64_revised_answer_eval.py" in source
    for reused in ("REF.SYSTEM", "REF.serialize", "REF.f1", "REF.nums"):
        assert reused in source, f"{reused} must come from the frozen harness"
    # Same decoding settings as the frozen arms.
    assert "temperature=0" in source and "max_tokens=750" in source
    # Persist after every completion so an interrupted paid run never repeats work.
    assert source.count("OUT.write_text") >= 1 and "completed" in source


def test_paid_fallback_eval_refuses_to_impute() -> None:
    source = (MDM / "75_fallback_answer_eval.py").read_text()
    assert "refusing to impute" in source
    assert "the policy is meant to guarantee non-empty evidence" in source
