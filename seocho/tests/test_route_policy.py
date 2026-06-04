"""F3 — data-grounded retrieval-lane policy tests.

Pins the measured contract: vector ≈ hybrid ≫ graph across FinDER/BC3/AMI, so the
policy NEVER routes to pure graph and uses the cheap vector-only path (skipping
expensive synthesis) for deterministic single-lookups.
"""
from __future__ import annotations

import pytest

from seocho.query.route_policy import recommend_lane, ROUTE_POLICY_VERSION


def test_deterministic_lookup_is_cheap_vector_no_escalation():
    p = recommend_lane("R1_LOOKUP", "deterministic")
    assert p.retrieval == "vector"
    assert p.escalate_synthesis is False
    assert p.policy_version == ROUTE_POLICY_VERSION


def test_uncertain_lookup_is_vector_but_escalates():
    p = recommend_lane("R1_LOOKUP", "non_deterministic")
    assert p.retrieval == "vector"
    assert p.escalate_synthesis is True


@pytest.mark.parametrize("rc", ["R4_GRAPH_JOIN", "R5_LONG_CONTEXT_REASONING"])
def test_relational_routes_to_hybrid_not_pure_graph(rc):
    p = recommend_lane(rc, "deterministic")
    # measured: graph alone <= vector on every dataset → use hybrid, never graph
    assert p.retrieval == "hybrid"
    assert p.escalate_synthesis is True


@pytest.mark.parametrize(
    "rc,det",
    [
        ("R1_LOOKUP", "deterministic"),
        ("R1_LOOKUP", "non_deterministic"),
        ("R1_LOOKUP", "hybrid"),
        ("R4_GRAPH_JOIN", "deterministic"),
        ("R5_LONG_CONTEXT_REASONING", "non_deterministic"),
        ("", ""),  # defaults
    ],
)
def test_never_recommends_pure_graph(rc, det):
    assert recommend_lane(rc, det).retrieval in {"vector", "hybrid"}


def test_unknown_route_class_defaults_safely():
    p = recommend_lane("", "")
    assert p.retrieval in {"vector", "hybrid"}
    assert p.policy_version == ROUTE_POLICY_VERSION
    assert p.rationale  # non-empty


def test_route_profile_carries_lane_policy():
    """_build_route_profile must emit the lane_policy so downstream + traces see it."""
    from seocho.query.intent import _build_route_profile
    prof = _build_route_profile(
        question="What is the revenue?",
        intent={"intent_id": "value_lookup", "focus_slots": []},
        semantic_context={}, memory_payload={},
        candidate_entities=[], selected_triples=[],
        grounded_slots=[], missing_slots=[],
    )
    assert "lane_policy" in prof
    lp = prof["lane_policy"]
    assert lp["retrieval"] in {"vector", "hybrid"}
    assert lp["policy_version"] == ROUTE_POLICY_VERSION
    assert isinstance(lp["escalate_synthesis"], bool)
