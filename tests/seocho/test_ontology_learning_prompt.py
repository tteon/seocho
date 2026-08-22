from __future__ import annotations

import pytest

from seocho.ontology.learning_prompt import (
    candidate_summary,
    normalize_candidates,
    prompt_for_arm,
)


def test_arms_share_a_contract_but_have_distinct_framing() -> None:
    basic_system, basic_user = prompt_for_arm("basic", "Ada works at Acme.")
    framed_system, framed_user = prompt_for_arm("llms4ol", "Ada works at Acme.")

    assert basic_system == framed_system
    assert "LLMs4OL" not in basic_user
    assert "LLMs4OL" in framed_user
    assert "Ada works at Acme." in basic_user


def test_candidate_summary_is_content_free_and_requires_arrays() -> None:
    summary = candidate_summary(
        {
            "terms": [{"term": "Ada", "type": "Person", "evidence": "Ada"}],
            "taxonomy": [],
            "relations": [{"predicate": "WORKS_AT"}],
            "axioms": [],
        }
    )
    assert summary == {
        "candidate_counts": {"terms": 1, "taxonomy": 0, "relations": 1, "axioms": 0},
        "candidate_total": 2,
        "evidence_coverage": 0.5,
    }
    with pytest.raises(ValueError, match="terms must be an array"):
        normalize_candidates(
            {"terms": {}, "taxonomy": [], "relations": [], "axioms": []}
        )
