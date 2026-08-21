"""Indexing concurrency primitive + shared intern table (seocho-ia4 parallelism)."""

from __future__ import annotations

import threading
import time

from seocho.index.parallel import concurrent_map, resolve_workers
from seocho.index.shared_intern import SharedInternTable


def test_concurrent_map_order_preserved():
    out = concurrent_map([1, 2, 3, 4, 5], lambda x: x * x, max_workers=4)
    assert out == [1, 4, 9, 16, 25]


def test_concurrent_map_captures_exceptions_in_place():
    def f(x):
        if x == 3:
            raise ValueError("boom")
        return x
    out = concurrent_map([1, 2, 3, 4], f, max_workers=3)
    assert out[0] == 1 and out[3] == 4
    assert isinstance(out[2], ValueError)   # failure in its slot, batch survived


def test_concurrent_map_overlaps_io_latency():
    # 8 items each "waiting" 0.1s (simulated LLM round-trip). Sequential ~0.8s;
    # with 8 workers it should overlap to ~0.1s. Assert a real speedup.
    def slow(x):
        time.sleep(0.1)
        return x
    t0 = time.perf_counter()
    concurrent_map(list(range(8)), slow, max_workers=8)
    parallel = time.perf_counter() - t0
    assert parallel < 0.4, f"expected overlap, got {parallel:.2f}s"


def test_resolve_workers_clamps():
    assert resolve_workers(1, 100) == 1
    assert resolve_workers(8, 3) == 3          # not more than items
    assert resolve_workers(100, 100, cap=16) == 16


def test_shared_intern_same_entity_one_address():
    t = SharedInternTable()
    a = t.intern("ws", "company|apple", "apple-1")
    b = t.intern("ws", "company|apple", "apple-2")   # second writer, same identity
    assert a == b == "apple-1"                        # first writer wins -> one node
    assert len(t) == 1


def test_shared_intern_workspace_isolation():
    t = SharedInternTable()
    t.intern("acme", "company|apple", "x")
    t.intern("globex", "company|apple", "y")          # same identity, diff tenant
    assert t.get("acme", "company|apple") == "x"
    assert t.get("globex", "company|apple") == "y"    # no collision across domains
    assert len(t) == 2


def test_shared_intern_concurrent_convergence():
    # 16 threads race to intern the SAME entity with different candidate ids;
    # exactly one canonical id must win for all -> no fragmentation.
    t = SharedInternTable()
    results = []
    barrier = threading.Barrier(16)

    def worker(tid):
        barrier.wait()
        results.append(t.intern("ws", "company|acme", f"cand-{tid}"))

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert len(set(results)) == 1        # all threads got the SAME canonical id
    assert len(t) == 1


def test_apply_identity_keys_interns_and_measures_collapse():
    """apply_identity_keys wired to a SharedInternTable registers canonical ids and
    counts collapse (same entity across calls -> a hit)."""
    from seocho.index.identity import apply_identity_keys
    from seocho.index.shared_intern import SharedInternTable

    class _ND:
        identity_keys = ["name", "year"]

    class _Onto:
        nodes = {"Company": _ND()}

    table = SharedInternTable()
    # doc 1: Apple(2023)
    n1 = [{"id": "x1", "label": "Company", "properties": {"name": "Apple", "year": "2023"}}]
    apply_identity_keys(_Onto(), n1, [], intern_table=table, workspace_id="ws")
    # doc 2: the SAME entity again (different raw id) -> must intern to the same canonical
    n2 = [{"id": "x2", "label": "Company", "properties": {"name": "Apple", "year": "2023"}}]
    apply_identity_keys(_Onto(), n2, [], intern_table=table, workspace_id="ws")
    assert n1[0]["id"] == n2[0]["id"]                       # converged to one canonical id
    s = table.stats()
    assert s["size"] == 1 and s["interns"] == 1 and s["hits"] == 1   # collapse measured
