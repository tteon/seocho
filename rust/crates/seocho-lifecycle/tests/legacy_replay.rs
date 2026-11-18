//! Legacy-writer replay (design §9 tests 2 and 3, invariants I1/I4, mixed old/new minter hazard).
//!
//! The SQL below is copied verbatim from the Python lifecycle stores and run
//! with the same control flow (BEGIN IMMEDIATE ... COMMIT / ROLLBACK) through
//! raw rusqlite against a v2 database. Every token-minting legacy path must be
//! aborted by a lifecycle trigger and leave the database unchanged.
//!
//! Sources (this repository's `src/seocho/ontology/`):
//! - v0: the Python stores before #783 (`pre-#783` below).
//! - v1: the #783 branch `fix/monotonic-lease-fencing-token` at 2abe7f4e
//!   (`#783` below). Line numbers refer to those revisions, not to `main`.

mod common;

use common::{temp_dir, TestResult, FP, FP2};
use rusqlite::{Connection, OptionalExtension};
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, LifecycleBackend, ManualClock, Millis, Scope, SqliteBackend,
};
use std::path::Path;

// --- pre-#783 src/seocho/ontology/active_pointer.py ---------------------------
/// active_pointer.py:85-88 (`read`)
const PTR_READ: &str = "SELECT version, fingerprint, generation, epoch, fencing_token FROM active_ontology WHERE workspace_id=? AND package_id=?";
/// active_pointer.py:95-96 (`_next_generation`, read)
const GEN_HWM_READ: &str = "SELECT hwm FROM generation_hwm WHERE workspace_id=? AND package_id=?";
/// active_pointer.py:98-99 (`_next_generation`, upsert)
const GEN_HWM_UPSERT: &str = "INSERT INTO generation_hwm (workspace_id, package_id, hwm) VALUES (?,?,?) ON CONFLICT(workspace_id, package_id) DO UPDATE SET hwm=excluded.hwm";
/// active_pointer.py:139-142 (`publish` first insert) and :181-184 (`recreate`)
const PTR_INSERT: &str = "INSERT INTO active_ontology (workspace_id, package_id, version, fingerprint, generation, epoch, fencing_token) VALUES (?,?,?,?,?,?,?)";
/// active_pointer.py:158-159 (`publish` swap)
const PTR_UPDATE: &str = "UPDATE active_ontology SET version=?, fingerprint=?, epoch=?, fencing_token=? WHERE workspace_id=? AND package_id=? AND generation=? AND epoch=?";
/// active_pointer.py:177 (`recreate`)
const PTR_DELETE: &str = "DELETE FROM active_ontology WHERE workspace_id=? AND package_id=?";

// --- pre-#783 src/seocho/ontology/lifecycle.py (main) --------------------------
/// lifecycle.py:218 (`acquire`, live lease check)
const MAIN_EXISTING: &str = "SELECT lease_id, owner, expires_at_ms FROM ontology_lease WHERE workspace_id=? AND package_id=? AND purpose=? AND expires_at_ms>? ORDER BY expires_at_ms DESC LIMIT 1";
/// lifecycle.py:223 (`acquire`, token from surviving lease rows)
const MAIN_MAX_TOKEN: &str = "SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease WHERE workspace_id=? AND package_id=?";
/// lifecycle.py:225 (`acquire`, insert)
const MAIN_INSERT: &str = "INSERT INTO ontology_lease VALUES (?,?,?,?,?,?,?,?,?,?,?)";
/// lifecycle.py:244 (`release`)
const MAIN_RELEASE: &str = "DELETE FROM ontology_lease WHERE lease_id=? AND owner=?";
/// lifecycle.py:238 (`renew`)
const MAIN_RENEW: &str = "UPDATE ontology_lease SET expires_at_ms=? WHERE lease_id=?";
/// lifecycle.py:172-178 and active_pointer.py:70-81: the v0 DDL.
const MAIN_DDL_V0: &str = "CREATE TABLE IF NOT EXISTS active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY (workspace_id, package_id));
CREATE TABLE IF NOT EXISTS generation_hwm (workspace_id TEXT, package_id TEXT, hwm INTEGER, PRIMARY KEY (workspace_id, package_id));
CREATE TABLE IF NOT EXISTS ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL, acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ontology_lease_live ON ontology_lease(workspace_id, package_id, expires_at_ms);";

