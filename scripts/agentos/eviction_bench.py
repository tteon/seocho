"""Eviction policy bench: cost-aware+fair vs naive LRU under contention.

Simulates the allocator's hot cache (compiled ontology contexts / schema
prefixes) under a multi-tenant, skewed-popularity, churny load — the regime the
naive fixed-LRU (`OntologyContextCache`) thrashes in (seocho-ia4). Deterministic
(no RNG): the access stream and the per-key size/recompute-cost are fixed, so the
comparison is reproducible.

Workload: a small set of SHARED ontologies with Zipf-ish popularity (onto-0 the
hottest, referenced by many legit tenants; expensive to recompute — a schema
prefix), plus one CHURNY tenant flooding unique one-shot keys (cheap, never
reused). The byte budget is smaller than the working set, so eviction is forced.

Metrics: overall hit-rate, recompute-ms avoided, and the fairness signal —
does the churny flood evict the hot SHARED ontology (onto-0)?

Usage: python scripts/agentos/eviction_bench.py --out outputs/agentos/eviction_bench.json
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Tuple, Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.eviction import CostAwareEvictionCache  # noqa: E402

# --- fixed workload -------------------------------------------------------
_N_SHARED = 10                 # shared ontologies onto-0..9
_N_TENANTS = 8                 # legit tenants t0..t7
_PREFIX_BYTES = 3000           # a compiled ontology context ~ a schema prefix
_PREFIX_RECOMPUTE_MS = 40.0    # expensive to rebuild (bge/schema compile)
_CHURN_BYTES = 800             # a churny one-shot artifact (small)
_CHURN_RECOMPUTE_MS = 3.0      # cheap
_STEPS = 4000


def _zipf_index(step: int, n: int) -> int:
    # deterministic skew: onto-0 hottest. Map a rolling counter through a
    # harmonic-ish schedule so lower indices recur far more often.
    r = (step * 2654435761) % 1000 / 1000.0     # deterministic pseudo-uniform
    # invert a Zipf CDF cheaply: bucket by thresholds ~ 1/(i+1)
    weights = [1.0 / (i + 1) for i in range(n)]
    total = sum(weights)
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w / total
        if r <= acc:
            return i
    return n - 1


def _stream() -> List[Tuple[str, str, int, float]]:
    """(key, tenant, size, recompute_cost). 70% legit shared-ontology accesses
    (Zipf, hot onto-0), 30% churny unique one-shots."""
    out = []
    churn_id = 0
    for step in range(_STEPS):
        if step % 10 < 7:                       # 70% legit
            oi = _zipf_index(step, _N_SHARED)
            tenant = f"t{step % _N_TENANTS}"
            out.append((f"onto-{oi}", tenant, _PREFIX_BYTES, _PREFIX_RECOMPUTE_MS))
        else:                                   # 30% churn
            out.append((f"churn-{churn_id}", "churn", _CHURN_BYTES,
                        _CHURN_RECOMPUTE_MS))
            churn_id += 1
    return out


class NaiveLRU:
    """Byte-budget LRU — the status quo policy, generalized to a byte budget."""

    def __init__(self, byte_budget: int) -> None:
        self.byte_budget = byte_budget
        self._od: "OrderedDict[str, int]" = OrderedDict()   # key -> size
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.recompute_ms_avoided = 0.0
        self.recompute_ms_incurred = 0.0

    def get(self, key, *, tenant, size, recompute_cost, compute_fn) -> None:
        if key in self._od:
            self.hits += 1
            self.recompute_ms_avoided += recompute_cost
            self._od.move_to_end(key)
            return
        self.misses += 1
        self.recompute_ms_incurred += recompute_cost
        compute_fn()
        self._od[key] = size
        self._bytes += size
        while self._bytes > self.byte_budget and self._od:
            _, sz = self._od.popitem(last=False)
            self._bytes -= sz

    def holds(self, key) -> bool:
        return key in self._od

    def stats(self):
        total = self.hits + self.misses
        return {"hit_rate": round(self.hits / total, 4) if total else 0.0,
                "recompute_ms_incurred": round(self.recompute_ms_incurred, 1),
                "recompute_ms_avoided": round(self.recompute_ms_avoided, 1)}


def _run(cache, stream) -> Dict[str, Any]:
    hot_shared_hits = hot_shared_accesses = 0
    for key, tenant, size, cost in stream:
        held_before = cache.holds(key)
        cache.get(key, tenant=tenant, size=size, recompute_cost=cost,
                  compute_fn=lambda: None)
        if key == "onto-0":
            hot_shared_accesses += 1
            hot_shared_hits += 1 if held_before else 0
    s = cache.stats()
    s["hot_shared_hit_rate"] = round(hot_shared_hits / hot_shared_accesses, 4) \
        if hot_shared_accesses else 0.0
    s["hot_shared_resident_at_end"] = cache.holds("onto-0")
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    # budget ~ half the shared working set -> forces eviction, exposes the policy
    ap.add_argument("--byte-budget", type=int, default=_N_SHARED * _PREFIX_BYTES // 2)
    ap.add_argument("--tenant-floor", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    stream = _stream()
    naive = _run(NaiveLRU(args.byte_budget), stream)
    fair = _run(CostAwareEvictionCache(byte_budget=args.byte_budget,
                                       tenant_floor=args.tenant_floor), stream)
    report = {"steps": _STEPS, "byte_budget": args.byte_budget,
              "tenant_floor": args.tenant_floor,
              "naive_lru": naive, "cost_aware_fair": fair}

    print(f"=== eviction bench ({_STEPS} accesses, budget={args.byte_budget}B "
          f"~ half the shared working set) ===")
    print(f"  {'policy':18s} {'hit_rate':>9s} {'recompute_ms_saved':>19s} "
          f"{'hot_shared_hit':>15s} {'hot_resident':>13s}")
    for name, s in (("naive_lru", naive), ("cost_aware_fair", fair)):
        print(f"  {name:18s} {s['hit_rate']:>9.2%} "
              f"{s['recompute_ms_avoided']:>19.0f} "
              f"{s['hot_shared_hit_rate']:>15.2%} "
              f"{str(s['hot_shared_resident_at_end']):>13s}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
