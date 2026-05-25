"""Regression tests for seocho-ni4u — workspace-aware schema cache.

The Neo4jGraphStore schema cache used to key only on database name. Two
workspaces sharing a database would see each other's cached schema for
up to 60s — a real cross-tenant data leak in introspection paths. This
fix re-keys on (database, workspace_id) and gives invalidate_schema_cache
a workspace_id parameter.
"""

from __future__ import annotations

from typing import Any, Dict


def _build_store():
    """Build a Neo4jGraphStore-shaped object without an actual Neo4j connection.

    We bypass __init__ (which requires the neo4j driver) by setting the cache
    attributes directly — schema cache keying is pure state manipulation.
    """
    from seocho.store.graph import Neo4jGraphStore
    store = object.__new__(Neo4jGraphStore)
    store._schema_cache = {}
    store._schema_cache_ts = {}
    store._schema_cache_ttl = 60.0
    store._index_stats_cache = {}
    store._index_stats_cache_ts = {}
    return store


def test_cache_key_is_database_workspace_pair() -> None:
    from seocho.store.graph import Neo4jGraphStore
    assert Neo4jGraphStore._schema_cache_key("foo", "alpha") == "foo::alpha"
    assert Neo4jGraphStore._schema_cache_key("foo", "") == "foo::default"
    assert Neo4jGraphStore._schema_cache_key("foo", "default") == "foo::default"


def test_two_workspaces_get_independent_cache_entries() -> None:
    """Caching a schema under workspace A doesn't leak to workspace B."""
    store = _build_store()
    schema_a = {"labels": ["Person"], "relationship_types": [], "property_keys": []}
    schema_b = {"labels": ["Bond"], "relationship_types": [], "property_keys": []}
    key_a = store._schema_cache_key("acme", "alpha")
    key_b = store._schema_cache_key("acme", "beta")

    store._schema_cache[key_a] = schema_a
    store._schema_cache[key_b] = schema_b
    assert store._schema_cache[key_a] == schema_a
    assert store._schema_cache[key_b] == schema_b
    assert key_a != key_b


def test_invalidate_specific_workspace_pair() -> None:
    """invalidate_schema_cache(db, workspace_id=ws) drops only that pair."""
    store = _build_store()
    store._schema_cache[store._schema_cache_key("foo", "alpha")] = {"a": 1}
    store._schema_cache[store._schema_cache_key("foo", "beta")] = {"b": 2}
    store._schema_cache[store._schema_cache_key("bar", "alpha")] = {"c": 3}

    store.invalidate_schema_cache("foo", workspace_id="alpha")

    keys = sorted(store._schema_cache.keys())
    assert "foo::alpha" not in keys
    assert "foo::beta" in keys
    assert "bar::alpha" in keys


def test_invalidate_database_clears_all_workspaces_for_back_compat() -> None:
    """invalidate_schema_cache(db) without workspace_id clears every workspace."""
    store = _build_store()
    store._schema_cache[store._schema_cache_key("foo", "alpha")] = {"a": 1}
    store._schema_cache[store._schema_cache_key("foo", "beta")] = {"b": 2}
    store._schema_cache[store._schema_cache_key("bar", "alpha")] = {"c": 3}

    store.invalidate_schema_cache("foo")

    keys = sorted(store._schema_cache.keys())
    assert "foo::alpha" not in keys
    assert "foo::beta" not in keys
    assert "bar::alpha" in keys


def test_invalidate_no_args_clears_everything() -> None:
    store = _build_store()
    store._schema_cache[store._schema_cache_key("foo", "alpha")] = {"a": 1}
    store._schema_cache[store._schema_cache_key("bar", "beta")] = {"b": 2}
    store.invalidate_schema_cache()
    assert store._schema_cache == {}


# --- GOPTS G1 (seocho-n67d.1) — index_stats cache shares the workspace-scoping
# contract with the schema cache. These regressions cover ADR-0097's
# requirement that cost-model inputs never leak across workspaces.


def test_index_stats_cache_is_workspace_scoped() -> None:
    """Caching index stats under workspace A doesn't leak to workspace B."""
    store = _build_store()
    stats_a = {"indexes": [], "label_counts": {"Person": 10}, "rel_counts": {}}
    stats_b = {"indexes": [], "label_counts": {"Bond": 99}, "rel_counts": {}}
    key_a = store._schema_cache_key("acme", "alpha")
    key_b = store._schema_cache_key("acme", "beta")

    store._index_stats_cache[key_a] = stats_a
    store._index_stats_cache[key_b] = stats_b
    assert store._index_stats_cache[key_a] == stats_a
    assert store._index_stats_cache[key_b] == stats_b
    assert key_a != key_b


def test_invalidate_specific_workspace_pair_clears_index_stats() -> None:
    """invalidate_schema_cache(db, workspace_id=ws) also drops the index stats."""
    store = _build_store()
    pair = store._schema_cache_key("foo", "alpha")
    store._schema_cache[pair] = {"labels": ["X"]}
    store._index_stats_cache[pair] = {"label_counts": {"X": 5}}
    other = store._schema_cache_key("foo", "beta")
    store._schema_cache[other] = {"labels": ["Y"]}
    store._index_stats_cache[other] = {"label_counts": {"Y": 7}}

    store.invalidate_schema_cache("foo", workspace_id="alpha")

    assert pair not in store._schema_cache
    assert pair not in store._index_stats_cache
    assert other in store._schema_cache
    assert other in store._index_stats_cache


def test_invalidate_database_clears_all_workspace_index_stats() -> None:
    """invalidate_schema_cache(db) clears index stats for every workspace."""
    store = _build_store()
    store._index_stats_cache[store._schema_cache_key("foo", "alpha")] = {"a": 1}
    store._index_stats_cache[store._schema_cache_key("foo", "beta")] = {"b": 2}
    store._index_stats_cache[store._schema_cache_key("bar", "alpha")] = {"c": 3}

    store.invalidate_schema_cache("foo")

    assert "foo::alpha" not in store._index_stats_cache
    assert "foo::beta" not in store._index_stats_cache
    assert "bar::alpha" in store._index_stats_cache


def test_invalidate_no_args_clears_index_stats_too() -> None:
    store = _build_store()
    store._index_stats_cache[store._schema_cache_key("foo", "alpha")] = {"a": 1}
    store._index_stats_cache[store._schema_cache_key("bar", "beta")] = {"b": 2}
    store.invalidate_schema_cache()
    assert store._index_stats_cache == {}