// --- #783 src/seocho/ontology/lifecycle.py (v1) ----------------------------
/// #783 lifecycle.py:353 (`acquire`, pointer read inside the write txn)
const V1_PTR_READ: &str = "SELECT version, fingerprint, generation, epoch, fencing_token FROM active_ontology WHERE workspace_id=? AND package_id=?";
/// #783 lifecycle.py:358 (`acquire`, live lease check)
const V1_EXISTING: &str = "SELECT lease_id, owner, expires_at_ms FROM ontology_lease WHERE workspace_id=? AND package_id=? AND purpose=? AND expires_at_ms>? ORDER BY expires_at_ms DESC LIMIT 1";
/// #783 lifecycle.py:302 (`_next_fencing_token`, high-water)
const V1_HW_READ: &str = "SELECT high_water FROM ontology_fence_high_water WHERE workspace_id=? AND package_id=?";
/// #783 lifecycle.py:306 (`_next_fencing_token`, live max)
const V1_LIVE_MAX: &str = "SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease WHERE workspace_id=? AND package_id=?";
/// #783 lifecycle.py:311-312 (`_next_fencing_token`, high-water upsert)
const V1_HW_UPSERT: &str = "INSERT INTO ontology_fence_high_water (workspace_id, package_id, high_water) VALUES (?,?,?) ON CONFLICT(workspace_id, package_id) DO UPDATE SET high_water=excluded.high_water";
/// #783 lifecycle.py:365 (`acquire`, insert)
const V1_INSERT: &str = "INSERT INTO ontology_lease VALUES (?,?,?,?,?,?,?,?,?,?,?)";

const WS: &str = "ws";
const PKG: &str = "pkg";
const NOW: i64 = 1_700_000_000_000;

fn trigger_msg(e: &rusqlite::Error) -> Option<String> {
    match e {
        rusqlite::Error::SqliteFailure(f, Some(msg))
            if f.extended_code == rusqlite::ffi::SQLITE_CONSTRAINT_TRIGGER =>
        {
            Some(msg.clone())
        }
        _ => None,
    }
}

/// Run `body` like the Python stores do: BEGIN IMMEDIATE, COMMIT on success,
/// ROLLBACK on error.
fn in_immediate<T>(
    conn: &Connection,
    body: impl FnOnce(&Connection) -> Result<T, rusqlite::Error>,
) -> Result<T, rusqlite::Error> {
    conn.execute_batch("BEGIN IMMEDIATE")?;
    match body(conn) {
        Ok(v) => {
            conn.execute_batch("COMMIT")?;
            Ok(v)
        }
        Err(e) => {
            conn.execute_batch("ROLLBACK")?;
            Err(e)
        }
    }
}

type PtrRow = (String, String, i64, i64, i64);

fn read_ptr(c: &Connection, sql: &str) -> Result<Option<PtrRow>, rusqlite::Error> {
    c.query_row(sql, (WS, PKG), |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
    })
    .optional()
}

/// pre-#783 lifecycle.py:209-230 `acquire` (main): returns the token it inserted.
fn main_acquire(conn: &Connection, lease_id: &str, purpose: &str) -> Result<i64, rusqlite::Error> {
    // main reads the pointer through its own connection (active_pointer.py:84-88).
    let (_, fp, generation, epoch, ptr_tok) =
        read_ptr(conn, PTR_READ)?.ok_or(rusqlite::Error::QueryReturnedNoRows)?;
    in_immediate(conn, |c| {
        let existing: Option<String> = c
            .query_row(MAIN_EXISTING, (WS, PKG, purpose, NOW), |r| r.get(0))
            .optional()?;
        if existing.is_some() {
            return Err(rusqlite::Error::QueryReturnedNoRows);
        }
        let max: i64 = c.query_row(MAIN_MAX_TOKEN, (WS, PKG), |r| r.get(0))?;
        let fence = (ptr_tok + 1).max(max + 1);
        c.execute(
            MAIN_INSERT,
            rusqlite::params![lease_id, WS, PKG, purpose, "legacy-main", fp, generation, epoch, fence, NOW, NOW + 60_000],
        )?;
        Ok(fence)
    })
}

