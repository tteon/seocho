//! Seeded xorshift model test (design §9 test 1, invariant I1/I2/I3/I9).
//!
//! `proptest`/`loom` are not in the offline registry, so a deterministic
//! xorshift driver runs 10k acquire/release/activate/clock/reopen operations
//! per seed against a file-backed v2 DB and checks every outcome against an
//! in-memory model. Tokens must be strictly increasing per scope, with no gap
//! from committed operations (`token == previous + 1`) and no movement from
//! rolled-back ones.

mod common;

use common::{fast_temp_dir, TestResult, FP, FP2};
use seocho_lifecycle::audit;
use seocho_lifecycle::{
    AcquireRequest, ActivateRequest, ErrorCode, LifecycleBackend, LifecycleError, ManualClock,
    Millis, Scope, SqliteBackend,
};
use std::path::Path;

const OPS: usize = 10_000;
const SCOPES: usize = 3;
const PURPOSES: [&str; 3] = ["projection", "index", "query"];

struct XorShift(u64);

impl XorShift {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

#[derive(Clone, Debug)]
struct ModelLease {
    id: String,
    owner: String,
    scope: usize,
    purpose: usize,
    expires: u64,
}

#[derive(Default, Clone)]
struct ModelScope {
    pointer: Option<(i64, i64)>,
    last_token: u64,
}

struct Model {
    scopes: Vec<ModelScope>,
    present: Vec<ModelLease>,
    known: Vec<(String, String)>,
    persisted_now: u64,
}

#[derive(Default, Debug)]
struct Stats {
    acquired: usize,
    released: usize,
    activated: usize,
    held: usize,
    absent: usize,
    owner_mismatch: usize,
    cas: usize,
    fenced: usize,
    no_active: usize,
    ttl: usize,
    reopen: usize,
}

fn code(r: &Result<impl std::fmt::Debug, LifecycleError>) -> Option<ErrorCode> {
    r.as_ref().err().map(LifecycleError::code)
}

fn check_hw(
    backend: &SqliteBackend<ManualClock>,
    scopes: &[Scope],
    model: &Model,
) -> TestResult {
    for (i, s) in scopes.iter().enumerate() {
        let hw = backend.high_water(s)?.map(|t| t.get()).unwrap_or(0);
        assert_eq!(hw, model.scopes[i].last_token, "high-water drift in {s}");
    }
    Ok(())
}

fn run_seed(seed: u64, dir: &Path) -> Result<Stats, Box<dyn std::error::Error>> {
    let db = dir.join(format!("model-{seed}.sqlite"));
    let clock = ManualClock::new(Millis::new(1_700_000_000_000));
    let mut backend = Some(SqliteBackend::create(&db, clock.clone())?);
    let scopes: Vec<Scope> = (0..SCOPES)
        .map(|i| Scope::new(&format!("ws{}", i % 2), &format!("pkg{i}")))
        .collect::<Result<_, _>>()?;
    let mut rng = XorShift(seed | 1);
    let mut m = Model {
        scopes: vec![ModelScope::default(); SCOPES],
        present: Vec::new(),
        known: Vec::new(),
        persisted_now: 0,
    };
    let mut st = Stats::default();
    let mut owner_seq = 0u64;

    for op in 0..OPS {
        let Some(b) = backend.as_mut() else {
            return Err("backend missing".into());
        };
        let r = rng.below(100);
        let si = usize::try_from(rng.below(SCOPES as u64))?;
        let scope = &scopes[si];
        let now_if_begin = clock.now_ms_value().max(m.persisted_now);
        if r < 40 {
            // acquire
            let pi = usize::try_from(rng.below(PURPOSES.len() as u64))?;
            let ttl = if rng.below(50) == 0 { 0 } else { 1 + rng.below(2000) };
            owner_seq += 1;
            let owner = format!("owner-{owner_seq}");
            let res = b.acquire(&AcquireRequest {
                scope,
                purpose: PURPOSES[pi],
                owner: &owner,
                ttl_ms: ttl,
            });
            if ttl == 0 {
                assert_eq!(code(&res), Some(ErrorCode::TtlOutOfRange), "op {op}");
                st.ttl += 1;
                continue;
            }
            let now = now_if_begin;
            if m.scopes[si].pointer.is_none() {
                assert_eq!(code(&res), Some(ErrorCode::NoActive), "op {op}");
                st.no_active += 1;
                continue;
            }
            let live_same = m
                .present
                .iter()
                .any(|l| l.scope == si && l.purpose == pi && l.expires > now);
            if live_same {
                assert_eq!(code(&res), Some(ErrorCode::LeaseHeld), "op {op}: {res:?}");
                st.held += 1;
                continue;
            }
            let grant = res.map_err(|e| format!("op {op}: unexpected acquire error {e}"))?;
            let tok = grant.fencing_token.get();
            assert_eq!(tok, m.scopes[si].last_token + 1, "op {op}: token not next in {scope}");
            assert_eq!(grant.acquired_at_ms.get(), now, "op {op}: authority clock (I9)");
            assert_eq!(grant.expires_at_ms.get(), now + ttl);
            let (g, e) = m.scopes[si].pointer.unwrap_or_default();
            assert_eq!((grant.generation, grant.epoch), (g, e));
            m.scopes[si].last_token = tok;
            m.persisted_now = now;
            m.present.retain(|l| !(l.scope == si && l.expires <= now));
            m.present.push(ModelLease {
                id: grant.lease_id.clone(),
                owner: owner.clone(),
                scope: si,
                purpose: pi,
                expires: now + ttl,
            });
            m.known.push((grant.lease_id, owner));
            if m.known.len() > 64 {
                m.known.remove(0);
            }
            st.acquired += 1;
        } else if r < 62 {
            // release
            let (id, owner) = if m.known.is_empty() || rng.below(20) == 0 {
                ("no-such-lease".to_string(), "nobody".to_string())
            } else if !m.present.is_empty() && rng.below(10) < 7 {
                let k = usize::try_from(rng.below(m.present.len() as u64))?;
                (m.present[k].id.clone(), m.present[k].owner.clone())
            } else {
                let k = usize::try_from(rng.below(m.known.len() as u64))?;
                m.known[k].clone()
            };
            let wrong_owner = rng.below(10) == 0;
            let who = if wrong_owner { "intruder".to_string() } else { owner };
            let res = b.release(&id, &who);
            let pos = m.present.iter().position(|l| l.id == id);
            match pos {
                None => {
                    assert_eq!(code(&res), Some(ErrorCode::LeaseAbsent), "op {op}");
                    st.absent += 1;
                }
                Some(p) if m.present[p].owner != who => {
                    assert_eq!(code(&res), Some(ErrorCode::OwnerMismatch), "op {op}");
                    st.owner_mismatch += 1;
                }
                Some(p) => {
                    res.map_err(|e| format!("op {op}: unexpected release error {e}"))?;
                    m.present.remove(p);
                    m.persisted_now = now_if_begin;
                    st.released += 1;
                }
            }
        } else if r < 78 {
            // activate
            let cur = m.scopes[si].pointer;
            let good = rng.below(100) < 85;
            let expected = match (cur, good) {
                (c, true) => c,
                (Some((g, e)), false) => Some((g, e + 1)),
                (None, false) => Some((0, 0)),
            };
            let last = m.scopes[si].last_token;
            let min_roll = rng.below(10);
            let min_token = match min_roll {
                0 => Some(last + 1001),
                1 => Some(last + 1),
                _ => None,
            };
            let fp = if rng.below(2) == 0 { FP } else { FP2 };
            let version = format!("v{op}");
            let res = b.activate(&ActivateRequest {
                scope,
                version: &version,
                fingerprint: fp,
                expected,
                min_token,
            });
            if expected != cur {
                assert_eq!(code(&res), Some(ErrorCode::CasMismatch), "op {op}");
                st.cas += 1;
                continue;
            }
            if min_roll == 0 {
                assert_eq!(code(&res), Some(ErrorCode::Fenced), "op {op}");
                let hw = b.high_water(scope)?.map(|t| t.get()).unwrap_or(0);
                assert_eq!(hw, last, "op {op}: fenced activate must write nothing");
                st.fenced += 1;
                continue;
            }
            let act = res.map_err(|e| format!("op {op}: unexpected activate error {e}"))?;
            assert_eq!(act.fencing_token.get(), last + 1, "op {op}: activation token");
            let next = match cur {
                None => (0, 0),
                Some((g, e)) => (g, e + 1),
            };
            assert_eq!((act.generation, act.epoch), next);
            let ptr = b.pointer(scope)?.ok_or("pointer vanished")?;
            assert_eq!(ptr.fencing_token, act.fencing_token.observed());
            m.scopes[si].pointer = Some(next);
            m.scopes[si].last_token = last + 1;
            m.persisted_now = now_if_begin;
            st.activated += 1;
        } else if r < 92 {
            clock.advance(rng.below(3000));
        } else if r < 94 {
            // Wall clock jumps backwards; the authority clock must not (I9).
            let t = clock.now_ms_value().saturating_sub(rng.below(10_000));
            clock.set(Millis::new(t));
        } else if r < 97 {
            drop(backend.take());
            backend = Some(SqliteBackend::open(&db, clock.clone())?);
            st.reopen += 1;
        } else if let Some(b) = backend.as_ref() {
            if op % 7 == 0 {
                check_hw(b, &scopes, &m)?;
            }
        }
        // I2: at most one live lease per (scope, purpose) in the model, which
        // the DB mirrored above via LeaseHeld.
        if op % 1000 == 999 {
            let report = audit::audit_path(&db, i64::try_from(m.persisted_now)?)?;
            assert_eq!(report.violations(), 0, "op {op}: {:?}", report.findings);
        }
    }
    if let Some(b) = backend.as_ref() {
        check_hw(b, &scopes, &m)?;
    }
    let report = audit::audit_path(&db, i64::try_from(m.persisted_now)?)?;
    assert_eq!(report.violations(), 0, "{:?}", report.findings);
    Ok(st)
}

trait NowValue {
    fn now_ms_value(&self) -> u64;
}

impl NowValue for ManualClock {
    fn now_ms_value(&self) -> u64 {
        use seocho_lifecycle::Clock;
        self.now_ms().get()
    }
}

#[test]
fn seeded_model_tokens_strictly_increase_per_scope() -> TestResult {
    let dir = fast_temp_dir("model")?;
    let seeds: Vec<u64> = match std::env::var("SEOCHO_MODEL_SEED") {
        Ok(v) => vec![v.parse()?],
        Err(_) => vec![0x5EED_0001, 0xC0FF_EE00, 42],
    };
    for seed in seeds {
        let st = run_seed(seed, dir.path())?;
        eprintln!("seed {seed:#x}: {st:?}");
        // The driver must actually exercise every branch.
        assert!(st.acquired > 1000, "seed {seed}: too few acquires {st:?}");
        assert!(st.released > 300 && st.activated > 500, "seed {seed}: {st:?}");
        assert!(st.held > 0 && st.absent > 0 && st.owner_mismatch > 0, "{st:?}");
        assert!(st.cas > 0 && st.fenced > 0 && st.ttl > 0 && st.reopen > 50, "{st:?}");
    }
    Ok(())
}
