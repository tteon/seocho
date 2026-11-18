//! Focused authority behaviour: schema integrity (I13), clock (I9),
//! `min_token` (I4), CAS (I3), tombstones and refusal of legacy schemas.

mod common;

use common::{temp_dir, TestResult, FP, FP2};
use rusqlite::Connection;
use seocho_lifecycle::schema::{self, SchemaVersion};
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, ErrorCode, LifecycleBackend, ManualClock, Millis, Scope,
    SqliteBackend,
};

fn activate_first(b: &mut SqliteBackend<ManualClock>, scope: &Scope) -> TestResult {
    b.activate(&ActivateRequest {
        scope,
        version: "1",
        fingerprint: FP,
        expected: None,
        min_token: None,
    })?;
    Ok(())
}

#[test]
fn dropped_trigger_is_detected_by_ddl_sha256_and_open_refuses() -> TestResult {
    let dir = temp_dir("tamper")?;
    let db = dir.join("state.sqlite");
    let conn = schema::create_for_test(&db)?;
    let good = schema::ddl_sha256(&conn)?;
    assert_eq!(good, schema::expected_ddl_sha256()?);
    conn.execute_batch("DROP TRIGGER lifecycle_t5_pointer_update_minted;")?;
    let bad = schema::ddl_sha256(&conn)?;
    assert_ne!(good, bad);
    drop(conn);
    let err = SqliteBackend::open(&db, ManualClock::default())
        .err()
        .ok_or("open must refuse a tampered schema")?;
    assert_eq!(err.code(), ErrorCode::SchemaTampered, "{err}");
    let report = seocho_lifecycle::audit::audit_path(&db, 0)?;
    assert!(report.has_kind("schema_tampered"), "{:?}", report.findings);
    assert_eq!(report.violations(), 1);
    Ok(())
}

#[test]
fn added_trigger_is_also_tampering() -> TestResult {
    let dir = temp_dir("tamper-add")?;
    let db = dir.join("state.sqlite");
    let conn = schema::create_for_test(&db)?;
    conn.execute_batch(
        "CREATE TRIGGER sneaky AFTER INSERT ON ontology_lease BEGIN SELECT 1; END;",
    )?;
    drop(conn);
    let err = SqliteBackend::open(&db, ManualClock::default())
        .err()
        .ok_or("open must refuse")?;
    assert_eq!(err.code(), ErrorCode::SchemaTampered);
    Ok(())
}

#[test]
fn legacy_schemas_are_refused_not_upgraded() -> TestResult {
    let dir = temp_dir("legacy-refuse")?;
    let db = dir.join("state.sqlite");
    let conn = Connection::open(&db)?;
    conn.execute_batch(
        "CREATE TABLE ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL, acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);",
    )?;
    assert_eq!(schema::detect(&conn)?, SchemaVersion::V0);
    drop(conn);
    let create = SqliteBackend::create(&db, ManualClock::default())
        .err()
        .ok_or("create must refuse v0")?;
    assert_eq!(create.code(), ErrorCode::SchemaMismatch);
    let open = SqliteBackend::open(&db, ManualClock::default())
        .err()
        .ok_or("open must refuse v0")?;
    assert_eq!(open.code(), ErrorCode::SchemaMismatch);
    Ok(())
}

#[test]
fn authority_clock_never_goes_backwards_across_restart() -> TestResult {
    let dir = temp_dir("clock")?;
    let db = dir.join("state.sqlite");
    let clock = ManualClock::new(Millis::new(10_000));
    let scope = Scope::new("ws", "pkg")?;
    let mut b = SqliteBackend::create(&db, clock.clone())?;
    activate_first(&mut b, &scope)?;
    let first = b.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "o",
        ttl_ms: 1_000,
    })?;
    assert_eq!(first.acquired_at_ms, Millis::new(10_000));
    drop(b);
    clock.set(Millis::new(5_000));
    let mut b = SqliteBackend::open(&db, clock.clone())?;
    // At wall time 5_000 the lease would look live until 11_000; the
    // authority clamps to 10_000 so it is still live and blocks.
    let held = b
        .acquire(&AcquireRequest {
            scope: &scope,
            purpose: "projection",
            owner: "p",
            ttl_ms: 1_000,
        })
        .err()
        .ok_or("must be held")?;
    assert_eq!(held.code(), ErrorCode::LeaseHeld);
    let other = b.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "query",
        owner: "p",
        ttl_ms: 1_000,
    })?;
    assert_eq!(other.acquired_at_ms, Millis::new(10_000), "clamped to persisted wall");
    clock.set(Millis::new(11_000));
    let after = b.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "p",
        ttl_ms: 1_000,
    })?;
    assert!(after.fencing_token > other.fencing_token);
    let raw = Connection::open(&db)?;
    let expired: i64 = raw.query_row(
        "SELECT COUNT(*) FROM ontology_lease_tombstone WHERE end_reason='expired'",
        [],
        |r| r.get(0),
    )?;
    assert_eq!(expired, 2, "both expired leases tombstoned by the acquire");
    Ok(())
}