/// #783 lifecycle.py:343-370 `acquire` with `_next_fencing_token` (:299-315).
fn v1_acquire(conn: &Connection, lease_id: &str, purpose: &str) -> Result<i64, rusqlite::Error> {
    in_immediate(conn, |c| {
        let (_, fp, generation, epoch, ptr_tok) =
            read_ptr(c, V1_PTR_READ)?.ok_or(rusqlite::Error::QueryReturnedNoRows)?;
        let existing: Option<String> = c
            .query_row(V1_EXISTING, (WS, PKG, purpose, NOW), |r| r.get(0))
            .optional()?;
        if existing.is_some() {
            return Err(rusqlite::Error::QueryReturnedNoRows);
        }
        let hw: Option<i64> = c.query_row(V1_HW_READ, (WS, PKG), |r| r.get(0)).optional()?;
        let live: i64 = c.query_row(V1_LIVE_MAX, (WS, PKG), |r| r.get(0))?;
        let token = hw.unwrap_or(0).max(live).max(ptr_tok) + 1;
        c.execute(V1_HW_UPSERT, (WS, PKG, token))?;
        c.execute(
            V1_INSERT,
            rusqlite::params![lease_id, WS, PKG, purpose, "legacy-v1", fp, generation, epoch, token, NOW, NOW + 60_000],
        )?;
        Ok(token)
    })
}

/// pre-#783 active_pointer.py:103-168 `publish` with a caller-chosen token.
fn legacy_publish(
    conn: &Connection,
    fingerprint: &str,
    fencing_token: i64,
    expected: Option<(i64, i64)>,
) -> Result<bool, rusqlite::Error> {
    in_immediate(conn, |c| match read_ptr(c, PTR_READ)? {
        None => {
            if expected.is_some() {
                return Ok(false);
            }
            let hwm: Option<i64> = c.query_row(GEN_HWM_READ, (WS, PKG), |r| r.get(0)).optional()?;
            let generation = hwm.map_or(0, |h| h + 1);
            c.execute(GEN_HWM_UPSERT, (WS, PKG, generation))?;
            c.execute(PTR_INSERT, rusqlite::params![WS, PKG, "legacy", fingerprint, generation, 0, fencing_token])?;
            Ok(true)
        }
        Some((_, _, g, e, tok)) => {
            if expected != Some((g, e)) || fencing_token < tok {
                return Ok(false);
            }
            c.execute(PTR_UPDATE, rusqlite::params!["legacy", fingerprint, e + 1, fencing_token, WS, PKG, g, e])?;
            Ok(true)
        }
    })
}

/// pre-#783 active_pointer.py:170-191 `recreate` with a caller-chosen token.
fn legacy_recreate(conn: &Connection, fencing_token: i64) -> Result<(), rusqlite::Error> {
    in_immediate(conn, |c| {
        c.execute(PTR_DELETE, (WS, PKG))?;
        let hwm: Option<i64> = c.query_row(GEN_HWM_READ, (WS, PKG), |r| r.get(0)).optional()?;
        let generation = hwm.map_or(0, |h| h + 1);
        c.execute(GEN_HWM_UPSERT, (WS, PKG, generation))?;
        c.execute(PTR_INSERT, rusqlite::params![WS, PKG, "legacy", FP2, generation, 0, fencing_token])?;
        Ok(())
    })
}

struct Fixture {
    _dir: common::TempDir,
    backend: SqliteBackend<ManualClock>,
    raw: Connection,
    scope: Scope,
}

