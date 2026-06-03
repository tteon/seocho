"""Scored ontology grounding (icml fibo_ground port) contract tests.

Pins the lexical scorer's ranking behavior (reproducing the fibo_ground
example: 'audit committee' grounds to committee-bearing types) + the
threshold/top_k gating + edge/label grounding over an ontology.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho.query.ontology_grounding import (
    ground,
    ground_edge_type,
    ground_node_label,
    lexical_similarity,
    tokenize_type_name,
)


def test_tokenize_camel_and_snake() -> None:
    assert tokenize_type_name("hasCommittee") == {"committee"}      # 'has' is a stopword
    assert tokenize_type_name("HAS_COMMITTEE") == {"committee"}
    assert tokenize_type_name("LED_BY") == {"led"}                   # 'by' stopword
    assert tokenize_type_name("audit committee") == {"audit", "committee"}


def test_lexical_similarity_committee_example() -> None:
    # the fibo_ground example: 'audit committee' should score committee-types high
    s_has = lexical_similarity("audit committee", "hasCommittee")
    s_oversee = lexical_similarity("audit committee", "OVERSEES")
    assert s_has > 0.4
    assert s_oversee == 0.0
    assert s_has > s_oversee


def test_ground_ranks_and_thresholds() -> None:
    cands = ["hasCommittee", "HAS_COMMITTEE", "OVERSEES", "REPORTS_TO"]
    ranked = ground("audit committee", cands, top_k=3, threshold=0.4)
    names = [n for n, _ in ranked]
    assert "hasCommittee" in names or "HAS_COMMITTEE" in names
    assert "OVERSEES" not in names  # below threshold (no token overlap)
    # scores descending
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_ground_empty_intent_returns_nothing() -> None:
    assert ground("", ["X", "Y"]) == []


def _audit_ontology() -> Ontology:
    return Ontology(
        name="audit",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Committee": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "hasCommittee": RelDef(source="Company", target="Committee", description="company committee"),
            "OVERSEES": RelDef(source="Committee", target="Company", description="oversight"),
            "MANAGES": RelDef(source="Person", target="Company", description="leadership"),
        },
    )


def test_ground_edge_type_collapses_to_canonical() -> None:
    onto = _audit_ontology()
    ranked = ground_edge_type("audit committee", onto, top_k=3, threshold=0.4)
    names = [n for n, _ in ranked]
    assert "hasCommittee" in names
    assert "MANAGES" not in names  # no token overlap with 'audit committee'


def test_ground_node_label_matches_label() -> None:
    onto = _audit_ontology()
    ranked = ground_node_label("committee", onto, top_k=2, threshold=0.4)
    assert ranked and ranked[0][0] == "Committee"


def test_ground_edge_type_uses_aliases() -> None:
    """Aliases/same_as are surface forms grounding can match, but the
    canonical type name is always returned."""
    onto = Ontology(
        name="alias",
        graph_model="lpg",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)}),
               "Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={
            "LED_BY": RelDef(source="Company", target="Person", description="leadership",
                             aliases=["managed by", "headed by"]),
        },
    )
    ranked = ground_edge_type("managed", onto, top_k=2, threshold=0.4)
    assert ranked and ranked[0][0] == "LED_BY"  # matched via 'managed by' alias → canonical
