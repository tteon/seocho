//! Multi-process acquire/release/activate against one control DB (design I1
//! test column: "8-process acquire/release").
//!
//! The test binary re-executes itself: `worker_process` is a no-op unless
//! `SEOCHO_LIFECYCLE_MP_DB` is set, in which case it is one worker. Each
//! worker prints `MPTOKEN <token>` for every token it was granted. SQLite
//! `BEGIN IMMEDIATE` is the only cross-process serialization here; the
//! single-writer flock of `serve` is P2.

mod common;

use common::{fast_temp_dir, TestResult, FP, FP2};
use seocho_lifecycle::audit;
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, ErrorCode, LifecycleBackend, LifecycleError, Scope,
    SqliteBackend, SystemClock,
};
use std::collections::BTreeSet;
use std::path::PathBuf;
use std::process::{Command, Stdio};

const WORKERS: usize = 8;
const CYCLES: usize = 40;
const DB_ENV: &str = "SEOCHO_LIFECYCLE_MP_DB";
const ID_ENV: &str = "SEOCHO_LIFECYCLE_MP_WORKER";

fn worker(db: PathBuf, id: usize) -> TestResult {
    let mut b = SqliteBackend::open(&db, SystemClock)?;
    let scope = Scope::new("ws", "pkg")?;
    let purpose = format!("p{}", id % 4);
    let owner = format!("worker-{id}");
    for i in 0..CYCLES {
        if id == 0 && i % 5 == 0 {
            // One worker also swaps the pointer; CAS races with nobody, but
            // its tokens share the same counter.
            loop {
                let cur = b.pointer(&scope)?.ok_or("pointer")?;
                match b.activate(&ActivateRequest {
                    scope: &scope,
                    version: "mp",
                    fingerprint: if i % 2 == 0 { FP } else { FP2 },
                    expected: Some((cur.generation, cur.epoch)),
                    min_token: None,
                }) {
                    Ok(a) => {
                        println!("MPTOKEN {}", a.fencing_token.get());
                        break;
                    }
                    Err(e) if e.code() == ErrorCode::CasMismatch => continue,
                    Err(e) => return Err(e.into()),
                }
            }
        }
        // Two workers share each purpose, so LeaseHeld contention is real.
        let grant = loop {
            match b.acquire(&AcquireRequest {
                scope: &scope,
                purpose: &purpose,
                owner: &owner,
                ttl_ms: 30_000,
            }) {
                Ok(g) => break g,
                Err(LifecycleError::LeaseHeld { .. } | LifecycleError::Busy) => {
                    std::thread::yield_now();
                }
                Err(e) => return Err(e.into()),
            }
        };
        println!("MPTOKEN {}", grant.fencing_token.get());
        b.release(&grant.lease_id, &owner)?;
    }
    Ok(())
}

#[test]
fn worker_process() -> TestResult {
    let (Ok(db), Ok(id)) = (std::env::var(DB_ENV), std::env::var(ID_ENV)) else {
        return Ok(());
    };
    worker(PathBuf::from(db), id.parse()?)
}

#[test]
fn eight_processes_never_share_a_token() -> TestResult {
    let dir = fast_temp_dir("mp")?;
    let db = dir.join("state.sqlite");
    let scope = Scope::new("ws", "pkg")?;
    {
        let mut b = SqliteBackend::create(&db, SystemClock)?;
        b.activate(&ActivateRequest {
            scope: &scope,
            version: "0",
            fingerprint: FP,
            expected: None,
            min_token: None,
        })?;
    }
    let exe = std::env::current_exe()?;
    let mut children = Vec::new();
    for id in 0..WORKERS {
        let child = Command::new(&exe)
            .args(["--exact", "worker_process", "--nocapture", "--test-threads=1"])
            .env(DB_ENV, &db)
            .env(ID_ENV, id.to_string())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        children.push(child);
    }
    let mut all = Vec::new();
    for (id, child) in children.into_iter().enumerate() {
        let out = child.wait_with_output()?;
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success(),
            "worker {id} failed\n{stdout}\n{}",
            String::from_utf8_lossy(&out.stderr)
        );
        // The harness prints "test worker_process ... " without a newline
        // before the first captured line, so search within lines.
        let mine: Vec<u64> = stdout
            .lines()
            .filter_map(|l| l.find("MPTOKEN ").map(|i| &l[i + "MPTOKEN ".len()..]))
            .map(|t| t.trim().parse::<u64>())
            .collect::<Result<_, _>>()?;
        let expected = CYCLES + if id == 0 { CYCLES / 5 } else { 0 };
        assert_eq!(mine.len(), expected, "worker {id} token count");
        assert!(mine.windows(2).all(|w| w[0] < w[1]), "worker {id} not increasing: {mine:?}");
        all.extend(mine);
    }
    let unique: BTreeSet<u64> = all.iter().copied().collect();
    assert_eq!(unique.len(), all.len(), "a token was issued twice");
    // Activation token 1 plus every worker token, gap-free.
    let max = unique.iter().next_back().copied().unwrap_or(0);
    assert_eq!(max, u64::try_from(all.len())? + 1);
    let b = SqliteBackend::open(&db, SystemClock)?;
    assert_eq!(b.high_water(&scope)?.map(|t| t.get()), Some(max));
    let report = audit::audit_path(&db, 0)?;
    assert_eq!(report.violations(), 0, "{:?}", report.findings);
    Ok(())
}