/// v2 DB with an active pointer (token 1) and one released projection lease
/// (token 2), i.e. exactly the token-reuse setup fixed by #783.
fn v2_fixture(tag: &str) -> Result<Fixture, Box<dyn std::error::Error>> {
    let dir = temp_dir(tag)?;
    let db = dir.join("state.sqlite");
    let clock = ManualClock::new(Millis::new(u64::try_from(NOW)?));
    let mut backend = SqliteBackend::create(&db, clock)?;
    let scope = Scope::new(WS, PKG)?;
    let act = backend.activate(&ActivateRequest {
        scope: &scope,
        version: "1.0",
        fingerprint: FP,
        expected: None,
        min_token: None,
    })?;
    assert_eq!(act.fencing_token.get(), 1);
    let lease = backend.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "rust",
        ttl_ms: 60_000,
    })?;
    assert_eq!(lease.fencing_token.get(), 2);
    backend.release(&lease.lease_id, "rust")?;
    let raw = open_raw(&db)?;
    Ok(Fixture {
        _dir: dir,
        backend,
        raw,
        scope,
    })
}

fn open_raw(db: &Path) -> Result<Connection, rusqlite::Error> {
    let raw = Connection::open(db)?;
    raw.busy_timeout(std::time::Duration::from_secs(10))?;
    Ok(raw)
}

fn lease_count(c: &Connection) -> Result<i64, rusqlite::Error> {
    c.query_row("SELECT COUNT(*) FROM ontology_lease", [], |r| r.get(0))
}

#[test]
fn main_acquire_reuses_released_token_on_v0() -> TestResult {
    // Reproduces the token-reuse incident fixed by #783 on the v0 schema so the v2 test below is
    // known to exercise a real hazard.
    let dir = temp_dir("v0-reuse")?;
    let conn = open_raw(&dir.join("state.sqlite"))?;
    conn.execute_batch(MAIN_DDL_V0)?;
    conn.execute(PTR_INSERT, rusqlite::params![WS, PKG, "1.0", FP, 0, 0, 1])?;
    let first = main_acquire(&conn, "lease-a", "projection")?;
    conn.execute(MAIN_RELEASE, ("lease-a", "legacy-main"))?;
    let second = main_acquire(&conn, "lease-b", "projection")?;
    assert_eq!(first, 2);
    assert_eq!(second, 2, "v0 reissues the released token");
    Ok(())
}

#[test]
fn main_acquire_is_aborted_by_t3_on_v2() -> TestResult {
    let f = v2_fixture("main-acquire")?;
    let err = main_acquire(&f.raw, "6f1c2b9e-1d2a-4c3b-9e8f-0a1b2c3d4e5f", "projection")
        .err()
        .ok_or("legacy main acquire must fail on v2")?;
    let msg = trigger_msg(&err).ok_or_else(|| format!("not a trigger abort: {err}"))?;
    assert!(msg.starts_with("lifecycle T3"), "{msg}");
    assert_eq!(lease_count(&f.raw)?, 0);
    assert_eq!(f.backend.high_water(&f.scope)?.map(|t| t.get()), Some(2));
    Ok(())
}

#[test]
fn v1_acquire_is_aborted_by_t1_insert_once_and_writes_nothing() -> TestResult {
    let f = v2_fixture("v1-acquire")?;
    let err = v1_acquire(&f.raw, "0e7d5c2a-9b8f-4e1d-8c3b-2a1f0e9d8c7b", "projection")
        .err()
        .ok_or("legacy v1 acquire must fail on v2")?;
    let msg = trigger_msg(&err).ok_or_else(|| format!("not a trigger abort: {err}"))?;
    // The v1 high-water UPSERT is an INSERT over an existing row, which the
    // T1 insert-once trigger refuses before the lease INSERT is reached.
    assert!(msg.starts_with("lifecycle T1"), "{msg}");
    assert_eq!(lease_count(&f.raw)?, 0);
    assert_eq!(f.backend.high_water(&f.scope)?.map(|t| t.get()), Some(2));
    // Even with the high-water raised the legal way (a plain UPDATE, T1
    // allows an increase), the lease INSERT has no mint label: T3.
    let err = in_immediate(&f.raw, |c| {
        c.execute("UPDATE ontology_fence_high_water SET high_water=3", [])?;
        c.execute(
            V1_INSERT,
            rusqlite::params!["0e7d5c2a", WS, PKG, "projection", "legacy-v1", FP, 0, 0, 3, NOW, NOW + 60_000],
        )
    })
    .err()
    .ok_or("lease insert without a mint label must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T3")), "{err}");
    assert_eq!(f.backend.high_water(&f.scope)?.map(|t| t.get()), Some(2), "rolled back");
    Ok(())
}

