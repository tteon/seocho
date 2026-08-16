"""RCU active-version pointer with atomic compare-and-swap (seocho-ia4.3, B1).

The one mutable word per ``(workspace_id, package_id)`` in the RCU model: which
immutable ontology version is currently ACTIVE. Publishing a new version is a
copy-on-write (a new immutable snapshot) followed by a single **atomic CAS** on this
pointer; readers dereference it once and pin (B2). This is B1 only — the pointer + CAS.

Design decisions forced by the B1/B2 adversarial review (2026-08-16):
- **Real CAS, not TOCTOU.** The swap is a single atomic conditional UPDATE
  (``WHERE generation=? AND epoch=?``) under SQLite ``BEGIN IMMEDIATE`` — the
  ``acknowledge_projection`` UPSERT-RETURNING shape, NOT the racy select-then-compare
  ``assert_projection_fence``. The ``expected=(generation, epoch)`` check is the
  linearization point: concurrent publishers with the same ``expected`` → exactly one
  wins (epoch bumps once); the loser sees its expected epoch is stale and fails.
- **(generation, epoch), globally non-decreasing.** ``epoch`` bumps per swap.
  ``generation`` bumps whenever the pointer is (re)created — seeded from a persistent
  high-water table that survives a delete/restore — so a stale reader holding an old
  ``epoch`` can never be counted against a new version that reused that epoch value.
- **Fencing token** additionally rejects a returned-from-the-dead leader whose lease
  expired (its token is below the stored one); the ``expected`` CAS does the
  serialization.

SQLite-backed now (cross-process atomic via BEGIN IMMEDIATE), interface-compatible with
an etcd backing later. Snapshot storage stays the immutable content-addressed store
(ADR-0117) — this only adds the "which version is active" pointer it lacks.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class ActiveVersion:
    workspace_id: str
    package_id: str
    version: str
    fingerprint: str
    generation: int
    epoch: int
    fencing_token: int

    def expected(self) -> Tuple[int, int]:
        """The (generation, epoch) a swapper must present to CAS on this pointer."""
        return (self.generation, self.epoch)


class ActiveOntologyPointer:
    """Atomic, CAS-able active-version pointer per (workspace_id, package_id)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()   # in-process guard; BEGIN IMMEDIATE = cross-process
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS active_ontology ("
                "workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, "
                "generation INTEGER, epoch INTEGER, fencing_token INTEGER, "
                "PRIMARY KEY (workspace_id, package_id))"
            )
            # persistent per-key high-water for generation — never deleted, so a
            # recreated pointer's generation is strictly greater than any prior one.
            c.execute(
                "CREATE TABLE IF NOT EXISTS generation_hwm ("
                "workspace_id TEXT, package_id TEXT, hwm INTEGER, "
                "PRIMARY KEY (workspace_id, package_id))"
            )

    def read(self, workspace_id: str, package_id: str) -> Optional[ActiveVersion]:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT version, fingerprint, generation, epoch, fencing_token "
                "FROM active_ontology WHERE workspace_id=? AND package_id=?",
                (workspace_id, package_id),
            ).fetchone()
        if row is None:
            return None
        return ActiveVersion(workspace_id, package_id, row[0], row[1], row[2], row[3], row[4])

    def _next_generation(self, c: sqlite3.Connection, ws: str, pkg: str) -> int:
        row = c.execute("SELECT hwm FROM generation_hwm WHERE workspace_id=? AND package_id=?",
                        (ws, pkg)).fetchone()
        nxt = (row[0] + 1) if row else 0
        c.execute("INSERT INTO generation_hwm (workspace_id, package_id, hwm) VALUES (?,?,?) "
                  "ON CONFLICT(workspace_id, package_id) DO UPDATE SET hwm=excluded.hwm",
                  (ws, pkg, nxt))
        return nxt

    def publish(
        self,
        workspace_id: str,
        package_id: str,
        *,
        version: str,
        fingerprint: str,
        fencing_token: int,
        expected: Optional[Tuple[int, int]] = None,
    ) -> Tuple[bool, Optional[ActiveVersion]]:
        """Atomically make ``version`` the ACTIVE pointer.

        - First publish (no row): pass ``expected=None`` → INSERT (generation from the
          high-water, epoch 0). Fails if a row already exists.
        - Swap: pass ``expected=(generation, epoch)`` you read → succeeds iff the stored
          (generation, epoch) still equals ``expected`` AND ``fencing_token`` is not
          below the stored token; bumps epoch by 1. Otherwise a no-op.

        Returns ``(ok, current)`` — ``current`` is the pointer AFTER the call (the new
        value on success, or the value that beat you on failure).
        """
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                cur = c.execute(
                    "SELECT version, fingerprint, generation, epoch, fencing_token "
                    "FROM active_ontology WHERE workspace_id=? AND package_id=?",
                    (workspace_id, package_id),
                ).fetchone()

                if cur is None:
                    if expected is not None:
                        c.execute("ROLLBACK")
                        return (False, None)
                    gen = self._next_generation(c, workspace_id, package_id)
                    c.execute(
                        "INSERT INTO active_ontology "
                        "(workspace_id, package_id, version, fingerprint, generation, epoch, fencing_token) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (workspace_id, package_id, version, fingerprint, gen, 0, int(fencing_token)),
                    )
                    c.execute("COMMIT")
                    return (True, ActiveVersion(workspace_id, package_id, version, fingerprint,
                                                gen, 0, int(fencing_token)))

                cur_ver, cur_fp, cur_gen, cur_epoch, cur_tok = cur
                current = ActiveVersion(workspace_id, package_id, cur_ver, cur_fp,
                                        cur_gen, cur_epoch, cur_tok)
                # linearization point: the (generation, epoch) CAS; fencing rejects a
                # stale leader whose token fell behind.
                if expected != (cur_gen, cur_epoch) or int(fencing_token) < cur_tok:
                    c.execute("ROLLBACK")
                    return (False, current)
                new_epoch = cur_epoch + 1
                c.execute(
                    "UPDATE active_ontology SET version=?, fingerprint=?, epoch=?, fencing_token=? "
                    "WHERE workspace_id=? AND package_id=? AND generation=? AND epoch=?",
                    (version, fingerprint, new_epoch, int(fencing_token),
                     workspace_id, package_id, cur_gen, cur_epoch),
                )
                c.execute("COMMIT")
                return (True, ActiveVersion(workspace_id, package_id, version, fingerprint,
                                            cur_gen, new_epoch, int(fencing_token)))
            except Exception:
                c.execute("ROLLBACK")
                raise

    def recreate(self, workspace_id: str, package_id: str, *, version: str,
                 fingerprint: str, fencing_token: int) -> ActiveVersion:
        """Delete + re-publish, bumping generation past any prior one (monotonic across
        recreation, so old epochs can't be reused against the new pointer)."""
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("DELETE FROM active_ontology WHERE workspace_id=? AND package_id=?",
                          (workspace_id, package_id))
                gen = self._next_generation(c, workspace_id, package_id)
                c.execute(
                    "INSERT INTO active_ontology "
                    "(workspace_id, package_id, version, fingerprint, generation, epoch, fencing_token) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (workspace_id, package_id, version, fingerprint, gen, 0, int(fencing_token)),
                )
                c.execute("COMMIT")
                return ActiveVersion(workspace_id, package_id, version, fingerprint,
                                     gen, 0, int(fencing_token))
            except Exception:
                c.execute("ROLLBACK")
                raise
