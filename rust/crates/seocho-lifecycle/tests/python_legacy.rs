//! Legacy-writer replay through the *real* Python stores (design I1 test
//! column: "Rust raw rusqlite와 Python subprocess 둘 다").
//!
//! Imports `OntologyLifecycleStore` from this repository's `src/` (the v1
//! store since #783) and, optionally, from a pre-#783 (v0) checkout named by
//! `SEOCHO_LIFECYCLE_V0_PY_SRC` (read-only: `python3 -I -B`, no bytecode
//! written), and runs `acquire()` and `pointer.publish()` with a
//! caller-chosen token against a v2 database. Both must raise
//! `sqlite3.IntegrityError` from a lifecycle trigger.
//!
//! When a source tree or `python3` is missing the case is skipped with a
//! message, unless `SEOCHO_LIFECYCLE_REQUIRE_PYTHON=1`, which turns a skip
//! into a failure (a missing dependency must never pass silently in CI).
//! The optional v0 case is skipped whenever its variable is unset.

mod common;

use common::{temp_dir, TestResult, FP};
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, LifecycleBackend, ManualClock, Millis, Scope, SqliteBackend,
};
use std::path::Path;
use std::process::Command;

/// This repository's Python sources: `rust/crates/seocho-lifecycle` -> `src/`.
fn repo_src() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../src")
}

const SCRIPT: &str = r#"
import sqlite3, sys
sys.path.insert(0, sys.argv[1])
from seocho.ontology.lifecycle import OntologyLifecycleStore
db, ws, pkg = sys.argv[2], sys.argv[3], sys.argv[4]
store = OntologyLifecycleStore(db)
try:
    lease = store.acquire(ws, pkg, purpose="projection", owner="legacy-py", ttl_seconds=60)
    print("ACQUIRE_OK", lease.fencing_token)
except sqlite3.IntegrityError as exc:
    print("ACQUIRE_ABORT", exc)
cur = store.pointer.read(ws, pkg)
try:
    ok, _ = store.pointer.publish(ws, pkg, version="evil", fingerprint="f" * 64,
                                  fencing_token=cur.fencing_token + 1000,
                                  expected=(cur.generation, cur.epoch))
    print("PUBLISH_OK", ok)
except sqlite3.IntegrityError as exc:
    print("PUBLISH_ABORT", exc)
print("STATUS_LIVE", len(store.status(ws, pkg)["live_leases"]))
"#;

fn require() -> bool {
    std::env::var("SEOCHO_LIFECYCLE_REQUIRE_PYTHON").as_deref() == Ok("1")
}

/// `acquire_trigger`: v0 inserts a lease with a self-computed token (T3);
/// v1 first UPSERTs the high-water over its existing row (T1 insert-once).
fn run_python(src: &Path, label: &str, acquire_trigger: &str) -> TestResult {
    if !src.join("seocho/ontology/lifecycle.py").is_file() {
        if require() {
            return Err(format!("{label}: {} missing and SEOCHO_LIFECYCLE_REQUIRE_PYTHON=1", src.display()).into());
        }
        eprintln!("SKIP {label}: {} not present", src.display());
        return Ok(());
    }
    let src = src.to_str().ok_or("non-UTF-8 source path")?;
    let dir = temp_dir(&format!("py-{label}"))?;
    let db = dir.join("state.sqlite");
    let mut backend = SqliteBackend::create(&db, ManualClock::new(Millis::new(1_700_000_000_000)))?;
    let scope = Scope::new("ws", "pkg")?;
    backend.activate(&ActivateRequest {
        scope: &scope,
        version: "1.0",
        fingerprint: FP,
        expected: None,
        min_token: None,
    })?;
    let lease = backend.acquire(&AcquireRequest {
        scope: &scope,
        purpose: "projection",
        owner: "rust",
        ttl_ms: 60_000,
    })?;
    backend.release(&lease.lease_id, "rust")?;
    let hw_before = backend.high_water(&scope)?;
    let ptr_before = backend.pointer(&scope)?;

    let db_str = db.to_str().ok_or("non-UTF-8 temp path")?;
    let out = match Command::new("python3")
        .args(["-I", "-B", "-c", SCRIPT, src, db_str, "ws", "pkg"])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
    {
        Ok(o) => o,
        Err(e) if !require() => {
            eprintln!("SKIP {label}: python3 not runnable: {e}");
            return Ok(());
        }
        Err(e) => return Err(e.into()),
    };
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(out.status.success(), "{label}: python failed\n{stdout}\n{stderr}");
    assert!(
        stdout.lines().any(|l| l.starts_with(&format!("ACQUIRE_ABORT {acquire_trigger}"))),
        "{label}: acquire not aborted by {acquire_trigger}\n{stdout}\n{stderr}"
    );
    assert!(
        stdout.lines().any(|l| l.starts_with("PUBLISH_ABORT lifecycle T5")),
        "{label}: publish not aborted by T5\n{stdout}\n{stderr}"
    );
    // Read paths of the legacy store keep working on v2.
    assert!(stdout.lines().any(|l| l == "STATUS_LIVE 0"), "{label}\n{stdout}");
    assert_eq!(backend.high_water(&scope)?, hw_before, "{label}: high-water moved");
    assert_eq!(backend.pointer(&scope)?, ptr_before, "{label}: pointer moved");
    Ok(())
}

#[test]
fn python_v0_store_cannot_mint_on_v2() -> TestResult {
    match std::env::var_os("SEOCHO_LIFECYCLE_V0_PY_SRC") {
        Some(src) => run_python(Path::new(&src), "v0", "lifecycle T3"),
        None => {
            eprintln!("SKIP v0: SEOCHO_LIFECYCLE_V0_PY_SRC not set");
            Ok(())
        }
    }
}

#[test]
fn python_repo_v1_store_cannot_mint_on_v2() -> TestResult {
    run_python(&repo_src(), "v1", "lifecycle T1")
}
