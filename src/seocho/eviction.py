"""Cost-aware, fair, concurrency-safe eviction for the allocator's hot cache.

The allocator interns entities on write; this is its missing half — reclamation
of the *hot* per-request resources (compiled ontology contexts, schema-prefix KV,
result buffers) under a byte budget, concurrency, and heterogeneous requests.
The status quo (``OntologyContextCache``) is a naive fixed-size LRU keyed by
``id(ontology)`` with no cost-awareness, no fairness, and no byte budget; under a
multi-tenant, skewed-popularity, churny load it thrashes and lets a churny tenant
evict a hot shared ontology (seocho-ia4).

This policy uses the signals the layer already has — reference frequency,
*recompute cost* (a schema prefix's KV/recompute cost is known, ADR-0166), and
size — in a GreedyDual-Size-Frequency (GDSF) value, with two additions the
agentic setting needs:

- **per-tenant working-set floor** (fairness): each active tenant keeps up to
  ``tenant_floor`` of its most valuable entries; only the surplus is contended,
  so a churny tenant cannot evict another tenant's — or a hot shared — page.
- **shared-entry boost**: an entry referenced by many tenants (a shared
  ontology) is valued higher, so broad reuse survives local churn.

Keyed by a stable content hash (e.g. ``stable_prefix_hash``), not object id, so
the cache is shareable across sessions. Thread-safe under one lock (the same
N-way concurrency the LaneScheduler admits).
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, Set


@dataclass
class _Entry:
    key: Hashable
    size: int                       # bytes (or tokens) — the budget unit
    recompute_cost: float           # ms to rebuild this entry (value of keeping it)
    freq: int = 0                   # reference count since admission
    tenants: Set[str] = field(default_factory=set)   # who has referenced it
    value: float = 0.0              # cached GDSF priority (with aging term)
    pins: int = 0                   # in-flight readers; pinned entries are never evicted


class CostAwareEvictionCache:
    """GDSF cost-aware cache with a per-tenant fairness floor and byte budget.

    Generic over the cached artifact; the caller supplies ``size`` and
    ``recompute_cost`` per key (both known for ontology contexts / schema
    prefixes). ``get`` computes on miss via ``compute_fn`` and evicts to budget.
    """

    def __init__(self, *, byte_budget: int, tenant_floor: int = 0) -> None:
        if byte_budget <= 0:
            raise ValueError("byte_budget must be positive")
        self.byte_budget = byte_budget
        self.tenant_floor = max(0, tenant_floor)
        self._entries: Dict[Hashable, _Entry] = {}
        self._store: Dict[Hashable, object] = {}
        self._bytes = 0
        self._age = 0.0                 # GDSF aging term ("L"): floor of evicted value
        self._lock = threading.Lock()
        # observability
        self.hits = 0
        self.misses = 0
        self.recompute_ms_incurred = 0.0     # paid on miss
        self.recompute_ms_avoided = 0.0      # saved on hit
        self.evictions = 0

    def _priority(self, e: _Entry) -> float:
        # GDSF: aging + frequency * cost / size, with a boost for shared reuse.
        shared_boost = 1.0 + 0.5 * max(0, len(e.tenants) - 1)
        return self._age + (e.freq * e.recompute_cost * shared_boost) / max(e.size, 1)

    def get(self, key: Hashable, *, tenant: str, size: int,
            recompute_cost: float, compute_fn: Callable[[], object]) -> object:
        with self._lock:
            e = self._entries.get(key)
            if e is not None:
                self.hits += 1
                self.recompute_ms_avoided += recompute_cost
                e.freq += 1
                e.tenants.add(tenant)
                e.value = self._priority(e)
                return self._store[key]
            self.misses += 1
            self.recompute_ms_incurred += recompute_cost
            value = compute_fn()
            e = _Entry(key=key, size=max(size, 1), recompute_cost=recompute_cost,
                       freq=1, tenants={tenant})
            e.value = self._priority(e)
            self._entries[key] = e
            self._store[key] = value
            self._bytes += e.size
            self._evict_to_budget(protect_tenant=tenant)
            return value

    def get_measured(self, key: Hashable, *, tenant: str,
                     compute_fn: Callable[[], tuple]) -> object:
        """Like :meth:`get`, but ``size`` and ``recompute_cost`` are *measured*.

        For artifacts whose true size and rebuild cost are only known after
        computing them (e.g. compiling an ontology context), ``compute_fn``
        returns ``(value, size, recompute_cost)`` and the entry is admitted with
        those measured values. On a hit the stored (measured) cost is credited
        to ``recompute_ms_avoided`` — so the avoided-cost metric reflects the
        real rebuild cost saved, not a caller estimate.
        """
        with self._lock:
            e = self._entries.get(key)
            if e is not None:
                self.hits += 1
                self.recompute_ms_avoided += e.recompute_cost
                e.freq += 1
                e.tenants.add(tenant)
                e.value = self._priority(e)
                return self._store[key]
            self.misses += 1
            value, size, recompute_cost = compute_fn()
            self.recompute_ms_incurred += float(recompute_cost)
            e = _Entry(key=key, size=max(int(size), 1),
                       recompute_cost=float(recompute_cost),
                       freq=1, tenants={tenant})
            e.value = self._priority(e)
            self._entries[key] = e
            self._store[key] = value
            self._bytes += e.size
            self._evict_to_budget(protect_tenant=tenant)
            return value

    def _evict_to_budget(self, *, protect_tenant: str) -> None:
        if self._bytes <= self.byte_budget:
            return
        # Per-tenant floor: the top-`tenant_floor` entries of each tenant are
        # protected. Contend only over the rest.
        protected: Set[Hashable] = set()
        if self.tenant_floor:
            by_tenant: Dict[str, list] = {}
            for e in self._entries.values():
                for t in e.tenants:
                    by_tenant.setdefault(t, []).append(e)
            for t, entries in by_tenant.items():
                entries.sort(key=self._priority, reverse=True)
                for e in entries[: self.tenant_floor]:
                    protected.add(e.key)
        # Evict lowest-priority unprotected entries until under budget.
        while self._bytes > self.byte_budget:
            # Safety gate (seocho-ia4.4): never evict a PINNED entry — one a
            # concurrent request is mid-flight on. Value ranking chooses only among
            # already-safe (unpinned, unprotected) candidates. The full epoch-based
            # gate (min_pinned_epoch / version reclamation) is ia4.3/RCU; this is the
            # hot-cache pin refcount that closes the use-after-evict bug without it.
            candidates = [e for e in self._entries.values()
                          if e.key not in protected and e.pins <= 0]
            if not candidates:
                break                    # everything is floor-protected or pinned
            victim = min(candidates, key=self._priority)
            self._age = self._priority(victim)   # GDSF aging: raise the floor
            self._bytes -= victim.size
            del self._entries[victim.key]
            del self._store[victim.key]
            self.evictions += 1

    def stats(self) -> Dict[str, float]:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "bytes": self._bytes,
            "byte_budget": self.byte_budget,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "recompute_ms_incurred": round(self.recompute_ms_incurred, 1),
            "recompute_ms_avoided": round(self.recompute_ms_avoided, 1),
            "evictions": self.evictions,
            "pinned": sum(1 for e in self._entries.values() if e.pins > 0),
        }

    def holds(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._entries

    # -- pinning (ia4.4): protect an in-use entry from reclamation ------------
    def pin(self, key: Hashable) -> bool:
        """Mark ``key`` as in-use so it is never evicted until unpinned. Returns
        False if the key is not resident."""
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                return False
            e.pins += 1
            return True

    def unpin(self, key: Hashable) -> None:
        with self._lock:
            e = self._entries.get(key)
            if e is not None and e.pins > 0:
                e.pins -= 1

    @contextlib.contextmanager
    def pinned(self, key: Hashable):
        """Context manager: pin ``key`` for the duration of an in-flight use."""
        ok = self.pin(key)
        try:
            yield ok
        finally:
            if ok:
                self.unpin(key)

    def pinned_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries.values() if e.pins > 0)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._store.clear()
            self._bytes = 0
            self._age = 0.0
            self.hits = 0
            self.misses = 0
            self.recompute_ms_incurred = 0.0
            self.recompute_ms_avoided = 0.0
            self.evictions = 0
