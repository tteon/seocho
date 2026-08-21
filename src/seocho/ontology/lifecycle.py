"""Single-host ontology lifecycle control plane.

This module deliberately keeps the mutable control plane small: the immutable
RDF bundle lives on the filesystem, while SQLite/WAL holds the active pointer
and expiring writer leases.  It is suitable for one host; it is *not* presented
as a distributed lock service.
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

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
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
                active = self._active_or_raise(self.pointer.read(workspace_id, package_id))
                existing = conn.execute(
                    "SELECT lease_id, owner, expires_at_ms FROM ontology_lease WHERE workspace_id=? AND package_id=? AND purpose=? AND expires_at_ms>? ORDER BY expires_at_ms DESC LIMIT 1",
                    (workspace_id, package_id, purpose, now),
                ).fetchone()
                if existing:
                    raise ValueError(f"live lease held by {existing[1]} until {existing[2]} (lease {existing[0]})")
                fence = max(active.fencing_token + 1, int(conn.execute("SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease WHERE workspace_id=? AND package_id=?", (workspace_id, package_id)).fetchone()[0]) + 1)
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
