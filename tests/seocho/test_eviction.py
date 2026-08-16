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
