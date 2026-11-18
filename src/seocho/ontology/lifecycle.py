"""Single-host ontology lifecycle control plane.

This module deliberately keeps the mutable control plane small: the immutable
RDF bundle lives on the filesystem, while SQLite/WAL holds the active pointer
and expiring writer leases.  It is suitable for one host; it is *not* presented
as a distributed lock service.

Fencing tokens (sr-xsu)
-----------------------
Lease fencing tokens are minted from a durable per-``(workspace_id,
package_id)`` counter, ``ontology_fence_high_water``, which is never deleted.
Each ``acquire()`` mints ``max(high_water, live lease tokens, pointer token) + 1``
inside ``BEGIN IMMEDIATE``, so every issued token is strictly greater than every
token issued before it for that scope, even after leases are released or
pruned.  ``seochod`` stamps the raw admitted lease ``fencing_token`` on graph
items with ``_fence_scope = package_id``; tokens are only comparable within
one ``(workspace_id, package_id)`` scope.  ``seochod`` only accepts leases with
``purpose='projection'``; query/index leases still consume tokens from the same
counter but cannot authorise a canonical write.

Migration limit and upgrade order: on first open by this code, existing DBs
seed the high-water from lease rows that still exist and from the active
pointer.  Tokens that pre-upgrade code issued *and already released* (rows
deleted) are not recoverable, so a new lease may reuse such a value.  That is
harmless because pre-upgrade ``seochod`` never stamped ``_fencing_token`` on
graph items, so no persisted fence can compare against a lost token.  It
follows that operators must:

1. upgrade the Python lifecycle store in **all** processes that open the
   control DB before deploying a ``seochod`` that stamps fencing tokens; and
2. not run pre-upgrade ``acquire()`` processes alongside upgraded ones (old
   code ignores the high-water, so a lease it mints and releases after the
   migration is lost again).

Opening an already-migrated DB performs only reads, so read-only openers (for
example ``file:/path/state.sqlite?mode=ro`` or a non-writable file) can call
``status()`` and ``admission()`` without a write lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .active_pointer import ActiveOntologyPointer, ActiveVersion


@dataclass(frozen=True)
class OntologyLease:
    lease_id: str
    workspace_id: str
    package_id: str
    purpose: str
    owner: str
    fingerprint: str
    generation: int
    epoch: int
    fencing_token: int
    acquired_at_ms: int
    expires_at_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_rdf_bundle(directory: str | Path) -> dict[str, Any]:
    """Verify manifest-declared artifact hashes without parsing untrusted RDF."""
    root = Path(directory)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "manifest.json is missing", "directory": str(root)}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
    except (OSError, ValueError, KeyError) as exc:
        return {"ok": False, "error": f"invalid manifest: {exc}", "directory": str(root)}
    actual: dict[str, str] = {}
    missing: list[str] = []
    for name in sorted(files):
        path = root / name
        if not path.is_file():
            missing.append(name)
        else:
            actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ok = not missing and actual == files and digest == manifest.get("bundle_sha256")
    return {
        "ok": ok, "directory": str(root), "bundle_sha256": manifest.get("bundle_sha256"),
        "computed_bundle_sha256": digest, "ontology": manifest.get("ontology", {}),
        "missing": missing,
    }


def build_bundle_atomically(ontology: Any, destination: str | Path) -> dict[str, Any]:
    """Publish a new bundle by atomic directory rename; never overwrite a bundle."""
    from .rdf_bundle import build_rdf_ontology_bundle

    target = Path(destination)
    if target.exists():
        raise ValueError(f"bundle destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        bundle = build_rdf_ontology_bundle(ontology, staging)
        checked = verify_rdf_bundle(staging)
        if not checked["ok"]:
            raise ValueError(f"refusing to publish invalid bundle: {checked.get('error', 'hash mismatch')}")
        # fsync files before publishing the directory entry.  Directory fsync is
        # best effort because not every platform supports it.
        for path in staging.rglob("*"):
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        os.replace(staging, target)
        try:
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
        return {"directory": str(target), "bundle_sha256": bundle.digest, "verified": True}
    except Exception:
        # Only our unique staging directory may be cleaned up on failure.
        if staging.exists():
            import shutil
            shutil.rmtree(staging)
        raise


def load_agent_profile(bundle_dir: str | Path, purpose: str) -> dict[str, Any]:
    """Load a verified, purpose-scoped profile without exposing a host path."""
    checked = verify_rdf_bundle(bundle_dir)
    if not checked["ok"]:
        raise ValueError("cannot load a profile from an invalid bundle")
    path = Path(bundle_dir) / "agent-profiles" / f"{purpose}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"agent profile unavailable: {purpose}") from exc
    if profile.get("schema_version") != "seocho.agent_ontology_profile.v1" or profile.get("purpose") != purpose:
        raise ValueError("agent profile schema or purpose mismatch")
    if profile.get("canonical_bundle_sha256") != checked["bundle_sha256"]:
        raise ValueError("agent profile is not derived from this bundle")
    candidate = dict(profile); profile_hash = candidate.pop("profile_sha256", "")
    computed = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if profile_hash != computed:
        raise ValueError("agent profile hash mismatch")
    return profile


def slice_agent_profile(bundle_dir: str | Path, purpose: str, terms: list[str], *, max_chars: int = 4000) -> dict[str, Any]:
    """Return a bounded, term-relevant profile view for JIT agent context."""
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    profile = load_agent_profile(bundle_dir, purpose)
    haystack = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    normalized = [term.casefold() for term in terms if term.strip()]
    if not normalized:
        raise ValueError("at least one retrieval term is required")
    matched = [term for term in normalized if term in haystack.casefold()]
    # Stable, bounded fields are intentionally returned instead of raw ontology
    # source.  The agent can request a second, more specific slice when needed.
    payload = {
        "schema_version": "seocho.agent_ontology_slice.v1",
        "purpose": purpose,
        "canonical_bundle_sha256": profile["canonical_bundle_sha256"],
        "profile_sha256": profile["profile_sha256"],
        "terms": normalized,
        "matched_terms": matched,
        "allowed_node_labels": profile.get("allowed_node_labels", []),
        "allowed_relationship_types": profile.get("allowed_relationship_types", []),
    }
    context = profile.get(f"{purpose}_context", {})
    selected = {key: value for key, value in context.items() if any(term in str(value).casefold() or term in key.casefold() for term in normalized)}
    payload["context"] = selected
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(rendered) > max_chars:
        payload["context"] = {"notice": "slice truncated; refine terms or request a narrower tool call"}
        payload["truncated"] = True
    else:
        payload["truncated"] = False
    return payload


def _is_readonly_error(exc: sqlite3.OperationalError) -> bool:
    return "readonly" in str(exc).casefold() or "read-only" in str(exc).casefold()


class OntologyLifecycleStore:
    """Persistent active-pointer plus expiring writer lease store for one host."""

    def __init__(self, state_db: str | Path) -> None:
        self.path = str(state_db)
        self.pointer = ActiveOntologyPointer(self.path)
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ontology_lease ("
                "lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, "
                "purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL, "
                "acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ontology_lease_live ON ontology_lease(workspace_id, package_id, expires_at_ms)")
            # Durable per-(workspace, package) fencing-token high-water.  Lease
            # rows are deleted on release, so MAX(ontology_lease.fencing_token)
            # alone would let a later acquire reissue a released token.  This
            # row is never deleted: every issued token is strictly greater than
            # every token issued before it for the lifetime of the control DB.
            fresh = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ontology_fence_high_water'"
            ).fetchone() is None
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ontology_fence_high_water ("
                "workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, high_water INTEGER NOT NULL, "
                "PRIMARY KEY (workspace_id, package_id))"
            )
            if fresh or self._fence_high_water_needs_migration(conn):
                self._migrate_fence_high_water(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS projection_idempotency ("
                "idempotency_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "state TEXT NOT NULL, response_json TEXT, created_at_ms INTEGER NOT NULL, completed_at_ms INTEGER)"
            )

    @staticmethod
    def _fence_high_water_needs_migration(conn: sqlite3.Connection) -> bool:
        """Cheap read-only check: does any live lease row sit above its high-water?

        Only lease rows matter here: they are the one token source that is
        deleted (on release), so a lease minted by pre-upgrade code must be
        folded into the high-water before it can be released and forgotten.
        Pointer tokens are durable and ``acquire()`` already takes
        ``max(high_water, live leases, pointer) + 1``, so a pointer above the
        high-water (the normal state right after ``activate``) needs no write.
        Leases minted by this code always satisfy ``token <= high_water``.
        """
        return conn.execute(
            "SELECT 1 FROM ("
            "  SELECT workspace_id, package_id, MAX(fencing_token) AS tok FROM ontology_lease"
            "  GROUP BY workspace_id, package_id"
            ") AS s LEFT JOIN ontology_fence_high_water AS h"
            "  ON h.workspace_id = s.workspace_id AND h.package_id = s.package_id "
            "WHERE h.high_water IS NULL OR s.tok > h.high_water LIMIT 1"
        ).fetchone() is not None

    @staticmethod
    def _migrate_fence_high_water(conn: sqlite3.Connection) -> None:
        """Seed/raise the high-water from surviving lease and pointer tokens.

        Idempotent and only ever raises ``high_water``.  It runs only when the
        table is freshly created or :meth:`_fence_high_water_needs_migration`
        finds drift, so opening an already-migrated DB takes no write lock and
        works on a read-only database.  Correctness of :meth:`acquire` does not
        depend on this having run: it mints ``max(high_water, live leases,
        pointer) + 1`` inside its own ``BEGIN IMMEDIATE``.  The migration only
        protects tokens of leases that are *still present* at upgrade time;
        see the module docstring for the upgrade-order limit.
        """
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_readonly_error(exc):
                return  # a read-only opener can neither acquire nor release
            raise
        try:
            conn.execute(
                "INSERT INTO ontology_fence_high_water (workspace_id, package_id, high_water) "
                "SELECT workspace_id, package_id, MAX(tok) FROM ("
                "  SELECT workspace_id, package_id, fencing_token AS tok FROM ontology_lease"
                "  UNION ALL"
                "  SELECT workspace_id, package_id, COALESCE(fencing_token, 0) AS tok FROM active_ontology"
                ") WHERE true GROUP BY workspace_id, package_id "
                "ON CONFLICT(workspace_id, package_id) DO UPDATE SET "
                "high_water = MAX(ontology_fence_high_water.high_water, excluded.high_water)"
            )
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            conn.execute("ROLLBACK")
            if _is_readonly_error(exc):
                # A read-only opener cannot acquire or release leases, so it
                # cannot lose a token; the next read-write open migrates.
                return
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _next_fencing_token(conn: sqlite3.Connection, workspace_id: str, package_id: str, pointer_token: int) -> int:
        """Mint the next token; caller must hold a write transaction on ``conn``."""
        high_water = conn.execute(
            "SELECT high_water FROM ontology_fence_high_water WHERE workspace_id=? AND package_id=?",
            (workspace_id, package_id),
        ).fetchone()
        live_max = conn.execute(
            "SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease WHERE workspace_id=? AND package_id=?",
            (workspace_id, package_id),
        ).fetchone()[0]
        token = max(int(high_water[0]) if high_water else 0, int(live_max), int(pointer_token or 0)) + 1
        conn.execute(
            "INSERT INTO ontology_fence_high_water (workspace_id, package_id, high_water) VALUES (?,?,?) "
            "ON CONFLICT(workspace_id, package_id) DO UPDATE SET high_water=excluded.high_water",
            (workspace_id, package_id, token),
        )
        return token

    def _conn(self) -> sqlite3.Connection:
        # ``uri=True`` lets callers pass e.g. ``file:/path/state.sqlite?mode=ro``
        # for a read-only status/admission reader; plain paths are unaffected.
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10.0, uri=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _active_or_raise(pointer: ActiveVersion | None) -> ActiveVersion:
        if pointer is None:
            raise ValueError("no active ontology for workspace/package")
        return pointer

    def activate(self, workspace_id: str, package_id: str, bundle_dir: str | Path, *, fencing_token: int, expected: tuple[int, int] | None = None) -> tuple[bool, ActiveVersion | None]:
        checked = verify_rdf_bundle(bundle_dir)
        if not checked["ok"]:
            raise ValueError(f"cannot activate invalid bundle: {checked}")
        ontology = checked["ontology"]
        if ontology.get("package_id") != package_id:
            raise ValueError("bundle package_id does not match --package")
        return self.pointer.publish(
            workspace_id, package_id, version=str(ontology.get("version", "")),
            fingerprint=str(checked["bundle_sha256"]), fencing_token=fencing_token, expected=expected,
        )

    def acquire(self, workspace_id: str, package_id: str, *, purpose: str, owner: str, ttl_seconds: int) -> OntologyLease:
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        now = int(time.time() * 1000)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Read the pointer inside this write transaction so the token we
                # mint is ordered against a concurrent activate() as well.
                row = conn.execute(
                    "SELECT version, fingerprint, generation, epoch, fencing_token FROM active_ontology WHERE workspace_id=? AND package_id=?",
                    (workspace_id, package_id),
                ).fetchone()
                active = self._active_or_raise(ActiveVersion(workspace_id, package_id, *row) if row else None)
                existing = conn.execute(
                    "SELECT lease_id, owner, expires_at_ms FROM ontology_lease WHERE workspace_id=? AND package_id=? AND purpose=? AND expires_at_ms>? ORDER BY expires_at_ms DESC LIMIT 1",
                    (workspace_id, package_id, purpose, now),
                ).fetchone()
                if existing:
                    raise ValueError(f"live lease held by {existing[1]} until {existing[2]} (lease {existing[0]})")
                fence = self._next_fencing_token(conn, workspace_id, package_id, active.fencing_token)
                lease = OntologyLease(str(uuid.uuid4()), workspace_id, package_id, purpose, owner, active.fingerprint, active.generation, active.epoch, fence, now, now + ttl_seconds * 1000)
                conn.execute("INSERT INTO ontology_lease VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(lease.to_dict().values()))
                conn.execute("COMMIT")
                return lease
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def renew(self, lease_id: str, *, owner: str, ttl_seconds: int) -> OntologyLease:
        now = int(time.time() * 1000)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ontology_lease WHERE lease_id=?", (lease_id,)).fetchone()
            if row is None or row[4] != owner or row[10] <= now:
                raise ValueError("lease is absent, owned by another process, or expired")
            conn.execute("UPDATE ontology_lease SET expires_at_ms=? WHERE lease_id=?", (now + ttl_seconds * 1000, lease_id))
            row = list(row); row[10] = now + ttl_seconds * 1000
            return OntologyLease(*row)

    def release(self, lease_id: str, *, owner: str) -> bool:
        with self._conn() as conn:
            result = conn.execute("DELETE FROM ontology_lease WHERE lease_id=? AND owner=?", (lease_id, owner))
        return result.rowcount == 1

    def admission(self, lease_id: str) -> dict[str, Any]:
        """Return the minimal daemon capability, without exposing filesystem paths."""
        now = int(time.time() * 1000)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ontology_lease WHERE lease_id=?", (lease_id,)).fetchone()
        if row is None:
            raise ValueError("lifecycle lease is absent")
        lease = OntologyLease(*row)
        if lease.expires_at_ms <= now:
            raise ValueError("lifecycle lease has expired")
        return {key: getattr(lease, key) for key in ("lease_id", "fingerprint", "generation", "epoch", "fencing_token")}

    def status(self, workspace_id: str, package_id: str) -> dict[str, Any]:
        now = int(time.time() * 1000)
        active = self.pointer.read(workspace_id, package_id)
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM ontology_lease WHERE workspace_id=? AND package_id=? AND expires_at_ms>? ORDER BY expires_at_ms", (workspace_id, package_id, now)).fetchall()
        return {"active": asdict(active) if active else None, "live_leases": [OntologyLease(*r).to_dict() for r in rows], "clock_ms": now}
