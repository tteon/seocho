"""Cost-aware+fair eviction policy (seocho-ia4) — the allocator's reclamation half."""

from __future__ import annotations

import threading

from seocho.eviction import CostAwareEvictionCache


def _get(c, key, tenant="t", size=100, cost=10.0):
    return c.get(key, tenant=tenant, size=size, recompute_cost=cost,
                 compute_fn=lambda: f"val-{key}")


def test_evicts_to_byte_budget():
    c = CostAwareEvictionCache(byte_budget=250)   # holds ~2 of size-100
    for k in ("a", "b", "c"):
        _get(c, k)
    assert c.stats()["bytes"] <= 250
    assert c.stats()["evictions"] >= 1


def test_high_cost_frequent_entry_survives_churn():
    """A frequently-referenced, expensive-to-recompute page beats cheap one-shots."""
    c = CostAwareEvictionCache(byte_budget=300)
    for _ in range(5):                    # hot + expensive
        _get(c, "hot", size=100, cost=100.0)
    for i in range(20):                   # churn: cheap one-shots
        _get(c, f"churn-{i}", size=100, cost=1.0)
    assert c.holds("hot"), "cost-aware policy should keep the hot expensive page"


def test_tenant_floor_protects_from_churny_tenant():
    c = CostAwareEvictionCache(byte_budget=300, tenant_floor=1)
    _get(c, "acme-page", tenant="acme", size=100, cost=50.0)
    for i in range(30):                   # a churny tenant floods
        _get(c, f"x-{i}", tenant="churn", size=100, cost=50.0)
    assert c.holds("acme-page"), "tenant floor must protect acme's working set"


def test_shared_entry_boost():
    c = CostAwareEvictionCache(byte_budget=250)
    for t in ("t1", "t2", "t3"):          # shared across tenants
        _get(c, "shared", tenant=t, size=100, cost=10.0)
    _get(c, "solo1", tenant="z", size=100, cost=10.0)
    _get(c, "solo2", tenant="z", size=100, cost=10.0)
    assert c.holds("shared"), "an entry shared across tenants should be boosted"


def test_thread_safe_under_concurrent_access():
    c = CostAwareEvictionCache(byte_budget=2000)

    def worker(tid):
        for i in range(200):
            _get(c, f"k-{i % 30}", tenant=f"t{tid}")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    s = c.stats()
    assert s["bytes"] <= 2000 and s["hits"] + s["misses"] == 8 * 200
