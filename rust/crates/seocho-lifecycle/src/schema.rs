//! The control-DB schema: the single DDL source (design §4.4).
//!
//! Versions:
//! - **v0**: Python `main` (pre-#783 src/seocho/ontology/lifecycle.py:171-183,
//!   pre-#783 src/seocho/ontology/active_pointer.py:69-81). No high-water table.
//! - **v1**: #783 (src/seocho/ontology/lifecycle.py:223-226 on that branch) adds
//!   `ontology_fence_high_water(workspace_id, package_id, high_water)`.
//! - **v2**: this crate. `PRAGMA user_version = 2`, `lifecycle_meta`,
//!   `minted_for` on the high-water, unique lease indexes, the lease
//!   tombstone table and triggers T1-T7 + M1.
//!
//! The legacy tables keep their exact column lists and order so that legacy
//! Python statements (`INSERT INTO ontology_lease VALUES (?,?,...)`,
//! `SELECT * FROM ontology_lease`) still parse against v2 and are stopped by
//! a *trigger*, with a clear message, rather than by an accidental column
//! count error. seochod's read-only verifier (crates/seochod/src/main.rs:
//! 211-280) reads only columns whose meaning v2 preserves.

use crate::error::LifecycleError;
use rusqlite::{Connection, OptionalExtension};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::OnceLock;

/// The schema version this crate owns.
pub const SCHEMA_VERSION: i64 = 2;

/// Value of `lifecycle_meta.writer`.
pub const WRITER: &str = "seocho-lifecycled";

/// Tables owned by the lifecycle authority. Every `sqlite_master` object whose
/// `tbl_name` is one of these is covered by [`ddl_sha256`], so an added or
/// dropped trigger/index on an owned table changes the digest.
pub const OWNED_TABLES: [&str; 7] = [
    "lifecycle_meta",
    "active_ontology",
    "generation_hwm",
    "ontology_lease",
    "ontology_fence_high_water",
    "ontology_lease_tombstone",
    "projection_idempotency",
];

/// Lease columns in legacy order (pre-#783 src/seocho/ontology/lifecycle.py:172-176).
pub(crate) const LEASE_COLUMNS: &str = "lease_id, workspace_id, package_id, purpose, owner, \
     fingerprint, generation, epoch, fencing_token, acquired_at_ms, expires_at_ms";

/// Schema v2 DDL. Tables and indexes first, then triggers T1-T7 + M1.
///
/// Trigger conditions are written as `(<allowed>) IS NOT 1` so that a NULL
/// anywhere in the condition (the legacy tables have nullable columns) makes
/// the statement abort instead of silently passing.
pub const DDL_V2: &str = r#"
CREATE TABLE lifecycle_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE active_ontology (
    workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT,
    generation INTEGER, epoch INTEGER, fencing_token INTEGER,
    PRIMARY KEY (workspace_id, package_id)
);

CREATE TABLE generation_hwm (
    workspace_id TEXT, package_id TEXT, hwm INTEGER,
    PRIMARY KEY (workspace_id, package_id)
);

CREATE TABLE ontology_lease (
    lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL,
    purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL,
    acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL
);
CREATE INDEX ontology_lease_live ON ontology_lease(workspace_id, package_id, expires_at_ms);
CREATE UNIQUE INDEX ontology_lease_scope_purpose ON ontology_lease(workspace_id, package_id, purpose);
CREATE UNIQUE INDEX ontology_lease_scope_token ON ontology_lease(workspace_id, package_id, fencing_token);

CREATE TABLE ontology_fence_high_water (
    workspace_id TEXT NOT NULL, package_id TEXT NOT NULL,
    high_water INTEGER NOT NULL CHECK (high_water >= 0),
    minted_for TEXT,
    PRIMARY KEY (workspace_id, package_id)
);

CREATE TABLE ontology_lease_tombstone (
    lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL,
    purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL,
    acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER NOT NULL,
    end_reason TEXT NOT NULL CHECK (end_reason IN ('released', 'expired'))
);
CREATE INDEX ontology_lease_tombstone_scope_token
    ON ontology_lease_tombstone(workspace_id, package_id, fencing_token);

CREATE TABLE projection_idempotency (
    idempotency_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    state TEXT NOT NULL, response_json TEXT, created_at_ms INTEGER NOT NULL, completed_at_ms INTEGER
);

