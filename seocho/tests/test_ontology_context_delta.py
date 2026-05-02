"""Regression tests for seocho-a9ay — compile_ontology_context_delta."""

from __future__ import annotations

import pytest


def _make_v1():
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name="kv_test", version="1.0.0",
        nodes={
            "Person":  NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={"WORKS_AT": RelDef(source="Person", target="Company")},
    )


def _make_v2():
    """v1 + Product node + MAKES rel."""
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name="kv_test", version="2.0.0",
        nodes={
            "Person":  NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Product": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company"),
            "MAKES":    RelDef(source="Company", target="Product"),
        },
    )


def test_delta_reports_added_labels_and_rels() -> None:
    from seocho.ontology_context import compile_ontology_context, compile_ontology_context_delta
    v1 = compile_ontology_context(_make_v1(), workspace_id="w")
    v2 = compile_ontology_context(_make_v2(), workspace_id="w")

    delta = compile_ontology_context_delta(v1, v2)
    assert delta["added_node_labels"] == ["Product"]
    assert delta["removed_node_labels"] == []
    assert delta["added_relationship_types"] == ["MAKES"]
    assert delta["removed_relationship_types"] == []


def test_delta_reports_hash_change() -> None:
    from seocho.ontology_context import compile_ontology_context, compile_ontology_context_delta
    v1 = compile_ontology_context(_make_v1(), workspace_id="w")
    v2 = compile_ontology_context(_make_v2(), workspace_id="w")

    delta = compile_ontology_context_delta(v1, v2)
    assert delta["from_context_hash"] != delta["to_context_hash"]
    assert delta["hash_changed"] is True


def test_delta_no_change_when_identical_ontologies() -> None:
    from seocho.ontology_context import compile_ontology_context, compile_ontology_context_delta
    v1a = compile_ontology_context(_make_v1(), workspace_id="w")
    v1b = compile_ontology_context(_make_v1(), workspace_id="w")
    delta = compile_ontology_context_delta(v1a, v1b)
    assert delta["hash_changed"] is False
    assert delta["added_node_labels"] == []
    assert delta["removed_node_labels"] == []
    assert delta["stable_prefix_changed"] is False


def test_delta_carries_identity_metadata() -> None:
    from seocho.ontology_context import compile_ontology_context, compile_ontology_context_delta
    v1 = compile_ontology_context(_make_v1(), workspace_id="w")
    v2 = compile_ontology_context(_make_v2(), workspace_id="w")
    delta = compile_ontology_context_delta(v1, v2)
    assert delta["identity"]["ontology_name"] == "kv_test"
    assert delta["identity"]["ontology_version"] == "2.0.0"
    assert delta["identity"]["workspace_id"] == "w"


def test_delta_stable_prefix_changed_flag_tracks_kv_cache_invalidation() -> None:
    """When the stable prefix changes, downstream prefix caches must invalidate."""
    from seocho.ontology_context import compile_ontology_context, compile_ontology_context_delta
    v1 = compile_ontology_context(_make_v1(), workspace_id="w")
    v2 = compile_ontology_context(_make_v2(), workspace_id="w")
    delta = compile_ontology_context_delta(v1, v2)
    assert delta["stable_prefix_changed"] is True
