"""GOPTS G3 (ADR-0097) — pattern catalog registry contract.

Pins the cypher_shape → PatternSpec mapping that the post-G3
CypherBuilder.build() dispatcher depends on. A regression where a
pattern is dropped from the registry would silently fall back to
``neighbors`` and only show up downstream as wrong Cypher; these
tests fail loudly instead.
"""

from __future__ import annotations

from seocho.query import pattern_catalog
from seocho.query.contracts import PatternSpec


_EXPECTED_SHAPES = {
    "entity_lookup": "pattern:entity_lookup_by_name",
    "relationship_lookup": "pattern:relationship_lookup_hop1",
    "financial_metric_lookup": "pattern:finance_metric_value",
    "financial_metric_delta": "pattern:finance_metric_delta",
    "neighbors": "pattern:neighbors_one_hop",
    "path": "pattern:shortest_path",
    "count": "pattern:label_count",
    "list_all": "pattern:label_list",
}


def test_every_pre_g3_cypher_shape_has_a_pattern() -> None:
    """The 8 cypher_shape strings that CypherBuilder.build() used to
    dispatch on must each resolve to exactly one registered pattern."""
    for shape, expected_pattern_id in _EXPECTED_SHAPES.items():
        spec = pattern_catalog.get_by_cypher_shape(shape)
        assert spec is not None, f"cypher_shape '{shape}' is unregistered"
        assert spec.pattern_id == expected_pattern_id, (
            f"cypher_shape '{shape}' resolves to {spec.pattern_id!r}, "
            f"expected {expected_pattern_id!r}"
        )


def test_unknown_shape_returns_none() -> None:
    assert pattern_catalog.get_by_cypher_shape("not_a_real_shape") is None


def test_neighbors_is_present_for_default_fallback() -> None:
    """build() falls back to the 'neighbors' shape when the intent does
    not match any registered cypher_shape. The pattern must exist."""
    spec = pattern_catalog.get_by_cypher_shape("neighbors")
    assert spec is not None
    assert spec.cost_hints.get("is_default_fallback") is True


def test_match_groups_by_intent_id() -> None:
    """match(intent_id) returns every pattern under that intent. For the
    G3 surface, relationship_lookup intent groups two patterns
    (relationship_lookup + shortest_path)."""
    relationship_patterns = pattern_catalog.match("relationship_lookup")
    relationship_ids = {p.pattern_id for p in relationship_patterns}
    assert "pattern:relationship_lookup_hop1" in relationship_ids
    assert "pattern:shortest_path" in relationship_ids


def test_all_patterns_have_template_factory() -> None:
    """No PatternSpec ships without a callable template_factory."""
    for spec in pattern_catalog.all_patterns():
        assert callable(spec.template_factory), (
            f"pattern {spec.pattern_id} has non-callable template_factory"
        )


def test_pattern_spec_is_frozen() -> None:
    """PatternSpec is frozen — accidental mutation must fail loudly."""
    import dataclasses
    spec = pattern_catalog.all_patterns()[0]
    assert dataclasses.is_dataclass(spec)
    try:
        spec.pattern_id = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("PatternSpec mutation should have raised FrozenInstanceError")