#[test]
fn mixed_old_and_new_python_writers_cannot_duplicate_tokens() -> TestResult {
    // Mixed old/new minters: v0 and v1 acquire interleaved with the authority. Every
    // legacy attempt aborts, so authority-minted tokens stay unique.
    let mut f = v2_fixture("mixed")?;
    let mut seen = vec![1u64, 2];
    for i in 0..5 {
        assert!(main_acquire(&f.raw, &format!("main-{i}"), "projection").is_err());
        assert!(v1_acquire(&f.raw, &format!("v1-{i}"), "index").is_err());
        let l = f.backend.acquire(&AcquireRequest {
            scope: &f.scope,
            purpose: "projection",
            owner: "rust",
            ttl_ms: 1_000,
        })?;
        seen.push(l.fencing_token.get());
        f.backend.release(&l.lease_id, "rust")?;
    }
    let mut sorted = seen.clone();
    sorted.dedup();
    assert_eq!(sorted, seen, "tokens strictly increasing and unique");
    assert_eq!(seen, vec![1, 2, 3, 4, 5, 6, 7]);
    Ok(())
}

#[test]
fn legacy_renew_is_allowed_but_token_rewrite_is_aborted_by_t4() -> TestResult {
    let mut f = v2_fixture("t4")?;
    let l = f.backend.acquire(&AcquireRequest {
        scope: &f.scope,
        purpose: "projection",
        owner: "rust",
        ttl_ms: 60_000,
    })?;
    // pre-#783 lifecycle.py:238 renew only touches expires_at_ms: still allowed.
    f.raw.execute(MAIN_RENEW, (NOW + 120_000, &l.lease_id))?;
    let err = f
        .raw
        .execute("UPDATE ontology_lease SET fencing_token=fencing_token+100 WHERE lease_id=?", [&l.lease_id])
        .err()
        .ok_or("token rewrite must fail")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T4")), "{err}");
    let err = f
        .raw
        .execute("UPDATE ontology_lease SET purpose='projection2' WHERE lease_id=?", [&l.lease_id])
        .err()
        .ok_or("purpose rewrite must fail")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T4")), "{err}");
    Ok(())
}

#[test]
fn legacy_publish_with_caller_chosen_token_is_aborted_by_t5() -> TestResult {
    let mut f = v2_fixture("t5")?;
    let before = f.backend.pointer(&f.scope)?.ok_or("pointer")?;
    let expected = Some((before.generation, before.epoch));
    // B wedge: a large caller-chosen token would fence every lease forever
    // and leave the high-water below the pointer.
    for token in [1_000_000_i64, 3, 2, 1] {
        let err = legacy_publish(&f.raw, FP2, token, expected)
            .err()
            .ok_or_else(|| format!("legacy publish with token {token} must abort"))?;
        let msg = trigger_msg(&err).ok_or_else(|| format!("not a trigger abort: {err}"))?;
        assert!(msg.starts_with("lifecycle T5"), "{msg}");
    }
    assert_eq!(f.backend.pointer(&f.scope)?.as_ref(), Some(&before));

    // Right after an authority activation the high-water's `minted_for` is an
    // activation label and `high_water == pointer token`. Replaying that very
    // token for another swap must still fail: the label binds (gen, epoch).
    let act = f.backend.activate(&ActivateRequest {
        scope: &f.scope,
        version: "2.0",
        fingerprint: FP2,
        expected,
        min_token: None,
    })?;
    let tok = i64::try_from(act.fencing_token.get())?;
    let err = legacy_publish(&f.raw, FP, tok, Some((act.generation, act.epoch)))
        .err()
        .ok_or("replayed activation token must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T5")), "{err}");
    Ok(())
}

