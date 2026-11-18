//! `seocho-lifecycled audit` against v0 / v1 / v2 fixtures (design §9 tests 4
//! and 5). Fixtures are built with the legacy Python DDL, copied verbatim.

use rusqlite::Connection;
use seocho_lifecycle::schema;
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, LifecycleBackend, ManualClock, Millis, Scope, SqliteBackend,
};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

type TestResult = Result<(), Box<dyn std::error::Error>>;

/// pre-#783 src/seocho/ontology/active_pointer.py:70-81 and
/// pre-#783 src/seocho/ontology/lifecycle.py:172-183 (v0).
const V0_DDL: &str = "CREATE TABLE IF NOT EXISTS active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY (workspace_id, package_id));
CREATE TABLE IF NOT EXISTS generation_hwm (workspace_id TEXT, package_id TEXT, hwm INTEGER, PRIMARY KEY (workspace_id, package_id));
CREATE TABLE IF NOT EXISTS ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, purpose TEXT NOT NULL, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL, fencing_token INTEGER NOT NULL, acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ontology_lease_live ON ontology_lease(workspace_id, package_id, expires_at_ms);
CREATE TABLE IF NOT EXISTS projection_idempotency (idempotency_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, response_json TEXT, created_at_ms INTEGER NOT NULL, completed_at_ms INTEGER);";

/// #783 src/seocho/ontology/lifecycle.py:223-226 (v1 addition).
const V1_HW_DDL: &str = "CREATE TABLE IF NOT EXISTS ontology_fence_high_water (workspace_id TEXT NOT NULL, package_id TEXT NOT NULL, high_water INTEGER NOT NULL, PRIMARY KEY (workspace_id, package_id));";

const FP: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const NOW: i64 = 1_700_000_000_000;

static N: AtomicU64 = AtomicU64::new(0);

struct Dir(PathBuf);
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn dir(tag: &str) -> std::io::Result<Dir> {
    let d = std::env::temp_dir().join(format!(
        "seocho-lifecycled-{tag}-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    std::fs::create_dir_all(&d)?;
    Ok(Dir(d))
}

/// A WAL-mode legacy DB, like Python leaves it (`PRAGMA journal_mode=WAL`).
fn legacy_db(path: &Path, v1: bool) -> Result<Connection, rusqlite::Error> {
    let c = Connection::open(path)?;
    c.query_row("PRAGMA journal_mode=WAL", [], |r| r.get::<_, String>(0))?;
    c.execute_batch(V0_DDL)?;
    if v1 {
        c.execute_batch(V1_HW_DDL)?;
    }
    Ok(c)
}

fn lease(c: &Connection, id: &str, purpose: &str, token: i64, expires: i64) -> Result<(), rusqlite::Error> {
    // pre-#783 src/seocho/ontology/lifecycle.py:225
    c.execute(
        "INSERT INTO ontology_lease VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rusqlite::params![id, "ws", "pkg", purpose, "py", FP, 0, 0, token, NOW - 1000, expires],
    )?;
    Ok(())
}

struct Run {
    code: Option<i32>,
    json: serde_json::Value,
}

fn audit(db: &Path, extra: &[&str]) -> Result<Run, Box<dyn std::error::Error>> {
    let out = Command::new(env!("CARGO_BIN_EXE_seocho-lifecycled"))
        .arg("audit")
        .arg(db)
        .args(["--json", "--now-ms", &NOW.to_string()])
        .args(extra)
        .output()?;
    let stdout = String::from_utf8(out.stdout)?;
    let json = serde_json::from_str(&stdout)
        .map_err(|e| format!("{e}: {stdout} {}", String::from_utf8_lossy(&out.stderr)))?;
    Ok(Run {
        code: out.status.code(),
        json,
    })
}

fn kinds(run: &Run) -> Vec<(String, String)> {
    run.json["findings"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|f| {
                    (
                        f["severity"].as_str().unwrap_or_default().to_string(),
                        f["kind"].as_str().unwrap_or_default().to_string(),
                    )
                })
                .collect()
        })
        .unwrap_or_default()
}

fn has(run: &Run, severity: &str, kind: &str) -> bool {
    kinds(run).iter().any(|(s, k)| s == severity && k == kind)
}

/// Contents of every file in `d`, ignoring the WAL shared-memory index and
/// empty files: a `mode=ro` reader of a WAL-mode DB may create `<db>-shm`
/// (SQLite needs it to read; it holds no data) and an empty `<db>-wal`, but
/// must never change the DB or any WAL content.
fn digest_files(d: &Path) -> Result<Vec<(String, Vec<u8>)>, std::io::Error> {
    let mut v = Vec::new();
    for e in std::fs::read_dir(d)? {
        let e = e?;
        let name = e.file_name().to_string_lossy().into_owned();
        let bytes = std::fs::read(e.path())?;
        if name.ends_with("-shm") || bytes.is_empty() {
            continue;
        }
        v.push((name, bytes));
    }
    v.sort();
    Ok(v)
}