-- Why every *_insert_once trigger exists: `INSERT OR REPLACE` resolves a
-- primary-key conflict by deleting the old row WITHOUT firing DELETE triggers
-- (recursive_triggers is OFF by default) and then inserts the new one, so a
-- BEFORE UPDATE trigger never sees it. Every guarded table therefore also
-- refuses an INSERT whose key already exists. BEFORE INSERT runs before
-- conflict resolution, which covers REPLACE and UPSERT alike; the authority
-- writes these tables with UPDATE-then-INSERT.

-- M1: lifecycle_meta keys are written once; only last_wall_ms may change, and
-- only to a larger-or-equal decimal value (I9 clamp, I13 recorded digest).
CREATE TRIGGER lifecycle_m1_meta_insert_once
BEFORE INSERT ON lifecycle_meta
WHEN EXISTS (SELECT 1 FROM lifecycle_meta m WHERE m.key = NEW.key)
BEGIN
    SELECT RAISE(ABORT, 'lifecycle M1: lifecycle_meta keys are written once');
END;
CREATE TRIGGER lifecycle_m1_meta_update_clock_only
BEFORE UPDATE ON lifecycle_meta
WHEN (NEW.key IS OLD.key
      AND OLD.key = 'last_wall_ms'
      AND NEW.value <> ''
      AND NEW.value NOT GLOB '*[^0-9]*'
      AND CAST(NEW.value AS INTEGER) >= CAST(OLD.value AS INTEGER)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle M1: only last_wall_ms may change, and only forward');
END;
CREATE TRIGGER lifecycle_m1_meta_no_delete
BEFORE DELETE ON lifecycle_meta
BEGIN
    SELECT RAISE(ABORT, 'lifecycle M1: lifecycle_meta rows are never deleted');
END;

-- T1: the fence high-water only ever increases, and its scope is immutable.
-- The one same-value UPDATE allowed is consuming the mint label
-- (minted_for -> 'consumed:' || minted_for), which the T3/T5/T6 consume
-- triggers do once the row the token was minted for has been written.
CREATE TRIGGER lifecycle_t1_high_water_increase_only
BEFORE UPDATE ON ontology_fence_high_water
WHEN ((NEW.high_water > OLD.high_water
       OR (NEW.high_water = OLD.high_water
           AND OLD.minted_for NOT LIKE 'consumed:%'
           AND NEW.minted_for = 'consumed:' || OLD.minted_for))
      AND NEW.workspace_id IS OLD.workspace_id
      AND NEW.package_id IS OLD.package_id) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T1: fence high-water may only increase');
END;
-- T1 (insert): a scope's high-water row is created once (no REPLACE/UPSERT
-- over an existing row), and above every token the scope already carries.
CREATE TRIGGER lifecycle_t1_high_water_insert_once
BEFORE INSERT ON ontology_fence_high_water
WHEN (NOT EXISTS (SELECT 1 FROM ontology_fence_high_water h
                  WHERE h.workspace_id = NEW.workspace_id AND h.package_id = NEW.package_id)
      AND NEW.high_water > COALESCE((SELECT MAX(l.fencing_token) FROM ontology_lease l
                                     WHERE l.workspace_id = NEW.workspace_id
                                       AND l.package_id = NEW.package_id), 0)
      AND NEW.high_water > COALESCE((SELECT MAX(t.fencing_token) FROM ontology_lease_tombstone t
                                     WHERE t.workspace_id = NEW.workspace_id
                                       AND t.package_id = NEW.package_id), 0)
      AND NEW.high_water > COALESCE((SELECT MAX(p.fencing_token) FROM active_ontology p
                                     WHERE p.workspace_id = NEW.workspace_id
                                       AND p.package_id = NEW.package_id), 0)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T1: fence high-water row already exists or would not dominate the scope (no REPLACE/UPSERT)');
END;

-- T2: the fence high-water is never deleted.
CREATE TRIGGER lifecycle_t2_high_water_no_delete
BEFORE DELETE ON ontology_fence_high_water
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T2: fence high-water rows are never deleted');
END;

-- T3: a lease row may only carry the token the authority just minted for
-- exactly this lease id (label not yet consumed), and a tombstoned lease can
-- never come back.
CREATE TRIGGER lifecycle_t3_lease_insert_minted
BEFORE INSERT ON ontology_lease
WHEN (NEW.lease_id NOT LIKE 'activate:%'
      AND NEW.lease_id NOT LIKE 'consumed:%'
      AND NOT EXISTS (SELECT 1 FROM ontology_lease_tombstone t WHERE t.lease_id = NEW.lease_id)
      AND EXISTS (SELECT 1 FROM ontology_fence_high_water h
                  WHERE h.workspace_id = NEW.workspace_id
                    AND h.package_id = NEW.package_id
                    AND h.high_water = NEW.fencing_token
                    AND h.minted_for = NEW.lease_id)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T3: lease fencing_token was not minted for this lease by seocho-lifecycle');
END;
-- T3 (consume): a mint label authorises exactly one lease row.
CREATE TRIGGER lifecycle_t3_lease_consume_mint
AFTER INSERT ON ontology_lease
BEGIN
    UPDATE ontology_fence_high_water SET minted_for = 'consumed:' || minted_for
    WHERE workspace_id = NEW.workspace_id AND package_id = NEW.package_id
      AND high_water = NEW.fencing_token AND minted_for = NEW.lease_id;
END;

-- T4: a lease row is immutable except for expires_at_ms (renew).
CREATE TRIGGER lifecycle_t4_lease_immutable
BEFORE UPDATE ON ontology_lease
WHEN (NEW.lease_id IS OLD.lease_id
      AND NEW.workspace_id IS OLD.workspace_id
      AND NEW.package_id IS OLD.package_id
      AND NEW.purpose IS OLD.purpose
      AND NEW.owner IS OLD.owner
      AND NEW.fingerprint IS OLD.fingerprint
      AND NEW.generation IS OLD.generation
      AND NEW.epoch IS OLD.epoch
      AND NEW.fencing_token IS OLD.fencing_token
      AND NEW.acquired_at_ms IS OLD.acquired_at_ms) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T4: lease rows are immutable except expires_at_ms');
END;
-- T4 (delete): a lease row leaves only after its tombstone was written
-- (release / expiry move, design 4.3).
CREATE TRIGGER lifecycle_t4_lease_delete_tombstoned
BEFORE DELETE ON ontology_lease
WHEN (EXISTS (SELECT 1 FROM ontology_lease_tombstone t
              WHERE t.lease_id = OLD.lease_id
                AND t.workspace_id = OLD.workspace_id
                AND t.package_id = OLD.package_id
                AND t.fencing_token = OLD.fencing_token)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T4: lease rows leave only through ontology_lease_tombstone');
END;
-- T4 (tombstone delete): pruning a tombstone (P2) is allowed only while the
-- durable high-water still dominates its token.
CREATE TRIGGER lifecycle_t4_tombstone_delete_dominated
BEFORE DELETE ON ontology_lease_tombstone
WHEN (EXISTS (SELECT 1 FROM ontology_fence_high_water h
              WHERE h.workspace_id = OLD.workspace_id
                AND h.package_id = OLD.package_id
                AND h.high_water >= OLD.fencing_token)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T4: tombstone token is not dominated by the fence high-water');
END;

-- T5: a pointer swap bumps epoch by one inside the same generation and
-- carries the token the authority minted for exactly that (generation, epoch).
CREATE TRIGGER lifecycle_t5_pointer_update_minted
BEFORE UPDATE ON active_ontology
WHEN (NEW.workspace_id IS OLD.workspace_id
      AND NEW.package_id IS OLD.package_id
      AND NEW.generation IS OLD.generation
      AND NEW.epoch = OLD.epoch + 1
      AND NEW.fencing_token > OLD.fencing_token
      AND EXISTS (SELECT 1 FROM ontology_fence_high_water h
                  WHERE h.workspace_id = NEW.workspace_id
                    AND h.package_id = NEW.package_id
                    AND h.high_water = NEW.fencing_token
                    AND h.minted_for = 'activate:' || NEW.generation || ':' || NEW.epoch)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T5: active pointer update needs an authority-minted activation token');
END;
-- T5 (consume): an activation label authorises exactly one pointer write.
CREATE TRIGGER lifecycle_t5_pointer_update_consume_mint
AFTER UPDATE ON active_ontology
BEGIN
    UPDATE ontology_fence_high_water SET minted_for = 'consumed:' || minted_for
    WHERE workspace_id = NEW.workspace_id AND package_id = NEW.package_id
      AND high_water = NEW.fencing_token
      AND minted_for = 'activate:' || NEW.generation || ':' || NEW.epoch;
END;

-- T6: a new pointer row starts at epoch 0 of the current generation
-- high-water and carries the unconsumed token minted for that (generation, 0).
CREATE TRIGGER lifecycle_t6_pointer_insert_minted
BEFORE INSERT ON active_ontology
WHEN (NEW.epoch = 0
      AND NEW.generation = (SELECT g.hwm FROM generation_hwm g
                            WHERE g.workspace_id = NEW.workspace_id AND g.package_id = NEW.package_id)
      AND EXISTS (SELECT 1 FROM ontology_fence_high_water h
                  WHERE h.workspace_id = NEW.workspace_id
                    AND h.package_id = NEW.package_id
                    AND h.high_water = NEW.fencing_token
                    AND h.minted_for = 'activate:' || NEW.generation || ':' || NEW.epoch)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T6: active pointer insert needs an authority-minted activation token');
END;
-- T6 (consume): see T5 (consume).
CREATE TRIGGER lifecycle_t6_pointer_insert_consume_mint
AFTER INSERT ON active_ontology
BEGIN
    UPDATE ontology_fence_high_water SET minted_for = 'consumed:' || minted_for
    WHERE workspace_id = NEW.workspace_id AND package_id = NEW.package_id
      AND high_water = NEW.fencing_token
      AND minted_for = 'activate:' || NEW.generation || ':' || NEW.epoch;
END;

-- T7: the generation high-water never decreases and is never deleted.
CREATE TRIGGER lifecycle_t7_generation_hwm_no_decrease
BEFORE UPDATE ON generation_hwm
WHEN (NEW.hwm >= OLD.hwm
      AND NEW.workspace_id IS OLD.workspace_id
      AND NEW.package_id IS OLD.package_id) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T7: generation high-water may not decrease');
END;
-- T7 (insert): created once per scope (no REPLACE/UPSERT over an existing
-- row), non-negative and not below the scope's pointer generation.
CREATE TRIGGER lifecycle_t7_generation_hwm_insert_once
BEFORE INSERT ON generation_hwm
WHEN (NOT EXISTS (SELECT 1 FROM generation_hwm g
                  WHERE g.workspace_id = NEW.workspace_id AND g.package_id = NEW.package_id)
      AND NEW.hwm >= 0
      AND NEW.hwm >= COALESCE((SELECT MAX(p.generation) FROM active_ontology p
                               WHERE p.workspace_id = NEW.workspace_id
                                 AND p.package_id = NEW.package_id), 0)) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T7: generation high-water row already exists or is below the pointer (no REPLACE/UPSERT)');
END;
CREATE TRIGGER lifecycle_t7_generation_hwm_no_delete
BEFORE DELETE ON generation_hwm
BEGIN
    SELECT RAISE(ABORT, 'lifecycle T7: generation high-water rows are never deleted');
END;
"#;

/// Which schema a database carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SchemaVersion {
    /// No lifecycle table at all.
    Empty,
    /// Python main: lease/pointer tables, no high-water.
    V0,
    /// v1 (#783): `ontology_fence_high_water` present, no `lifecycle_meta`.
    V1,
    /// This crate: `lifecycle_meta` present and `user_version = 2`.
    V2,
}

impl SchemaVersion {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Empty => "empty",
            Self::V0 => "v0",
            Self::V1 => "v1",
            Self::V2 => "v2",
        }
    }
}

pub(crate) fn table_exists(conn: &Connection, name: &str) -> Result<bool, rusqlite::Error> {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
        [name],
        |_| Ok(()),
    )
    .optional()
    .map(|r| r.is_some())
}

/// Classify the schema of an open database (read-only safe).
pub fn detect(conn: &Connection) -> Result<SchemaVersion, LifecycleError> {
    let user_version: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    if table_exists(conn, "lifecycle_meta")? {
        if user_version == SCHEMA_VERSION {
            return Ok(SchemaVersion::V2);
        }
        return Err(LifecycleError::SchemaMismatch(format!(
            "lifecycle_meta present but user_version={user_version}"
        )));
    }
    if user_version != 0 {
        return Err(LifecycleError::SchemaMismatch(format!(
            "unknown user_version={user_version} without lifecycle_meta"
        )));
    }
    if table_exists(conn, "ontology_fence_high_water")? {
        return Ok(SchemaVersion::V1);
    }
    let legacy = ["ontology_lease", "active_ontology", "generation_hwm", "projection_idempotency"];
    for name in legacy {
        if table_exists(conn, name)? {
            return Ok(SchemaVersion::V0);
        }
    }
    Ok(SchemaVersion::Empty)
}

fn normalize_sql(sql: &str) -> String {
    sql.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// SHA-256 over the normalized `sqlite_master.sql` of every object attached
/// to an [`OWNED_TABLES`] table, ordered by `(type, name)`. Auto-indexes
/// (NULL `sql`) are implied by their table's DDL and skipped.
pub fn ddl_sha256(conn: &Connection) -> Result<String, LifecycleError> {
    let placeholders = vec!["?"; OWNED_TABLES.len()].join(",");
    let sql = format!(
        "SELECT type, name, tbl_name, sql FROM sqlite_master \
         WHERE tbl_name IN ({placeholders}) AND sql IS NOT NULL ORDER BY type, name"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params_from_iter(OWNED_TABLES.iter()), |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, String>(3)?,
        ))
    })?;
    let mut hasher = Sha256::new();
    for row in rows {
        let (kind, name, table, body) = row?;
        for part in [kind.as_str(), name.as_str(), table.as_str()] {
            hasher.update(part.as_bytes());
            hasher.update([0u8]);
        }
        hasher.update(normalize_sql(&body).as_bytes());
        hasher.update([b'\n']);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// The digest of the compiled-in [`DDL_V2`], computed once on an in-memory
/// database with the same SQLite build.
pub fn expected_ddl_sha256() -> Result<String, LifecycleError> {
    static EXPECTED: OnceLock<String> = OnceLock::new();
    if let Some(v) = EXPECTED.get() {
        return Ok(v.clone());
    }
    let conn = Connection::open_in_memory()?;
    conn.execute_batch(DDL_V2)?;
    let digest = ddl_sha256(&conn)?;
    Ok(EXPECTED.get_or_init(|| digest).clone())
}

pub(crate) fn new_uuid_v4() -> Result<String, LifecycleError> {
    let mut b = [0u8; 16];
    getrandom::getrandom(&mut b)
        .map_err(|e| LifecycleError::Internal(format!("getrandom failed: {e}")))?;
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = hex::encode(b);
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    ))
}