#[test]
fn legacy_first_publish_and_recreate_are_aborted_by_t6_or_t7() -> TestResult {
    let f = v2_fixture("t6")?;
    let before = f.backend.pointer(&f.scope)?.ok_or("pointer")?;
    // Legacy recreate bumps the generation with an UPSERT over the existing
    // generation_hwm row, which the T7 insert-once trigger refuses.
    let err = legacy_recreate(&f.raw, 1_000).err().ok_or("legacy recreate must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T7")), "{err}");
    assert_eq!(f.backend.pointer(&f.scope)?.as_ref(), Some(&before), "rolled back");
    let hwm: i64 = f.raw.query_row("SELECT hwm FROM generation_hwm", [], |r| r.get(0))?;
    assert_eq!(hwm, 0, "generation bump rolled back");
    // The same recreate with the bump done as a plain UPDATE still has no
    // activation mint: T6.
    let err = in_immediate(&f.raw, |c| {
        c.execute(PTR_DELETE, (WS, PKG))?;
        c.execute("UPDATE generation_hwm SET hwm=hwm+1 WHERE workspace_id=? AND package_id=?", (WS, PKG))?;
        c.execute(PTR_INSERT, rusqlite::params![WS, PKG, "legacy", FP2, 1, 0, 1_000])
    })
    .err()
    .ok_or("recreate with plain hwm UPDATE must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T6")), "{err}");
    assert_eq!(f.backend.pointer(&f.scope)?.as_ref(), Some(&before), "rolled back");

    // First publish on a brand-new scope, with the token the authority would
    // mint next: no mint happened, so T6 still refuses it.
    let other = in_immediate(&f.raw, |c| {
        c.execute(GEN_HWM_UPSERT, ("ws", "other", 0))?;
        c.execute(PTR_INSERT, rusqlite::params!["ws", "other", "x", FP, 0, 0, 1])
    })
    .err()
    .ok_or("legacy first publish must abort")?;
    assert!(trigger_msg(&other).is_some_and(|m| m.starts_with("lifecycle T6")), "{other}");
    Ok(())
}

#[test]
fn high_water_and_generation_rows_cannot_move_back_or_vanish() -> TestResult {
    let f = v2_fixture("t1-t2-t7")?;
    let cases = [
        ("UPDATE ontology_fence_high_water SET high_water=1", "lifecycle T1"),
        ("UPDATE ontology_fence_high_water SET high_water=high_water", "lifecycle T1"),
        ("UPDATE ontology_fence_high_water SET minted_for='forged-lease'", "lifecycle T1"),
        ("DELETE FROM ontology_fence_high_water", "lifecycle T2"),
        ("UPDATE generation_hwm SET hwm=hwm-1", "lifecycle T7"),
        ("DELETE FROM generation_hwm", "lifecycle T7"),
        // REPLACE deletes the conflicting row without firing DELETE triggers,
        // so it is stopped by the insert-once triggers (review r1).
        ("INSERT OR REPLACE INTO ontology_fence_high_water VALUES ('ws','pkg',0,NULL)", "lifecycle T1"),
        ("INSERT OR REPLACE INTO ontology_fence_high_water VALUES ('ws','pkg',99,'forged')", "lifecycle T1"),
        ("REPLACE INTO ontology_fence_high_water VALUES ('ws','pkg',2,'forged')", "lifecycle T1"),
        (
            "INSERT INTO ontology_fence_high_water VALUES ('ws','pkg',0,NULL) \
             ON CONFLICT(workspace_id, package_id) DO UPDATE SET high_water=excluded.high_water",
            "lifecycle T1",
        ),
        // A brand-new scope row must still dominate what the scope carries.
        ("INSERT INTO ontology_fence_high_water VALUES ('ws','pkg2',0,NULL)", "lifecycle T1"),
        ("INSERT OR REPLACE INTO generation_hwm VALUES ('ws','pkg',-5)", "lifecycle T7"),
        ("INSERT OR REPLACE INTO generation_hwm VALUES ('ws','pkg',7)", "lifecycle T7"),
        ("INSERT INTO generation_hwm VALUES ('ws','pkg2',-1)", "lifecycle T7"),
    ];
    for (sql, want) in cases {
        let err = f.raw.execute(sql, []).err().ok_or_else(|| format!("{sql} must abort"))?;
        assert!(trigger_msg(&err).is_some_and(|m| m.starts_with(want)), "{sql}: {err}");
    }
    // Re-inserting a released lease with its original token and id is
    // refused even though high_water/minted_for still name it.
    let tomb: (String, i64) = f.raw.query_row(
        "SELECT lease_id, fencing_token FROM ontology_lease_tombstone",
        [],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    let err = f
        .raw
        .execute(
            MAIN_INSERT,
            rusqlite::params![tomb.0, WS, PKG, "projection", "rust", FP, 0, 0, tomb.1, NOW, NOW + 60_000],
        )
        .err()
        .ok_or("resurrected lease must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T3")), "{err}");
    Ok(())
}

