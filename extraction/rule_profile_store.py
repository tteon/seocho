from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


DEFAULT_RULE_PROFILE_DIR = "outputs/rule_profiles"
DEFAULT_RETENTION_MAX = 200


def save_rule_profile(
    workspace_id: str,
    rule_profile: Dict[str, Any],
    name: Optional[str] = None,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
    *,
    ontology_identity_hash: str = "",
) -> Dict[str, Any]:
    """Persist a rule profile to the workspace's sqlite store.

    Phase 5: ``ontology_identity_hash`` (typically
    ``OntologyContextDescriptor.context_hash``, or
    ``RuleSet.ontology_identity_hash`` from the inferred profile) is
    stamped on the row so the loader can refuse application across an
    ontology version change. Empty string preserves legacy behavior.
    The hash is also propagated into the stored ``rule_profile_json``
    so a profile reconstructed from the JSON alone still carries it.
    """

    profile_id = _new_profile_id()
    created_at = _now_iso()
    schema_version = rule_profile.get("schema_version", "rules.v1")
    rule_count = len(rule_profile.get("rules", []))

    stamped_hash = str(
        ontology_identity_hash or rule_profile.get("ontology_identity_hash", "") or ""
    ).strip()
    if stamped_hash:
        rule_profile = {**rule_profile, "ontology_identity_hash": stamped_hash}

    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        profile_version = _next_profile_version(conn, workspace_id)
        conn.execute(
            """
            INSERT INTO rule_profiles (
              profile_id, workspace_id, profile_version, name, created_at,
              schema_version, rule_count, rule_profile_json, ontology_identity_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                workspace_id,
                profile_version,
                name or profile_id,
                created_at,
                schema_version,
                rule_count,
                json.dumps(rule_profile, ensure_ascii=True),
                stamped_hash,
            ),
        )
        _apply_retention(conn, workspace_id)

    return {
        "profile_id": profile_id,
        "workspace_id": workspace_id,
        "profile_version": profile_version,
        "name": name or profile_id,
        "created_at": created_at,
        "schema_version": schema_version,
        "rule_count": rule_count,
        "ontology_identity_hash": stamped_hash,
        "rule_profile": rule_profile,
    }


def get_rule_profile(
    workspace_id: str,
    profile_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
    *,
    expected_ontology_hash: str = "",
) -> Dict[str, Any]:
    """Load a rule profile by id.

    Phase 5: when ``expected_ontology_hash`` is non-empty, the returned
    payload carries an ``artifact_ontology_mismatch`` block summarizing
    whether the stored ``ontology_identity_hash`` matches. Reads
    themselves don't fail on mismatch — the caller decides whether to
    refuse application.
    """

    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        row = conn.execute(
            """
            SELECT profile_id, workspace_id, profile_version, name, created_at,
                   schema_version, rule_count, rule_profile_json, ontology_identity_hash
            FROM rule_profiles
            WHERE workspace_id = ? AND profile_id = ?
            """,
            (workspace_id, profile_id),
        ).fetchone()

    if row is None:
        raise FileNotFoundError(f"rule profile not found: workspace={workspace_id}, profile_id={profile_id}")
    payload = _row_to_payload(row)
    if expected_ontology_hash:
        payload["artifact_ontology_mismatch"] = _assess_rule_profile_match(
            payload, active_ontology_hash=expected_ontology_hash
        )
    return payload


def list_rule_profiles(
    workspace_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> List[Dict[str, Any]]:
    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        rows = conn.execute(
            """
            SELECT profile_id, workspace_id, profile_version, name, created_at,
                   schema_version, rule_count, ontology_identity_hash
            FROM rule_profiles
            WHERE workspace_id = ?
            ORDER BY profile_version DESC, created_at DESC
            """,
            (workspace_id,),
        ).fetchall()

    return [
        {
            "profile_id": row["profile_id"],
            "workspace_id": row["workspace_id"],
            "profile_version": row["profile_version"],
            "name": row["name"],
            "created_at": row["created_at"],
            "schema_version": row["schema_version"],
            "rule_count": row["rule_count"],
            "ontology_identity_hash": row["ontology_identity_hash"],
        }
        for row in rows
    ]


def _assess_rule_profile_match(
    payload: Dict[str, Any],
    *,
    active_ontology_hash: str,
) -> Dict[str, Any]:
    """Compare a rule profile's stored ontology_identity_hash to the active hash."""

    stored = str(payload.get("ontology_identity_hash", "") or "").strip()
    active = str(active_ontology_hash or "").strip()
    if not stored:
        return {
            "stored_ontology_hash": "",
            "active_ontology_hash": active,
            "mismatch": False,
            "status": "unknown",
            "warning": (
                "Rule profile has no ontology_identity_hash stamped. "
                "Re-save under Phase 5 to gain hash parity guarantees."
            ),
        }
    if stored == active:
        return {
            "stored_ontology_hash": stored,
            "active_ontology_hash": active,
            "mismatch": False,
            "status": "match",
            "warning": "",
        }
    return {
        "stored_ontology_hash": stored,
        "active_ontology_hash": active,
        "mismatch": True,
        "status": "drift",
        "warning": (
            "Rule profile ontology_identity_hash differs from active runtime hash. "
            "Refuse application or re-derive the profile from the active ontology."
        ),
    }


def _connect(base_dir: str) -> sqlite3.Connection:
    db_path = _resolve_db_path(base_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_profiles (
          profile_id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL,
          profile_version INTEGER NOT NULL,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          rule_count INTEGER NOT NULL,
          rule_profile_json TEXT NOT NULL,
          ontology_identity_hash TEXT NOT NULL DEFAULT ''
        )
        """
    )
    # Phase 5: ALTER existing tables to add the column. NOT NULL with a
    # DEFAULT is sqlite-safe for ADD COLUMN; the default backfills rows
    # written before Phase 5 with the empty-string sentinel.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rule_profiles)")}
    if "ontology_identity_hash" not in columns:
        conn.execute(
            "ALTER TABLE rule_profiles "
            "ADD COLUMN ontology_identity_hash TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rule_profiles_workspace_version
        ON rule_profiles(workspace_id, profile_version DESC)
        """
    )
    return conn


def _resolve_db_path(base_dir: str) -> Path:
    base_path = Path(base_dir)
    if base_path.suffix in {".db", ".sqlite", ".sqlite3"}:
        return base_path
    return base_path / "rule_profiles.db"


def _legacy_workspace_dir(base_dir: str, workspace_id: str) -> Path | None:
    base_path = Path(base_dir)
    if base_path.suffix in {".db", ".sqlite", ".sqlite3"}:
        return None
    return base_path / workspace_id


def _maybe_import_legacy_workspace(conn: sqlite3.Connection, base_dir: str, workspace_id: str) -> None:
    existing = conn.execute(
        "SELECT COUNT(1) AS count FROM rule_profiles WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    if existing and int(existing["count"]) > 0:
        return

    legacy_dir = _legacy_workspace_dir(base_dir, workspace_id)
    if legacy_dir is None or not legacy_dir.exists():
        return

    payloads: List[Dict[str, Any]] = []
    for path in sorted(legacy_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("workspace_id") != workspace_id:
            continue
        payloads.append(payload)

    payloads.sort(key=lambda item: str(item.get("created_at", "")))
    version = 1
    for payload in payloads:
        profile_id = str(payload.get("profile_id") or _new_profile_id())
        rule_profile = payload.get("rule_profile") or {"schema_version": "rules.v1", "rules": []}
        conn.execute(
            """
            INSERT OR IGNORE INTO rule_profiles (
              profile_id, workspace_id, profile_version, name, created_at,
              schema_version, rule_count, rule_profile_json, ontology_identity_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                workspace_id,
                version,
                str(payload.get("name") or profile_id),
                str(payload.get("created_at") or _now_iso()),
                str(payload.get("schema_version") or "rules.v1"),
                int(payload.get("rule_count") or len(rule_profile.get("rules", []))),
                json.dumps(rule_profile, ensure_ascii=True),
                str(
                    payload.get("ontology_identity_hash")
                    or rule_profile.get("ontology_identity_hash")
                    or ""
                ),
            ),
        )
        version += 1


def _next_profile_version(conn: sqlite3.Connection, workspace_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(profile_version), 0) AS current FROM rule_profiles WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    current = int(row["current"]) if row else 0
    return current + 1


def _apply_retention(conn: sqlite3.Connection, workspace_id: str) -> None:
    limit = _retention_limit()
    if limit <= 0:
        return
    conn.execute(
        """
        DELETE FROM rule_profiles
        WHERE workspace_id = ?
          AND profile_id IN (
            SELECT profile_id FROM rule_profiles
            WHERE workspace_id = ?
            ORDER BY profile_version DESC
            LIMIT -1 OFFSET ?
          )
        """,
        (workspace_id, workspace_id, limit),
    )


def _retention_limit() -> int:
    raw = str(os.getenv("RULE_PROFILE_RETENTION_MAX", str(DEFAULT_RETENTION_MAX))).strip()
    if not raw:
        return DEFAULT_RETENTION_MAX
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_MAX
    return max(value, 0)


def _row_to_payload(row: sqlite3.Row) -> Dict[str, Any]:
    rule_profile = json.loads(row["rule_profile_json"])
    # Read with sqlite3.Row .keys() since older rows may not have the column
    # in the result set when the row was inserted before Phase 5's migration.
    row_keys = set(row.keys())
    stored_hash = (
        str(row["ontology_identity_hash"] or "").strip()
        if "ontology_identity_hash" in row_keys
        else ""
    )
    return {
        "profile_id": row["profile_id"],
        "workspace_id": row["workspace_id"],
        "profile_version": row["profile_version"],
        "name": row["name"],
        "created_at": row["created_at"],
        "schema_version": row["schema_version"],
        "rule_count": row["rule_count"],
        "ontology_identity_hash": stored_hash,
        "rule_profile": rule_profile,
    }


def _new_profile_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"rp_{ts}_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