#[test]
fn min_token_assertion_fences_and_writes_nothing() -> TestResult {
    let dir = temp_dir("min-token")?;
    let db = dir.join("state.sqlite");
    let scope = Scope::new("ws", "pkg")?;
    let mut b = SqliteBackend::create(&db, ManualClock::new(Millis::new(1)))?;
    activate_first(&mut b, &scope)?;
    let before = b.pointer(&scope)?.ok_or("pointer")?;
    let err = b
        .activate(&ActivateRequest {
            scope: &scope,
            version: "2",
            fingerprint: FP2,
            expected: Some((before.generation, before.epoch)),
            min_token: Some(100),
        })
        .err()
        .ok_or("must fence")?;
    assert_eq!(err.code(), ErrorCode::Fenced);
    assert_eq!(b.pointer(&scope)?.as_ref(), Some(&before));
    assert_eq!(b.high_water(&scope)?.map(|t| t.get()), Some(1));
    // min_token at or below the mint is a passing assertion.
    let ok = b.activate(&ActivateRequest {
        scope: &scope,
        version: "2",
        fingerprint: FP2,
        expected: Some((before.generation, before.epoch)),
        min_token: Some(2),
    })?;
    assert_eq!(ok.fencing_token.get(), 2);
    assert_eq!((ok.generation, ok.epoch), (0, 1));
    Ok(())
}

#[test]
fn stale_cas_and_double_first_publish_are_rejected() -> TestResult {
    let dir = temp_dir("cas")?;
    let db = dir.join("state.sqlite");
    let scope = Scope::new("ws", "pkg")?;
    let mut b = SqliteBackend::create(&db, ManualClock::new(Millis::new(1)))?;
    activate_first(&mut b, &scope)?;
    let again = b
        .activate(&ActivateRequest {
            scope: &scope,
            version: "1",
            fingerprint: FP,
            expected: None,
            min_token: None,
        })
        .err()
        .ok_or("second first-publish must fail")?;
    assert_eq!(again.code(), ErrorCode::CasMismatch);
    let stale = b
        .activate(&ActivateRequest {
            scope: &scope,
            version: "1",
            fingerprint: FP,
            expected: Some((0, 7)),
            min_token: None,
        })
        .err()
        .ok_or("stale CAS must fail")?;
    assert_eq!(stale.code(), ErrorCode::CasMismatch);
    Ok(())
}

#[test]
fn release_requires_owner_and_moves_row_to_tombstone() -> TestResult {
    let dir = temp_dir("release")?;
    let db = dir.join("state.sqlite");
    let scope = Scope::new("ws", "pkg")?;
    let mut b = SqliteBackend::create(&db, ManualClock::new(Millis::new(1)))?;
    activate_first(&mut b, &scope)?;
    let l = b.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "o",
        ttl_ms: 1_000,
    })?;
    let wrong = b.release(&l.lease_id, "x").err().ok_or("owner mismatch")?;
    assert_eq!(wrong.code(), ErrorCode::OwnerMismatch);
    b.release(&l.lease_id, "o")?;
    let absent = b.release(&l.lease_id, "o").err().ok_or("absent")?;
    assert_eq!(absent.code(), ErrorCode::LeaseAbsent);
    let raw = Connection::open(&db)?;
    let (reason, tok): (String, i64) = raw.query_row(
        "SELECT end_reason, fencing_token FROM ontology_lease_tombstone WHERE lease_id=?1",
        [&l.lease_id],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    assert_eq!(reason, "released");
    assert_eq!(u64::try_from(tok)?, l.fencing_token.get());
    let next = b.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "o",
        ttl_ms: 1_000,
    })?;
    assert_eq!(next.fencing_token.get(), l.fencing_token.get() + 1, "never reused");
    let ttl = b
        .acquire(&AcquireRequest {
            scope: &scope,
            purpose: "index",
            owner: "o",
            ttl_ms: seocho_lifecycle::DEFAULT_MAX_TTL_MS + 1,
        })
        .err()
        .ok_or("ttl range")?;
    assert_eq!(ttl.code(), ErrorCode::TtlOutOfRange);
    Ok(())
}

#[test]
fn scopes_have_independent_counters() -> TestResult {
    let dir = temp_dir("scopes")?;
    let db = dir.join("state.sqlite");
    let a = Scope::new("ws1", "pkg")?;
    let c = Scope::new("ws2", "pkg")?;
    let mut b = SqliteBackend::create(&db, ManualClock::new(Millis::new(1)))?;
    activate_first(&mut b, &a)?;
    activate_first(&mut b, &c)?;
    let la = b.acquire(&AcquireRequest {
        scope: &a,
        purpose: "projection",
        owner: "o",
        ttl_ms: 1_000,
    })?;
    let lc = b.acquire(&AcquireRequest {
        scope: &c,
        purpose: "projection",
        owner: "o",
        ttl_ms: 1_000,
    })?;
    assert_eq!((la.fencing_token.get(), lc.fencing_token.get()), (2, 2));
    let none = b
        .acquire(&AcquireRequest {
            scope: &Scope::new("ws3", "pkg")?,
            purpose: "projection",
            owner: "o",
            ttl_ms: 1_000,
        })
        .err()
        .ok_or("no pointer")?;
    assert_eq!(none.code(), ErrorCode::NoActive);
    Ok(())
}
