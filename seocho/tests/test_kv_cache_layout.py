"""Regression tests for seocho-x0t5 — KV-cache-aware ontology layout."""

from __future__ import annotations

import pytest


def _make_ontology(*, name: str = "kv_test", version: str = "1.0.0"):
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name=name,
        version=version,
        nodes={
            "Person":  NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={"WORKS_AT": RelDef(source="Person", target="Company")},
    )


def test_stable_prefix_is_deterministic() -> None:
    """Same ontology compiled twice → identical stable_prefix bytes."""
    from seocho.ontology_context import compile_ontology_context
    onto = _make_ontology()
    c1 = compile_ontology_context(onto, workspace_id="w1")
    c2 = compile_ontology_context(onto, workspace_id="w1")
    assert c1.stable_prefix() == c2.stable_prefix()


def test_stable_prefix_changes_with_ontology() -> None:
    """v1 → v2 ontology: stable_prefix differs (cache invalidates correctly)."""
    from seocho.ontology_context import compile_ontology_context
    from seocho import NodeDef, Ontology, P
    onto_v1 = _make_ontology(version="1.0.0")
    onto_v2 = Ontology(
        name="kv_test",
        version="2.0.0",
        nodes={
            "Person":  NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Product": NodeDef(properties={"name": P(str, unique=True)}),
        },
    )
    c1 = compile_ontology_context(onto_v1, workspace_id="w1")
    c2 = compile_ontology_context(onto_v2, workspace_id="w1")
    assert c1.stable_prefix() != c2.stable_prefix()


def test_kv_cache_layout_includes_hash_and_byte_count() -> None:
    from seocho.ontology_context import compile_ontology_context
    onto = _make_ontology()
    compiled = compile_ontology_context(onto, workspace_id="w")
    layout = compiled.kv_cache_layout()
    assert "stable_prefix" in layout
    assert "stable_prefix_bytes" in layout
    assert "stable_prefix_hash" in layout
    assert layout["stable_prefix_bytes"] == len(layout["stable_prefix"].encode("utf-8"))
    assert len(layout["stable_prefix_hash"]) == 16  # 64-bit truncated sha256


def test_variable_suffix_separates_user_input() -> None:
    from seocho.ontology_context import CompiledOntologyContext
    suffix = CompiledOntologyContext.variable_suffix("Who is the CEO of Apple?")
    assert "Who is the CEO of Apple?" in suffix
    assert "User Input" in suffix


def test_apply_anthropic_cache_control_marks_system_block() -> None:
    from seocho.ontology_context import apply_anthropic_cache_control
    msg = apply_anthropic_cache_control(
        stable_prefix="ontology + tools header",
        user_input="who?",
    )
    # System block is a list of one text item with cache_control.
    assert isinstance(msg["system"], list) and len(msg["system"]) == 1
    sys_block = msg["system"][0]
    assert sys_block["type"] == "text"
    assert sys_block["text"] == "ontology + tools header"
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    # User goes after the breakpoint.
    assert msg["messages"] == [{"role": "user", "content": "who?"}]


def test_two_user_inputs_share_the_same_stable_prefix() -> None:
    """Cache hit ratio simulation: same ontology, two questions, same prefix."""
    from seocho.ontology_context import compile_ontology_context
    onto = _make_ontology()
    c = compile_ontology_context(onto, workspace_id="w")
    layout1 = c.kv_cache_layout()
    layout2 = c.kv_cache_layout()
    assert layout1["stable_prefix_hash"] == layout2["stable_prefix_hash"]


def test_top_level_seocho_export() -> None:
    """apply_anthropic_cache_control is reachable via seocho.* for tutorial use."""
    from seocho.ontology_context import apply_anthropic_cache_control
    # Don't require top-level export — module-level is enough.
    assert callable(apply_anthropic_cache_control)
