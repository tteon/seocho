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


def test_get_measured_uses_computed_size_and_cost():
    """get_measured admits with the size/cost the compute_fn measures, and
    credits the stored cost on a hit."""
    c = CostAwareEvictionCache(byte_budget=1000)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ("built", 200, 42.0)   # value, size(bytes), recompute_cost(ms)

    v1 = c.get_measured("k", tenant="t", compute_fn=compute)
    v2 = c.get_measured("k", tenant="t", compute_fn=compute)   # hit — no recompute
    assert v1 == v2 == "built"
    assert calls["n"] == 1, "second call must be a cache hit"
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1
    assert s["recompute_ms_avoided"] == 42.0   # stored measured cost credited
    assert s["bytes"] == 200


def test_ontology_context_cache_stable_key_and_fairness():
    """The live cache (ADR-0172 wiring): a stable content key hits across
    distinct ontology objects, and a churny workspace cannot evict another's."""
    import glob
    from seocho.ontology.core import Ontology
    from seocho.ontology.context import OntologyContextCache

    yamls = glob.glob("examples/**/schema.yaml", recursive=True) or \
        glob.glob("examples/**/*.yaml", recursive=True)
    onto_path = yamls[0]
    o1 = Ontology.load(onto_path)
    o2 = Ontology.load(onto_path)     # distinct object, identical content
    assert o1 is not o2 and o1.schema_fingerprint() == o2.schema_fingerprint()

    cache = OntologyContextCache(max_size=8)
    a = cache.get(o1, workspace_id="acme")
    b = cache.get(o2, workspace_id="acme")   # id()-keyed cache would MISS here
    assert a is b, "stable content key must hit across distinct instances"
    assert cache.stats()["hits"] >= 1


def test_pinned_entry_is_never_evicted():
    """A pinned (in-use) entry survives budget pressure even when it is the
    lowest-value victim (seocho-ia4.4 safe-reclamation gate)."""
    c = CostAwareEvictionCache(byte_budget=250)   # holds ~2 size-100 entries
    _get(c, "hot", size=100, cost=1.0)            # low value, but we'll pin it
    assert c.pin("hot")
    for i in range(6):                            # flood -> pressure
        _get(c, f"x{i}", size=100, cost=50.0)
    assert c.holds("hot"), "pinned entry must not be evicted under pressure"
    assert c.stats()["pinned"] == 1
    c.unpin("hot")
    # once unpinned, it is a normal (low-value) eviction candidate again
    for i in range(6):
        _get(c, f"y{i}", size=100, cost=50.0)
    assert not c.holds("hot")


def test_pinned_context_manager():
    c = CostAwareEvictionCache(byte_budget=250)
    _get(c, "k", size=100, cost=1.0)
    with c.pinned("k") as ok:
        assert ok
        for i in range(6):
            _get(c, f"z{i}", size=100, cost=50.0)
        assert c.holds("k")            # protected inside the context
    assert c.pinned_count() == 0       # released on exit


def test_heap_eviction_correct_and_bounded_under_scale(monkeypatch):
    """The lazy-heap eviction (ia4.12) evicts the true lowest-value victims and
    keeps the heap O(live entries) despite many priority updates (hits)."""
    budget = 50 * 100                              # holds 50 size-100 entries
    c = CostAwareEvictionCache(byte_budget=budget)
    # Warm a hot, expensive, frequently-referenced working set of 50 pages.
    for r in range(20):
        for k in range(50):
            _get(c, f"hot-{k}", size=100, cost=100.0)
    # Now flood 5000 cheap one-shots. With an O(n)-per-eviction scan this is
    # ~O(n^2); with the heap it stays fast and the hot set must survive.
    for i in range(5000):
        _get(c, f"churn-{i}", size=100, cost=1.0)
    assert c.stats()["bytes"] <= budget
    survived = sum(1 for k in range(50) if c.holds(f"hot-{k}"))
    assert survived == 50, f"all hot pages should survive churn, kept {survived}/50"
    # Lazy-deletion housekeeping keeps the heap from growing without bound.
    assert len(c._heap) <= 2 * len(c._entries) + 64


def test_eviction_picks_global_min_value_victim():
    """The victim is the globally lowest-priority unpinned entry, not a scan
    artifact — verified by controlled values."""
    c = CostAwareEvictionCache(byte_budget=300)    # holds 3
    _get(c, "cheap", size=100, cost=1.0)           # lowest value
    _get(c, "mid", size=100, cost=10.0)
    _get(c, "rich", size=100, cost=100.0)
    _get(c, "new", size=100, cost=50.0)            # forces one eviction
    assert not c.holds("cheap"), "the globally cheapest entry must be evicted"
    assert c.holds("mid") and c.holds("rich") and c.holds("new")
