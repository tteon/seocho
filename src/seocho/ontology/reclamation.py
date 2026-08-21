"""EBR safe-reclamation gate for retired ontology versions (seocho-ia4.4, RCU B3).

The read side (B2, ``VersionPinRegistry``) publishes, per request, the minimum
epoch any reader still holds. B1 (``ActiveOntologyPointer``) swaps the active
version by CAS. What was missing — and what the review flagged as "the pins gate
nothing" — is the **reclaimer**: the piece that actually frees a retired
immutable snapshot, and only once no reader can still reach it. Without it the
snapshot store grows without bound (every published version kept forever) OR a
naive delete races a reader mid-request (use-after-free). This module is that
epoch-based reclamation (EBR) gate.

Model (single-generation publish/swap lifecycle, the B1/B2 case):

- A version ``V`` is active during some epoch ``e_V``. A swap to a new version
  bumps the epoch to ``e_V + 1`` and RETIRES ``V``; we record ``V``'s
  **retirement epoch** = the new active epoch (``e_V + 1``).
- A reader that could observe ``V`` pinned epoch ``e_V`` (= retirement_epoch − 1).
- Therefore ``V`` is safe to reclaim iff **no reader pins an epoch below its
  retirement epoch**: ``min_pinned_epoch is None`` (no readers) OR
  ``min_pinned_epoch >= retirement_epoch``.

The rule is **conservatively safe by construction**: it never reclaims a version
a live reader might still dereference. Across a pointer *recreation* (generation
bump, epoch reset) it may merely *delay* reclamation to a later pass — the safe
bias for a reclaimer. Retirement records are kept in a small SQLite table
(cross-process, same discipline as ``ActiveOntologyPointer``).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional


@dataclass(frozen=True)
class RetiredVersion:
    workspace_id: str
    package_id: str
    version: str
    fingerprint: str
    retirement_epoch: int
    generation: int


@dataclass(frozen=True)
class ReclamationResult:
    reclaimed: List[RetiredVersion]
    held: List[RetiredVersion]          # still reachable by a live reader
    min_pinned_epoch: Optional[int]

    @property
    def reclaimed_versions(self) -> List[str]:
        return [r.version for r in self.reclaimed]

    @property
    def held_versions(self) -> List[str]:
        return [r.version for r in self.held]


class SafeReclamationGate:
    """Reclaims retired ontology snapshots only when no reader can still reach
    them, consuming ``VersionPinRegistry.min_pinned_epoch`` (RCU B3)."""

    def __init__(self, *, pin_registry: Any, snapshot_store: Any, path: str | Path) -> None:
        self._pins = pin_registry
        self._store = snapshot_store
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS retired_ontology ("
                "workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, "
                "retirement_epoch INTEGER, generation INTEGER, "
                "PRIMARY KEY (workspace_id, package_id, version))"
            )

    def retire(self, workspace_id: str, package_id: str, version: str, *,
               fingerprint: str, retirement_epoch: int, generation: int = 0) -> None:
        """Record that ``version`` stopped being active at ``retirement_epoch``
        (the epoch of the version that replaced it). Call this after a successful
        pointer swap, passing the NEW active epoch."""
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO retired_ontology "
                "(workspace_id, package_id, version, fingerprint, retirement_epoch, generation) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id, package_id, version) "
                "DO UPDATE SET retirement_epoch=excluded.retirement_epoch, "
                "generation=excluded.generation",
                (workspace_id, package_id, version, fingerprint, int(retirement_epoch), int(generation)),
            )

    def retired(self, workspace_id: str, package_id: str) -> List[RetiredVersion]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT version, fingerprint, retirement_epoch, generation FROM retired_ontology "
                "WHERE workspace_id=? AND package_id=? ORDER BY retirement_epoch",
                (workspace_id, package_id),
            ).fetchall()
        return [RetiredVersion(workspace_id, package_id, r[0], r[1], r[2], r[3]) for r in rows]

    def _forget(self, workspace_id: str, package_id: str, version: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM retired_ontology WHERE workspace_id=? AND package_id=? AND version=?",
                      (workspace_id, package_id, version))

    def reclaim(self, workspace_id: str, package_id: str, *, dry_run: bool = False) -> ReclamationResult:
        """Reclaim every retired version no live reader can still reach.

        A version is reclaimed iff ``min_pinned_epoch is None`` (no readers) or
        ``min_pinned_epoch >= retirement_epoch``. On reclaim, the immutable
        snapshot file is deleted (``store.delete``) and the retirement record is
        forgotten. ``dry_run`` reports the decision without mutating anything."""
        min_e = self._pins.min_pinned_epoch(workspace_id, package_id)
        reclaimed: List[RetiredVersion] = []
        held: List[RetiredVersion] = []
        for rec in self.retired(workspace_id, package_id):
            safe = (min_e is None) or (min_e >= rec.retirement_epoch)
            if safe:
                if not dry_run:
                    self._store.delete(package_id, rec.version)
                    self._forget(workspace_id, package_id, rec.version)
                reclaimed.append(rec)
            else:
                held.append(rec)
        return ReclamationResult(reclaimed, held, min_e)