pub(crate) fn configure_rw(conn: &Connection) -> Result<(), LifecycleError> {
    conn.busy_timeout(std::time::Duration::from_millis(10_000))?;
    let mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
    if !mode.eq_ignore_ascii_case("wal") && !mode.eq_ignore_ascii_case("memory") {
        return Err(LifecycleError::Internal(format!(
            "could not enable WAL (journal_mode={mode})"
        )));
    }
    // Explicit, never the build default (design §3 judge correction).
    conn.execute_batch("PRAGMA synchronous=FULL;")?;
    Ok(())
}

/// Create schema v2 on an empty database, or verify an existing v2 one.
///
/// A v0/v1 database is refused: upgrading one is `migrate` (P2), which needs
/// a DozerDB floor and a backup first.
pub fn apply_v2(conn: &mut Connection) -> Result<(), LifecycleError> {
    let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    match detect(&tx)? {
        SchemaVersion::V2 => {
            drop(tx);
            return verify_v2(conn);
        }
        SchemaVersion::V0 | SchemaVersion::V1 => {
            return Err(LifecycleError::SchemaMismatch(
                "database carries a legacy (v0/v1) lifecycle schema; upgrade it with `migrate`".into(),
            ));
        }
        SchemaVersion::Empty => {}
    }
    for name in OWNED_TABLES {
        if table_exists(&tx, name)? {
            return Err(LifecycleError::SchemaMismatch(format!(
                "unexpected pre-existing table {name}"
            )));
        }
    }
    tx.execute_batch(DDL_V2)?;
    let digest = ddl_sha256(&tx)?;
    let expected = expected_ddl_sha256()?;
    if digest != expected {
        return Err(LifecycleError::SchemaTampered {
            expected,
            found: digest,
        });
    }
    let db_uuid = new_uuid_v4()?;
    let meta = [
        ("schema_version", SCHEMA_VERSION.to_string()),
        ("db_uuid", db_uuid),
        ("writer", WRITER.to_string()),
        ("ddl_sha256", digest),
        ("migrated_from", "none".to_string()),
        ("last_wall_ms", "0".to_string()),
    ];
    for (k, v) in &meta {
        tx.execute(
            "INSERT INTO lifecycle_meta (key, value) VALUES (?1, ?2)",
            (k, v),
        )?;
    }
    tx.execute_batch(&format!("PRAGMA user_version = {SCHEMA_VERSION};"))?;
    tx.commit()?;
    Ok(())
}