#[test]
fn v0_reused_token_is_a_violation_and_db_is_untouched() -> TestResult {
    let d = dir("v0")?;
    let db = d.0.join("state.sqlite");
    let c = legacy_db(&db, false)?;
    c.execute(
        "INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,1)",
        [FP],
    )?;
    // Two surviving rows carry token 2: the reuse main acquire produces when
    // a released lease's token is reissued and the first holder's row is
    // later re-inserted by another process (the #783 incident and the mixed old/new minter hazard).
    lease(&c, "lease-a", "projection", 2, NOW + 60_000)?;
    lease(&c, "lease-b", "query", 2, NOW + 60_000)?;
    lease(&c, "lease-c", "index", 3, NOW - 1)?;
    drop(c);
    let before = digest_files(&d.0)?;
    let run = audit(&db, &[])?;
    assert_eq!(run.code, Some(1), "{}", run.json);
    assert_eq!(run.json["schema"], "v0");
    assert!(has(&run, "violation", "duplicate_token"), "{}", run.json);
    assert!(has(&run, "warning", "expired_but_held"), "{}", run.json);
    assert!(has(&run, "warning", "no_high_water_table"), "{}", run.json);
    assert_eq!(run.json["violations"], 1);
    assert_eq!(digest_files(&d.0)?, before, "audit must not write (mode=ro)");
    Ok(())
}

#[test]
fn v0_clean_db_passes_unless_strict() -> TestResult {
    let d = dir("v0-clean")?;
    let db = d.0.join("state.sqlite");
    let c = legacy_db(&db, false)?;
    c.execute("INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,1)", [FP])?;
    lease(&c, "lease-a", "projection", 2, NOW + 60_000)?;
    drop(c);
    let run = audit(&db, &[])?;
    assert_eq!(run.code, Some(0), "{}", run.json);
    let strict = audit(&db, &["--strict"])?;
    assert_eq!(strict.code, Some(1), "v0 always warns no_high_water_table");
    Ok(())
}

#[test]
fn v1_high_water_below_lease_is_a_violation_pointer_drift_a_warning() -> TestResult {
    let d = dir("v1")?;
    let db = d.0.join("state.sqlite");
    let c = legacy_db(&db, true)?;
    c.execute("INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,9)", [FP])?;
    c.execute("INSERT INTO active_ontology VALUES ('ws','other','1.0',?1,0,0,4)", [FP])?;
    c.execute("INSERT INTO ontology_fence_high_water VALUES ('ws','pkg',5)", [])?;
    c.execute("INSERT INTO ontology_fence_high_water VALUES ('ws','other',3)", [])?;
    lease(&c, "lease-a", "projection", 7, NOW + 60_000)?;
    drop(c);
    let run = audit(&db, &[])?;
    assert_eq!(run.json["schema"], "v1");
    assert_eq!(run.code, Some(1));
    assert!(has(&run, "violation", "high_water_below_lease"), "{}", run.json);
    assert!(has(&run, "warning", "high_water_below_pointer"), "{}", run.json);
    assert_eq!(run.json["violations"], 1, "{}", run.json);
    Ok(())
}

#[test]
fn v2_authority_db_is_clean_and_tampering_is_reported() -> TestResult {
    let d = dir("v2")?;
    let db = d.0.join("state.sqlite");
    let mut b = SqliteBackend::create(&db, ManualClock::new(Millis::new(u64::try_from(NOW)?)))?;
    let scope = Scope::new("ws", "pkg")?;
    b.activate(&ActivateRequest {
        scope: &scope,
        version: "1",
        fingerprint: FP,
        expected: None,
        min_token: None,
    })?;
    for p in ["projection", "query"] {
        let l = b.acquire(&AcquireRequest {
            scope: &scope,
            purpose: p,
            owner: "o",
            ttl_ms: 60_000,
        })?;
        if p == "query" {
            b.release(&l.lease_id, "o")?;
        }
    }
    drop(b);
    let run = audit(&db, &["--strict"])?;
    assert_eq!(run.code, Some(0), "{}", run.json);
    assert_eq!(run.json["schema"], "v2");
    assert_eq!(run.json["scopes"][0]["high_water"], 3);
    assert_eq!(run.json["scopes"][0]["max_tombstone_token"], 3);

    let c = Connection::open(&db)?;
    c.execute_batch("DROP TRIGGER lifecycle_t3_lease_insert_minted;")?;
    assert_ne!(schema::ddl_sha256(&c)?, schema::expected_ddl_sha256()?);
    drop(c);
    let run = audit(&db, &[])?;
    assert_eq!(run.code, Some(1));
    assert!(has(&run, "violation", "schema_tampered"), "{}", run.json);
    Ok(())
}

#[test]
fn missing_db_and_bad_usage_exit_2() -> TestResult {
    let d = dir("missing")?;
    let bin = env!("CARGO_BIN_EXE_seocho-lifecycled");
    let missing = Command::new(bin).arg("audit").arg(d.0.join("nope.sqlite")).output()?;
    assert_eq!(missing.status.code(), Some(2));
    assert!(!d.0.join("nope.sqlite").exists(), "audit must not create a DB");
    let usage = Command::new(bin).arg("serve").output()?;
    assert_eq!(usage.status.code(), Some(2));
    Ok(())
}