fn expect_abort(c: &Connection, sql: &str, want: &str) -> TestResult {
    let err = c.execute(sql, []).err().ok_or_else(|| format!("{sql} must abort"))?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with(want)), "{sql}: {err}");
    Ok(())
}

#[test]
fn released_token_is_not_reissued_after_replace_and_tombstone_prune() -> TestResult {
    // Review r1 scenario: lower the high-water with REPLACE, prune the
    // tombstone, acquire again. REPLACE is refused, the prune is allowed
    // (the high-water dominates the tombstone), and the next token is fresh.
    let mut f = v2_fixture("replace-prune")?;
    expect_abort(
        &f.raw,
        "INSERT OR REPLACE INTO ontology_fence_high_water VALUES ('ws','pkg',0,NULL)",
        "lifecycle T1",
    )?;
    let pruned = f.raw.execute("DELETE FROM ontology_lease_tombstone", [])?;
    assert_eq!(pruned, 1);
    let l = f.backend.acquire(&AcquireRequest {
        scope: &f.scope,
        purpose: "projection",
        owner: "rust",
        ttl_ms: 60_000,
    })?;
    assert_eq!(l.fencing_token.get(), 3, "released token 2 is never reissued");
    Ok(())
}

#[test]
fn lease_rows_leave_only_through_the_tombstone_and_labels_are_single_use() -> TestResult {
    let mut f = v2_fixture("lease-delete")?;
    let l = f.backend.acquire(&AcquireRequest {
        scope: &f.scope,
        purpose: "projection",
        owner: "rust",
        ttl_ms: 60_000,
    })?;
    let tok = i64::try_from(l.fencing_token.get())?;
    // Legacy main `release()` (room lifecycle.py:244) skips the tombstone.
    let err = f
        .raw
        .execute(MAIN_RELEASE, (&l.lease_id, "rust"))
        .err()
        .ok_or("legacy release must abort on v2")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T4")), "{err}");
    assert_eq!(lease_count(&f.raw)?, 1);
    // The mint label was consumed by the lease insert, so neither a REPLACE
    // of the live lease with another owner nor a re-insert works.
    let minted_for: String =
        f.raw.query_row("SELECT minted_for FROM ontology_fence_high_water", [], |r| r.get(0))?;
    assert_eq!(minted_for, format!("consumed:{}", l.lease_id));
    let err = f
        .raw
        .execute(
            "INSERT OR REPLACE INTO ontology_lease VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rusqlite::params![l.lease_id, WS, PKG, "projection", "thief", FP, 0, 0, tok, NOW, NOW + 60_000],
        )
        .err()
        .ok_or("REPLACE of a live lease must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T3")), "{err}");
    // Un-consuming a label is not an increase either.
    expect_abort(
        &f.raw,
        &format!("UPDATE ontology_fence_high_water SET minted_for='{}'", l.lease_id),
        "lifecycle T1",
    )?;
    expect_abort(
        &f.raw,
        "UPDATE ontology_fence_high_water SET minted_for='consumed:' || minted_for",
        "lifecycle T1",
    )?;
    // A tombstone whose token the high-water does not dominate (only
    // possible on a damaged DB) cannot be pruned.
    f.raw.execute_batch("BEGIN")?;
    f.raw.execute("DROP TRIGGER lifecycle_t1_high_water_increase_only", [])?;
    f.raw.execute("DROP TRIGGER lifecycle_t1_high_water_insert_once", [])?;
    f.raw.execute("DROP TRIGGER lifecycle_t2_high_water_no_delete", [])?;
    f.raw.execute("DELETE FROM ontology_fence_high_water", [])?;
    expect_abort(&f.raw, "DELETE FROM ontology_lease_tombstone", "lifecycle T4")?;
    f.raw.execute_batch("ROLLBACK")?;
    f.backend.release(&l.lease_id, "rust")?;
    assert_eq!(lease_count(&f.raw)?, 0);
    Ok(())
}