pub(crate) fn meta_value(conn: &Connection, key: &str) -> Result<Option<String>, LifecycleError> {
    Ok(conn
        .query_row("SELECT value FROM lifecycle_meta WHERE key=?1", [key], |r| {
            r.get(0)
        })
        .optional()?)
}

/// Verify that `conn` is exactly schema v2 with untampered DDL (design I13).
pub fn verify_v2(conn: &Connection) -> Result<(), LifecycleError> {
    let version = detect(conn)?;
    if version != SchemaVersion::V2 {
        return Err(LifecycleError::SchemaMismatch(format!(
            "expected schema v2, found {}",
            version.as_str()
        )));
    }
    let declared = meta_value(conn, "schema_version")?;
    if declared.as_deref() != Some("2") {
        return Err(LifecycleError::SchemaMismatch(format!(
            "lifecycle_meta.schema_version={declared:?}"
        )));
    }
    let expected = expected_ddl_sha256()?;
    let found = ddl_sha256(conn)?;
    if found != expected {
        return Err(LifecycleError::SchemaTampered { expected, found });
    }
    let recorded = meta_value(conn, "ddl_sha256")?.unwrap_or_default();
    if recorded != expected {
        return Err(LifecycleError::SchemaTampered {
            expected,
            found: format!("lifecycle_meta.ddl_sha256={recorded}"),
        });
    }
    last_wall_ms(conn)?;
    Ok(())
}

