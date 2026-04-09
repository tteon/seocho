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
) -> Dict[str, Any]:
    profile_id = _new_profile_id()
    created_at = _now_iso()
    schema_version = rule_profile.get("schema_version", "rules.v1")
    rule_count = len(rule_profile.get("rules", []))

    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        profile_version = _next_profile_version(conn, workspace_id)
        conn.execute(
            """
            INSERT INTO rule_profiles (
              profile_id, workspace_id, profile_version, name, created_at,
              schema_version, rule_count, rule_profile_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        "rule_profile": rule_profile,
    }


def get_rule_profile(
    workspace_id: str,
    profile_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> Dict[str, Any]:
    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        row = conn.execute(
            """
            SELECT profile_id, workspace_id, profile_version, name, created_at,
                   schema_version, rule_count, rule_profile_json
            FROM rule_profiles
            WHERE workspace_id = ? AND profile_id = ?
            """,
            (workspace_id, profile_id),
        ).fetchone()

    if row is None:
        raise FileNotFoundError(f"rule profile not found: workspace={workspace_id}, profile_id={profile_id}")
    return _row_to_payload(row)


def list_rule_profiles(
    workspace_id: str,
    base_dir: str = DEFAULT_RULE_PROFILE_DIR,
) -> List[Dict[str, Any]]:
    with _connect(base_dir) as conn:
        _maybe_import_legacy_workspace(conn, base_dir, workspace_id)
        rows = conn.execute(
            """
            SELECT profile_id, workspace_id, profile_version, name, created_at,
                   schema_version, rule_count
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
        }
        for row in rows
    ]


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
          rule_profile_json TEXT NOT NULL
        )
        """
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
              schema_version, rule_count, rule_profile_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    return {
        "profile_id": row["profile_id"],
        "workspace_id": row["workspace_id"],
        "profile_version": row["profile_version"],
        "name": row["name"],
        "created_at": row["created_at"],
        "schema_version": row["schema_version"],
        "rule_count": row["rule_count"],
        "rule_profile": rule_profile,
    }


def _new_profile_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"rp_{ts}_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
