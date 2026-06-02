"""Regression tests for seocho-6c9v — AgentFactoryCache."""

from __future__ import annotations

import time

import pytest


def test_cache_key_shape() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    key = AgentFactoryCache.make_key(
        role="indexing",
        ontology_identity_hash="abc123",
        ontology_profile="default",
        agent_config=None,
    )
    assert key[0] == "indexing"
    assert key[1] == "abc123"
    assert key[2] == "default"
    # agent_config=None hashes to empty
    assert key[3] == ""


def test_get_or_create_caches_factory_result() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    cache = AgentFactoryCache()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return object()

    key = AgentFactoryCache.make_key(role="indexing", ontology_identity_hash="hash1")
    a = cache.get_or_create(key, factory)
    b = cache.get_or_create(key, factory)
    assert a is b
    assert calls["n"] == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_distinct_ontology_versions_get_distinct_agents() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    cache = AgentFactoryCache()
    k1 = AgentFactoryCache.make_key(role="indexing", ontology_identity_hash="v1")
    k2 = AgentFactoryCache.make_key(role="indexing", ontology_identity_hash="v2")
    a1 = cache.get_or_create(k1, lambda: object())
    a2 = cache.get_or_create(k2, lambda: object())
    assert a1 is not a2


def test_distinct_agent_configs_get_distinct_agents() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    from seocho.agent_config import AgentConfig
    cache = AgentFactoryCache()
    cfg1 = AgentConfig(execution_mode="agent")
    cfg2 = AgentConfig(execution_mode="supervisor", handoff=True)
    k1 = AgentFactoryCache.make_key(role="indexing", ontology_identity_hash="h", agent_config=cfg1)
    k2 = AgentFactoryCache.make_key(role="indexing", ontology_identity_hash="h", agent_config=cfg2)
    assert k1 != k2


def test_lru_evicts_oldest_when_full() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    cache = AgentFactoryCache(max_entries=2)
    k1 = AgentFactoryCache.make_key(role="r", ontology_identity_hash="h1")
    k2 = AgentFactoryCache.make_key(role="r", ontology_identity_hash="h2")
    k3 = AgentFactoryCache.make_key(role="r", ontology_identity_hash="h3")
    a1 = cache.get_or_create(k1, lambda: object())
    a2 = cache.get_or_create(k2, lambda: object())
    a3 = cache.get_or_create(k3, lambda: object())  # evicts k1 (oldest)

    assert cache.get(k1) is None  # evicted
    assert cache.get(k2) is a2
    assert cache.get(k3) is a3


def test_ttl_evicts_stale_entries_on_access() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    cache = AgentFactoryCache(ttl_seconds=0.05)
    k = AgentFactoryCache.make_key(role="r", ontology_identity_hash="h")
    cache.set(k, object())
    assert cache.get(k) is not None
    time.sleep(0.1)
    assert cache.get(k) is None


def test_stats_reports_hit_ratio() -> None:
    from seocho.agent_factory_cache import AgentFactoryCache
    cache = AgentFactoryCache()
    k = AgentFactoryCache.make_key(role="r", ontology_identity_hash="h")
    cache.get_or_create(k, lambda: object())  # miss + set
    cache.get_or_create(k, lambda: object())  # hit
    cache.get_or_create(k, lambda: object())  # hit
    s = cache.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert abs(s["hit_ratio"] - 2/3) < 1e-9


def test_default_cache_singleton() -> None:
    from seocho.agent_factory_cache import get_default_agent_factory_cache
    a = get_default_agent_factory_cache()
    b = get_default_agent_factory_cache()
    assert a is b