/// `lifecycle_meta.last_wall_ms`, the persisted clock floor (I9). A missing or
/// non-decimal value is tampering, never a silent 0.
pub(crate) fn last_wall_ms(conn: &Connection) -> Result<u64, LifecycleError> {
    let raw = meta_value(conn, "last_wall_ms")?;
    raw.as_deref()
        .filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|v| v.parse::<u64>().ok())
        .ok_or_else(|| LifecycleError::SchemaTampered {
            expected: "lifecycle_meta.last_wall_ms = <decimal ms>".into(),
            found: format!("lifecycle_meta.last_wall_ms={raw:?}"),
        })
}

/// Create a fresh schema v2 database at `path` for tests and fixtures
/// (replaces hand-copied DDL in other crates' tests, design P1).
pub fn create_for_test(path: &Path) -> Result<Connection, LifecycleError> {
    let mut conn = Connection::open(path)?;
    configure_rw(&conn)?;
    apply_v2(&mut conn)?;
    Ok(conn)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ddl_digest_is_stable_and_detects_dropped_trigger() -> Result<(), LifecycleError> {
        let mut conn = Connection::open_in_memory()?;
        apply_v2(&mut conn)?;
        verify_v2(&conn)?;
        assert_eq!(ddl_sha256(&conn)?, expected_ddl_sha256()?);
        conn.execute_batch("DROP TRIGGER lifecycle_t3_lease_insert_minted;")?;
        assert_ne!(ddl_sha256(&conn)?, expected_ddl_sha256()?);
        assert!(matches!(
            verify_v2(&conn),
            Err(LifecycleError::SchemaTampered { .. })
        ));
        Ok(())
    }

    #[test]
    fn uuid_v4_shape() -> Result<(), LifecycleError> {
        let id = new_uuid_v4()?;
        assert_eq!(id.len(), 36);
        assert_eq!(&id[14..15], "4");
        Ok(())
    }
}