#[test]
fn pointer_cannot_be_republished_under_a_consumed_activation_token() -> TestResult {
    // Review r1: right after a first publish, hw = pointer token and the label
    // names (generation, 0). DELETE + INSERT or REPLACE with other content
    // must not reuse that (generation, epoch, token).
    let mut f = v2_fixture("t6-reinsert")?;
    let fresh = Scope::new(WS, "fresh")?;
    let act = f.backend.activate(&ActivateRequest {
        scope: &fresh,
        version: "1.0",
        fingerprint: FP,
        expected: None,
        min_token: None,
    })?;
    let tok = i64::try_from(act.fencing_token.get())?;
    let before = f.backend.pointer(&fresh)?;
    let err = f
        .raw
        .execute(
            "INSERT OR REPLACE INTO active_ontology VALUES (?,?,?,?,?,?,?)",
            rusqlite::params![WS, "fresh", "evil", FP2, act.generation, 0, tok],
        )
        .err()
        .ok_or("pointer REPLACE must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T6")), "{err}");
    let err = in_immediate(&f.raw, |c| {
        c.execute(PTR_DELETE, (WS, "fresh"))?;
        c.execute(PTR_INSERT, rusqlite::params![WS, "fresh", "evil", FP2, act.generation, 0, tok])
    })
    .err()
    .ok_or("pointer DELETE + re-insert must abort")?;
    assert!(trigger_msg(&err).is_some_and(|m| m.starts_with("lifecycle T6")), "{err}");
    assert_eq!(f.backend.pointer(&fresh)?, before);
    Ok(())
}

#[test]
fn lifecycle_meta_is_guarded_and_clock_floor_never_silently_resets() -> TestResult {
    let f = v2_fixture("meta")?;
    for sql in [
        "UPDATE lifecycle_meta SET value='0' WHERE key='last_wall_ms'",
        "UPDATE lifecycle_meta SET value='garbled' WHERE key='last_wall_ms'",
        "UPDATE lifecycle_meta SET value='' WHERE key='last_wall_ms'",
        "UPDATE lifecycle_meta SET value='x' WHERE key='ddl_sha256'",
        "UPDATE lifecycle_meta SET key='last_wall_ms_old' WHERE key='last_wall_ms'",
        "DELETE FROM lifecycle_meta WHERE key='last_wall_ms'",
        "INSERT OR REPLACE INTO lifecycle_meta VALUES ('last_wall_ms','0')",
        "INSERT OR REPLACE INTO lifecycle_meta VALUES ('ddl_sha256','x')",
    ] {
        expect_abort(&f.raw, sql, "lifecycle M1")?;
    }
    // With the guard dropped (tampering), `open` refuses the DB, and a
    // backend already open fails `schema_tampered` instead of clamping to 0.
    let db = f.backend.path().to_path_buf();
    let mut backend = f.backend;
    f.raw.execute_batch(
        "DROP TRIGGER lifecycle_m1_meta_update_clock_only;
         UPDATE lifecycle_meta SET value='garbled' WHERE key='last_wall_ms';",
    )?;
    let err = backend
        .acquire(&AcquireRequest {
            scope: &f.scope,
            purpose: "projection",
            owner: "rust",
            ttl_ms: 60_000,
        })
        .err()
        .ok_or("garbled last_wall_ms must fail")?;
    assert_eq!(err.code(), seocho_lifecycle::ErrorCode::SchemaTampered, "{err}");
    let err = SqliteBackend::open(&db, ManualClock::new(Millis::new(1)))
        .err()
        .ok_or("tampered DB must not open")?;
    assert_eq!(err.code(), seocho_lifecycle::ErrorCode::SchemaTampered, "{err}");
    Ok(())
}
