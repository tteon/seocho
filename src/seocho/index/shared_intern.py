"""A thread-safe shared intern table — the allocator's shared-memory core.

hadry's OS-shared-memory intuition made concrete: the intern table IS the
canonical-entity namespace (the allocator's heap). When indexing goes concurrent
(``parallel.concurrent_map`` over chunks) or several agents write under one
workspace, they must see ONE canonical address per entity — otherwise the same
entity, interned on two threads, fragments into two nodes and cross-chunk axioms
lose support. This is a **process-wide, thread-safe, workspace-scoped** map from a
composite identity to its canonical id: shared memory with a protection domain.

Keyed by ``(workspace_id, compute_node_identity(...))`` so tenants never collide
(the ``workspace_id`` protection domain). Sharded by key hash under per-shard locks
so concurrent interning does not serialize on one global lock — the same discipline
a concurrent allocator uses. Pure-Python today; if profiling shows this is the
CPU-bound hot path at scale, it is the natural candidate for a Rust
``seocho-core`` concurrent map (the profile ADR states the trigger — we do not
Rust-rewrite before measuring).
"""

from __future__ import annotations

import threading
from typing import Dict, Tuple


class SharedInternTable:
    """Concurrent (workspace, identity) -> canonical-id intern table."""

    def __init__(self, *, shards: int = 16) -> None:
        self._shards = max(1, shards)
        self._maps: list[Dict[Tuple[str, str], str]] = [dict() for _ in range(self._shards)]
        self._locks = [threading.Lock() for _ in range(self._shards)]
        self._interns = 0
        self._hits = 0
        self._stats_lock = threading.Lock()

    def _shard(self, key: Tuple[str, str]) -> int:
        return hash(key) % self._shards

    def intern(self, workspace_id: str, identity: str, canonical_id: str) -> str:
        """Return the canonical id for ``(workspace_id, identity)``, inserting
        ``canonical_id`` if unseen. First writer wins — subsequent callers (any
        thread) get the same address, so concurrent interning of the same entity
        converges to one node. Thread-safe."""
        key = (str(workspace_id), str(identity))
        s = self._shard(key)
        with self._locks[s]:
            existing = self._maps[s].get(key)
            if existing is not None:
                with self._stats_lock:
                    self._hits += 1
                return existing
            self._maps[s][key] = canonical_id
        with self._stats_lock:
            self._interns += 1
        return canonical_id

    def get(self, workspace_id: str, identity: str) -> str:
        key = (str(workspace_id), str(identity))
        return self._maps[self._shard(key)].get(key, "")

    def __len__(self) -> int:
        return sum(len(m) for m in self._maps)

    def stats(self) -> Dict[str, int]:
        return {"size": len(self), "interns": self._interns, "hits": self._hits,
                "shards": self._shards}

    def clear(self) -> None:
        for i in range(self._shards):
            with self._locks[i]:
                self._maps[i].clear()
        with self._stats_lock:
            self._interns = 0
            self._hits = 0

    # -- cross-session persistence -------------------------------------------
    # The canonical namespace outlives one process: persist to a shared file so a
    # later session (or a different model run) loads the SAME addresses and its
    # entities intern INTO the existing namespace — the allocator's heap survives
    # the process, and many sessions/agents/models share one address space.

    def snapshot(self) -> list:
        """Return a JSON-serialisable list of [workspace, identity, canonical]."""
        out = []
        for i in range(self._shards):
            with self._locks[i]:
                for (ws, ident), canon in self._maps[i].items():
                    out.append([ws, ident, canon])
        return out

    def persist(self, path) -> None:
        import json
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"entries": self.snapshot()}, indent=0))

    def load(self, path, *, merge: bool = True) -> int:
        """Load a persisted namespace. ``merge`` keeps existing entries (first-writer
        wins across the merge too). Returns the number of entries loaded."""
        import json
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return 0
        data = json.loads(p.read_text() or "{}")
        n = 0
        for ws, ident, canon in data.get("entries", []):
            key = (str(ws), str(ident))
            s = self._shard(key)
            with self._locks[s]:
                if not merge:
                    self._maps[s][key] = canon
                    n += 1
                elif key not in self._maps[s]:
                    self._maps[s][key] = canon
                    n += 1
        return n
