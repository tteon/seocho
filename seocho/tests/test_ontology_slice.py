"""Regression tests for seocho-cvys — ontology slice extraction."""

from __future__ import annotations

import pytest


def _make_finance_ontology():
    """Mimics the FIBO BE minimal slice from tutorial 3 (5 classes, 2 rels)."""
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name="fibo_be_minimal",
        version="1.0.0",
        nodes={
            "Party":              NodeDef(properties={"hasName": P(str, unique=True)}),
            "Person":             NodeDef(properties={"hasName": P(str, unique=True), "hasTitle": P(str)}),
            "LegalPerson":        NodeDef(properties={"hasName": P(str, unique=True)}),
            "FormalOrganization": NodeDef(properties={"hasName": P(str, unique=True)}),
            "Corporation":        NodeDef(properties={"hasName": P(str, unique=True)}),
            "Product":            NodeDef(properties={"hasName": P(str, unique=True)}),
        },
        relationships={
            "isOfficerOf": RelDef(source="Person", target="Corporation"),
            "MAKES":       RelDef(source="Corporation", target="Product"),
        },
    )


def test_slice_matches_explicit_label_in_intent() -> None:
    """A question mentioning 'Person' surfaces the Person label."""
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "List every Person in the graph")
    assert "Person" in sl.matched_labels
    assert sl.fallback_to_full is False


def test_slice_walks_neighbours_via_relationships() -> None:
    """Matching Corporation pulls in Person via isOfficerOf and Product via MAKES."""
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "Tell me about each Corporation")
    assert "Corporation" in sl.matched_labels
    # neighbours via isOfficerOf and MAKES
    assert "Person" in sl.related_labels
    assert "Product" in sl.related_labels
    assert "isOfficerOf" in sl.matched_relationships
    assert "MAKES" in sl.matched_relationships


def test_slice_falls_back_to_full_on_zero_matches() -> None:
    """A query with no on-ontology vocabulary triggers fallback."""
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "What is the weather today?")
    assert sl.fallback_to_full is True


def test_slice_pascal_case_split_matches() -> None:
    """LegalPerson should match the term 'legal' or 'person'."""
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "Show me legal entities")
    assert "LegalPerson" in sl.matched_labels


def test_render_slice_returns_extraction_context_shape() -> None:
    """The rendered slice has the same keys as ontology.to_extraction_context()."""
    from seocho.ontology_slice import render_slice_extraction_context, slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "Person and Corporation")
    ctx = render_slice_extraction_context(onto, sl)
    assert "entity_types" in ctx
    assert "relationship_types" in ctx
    assert "Person" in ctx["entity_types"]
    assert "Corporation" in ctx["entity_types"]


def test_render_slice_falls_back_to_full_on_zero_match() -> None:
    """When fallback_to_full is set, render returns the full ontology context."""
    from seocho.ontology_slice import render_slice_extraction_context, slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "weather")
    ctx = render_slice_extraction_context(onto, sl)
    # Full context includes ALL labels — at minimum Person, Corporation, Product.
    assert "Party" in ctx["entity_types"]
    assert "Corporation" in ctx["entity_types"]


def test_no_neighbour_expansion_when_disabled() -> None:
    """expand_neighbours=False skips the neighbour walk."""
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "Corporation", expand_neighbours=False)
    assert "Corporation" in sl.matched_labels
    # No neighbour expansion — Person/Product not pulled in.
    assert "Person" not in sl.related_labels
    assert "Product" not in sl.related_labels


def test_slice_to_dict_serializes() -> None:
    from seocho.ontology_slice import slice_ontology
    onto = _make_finance_ontology()
    sl = slice_ontology(onto, "List every Person")
    d = sl.to_dict()
    assert "matched_labels" in d
    assert "fallback_to_full" in d
    assert d["label_count"] == len(sl.all_labels)
