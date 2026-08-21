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
import heapq
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, List, Set, Tuple


@dataclass
class _Entry:
    key: Hashable
    size: int                       # bytes (or tokens) — the budget unit
    recompute_cost: float           # ms to rebuild this entry (value of keeping it)
    freq: int = 0                   # reference count since admission
    tenants: Set[str] = field(default_factory=set)   # who has referenced it
    value: float = 0.0              # cached GDSF priority (with aging term)
    pins: int = 0                   # in-flight readers; pinned entries are never evicted
    heap_seq: int = 0               # seq of this entry's most recent heap node (lazy deletion)


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
        # Lazy-deletion min-heap of (priority, seq, key) for O(log n) victim
        # selection (seocho-ia4.12). A key may have several nodes (one per
        # priority change); only the one whose seq == entry.heap_seq is live, the
        # rest are discarded on pop. Replaces the old O(n)-scan-per-eviction.
        self._heap: List[Tuple[float, int, Hashable]] = []
        self._seq = 0
        self._lock = threading.Lock()
        # observability
        self.hits = 0
        self.misses = 0
        self.recompute_ms_incurred = 0.0     # paid on miss
        self.recompute_ms_avoided = 0.0      # saved on hit
        self.evictions = 0

    def _contribution(self, e: _Entry) -> float:
        # The age-invariant GDSF value: frequency * cost / size, boosted for
        # shared reuse. This — NOT the aging term — is what orders eviction: the
        # aging term is added uniformly to every entry, so it cancels out of the
        # argmin. Ordering the heap by contribution reproduces the original
        # cost-weighted-LFU victim exactly (a hot/expensive/shared page survives
        # churn as the docstring promises), while giving O(log n) selection.
        shared_boost = 1.0 + 0.5 * max(0, len(e.tenants) - 1)
        return (e.freq * e.recompute_cost * shared_boost) / max(e.size, 1)

    def _priority(self, e: _Entry) -> float:
        # GDSF: aging + contribution. Used for the fairness-floor ranking and to
        # advance the aging term on eviction (kept for behavioural compatibility);
        # the aging term does not affect relative eviction order (see above).
        return self._age + self._contribution(e)

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
                self._push(e)
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
            self._push(e)
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
                self._push(e)
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
            self._push(e)
            self._evict_to_budget(protect_tenant=tenant)
            return value

    def _push(self, e: _Entry) -> None:
        """Record ``e``'s current priority as a fresh heap node (lazy deletion:
        older nodes for the same key become stale and are skipped on pop)."""
        self._seq += 1
        e.heap_seq = self._seq
        # Order by the age-invariant contribution: it only ever rises (freq and
        # tenant-set grow), so a key's stale nodes carry SMALLER values and are
        # popped-then-skipped before its current one — the first live node popped
        # is the true minimum-contribution victim.
        heapq.heappush(self._heap, (self._contribution(e), self._seq, e.key))
        # Housekeeping: rebuild from the live set when stale nodes dominate, so
        # the heap stays O(live entries) rather than growing with every hit.
        if len(self._heap) > 2 * len(self._entries) + 32:
            self._heap = [(self._contribution(en), en.heap_seq, en.key)
                          for en in self._entries.values()]
            heapq.heapify(self._heap)

    def _evict_to_budget(self, *, protect_tenant: str) -> None:
        if self._bytes <= self.byte_budget:
            return
        # Per-tenant floor (fairness): protect each active tenant's top-`tenant_floor`
        # most-valuable entries. Computed ONCE per pressured admission, not per
        # victim — the O(n)-per-eviction scan is gone (seocho-ia4.12).
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
        # Lazy min-heap eviction: pop the lowest-priority LIVE node. A node is
        # stale (skip) if its key is gone or its seq was superseded by a later
        # push. A PINNED (in-flight, seocho-ia4.4) or floor-protected current node
        # cannot be evicted now — set it aside and re-push after the loop so it
        # stays a future candidate. Victim selection is ~O(log n), not O(n).
        deferred: List[Tuple[float, int, Hashable]] = []
        while self._bytes > self.byte_budget and self._heap:
            val, seq, key = heapq.heappop(self._heap)
            e = self._entries.get(key)
            if e is None or seq != e.heap_seq:
                continue                          # stale node — entry gone or superseded
            if key in protected or e.pins > 0:
                deferred.append((val, seq, key))  # safe candidate, just not now
                continue
            self._age = self._priority(e)         # GDSF aging: floor rises to evicted key
            self._bytes -= e.size
            del self._entries[key]
            del self._store[key]
            self.evictions += 1
        for node in deferred:
            heapq.heappush(self._heap, node)

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
            self._heap.clear()
            self._seq = 0
            self._bytes = 0
            self._age = 0.0
            self.hits = 0
            self.misses = 0
            self.recompute_ms_incurred = 0.0
            self.recompute_ms_avoided = 0.0
            self.evictions = 0
