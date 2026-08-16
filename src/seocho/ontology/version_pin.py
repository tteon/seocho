"""RCU reader pin/epoch registry (seocho-ia4.3, B2).

The read side of RCU: a request pins the ACTIVE ontology version's epoch for its whole
duration, reads a frozen version (no torn read), and unpins at the end; the minimum
pinned epoch tells the reclaimer (B3) which versions still have readers.

Redesigned per the B1/B2 adversarial review — the three blockers were all here:
- **[fix #1] increment-then-recheck (publish-before-observe):** ``pin`` reads the active
  pointer → E, increments ``refcount[(ws,pkg,E)]``, then RE-READS the pointer; if it
  advanced, it decrements E and retries on the new epoch. So the refcount is *published*
  before the reader can proceed, and the returned epoch is both refcounted and current —
  closing the read-vs-reclaim race. ``pin`` returns the epoch it actually incremented;
  ``unpin`` decrements THAT epoch, never a fresh read.
- **[fix #2] request-level, decoupled from admission:** this is a standalone registry the
  request-context wrapper drives (``with reg.pinned(ws,pkg): ...`` around the whole agent
  request) — NOT wired inside LaneScheduler.acquire (which short-circuits when the gate is
  disabled and fires per-Cypher-call, both of which would break the guarantee).
- **[fix #6] ``min_pinned_epoch`` returns None when there are no pins** (never "current"),
  so the B3 reclamation gate — not this function — owns the grace-period decision.

Sharded locks (SharedInternTable discipline). Composes with the B1 ``ActiveOntologyPointer``.
"""

from __future__ import annotations

import contextlib
import threading
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple


class VersionPinRegistry:
    def __init__(self, pointer: Any, *, shards: int = 16, max_retries: int = 64) -> None:
        self._pointer = pointer
        self._shards = max(1, shards)
        self._locks = [threading.Lock() for _ in range(self._shards)]
        self._counts: list[Dict[Tuple[str, str, int], int]] = [defaultdict(int) for _ in range(self._shards)]
        self._max_retries = max_retries

    def _shard(self, ws: str, pkg: str) -> int:
        return hash((ws, pkg)) % self._shards

    def _incr(self, ws: str, pkg: str, epoch: int) -> None:
        s = self._shard(ws, pkg)
        with self._locks[s]:
            self._counts[s][(ws, pkg, epoch)] += 1

    def _decr(self, ws: str, pkg: str, epoch: int) -> None:
        s = self._shard(ws, pkg)
        with self._locks[s]:
            key = (ws, pkg, epoch)
            if self._counts[s].get(key, 0) > 0:
                self._counts[s][key] -= 1
                if self._counts[s][key] == 0:
                    del self._counts[s][key]

    def pin(self, ws: str, pkg: str) -> Optional[int]:
        """Pin the current active epoch (increment-then-recheck). Returns the pinned
        epoch, or None if there is no active pointer for (ws, pkg)."""
        for _ in range(self._max_retries):
            av = self._pointer.read(ws, pkg)
            if av is None:
                return None
            e = av.epoch
            self._incr(ws, pkg, e)                       # publish
            av2 = self._pointer.read(ws, pkg)            # observe
            if av2 is not None and av2.epoch == e:
                return e                                  # stable: refcount published on the current epoch
            self._decr(ws, pkg, e)                        # pointer moved mid-pin -> retry on the new epoch
        # extremely contended: pin the last observed epoch rather than spin forever
        av = self._pointer.read(ws, pkg)
        if av is None:
            return None
        self._incr(ws, pkg, av.epoch)
        return av.epoch

    def unpin(self, ws: str, pkg: str, epoch: int) -> None:
        self._decr(ws, pkg, epoch)

    def pin_count(self, ws: str, pkg: str, epoch: int) -> int:
        s = self._shard(ws, pkg)
        with self._locks[s]:
            return self._counts[s].get((ws, pkg, epoch), 0)

    def min_pinned_epoch(self, ws: str, pkg: str) -> Optional[int]:
        """Lowest epoch with a live pin for (ws, pkg), or None if no readers.
        Never substitutes the current epoch — the B3 reclamation gate decides."""
        s = self._shard(ws, pkg)
        with self._locks[s]:
            epochs = [k[2] for k, c in self._counts[s].items()
                      if k[0] == ws and k[1] == pkg and c > 0]
        return min(epochs) if epochs else None

    @contextlib.contextmanager
    def pinned(self, ws: str, pkg: str):
        """Request-level pin: pin for the whole request, unpin in finally (also
        released on request abort/deadline — the liveness bound, review fix #4)."""
        epoch = self.pin(ws, pkg)
        try:
            yield epoch
        finally:
            if epoch is not None:
                self.unpin(ws, pkg, epoch)
