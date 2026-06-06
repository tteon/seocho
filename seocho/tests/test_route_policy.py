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


@pytest.mark.parametrize(
    "intent_id,expected",
    [
        ("relationship_lookup", "R4_GRAPH_JOIN"),
        ("responsibility_lookup", "R4_GRAPH_JOIN"),
        ("engineering_tradeoff_lookup", "R5_LONG_CONTEXT_REASONING"),
        ("entity_lookup", "R1_LOOKUP"),
        ("metric_lookup", "R1_LOOKUP"),
        ("", "R1_LOOKUP"),
    ],
)
def test_derive_route_class_mapping(intent_id, expected):
    """Shared route-class derivation (runtime lane gating + evidence bundle agree)."""
    from seocho.query.intent import derive_route_class
    assert derive_route_class(intent_id=intent_id) == expected


def test_derive_route_class_explanation_depends_on_source():
    from seocho.query.intent import derive_route_class
    assert derive_route_class(intent_id="explanation_lookup", source_types=["text"]) == "R5_LONG_CONTEXT_REASONING"
    assert derive_route_class(intent_id="explanation_lookup", source_types=["graph"]) == "R1_LOOKUP"


def test_runtime_lane_gate_default_off():
    """F3 runtime gate must be OFF unless explicitly enabled (no silent change)."""
    import os
    from seocho.local_engine import _lane_policy_enabled
    os.environ.pop("SEOCHO_LANE_POLICY", None)
    assert _lane_policy_enabled() is False
    os.environ["SEOCHO_LANE_POLICY"] = "1"
    try:
        assert _lane_policy_enabled() is True
    finally:
        os.environ.pop("SEOCHO_LANE_POLICY", None)


def test_relationship_intent_keeps_graph_context_lane():
    """A relationship query → R4 → hybrid → graph context retained (not gated out)."""
    from seocho.query.intent import derive_route_class
    from seocho.query.route_policy import recommend_lane
    lane = recommend_lane(derive_route_class(intent_id="relationship_lookup"), "deterministic")
    assert lane.retrieval == "hybrid"  # graph context kept for relational


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


# --- Answerability Gate (ontology-as-predicate, opt-in) ---

from seocho.query.route_policy import answerability_gate, ANSWERABILITY_VERSION


def test_gate_off_by_default_unchanged():
    """No ontology relations supplied → gate OFF → identical to route_policy@v1."""
    p = recommend_lane("R1_LOOKUP", "deterministic")
    assert p.retrieval == "vector" and p.answerability is None


def test_gate_certified_when_required_relation_declared():
    g = answerability_gate(["PROPOSES"], ["PROPOSES", "SENT", "RESOLVES"])
    assert g.verdict == "CERTIFIED" and "PROPOSES" in g.declared_match
    assert g.version == ANSWERABILITY_VERSION


def test_gate_uncovered_when_relation_absent():
    # E4: 'decision' arm declares no opinion relation
    g = answerability_gate(["HOLDS_POSITION"], ["PROPOSES", "SENT", "DECIDES", "RESOLVES"])
    assert g.verdict == "UNCOVERED" and g.declared_match == ()


def test_gate_partial_when_only_related_relation_declared():
    g = answerability_gate(["HOLDS_POSITION"], ["SUPPORTS", "PROPOSES"],
                           partial_relations=["SUPPORTS", "OPPOSES"])
    assert g.verdict == "PARTIAL" and "SUPPORTS" in g.declared_match


def test_certified_deterministic_routes_graph_llm_free():
    p = recommend_lane("R1_LOOKUP", "deterministic",
                       required_relations=["PROPOSES"], declared_relations=["PROPOSES", "SENT"])
    assert p.retrieval == "graph_certified" and p.escalate_synthesis is False
    assert p.answerability.verdict == "CERTIFIED"


def test_uncovered_relational_firewalled_to_vector():
    # relational class that would normally be hybrid (pulls graph context), but the
    # ontology declares no serving relation → firewall drops to vector.
    p = recommend_lane("R4_GRAPH_JOIN", "hybrid",
                       required_relations=["HOLDS_POSITION"],
                       declared_relations=["PROPOSES", "SENT", "RESOLVES"])
    assert p.retrieval == "vector"
    assert p.answerability.verdict == "UNCOVERED"


def test_uncovered_does_not_upgrade_to_graph():
    """Firewall: a deterministic lookup whose relation is undeclared must NOT get
    the graph_certified lane (never serve LLM-free from an undeclared edge)."""
    p = recommend_lane("R1_LOOKUP", "deterministic",
                       required_relations=["HOLDS_POSITION"], declared_relations=["PROPOSES"])
    assert p.retrieval == "vector" and p.answerability.verdict == "UNCOVERED"
