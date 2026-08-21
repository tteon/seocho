"""Demonstration: the ontology memory manager under pressure (seocho-ia4).

hadry's mental model, made an end-to-end scenario: an ontology has a ref-count
(freq + pins); it is looked up in the intern hash table and used; when memory fills,
the memory manager (cost-aware + fair + pin-safe eviction) decides what to evict and
what to protect. This script runs that story on the SHIPPED pieces
(CostAwareEvictionCache = the memory manager; SharedInternTable = the canonical hash
table) and asserts the manager does the right thing.

Scenario (deterministic, no API/DB):
  Act 1 — 6 tenants each look up + compile their ontology context into a shared
          memory whose byte budget holds only ~half the working set. Memory fills.
  Act 2 — pressure: cold contexts are evicted (cost-aware), a hot shared ontology
          (referenced by many tenants) is retained (fairness floor + shared boost).
  Act 3 — a request PINS its ontology mid-flight; a churny tenant then floods the
          cache. Assert: the pinned (in-use) ontology is NEVER evicted, even though
          it is low-value — the safe-reclamation gate (ia4.4).
  Act 4 — the request finishes (unpin); the now-cold ontology becomes evictable again.

Reports hit-rate, recompute-ms avoided, evictions, and the two invariants that make
the manager correct: pinned-survival and hot-shared-retention.

Usage: python scripts/agentos/demo_memory_manager.py --out outputs/agentos/memory_manager_demo.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.eviction import CostAwareEvictionCache  # noqa: E402
from seocho.index.shared_intern import SharedInternTable  # noqa: E402

_PREFIX_BYTES = 3000          # a compiled ontology context (~a schema prefix)
_COMPILE_MS = 40.0            # cost to rebuild it (the ref-count value signal)


def _use(cache, key, tenant, *, cost=_COMPILE_MS):
    """Look up (or compile) an ontology context for a tenant — a 'use' that bumps
    the ontology's ref-count (freq) in the memory manager."""
    return cache.get(key, tenant=tenant, size=_PREFIX_BYTES, recompute_cost=cost,
                     compute_fn=lambda: f"ctx::{key}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    acts: Dict[str, Any] = {}
    # budget holds ~4 of the ~8-context working set -> forces the manager to work
    budget = 4 * _PREFIX_BYTES
    cache = CostAwareEvictionCache(byte_budget=budget, tenant_floor=1)
    intern = SharedInternTable()

    tenants = [f"t{i}" for i in range(6)]
    # a hot SHARED ontology used by everyone, + per-tenant private ontologies
    HOT = "onto-shared"

    # Act 1 — lookups fill memory. Everyone uses the hot shared ontology; each tenant
    # also uses its own. Intern the canonical ontology ids (the hash table).
    for t in tenants:
        _use(cache, HOT, t, cost=_COMPILE_MS * 2)     # shared + expensive = high value
        intern.intern("shared", f"onto|{HOT}", HOT)
        _use(cache, f"onto-{t}", t)
        intern.intern(t, f"onto|onto-{t}", f"onto-{t}")
    acts["act1_fill"] = {**cache.stats(), "intern": intern.stats(),
                         "hot_resident": cache.holds(HOT)}

    # Act 2 — sustained access to the hot shared ontology; cold private ones age out.
    for _ in range(20):
        for t in tenants[:3]:
            _use(cache, HOT, t, cost=_COMPILE_MS * 2)
    acts["act2_pressure"] = {**cache.stats(), "hot_resident": cache.holds(HOT)}

    # Act 3 — a request pins its (low-value, cold) ontology mid-flight, then a churny
    # tenant floods. The pinned in-use ontology must NOT be evicted. (It belongs to the
    # churn tenant and is low-value, so it is beyond the fairness floor — only the pin
    # protects it; that isolates the pin's effect from the per-tenant floor.)
    _use(cache, "onto-inflight", "churn", cost=1.0)   # low value, churn-owned
    pinned_survived = None
    with cache.pinned("onto-inflight"):
        for i in range(30):                            # churn flood
            _use(cache, f"churn-{i}", "churn", cost=5.0)
        pinned_survived = cache.holds("onto-inflight")
    acts["act3_pin_under_churn"] = {
        "pinned_in_use_survived": pinned_survived,
        "hot_shared_still_resident": cache.holds(HOT),
        **cache.stats(),
    }

    # Act 4 — request done (unpinned): the cold ontology is evictable again.
    for i in range(30):
        _use(cache, f"churn2-{i}", "churn", cost=5.0)
    acts["act4_after_unpin"] = {"inflight_evictable_now": not cache.holds("onto-inflight"),
                                **cache.stats()}

    ok = {
        "pinned_in_use_never_evicted": acts["act3_pin_under_churn"]["pinned_in_use_survived"] is True,
        "hot_shared_retained_under_churn": acts["act3_pin_under_churn"]["hot_shared_still_resident"] is True,
        "cold_reclaimed_when_unpinned": acts["act4_after_unpin"]["inflight_evictable_now"] is True,
        "memory_manager_evicted_under_pressure": cache.stats()["evictions"] > 0,
    }
    report = {"budget_bytes": budget, "acts": acts, "invariants": ok,
              "all_invariants_hold": all(ok.values())}

    print("=== ontology memory-manager demonstration (seocho-ia4) ===")
    print(f"  byte budget: {budget} (~4 contexts; working set ~8+)")
    s = cache.stats()
    print(f"  final: hit_rate={s['hit_rate']:.0%} evictions={s['evictions']} "
          f"recompute_ms_avoided={s['recompute_ms_avoided']:.0f}")
    print("  invariants:")
    for k, v in ok.items():
        print(f"    {'OK ' if v else 'FAIL'} {k}: {v}")
    print(f"  ALL INVARIANTS HOLD: {report['all_invariants_hold']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")
    if not report["all_invariants_hold"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
