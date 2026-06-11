"""Fact-triple quorum for debate convergence (seocho-6gt, Lamport).

Citation-Jaccard is an echo-chamber signal: two agents citing the same source
while asserting CONTRADICTORY facts read as 'converged'. The quorum layer makes
convergence additionally require >2/3 of the panel to assert the same top facts.
Default (no quorum_curve) preserves prior behavior exactly.
"""

from __future__ import annotations

from seocho.debate import (
    convergence_curve,
    extract_fact_triples,
    quorum_report,
    should_stop,
    triple_agreement_curve,
)

# Same citation, contradictory revenue numbers — the echo chamber.
_ECHO_PANEL = {
    "openai": "ACME reported revenue of $2.1 billion in 2023. [src:10k]",
    "kimi": "ACME reported revenue of $9.9 billion in 2023. [src:10k]",
}
# Same citation, same fact.
_AGREE_PANEL = {
    "openai": "ACME reported revenue of $2.1 billion in 2023. [src:10k]",
    "kimi": "ACME reported revenue of $2.1 billion in 2023. [src:10k]",
}


def test_extract_fact_triples_numeric_fact():
    triples = extract_fact_triples("ACME reported revenue of $2.1 billion in 2023. [src:10k]")
    assert ("acme", "reported revenue of", "2.1 billion") in triples


def test_canonicalization_matches_format_variants():
    a = extract_fact_triples("ACME reported revenue of $2,100,000,000.")
    b = extract_fact_triples("ACME reported revenue of 2100000000.")
    assert a and a == b  # commas/$ stripped -> same canonical triple


def test_echo_chamber_jaccard_converges_but_quorum_does_not():
    jacc = convergence_curve([_ECHO_PANEL])
    quorum = triple_agreement_curve([_ECHO_PANEL])
    assert jacc[-1] == 1.0          # identical citations -> "converged" by Jaccard
    assert quorum[-1] < 2 / 3       # contradictory facts -> no quorum


def test_should_stop_blocks_echo_chamber_convergence():
    jacc = convergence_curve([_ECHO_PANEL])
    quorum = triple_agreement_curve([_ECHO_PANEL])
    stop, reason = should_stop(
        jacc, elapsed_ms=0, tokens=0, max_rounds=5,
        quorum_curve=quorum,
    )
    assert stop is False and reason == ""  # keeps debating instead of converging


def test_should_stop_converges_when_facts_agree():
    jacc = convergence_curve([_AGREE_PANEL])
    quorum = triple_agreement_curve([_AGREE_PANEL])
    stop, reason = should_stop(
        jacc, elapsed_ms=0, tokens=0, max_rounds=5,
        quorum_curve=quorum,
    )
    assert stop is True
    assert "convergence" in reason and "fact-quorum" in reason


def test_default_behavior_unchanged_without_quorum_curve():
    jacc = convergence_curve([_ECHO_PANEL])
    stop, reason = should_stop(jacc, elapsed_ms=0, tokens=0, max_rounds=5)
    assert stop is True and "convergence" in reason  # prior contract preserved


def test_other_stop_criteria_unaffected_by_quorum_block():
    # echo chamber blocked from converging, but the hard round cap still stops it
    jacc = [1.0, 1.0, 1.0]
    quorum = [0.0, 0.0, 0.0]
    stop, reason = should_stop(jacc, elapsed_ms=0, tokens=0, max_rounds=3,
                               quorum_curve=quorum)
    # stops via a NON-convergence rule (stagnation fires before the round cap)
    assert stop is True and "convergence" not in reason


def test_quorum_report_exposes_contested_for_moderator():
    report = quorum_report(_ECHO_PANEL)
    # both contradictory revenue triples are contested -> moderator_chain input
    assert report["score"] < 2 / 3
    assert len(report["contested"]) == 2 and report["agreed"] == []
    assert report["panel_size"] == 2
