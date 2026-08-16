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
so concurrent interning does not serialize on one global lock.

Hardening (seocho-ia4, review-flagged — "a heap with no free()"):

- **free()/reclamation (bounded).** Optional ``max_entries`` caps the resident
  L1 map; when exceeded, the least-recently-used **zero-reference** entries are
  reclaimed. ``retain``/``release`` refcount the entries a live consumer still
  holds, so an in-flight canonical address is never reclaimed under it. Without a
  cap the table grows unbounded (the OOM the review flagged); with one, RAM is
  bounded and correctness is preserved because canonical values are immutable
  (see below), so a reclaimed entry re-interns to the same address.
- **Cross-process shared namespace (optional SQLite backing).** With
  ``sqlite_path`` set, interning is a cross-process **atomic first-writer-wins**
  (``INSERT OR IGNORE`` + ``SELECT``) into a durable table; the in-memory shards
  become a bounded, coherent cache of it. Coherent *by construction*: a
  ``(workspace, identity)`` mapping is written once and never changes, so a
  cached value can never be stale, and L1 reclamation is always safe (re-fetch
  yields the identical value). This replaces the process-local, racy JSON merge
  the review flagged for the read/intern path. Cross-process *refcount-driven
  reclamation* is deliberately out of scope (deferred): the durable store is
  append-only and GC'd offline; only the per-process L1 is bounded here.
- **Atomic persist.** The legacy JSON snapshot now writes to a temp file and
  ``os.replace``s it into place, so a concurrent reader never sees a torn file.
  Prefer the SQLite backing when several processes share one namespace.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import Dict, Optional, Tuple


