"""Tests for ADR-0092 PropertyShaper."""

import pytest

from seocho.index.property_shaper import (
    PromotionRequest,
    PropertyShaper,
    REASONING_ROLE_ENUM,
    RELATIONSHIP_TYPES,
    REQUIRED_EDGE_FIELDS,
    REQUIRED_NODE_FIELDS,
    SEMANTIC_ROLE_ENUM,
)


@pytest.fixture
def shaper() -> PropertyShaper:
    return PropertyShaper()


def test_shape_node_fills_all_required_fields(shaper: PropertyShaper) -> None:
    out = shaper.shape_node({"id": "ent:foo", "name": "Foo Corp"})
    for field in REQUIRED_NODE_FIELDS:
        assert field in out, f"missing required field: {field}"
    assert out["id"] == "ent:foo"
    assert out["title"] == "Foo Corp"
    assert out["claim"] == "Foo Corp"
    assert out["agentSummary"].startswith("Foo Corp")
    assert out["semanticRole"] in SEMANTIC_ROLE_ENUM
    assert out["reasoningRole"] in REASONING_ROLE_ENUM
    assert out["answers"] == []
    assert out["useWhen"] == []
    assert out["confidence"] == 0.5
    assert out["sourceRefs"] == []
    assert "Foo Corp" in out["embeddingText"]


def test_shape_node_preserves_explicit_values(shaper: PropertyShaper) -> None:
    raw = {
        "id": "claim:graphcot:iterative_traversal",
        "title": "Graph-CoT iterative traversal",
        "claim": "Graph-CoT reasons through iterative graph traversal.",
        "semanticRole": "method",
        "reasoningRole": "strategy",
        "answers": ["How should an agent use a knowledge graph?"],
        "useWhen": ["question asks about Graph-CoT design"],
        "confidence": 0.86,
        "sourceRefs": ["paper:graph-cot-2024"],
    }
    out = shaper.shape_node(raw)
    assert out["semanticRole"] == "method"
    assert out["reasoningRole"] == "strategy"
    assert out["confidence"] == 0.86
    assert out["answers"] == ["How should an agent use a knowledge graph?"]
    assert out["sourceRefs"] == ["paper:graph-cot-2024"]
    embedding_text = out["embeddingText"]
    assert "Graph-CoT iterative traversal" in embedding_text
    assert "iterative graph traversal" in embedding_text
    assert "Graph-CoT design" in embedding_text


def test_shape_node_rejects_unknown_semantic_role(shaper: PropertyShaper) -> None:
    with pytest.raises(ValueError, match="semanticRole"):
        shaper.shape_node({"id": "x", "name": "x", "semanticRole": "made-up-role"})


def test_shape_node_rejects_unknown_reasoning_role(shaper: PropertyShaper) -> None:
    with pytest.raises(ValueError, match="reasoningRole"):
        shaper.shape_node({"id": "x", "name": "x", "reasoningRole": "made-up"})


def test_shape_node_requires_id_or_name(shaper: PropertyShaper) -> None:
    with pytest.raises(ValueError, match="requires either 'id' or 'name'"):
        shaper.shape_node({})


def test_shape_node_clips_confidence_to_unit_interval(shaper: PropertyShaper) -> None:
    over = shaper.shape_node({"id": "x", "name": "x", "confidence": 1.7})
    under = shaper.shape_node({"id": "y", "name": "y", "confidence": -0.4})
    assert over["confidence"] == 1.0
    assert under["confidence"] == 0.0


def test_compose_embedding_text_is_deterministic(shaper: PropertyShaper) -> None:
    raw = {
        "id": "n",
        "name": "Node",
        "claim": "A claim.",
        "answers": ["q1", "q2"],
        "useWhen": ["c1"],
    }
    a = shaper.shape_node(dict(raw))
    b = shaper.shape_node(dict(raw))
    assert a["embeddingText"] == b["embeddingText"]
    assert a["embeddingText"] == "Node A claim. A claim. q1 q2 c1"


def test_compose_embedding_text_respects_explicit_value(shaper: PropertyShaper) -> None:
    raw = {"id": "n", "name": "Node", "embeddingText": "  explicit text  "}
    out = shaper.shape_node(raw)
    assert out["embeddingText"] == "explicit text"


def test_shape_edge_accepts_canonical_types(shaper: PropertyShaper) -> None:
    for edge_type in RELATIONSHIP_TYPES:
        out = shaper.shape_edge({}, edge_type=edge_type)
        for field in REQUIRED_EDGE_FIELDS:
            assert field in out, f"missing edge field: {field} for {edge_type}"
        assert out["confidence"] == 0.5
        assert out["sourceRefs"] == []


def test_shape_edge_rejects_unknown_type(shaper: PropertyShaper) -> None:
    with pytest.raises(ValueError, match="edge_type"):
        shaper.shape_edge({}, edge_type="MENTIONS_NOT_REAL")


def test_shape_edge_rejects_unknown_reasoning_role(shaper: PropertyShaper) -> None:
    with pytest.raises(ValueError, match="reasoningRole"):
        shaper.shape_edge({"reasoningRole": "bogus"}, edge_type="MENTIONS")


def test_promotion_candidates_flags_long_lists(shaper: PropertyShaper) -> None:
    node = {
        "id": "n",
        "name": "Node",
        "sourceRefs": [f"src:{i}" for i in range(7)],
    }
    candidates = shaper.promotion_candidates(node)
    assert any(c.field == "sourceRefs" for c in candidates)
    assert all(isinstance(c, PromotionRequest) for c in candidates)


def test_promotion_candidates_flags_long_evidence_string(shaper: PropertyShaper) -> None:
    node = {
        "id": "n",
        "name": "Node",
        "evidenceText": "x" * 600,
    }
    candidates = shaper.promotion_candidates(node)
    assert any(c.field == "evidenceText" for c in candidates)


def test_promotion_candidates_returns_empty_for_small_node(shaper: PropertyShaper) -> None:
    assert shaper.promotion_candidates({"id": "n", "name": "Node"}) == []


def test_shaper_default_role_validation() -> None:
    with pytest.raises(ValueError, match="default_semantic_role"):
        PropertyShaper(default_semantic_role="not-a-role")
    with pytest.raises(ValueError, match="default_reasoning_role"):
        PropertyShaper(default_reasoning_role="not-a-role")


def test_shape_node_accepts_mixed_iterable_in_answers(shaper: PropertyShaper) -> None:
    out = shaper.shape_node(
        {
            "id": "n",
            "name": "Node",
            "answers": ("q1", "", None, "q2"),
            "useWhen": "single string",
        }
    )
    assert out["answers"] == ["q1", "q2"]
    assert out["useWhen"] == ["single string"]
