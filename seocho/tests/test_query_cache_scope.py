"""Regression tests for seocho-9gdm — query cache scope + LRU + TTL.

Before this fix, SessionContext._query_cache was keyed only on the
lowercased question. Two workspaces or two ontology versions in the
same Session collided. This rewrites the cache to key on
(workspace_id, database, ontology_identity_hash, normalized_question)
with a configurable LRU bound and TTL.
"""

from __future__ import annotations

import time

import pytest


def _make_context():
    from seocho.agent.context import SessionContext
    return SessionContext()


def test_legacy_cache_query_still_works() -> None:
    """No identity kwargs → still caches and returns under empty-key tuple (back-compat)."""
    ctx = _make_context()
    ctx.cache_query("Who is the CEO of Apple?", "Tim Cook")
    assert ctx.get_cached_answer("Who is the CEO of Apple?") == "Tim Cook"
    # Cache miss for a different normalised question
    assert ctx.get_cached_answer("Different question") is None


def test_two_workspaces_do_not_collide() -> None:
    """Same question, different workspace_id → independent cache entries."""
    ctx = _make_context()
    ctx.cache_query("ceo?", "Alice", workspace_id="A", database="db1")
    ctx.cache_query("ceo?", "Bob",   workspace_id="B", database="db1")
    assert ctx.get_cached_answer("ceo?", workspace_id="A", database="db1") == "Alice"
    assert ctx.get_cached_answer("ceo?", workspace_id="B", database="db1") == "Bob"


def test_two_ontology_versions_do_not_collide() -> None:
    """Same workspace, same question, different ontology_identity_hash → independent."""
    ctx = _make_context()
    ctx.cache_query("ceo?", "v1-answer", workspace_id="A", ontology_identity_hash="hash-v1")
    ctx.cache_query("ceo?", "v2-answer", workspace_id="A", ontology_identity_hash="hash-v2")
    assert ctx.get_cached_answer("ceo?", workspace_id="A", ontology_identity_hash="hash-v1") == "v1-answer"
    assert ctx.get_cached_answer("ceo?", workspace_id="A", ontology_identity_hash="hash-v2") == "v2-answer"


def test_lru_eviction_drops_oldest() -> None:
    """When cache exceeds the bound, the oldest entry is dropped."""
    ctx = _make_context()
    ctx._query_cache_max_entries = 3

    ctx.cache_query("q1", "a1", workspace_id="A")
    ctx.cache_query("q2", "a2", workspace_id="A")
    ctx.cache_query("q3", "a3", workspace_id="A")
    # All three are present
    assert ctx.get_cached_answer("q1", workspace_id="A") == "a1"
    # Bumps q1 to MRU; q2 is now the LRU
    ctx.cache_query("q4", "a4", workspace_id="A")
    # q2 should have been evicted
    assert ctx.get_cached_answer("q2", workspace_id="A") is None
    assert ctx.get_cached_answer("q1", workspace_id="A") == "a1"
    assert ctx.get_cached_answer("q3", workspace_id="A") == "a3"
    assert ctx.get_cached_answer("q4", workspace_id="A") == "a4"


def test_ttl_evicts_stale_entries() -> None:
    """Entries beyond TTL miss the cache and are evicted on read."""
    ctx = _make_context()
    ctx._query_cache_ttl_seconds = 0.05  # 50ms

    ctx.cache_query("q1", "a1", workspace_id="A")
    assert ctx.get_cached_answer("q1", workspace_id="A") == "a1"

    time.sleep(0.10)

    assert ctx.get_cached_answer("q1", workspace_id="A") is None
    # Confirm eviction
    assert len(ctx._query_cache) == 0


def test_normalization_trims_and_lowercases() -> None:
    ctx = _make_context()
    ctx.cache_query("  Who is the CEO?  ", "Tim", workspace_id="A")
    assert ctx.get_cached_answer("who is the ceo?", workspace_id="A") == "Tim"