class SharedInternTable:
    """Concurrent (workspace, identity) -> canonical-id intern table."""

    def __init__(self, *, shards: int = 16, max_entries: Optional[int] = None,
                 sqlite_path: Optional[str] = None) -> None:
        self._shards = max(1, shards)
        self._maps: list[OrderedDict[Tuple[str, str], str]] = [OrderedDict() for _ in range(self._shards)]
        self._refs: list[Dict[Tuple[str, str], int]] = [dict() for _ in range(self._shards)]
        # Read-side resolution index (seocho-t28/zfe): a source-agnostic
        # (workspace, normalized-name) -> {canonical_id, ...} MULTIMAP. Writes
        # register their canonical id under the entity's bare name so a read that
        # only knows the mention text can find it — the composite identity
        # (label|name|company|year) is not reconstructable from a bare mention, so
        # the primary map alone always misses multi-key entities. Homonyms keep a
        # SET (not first-writer-wins): resolution surfaces the candidates rather
        # than silently collapsing "PTC revenue" and "Tesla revenue" onto one node.
        self._alias: list[Dict[Tuple[str, str], set]] = [dict() for _ in range(self._shards)]
        self._locks = [threading.Lock() for _ in range(self._shards)]
        self._max_entries = int(max_entries) if max_entries else None
        # per-shard soft cap; None = unbounded (back-compatible default)
        self._shard_cap = (
            max(1, -(-self._max_entries // self._shards)) if self._max_entries else None
        )
        self._sqlite_path = str(sqlite_path) if sqlite_path else None
        if self._sqlite_path:
            self._init_sqlite()
        self._interns = 0
        self._hits = 0
        self._reclaimed = 0
        self._stats_lock = threading.Lock()

    def _shard(self, key: Tuple[str, str]) -> int:
        return hash(key) % self._shards

    # -- read-side name resolution index (seocho-t28/zfe) --------------------
    @staticmethod
    def _norm_name(name: str) -> str:
        return " ".join(str(name or "").strip().lower().split())

    def alias(self, workspace_id: str, name: str, canonical_id: str) -> None:
        """Register ``name -> canonical_id`` so a read that knows only the mention
        text can find this entity. Additive to :meth:`intern`; homonyms accumulate
        (a SET), they do not overwrite. No-op for an empty name/canonical."""
        norm = self._norm_name(name)
        if not norm or not canonical_id:
            return
        key = (str(workspace_id), norm)
        s = self._shard(key)
        with self._locks[s]:
            self._alias[s].setdefault(key, set()).add(canonical_id)

    def candidates(self, workspace_id: str, name: str) -> Tuple[str, ...]:
        """Canonical ids registered under ``name`` in this workspace, sorted.
        One element = an unambiguous resolve; more than one = a homonym the caller
        must disambiguate with query context (never silently pick one)."""
        norm = self._norm_name(name)
        if not norm:
            return ()
        key = (str(workspace_id), norm)
        s = self._shard(key)
        with self._locks[s]:
            return tuple(sorted(self._alias[s].get(key, set())))

    def resolve_one(self, workspace_id: str, name: str) -> str:
        """The single canonical id for ``name``, or ``""`` if absent OR ambiguous
        (a homonym is deliberately NOT resolved to a guess here)."""
        cands = self.candidates(workspace_id, name)
        return cands[0] if len(cands) == 1 else ""

    # -- SQLite cross-process backing ----------------------------------------
    def _init_sqlite(self) -> None:
        import sqlite3
        from pathlib import Path
        Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self._sqlite_path, timeout=10.0)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=10000")
            c.execute(
                "CREATE TABLE IF NOT EXISTS intern ("
                "workspace_id TEXT, identity TEXT, canonical TEXT, "
                "PRIMARY KEY (workspace_id, identity))"
            )
            c.commit()
        finally:
            c.close()

    def _sqlite_intern(self, ws: str, identity: str, canonical: str) -> str:
        """Cross-process atomic first-writer-wins; returns the winning canonical."""
        import sqlite3
        c = sqlite3.connect(self._sqlite_path, timeout=10.0)
        try:
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("INSERT OR IGNORE INTO intern (workspace_id, identity, canonical) "
                      "VALUES (?,?,?)", (ws, identity, canonical))
            c.commit()
            row = c.execute("SELECT canonical FROM intern WHERE workspace_id=? AND identity=?",
                            (ws, identity)).fetchone()
            return row[0] if row else canonical
        finally:
            c.close()

    def _sqlite_get(self, ws: str, identity: str) -> str:
        import sqlite3
        c = sqlite3.connect(self._sqlite_path, timeout=10.0)
        try:
            row = c.execute("SELECT canonical FROM intern WHERE workspace_id=? AND identity=?",
                            (ws, identity)).fetchone()
            return row[0] if row else ""
        finally:
            c.close()

    # -- intern / get --------------------------------------------------------
    def intern(self, workspace_id: str, identity: str, canonical_id: str) -> str:
        """Return the canonical id for ``(workspace_id, identity)``, inserting
        ``canonical_id`` if unseen. First writer wins — subsequent callers (any
        thread, and with a SQLite backing any process) get the same address, so
        concurrent interning of the same entity converges to one node.
        Thread-safe."""
        ws, ident = str(workspace_id), str(identity)
        key = (ws, ident)
        s = self._shard(key)
        with self._locks[s]:
            existing = self._maps[s].get(key)
            if existing is not None:
                self._maps[s].move_to_end(key)          # LRU recency
                with self._stats_lock:
                    self._hits += 1
                return existing
            winner = self._sqlite_intern(ws, ident, canonical_id) if self._sqlite_path else canonical_id
            self._maps[s][key] = winner
            self._refs[s].setdefault(key, 0)
            self._reclaim_shard(s)
        with self._stats_lock:
            self._interns += 1
        return winner

    def get(self, workspace_id: str, identity: str) -> str:
        ws, ident = str(workspace_id), str(identity)
        key = (ws, ident)
        s = self._shard(key)
        with self._locks[s]:
            existing = self._maps[s].get(key)
            if existing is not None:
                self._maps[s].move_to_end(key)
                return existing
        if self._sqlite_path:
            v = self._sqlite_get(ws, ident)
            if v:
                with self._locks[s]:                    # populate L1 (immutable → safe)
                    self._maps[s].setdefault(key, v)
                    self._refs[s].setdefault(key, 0)
                    self._reclaim_shard(s)
            return v
        return ""

    # -- refcount + reclamation (the free() the review demanded) -------------
    def retain(self, workspace_id: str, identity: str) -> None:
        """Mark ``(workspace_id, identity)`` as in-use so it is never reclaimed
        while referenced. No-op if the key is not resident."""
        key = (str(workspace_id), str(identity))
        s = self._shard(key)
        with self._locks[s]:
            if key in self._maps[s]:
                self._refs[s][key] = self._refs[s].get(key, 0) + 1
                self._maps[s].move_to_end(key)

    def release(self, workspace_id: str, identity: str) -> None:
        key = (str(workspace_id), str(identity))
        s = self._shard(key)
        with self._locks[s]:
            if self._refs[s].get(key, 0) > 0:
                self._refs[s][key] -= 1

    @contextmanager
    def referenced(self, workspace_id: str, identity: str):
        """Context manager: retain for the duration of an in-flight use."""
        self.retain(workspace_id, identity)
        try:
            yield
        finally:
            self.release(workspace_id, identity)

    def _reclaim_shard(self, s: int) -> None:
        """Evict LRU zero-reference entries from shard ``s`` down to its cap.
        Must be called under ``self._locks[s]``."""
        if self._shard_cap is None:
            return
        m, refs = self._maps[s], self._refs[s]
        while len(m) > self._shard_cap:
            victim = None
            for key in m:                                # oldest-first (LRU)
                if refs.get(key, 0) <= 0:
                    victim = key
                    break
            if victim is None:
                break                                    # everything resident is pinned
            del m[victim]
            refs.pop(victim, None)
            with self._stats_lock:
                self._reclaimed += 1

    def reclaim(self) -> int:
        """Force a reclamation pass across all shards; returns entries reclaimed.
        Only zero-reference entries are removed. In SQLite-backed mode the durable
        namespace is untouched (re-fetchable); this bounds the in-memory cache."""
        before = self._reclaimed
        for s in range(self._shards):
            with self._locks[s]:
                self._reclaim_shard(s)
        return self._reclaimed - before

    def pinned_count(self) -> int:
        return sum(1 for s in range(self._shards) for v in self._refs[s].values() if v > 0)

    def __len__(self) -> int:
        return sum(len(m) for m in self._maps)

    def stats(self) -> Dict[str, int]:
        return {"size": len(self), "interns": self._interns, "hits": self._hits,
                "shards": self._shards, "reclaimed": self._reclaimed,
                "pinned": self.pinned_count(),
                "aliases": sum(len(a) for a in self._alias),
                "max_entries": self._max_entries or 0,
                "backing": "sqlite" if self._sqlite_path else "memory"}

    def clear(self) -> None:
        for i in range(self._shards):
            with self._locks[i]:
                self._maps[i].clear()
                self._refs[i].clear()
                self._alias[i].clear()
        with self._stats_lock:
            self._interns = 0
            self._hits = 0
            self._reclaimed = 0

    # -- cross-session persistence -------------------------------------------
    # The canonical namespace outlives one process. Prefer the SQLite backing for
    # concurrent multi-process sharing; this JSON snapshot remains for single-writer
    # export/import and is now written atomically (temp file + os.replace) so a
    # concurrent reader never observes a torn file.

    def snapshot(self) -> list:
        """Return a JSON-serialisable list of [workspace, identity, canonical].
        With a SQLite backing, returns the full durable namespace; otherwise the
        resident in-memory entries."""
        if self._sqlite_path:
            import sqlite3
            c = sqlite3.connect(self._sqlite_path, timeout=10.0)
            try:
                return [[ws, ident, canon] for ws, ident, canon
                        in c.execute("SELECT workspace_id, identity, canonical FROM intern")]
            finally:
                c.close()
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
        tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps({"entries": self.snapshot()}, indent=0))
        os.replace(tmp, p)                               # atomic publish

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
        if self._sqlite_path:
            import sqlite3
            c = sqlite3.connect(self._sqlite_path, timeout=10.0)
            try:
                c.execute("PRAGMA busy_timeout=10000")
                sql = ("INSERT OR IGNORE" if merge else "INSERT OR REPLACE")
                for ws, ident, canon in data.get("entries", []):
                    c.execute(f"{sql} INTO intern (workspace_id, identity, canonical) "
                              "VALUES (?,?,?)", (str(ws), str(ident), canon))
                    n += 1
                c.commit()
            finally:
                c.close()
            return n
        for ws, ident, canon in data.get("entries", []):
            key = (str(ws), str(ident))
            s = self._shard(key)
            with self._locks[s]:
                if not merge:
                    self._maps[s][key] = canon
                    self._refs[s].setdefault(key, 0)
                    n += 1
                elif key not in self._maps[s]:
                    self._maps[s][key] = canon
                    self._refs[s].setdefault(key, 0)
                    n += 1
        return n
