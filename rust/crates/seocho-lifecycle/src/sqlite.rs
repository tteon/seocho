//! SQLite backend of the authority: acquire / release / activate.

use crate::clock::{Clock, Millis};
use crate::error::LifecycleError;
use crate::fence::{FencingToken, MintFor, Scope, WriteTxn};
use crate::schema::{self, LEASE_COLUMNS};
use rusqlite::{Connection, OptionalExtension};
use seocho_lifecycle_proto::ObservedToken;
use std::path::{Path, PathBuf};

/// Default upper bound for `ttl_ms` (design §8 open question 6: 3600 s).
pub const DEFAULT_MAX_TTL_MS: u64 = 3_600_000;

/// A lease just granted by `acquire`, carrying the freshly minted token.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LeaseGrant {
    pub lease_id: String,
    pub scope: Scope,
    pub purpose: String,
    pub owner: String,
    pub fingerprint: String,
    pub generation: i64,
    pub epoch: i64,
    pub fencing_token: FencingToken,
    pub acquired_at_ms: Millis,
    pub expires_at_ms: Millis,
}

/// A pointer just published by `activate`, carrying the minted token.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Activation {
    pub scope: Scope,
    pub version: String,
    pub fingerprint: String,
    pub generation: i64,
    pub epoch: i64,
    pub fencing_token: FencingToken,
}

/// Read-side view of the active pointer. Its token is only *observed*.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PointerView {
    pub version: String,
    pub fingerprint: String,
    pub generation: i64,
    pub epoch: i64,
    pub fencing_token: ObservedToken,
}

#[derive(Clone, Debug)]
pub struct AcquireRequest<'a> {
    pub scope: &'a Scope,
    pub purpose: &'a str,
    pub owner: &'a str,
    pub ttl_ms: u64,
}

#[derive(Clone, Debug)]
pub struct ActivateRequest<'a> {
    pub scope: &'a Scope,
    pub version: &'a str,
    pub fingerprint: &'a str,
    /// `None` for the first publish of a scope, otherwise the
    /// `(generation, epoch)` the caller read.
    pub expected: Option<(i64, i64)>,
    /// Legacy `--fencing-token` reinterpreted as an assertion: if the
    /// authority would mint less than this, the call fails `fenced` and
    /// writes nothing (design I4).
    pub min_token: Option<u64>,
}

/// The authority operations a backend provides (PR-1 subset). The design's
/// GAT-shaped `LifecycleBackend { type Txn; write(); read() }` (§4.3) would
/// expose the crate-private `WriteTxn` through a public trait; this crate
/// instead keeps transactions internal and puts the domain operations on the
/// trait, which is what a future `Authority<B, C>` dispatches to.
pub trait LifecycleBackend {
    fn acquire(&mut self, req: &AcquireRequest<'_>) -> Result<LeaseGrant, LifecycleError>;
    fn release(&mut self, lease_id: &str, owner: &str) -> Result<(), LifecycleError>;
    fn activate(&mut self, req: &ActivateRequest<'_>) -> Result<Activation, LifecycleError>;
}

/// The authority's read-write connection to a schema v2 control DB.
pub struct SqliteBackend<C: Clock> {
    conn: Connection,
    clock: C,
    path: PathBuf,
    max_ttl_ms: u64,
}

impl<C: Clock> SqliteBackend<C> {
    /// Create schema v2 at `path` (or verify an existing v2 DB) and open it.
    pub fn create(path: &Path, clock: C) -> Result<Self, LifecycleError> {
        let mut conn = Connection::open(path)?;
        schema::configure_rw(&conn)?;
        schema::apply_v2(&mut conn)?;
        Ok(Self {
            conn,
            clock,
            path: path.to_path_buf(),
            max_ttl_ms: DEFAULT_MAX_TTL_MS,
        })
    }

    /// Open an existing DB. Refuses anything but exact, untampered v2
    /// (`schema_mismatch` / `schema_tampered`).
    pub fn open(path: &Path, clock: C) -> Result<Self, LifecycleError> {
        let conn = Connection::open_with_flags(
            path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        schema::configure_rw(&conn)?;
        schema::verify_v2(&conn)?;
        Ok(Self {
            conn,
            clock,
            path: path.to_path_buf(),
            max_ttl_ms: DEFAULT_MAX_TTL_MS,
        })
    }

    pub fn with_max_ttl_ms(mut self, max_ttl_ms: u64) -> Self {
        self.max_ttl_ms = max_ttl_ms;
        self
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn clock(&self) -> &C {
        &self.clock
    }

    /// Current active pointer of `scope`.
    pub fn pointer(&self, scope: &Scope) -> Result<Option<PointerView>, LifecycleError> {
        read_pointer(&self.conn, scope)
    }

    /// Current fence high-water of `scope`.
    pub fn high_water(&self, scope: &Scope) -> Result<Option<ObservedToken>, LifecycleError> {
        let hw: Option<i64> = self
            .conn
            .query_row(
                "SELECT high_water FROM ontology_fence_high_water WHERE workspace_id=?1 AND package_id=?2",
                (scope.workspace_id(), scope.package_id()),
                |r| r.get(0),
            )
            .optional()?;
        Ok(hw.and_then(|v| u64::try_from(v).ok()).map(ObservedToken::new))
    }

    fn begin(&mut self) -> Result<WriteTxn<'_>, LifecycleError> {
        WriteTxn::begin(&mut self.conn, &self.clock)
    }
}

struct PointerRow {
    version: String,
    fingerprint: String,
    generation: i64,
    epoch: i64,
    fencing_token: i64,
}

fn read_pointer_row(conn: &Connection, scope: &Scope) -> Result<Option<PointerRow>, LifecycleError> {
    Ok(conn
        .query_row(
            "SELECT version, fingerprint, generation, epoch, fencing_token FROM active_ontology \
             WHERE workspace_id=?1 AND package_id=?2",
            (scope.workspace_id(), scope.package_id()),
            |r| {
                Ok(PointerRow {
                    version: r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                    fingerprint: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    generation: r.get::<_, Option<i64>>(2)?.unwrap_or(0),
                    epoch: r.get::<_, Option<i64>>(3)?.unwrap_or(0),
                    fencing_token: r.get::<_, Option<i64>>(4)?.unwrap_or(0),
                })
            },
        )
        .optional()?)
}

fn read_pointer(conn: &Connection, scope: &Scope) -> Result<Option<PointerView>, LifecycleError> {
    Ok(read_pointer_row(conn, scope)?.map(|p| PointerView {
        version: p.version,
        fingerprint: p.fingerprint,
        generation: p.generation,
        epoch: p.epoch,
        fencing_token: ObservedToken::new(u64::try_from(p.fencing_token).unwrap_or(0)),
    }))
}

fn to_i64(v: u64, what: &str) -> Result<i64, LifecycleError> {
    i64::try_from(v).map_err(|_| LifecycleError::Internal(format!("{what} {v} exceeds i64")))
}

/// Move every expired lease row of `scope` to the tombstone table.
fn tombstone_expired(txn: &WriteTxn<'_>, scope: &Scope) -> Result<usize, LifecycleError> {
    let now = txn.now_i64()?;
    let ws = scope.workspace_id();
    let pkg = scope.package_id();
    txn.conn().execute(
        &format!(
            "INSERT INTO ontology_lease_tombstone ({LEASE_COLUMNS}, ended_at_ms, end_reason) \
             SELECT {LEASE_COLUMNS}, ?3, 'expired' FROM ontology_lease \
             WHERE workspace_id=?1 AND package_id=?2 AND expires_at_ms <= ?3"
        ),
        (ws, pkg, now),
    )?;
    Ok(txn.conn().execute(
        "DELETE FROM ontology_lease WHERE workspace_id=?1 AND package_id=?2 AND expires_at_ms <= ?3",
        (ws, pkg, now),
    )?)
}

impl<C: Clock> LifecycleBackend for SqliteBackend<C> {
    fn acquire(&mut self, req: &AcquireRequest<'_>) -> Result<LeaseGrant, LifecycleError> {
        if req.ttl_ms == 0 || req.ttl_ms > self.max_ttl_ms {
            return Err(LifecycleError::TtlOutOfRange {
                ttl_ms: req.ttl_ms,
                max_ttl_ms: self.max_ttl_ms,
            });
        }
        if req.purpose.is_empty() || req.owner.is_empty() {
            return Err(LifecycleError::InvalidRequest(
                "purpose and owner must be non-empty".into(),
            ));
        }
        let scope = req.scope;
        let ttl_ms = req.ttl_ms;
        let mut txn = self.begin()?;
        let pointer = read_pointer_row(txn.conn(), scope)?.ok_or(LifecycleError::NoActive)?;
        tombstone_expired(&txn, scope)?;
        let held: Option<(String, String, i64)> = txn
            .conn()
            .query_row(
                "SELECT lease_id, owner, expires_at_ms FROM ontology_lease \
                 WHERE workspace_id=?1 AND package_id=?2 AND purpose=?3",
                (scope.workspace_id(), scope.package_id(), req.purpose),
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?;
        if let Some((lease_id, owner, expires_at_ms)) = held {
            return Err(LifecycleError::LeaseHeld {
                lease_id,
                owner,
                expires_at_ms,
            });
        }
        let lease_id = schema::new_uuid_v4()?;
        let token = txn.mint(scope, MintFor::Lease(&lease_id))?;
        let acquired = txn.now();
        let expires = acquired.saturating_add(ttl_ms);
        txn.conn().execute(
            &format!("INSERT INTO ontology_lease ({LEASE_COLUMNS}) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)"),
            rusqlite::params![
                lease_id,
                scope.workspace_id(),
                scope.package_id(),
                req.purpose,
                req.owner,
                pointer.fingerprint,
                pointer.generation,
                pointer.epoch,
                to_i64(token.get(), "token")?,
                to_i64(acquired.get(), "acquired_at_ms")?,
                to_i64(expires.get(), "expires_at_ms")?,
            ],
        )?;
        txn.commit()?;
        Ok(LeaseGrant {
            lease_id,
            scope: scope.clone(),
            purpose: req.purpose.to_string(),
            owner: req.owner.to_string(),
            fingerprint: pointer.fingerprint,
            generation: pointer.generation,
            epoch: pointer.epoch,
            fencing_token: token,
            acquired_at_ms: acquired,
            expires_at_ms: expires,
        })
    }

    fn release(&mut self, lease_id: &str, owner: &str) -> Result<(), LifecycleError> {
        let txn = self.begin()?;
        let row_owner: Option<String> = txn
            .conn()
            .query_row(
                "SELECT owner FROM ontology_lease WHERE lease_id=?1",
                [lease_id],
                |r| r.get(0),
            )
            .optional()?;
        match row_owner {
            None => return Err(LifecycleError::LeaseAbsent),
            Some(o) if o != owner => return Err(LifecycleError::OwnerMismatch),
            Some(_) => {}
        }
        let now = txn.now_i64()?;
        txn.conn().execute(
            &format!(
                "INSERT INTO ontology_lease_tombstone ({LEASE_COLUMNS}, ended_at_ms, end_reason) \
                 SELECT {LEASE_COLUMNS}, ?2, 'released' FROM ontology_lease WHERE lease_id=?1"
            ),
            (lease_id, now),
        )?;
        txn.conn()
            .execute("DELETE FROM ontology_lease WHERE lease_id=?1", [lease_id])?;
        txn.commit()
    }

    fn activate(&mut self, req: &ActivateRequest<'_>) -> Result<Activation, LifecycleError> {
        let scope = req.scope;
        let ws = scope.workspace_id();
        let pkg = scope.package_id();
        let mut txn = self.begin()?;
        let current = read_pointer_row(txn.conn(), scope)?;
        let (generation, epoch, is_insert) = match (&current, req.expected) {
            (None, None) => {
                let hwm: Option<i64> = txn
                    .conn()
                    .query_row(
                        "SELECT hwm FROM generation_hwm WHERE workspace_id=?1 AND package_id=?2",
                        (ws, pkg),
                        |r| r.get(0),
                    )
                    .optional()?
                    .flatten();
                let next = match hwm {
                    Some(h) => h.checked_add(1).ok_or_else(|| {
                        LifecycleError::Internal("generation overflow".into())
                    })?,
                    None => 0,
                };
                // UPDATE-then-INSERT: the T7 insert trigger refuses an
                // INSERT (and so UPSERT/REPLACE) over an existing row.
                if hwm.is_some() {
                    txn.conn().execute(
                        "UPDATE generation_hwm SET hwm=?3 WHERE workspace_id=?1 AND package_id=?2",
                        (ws, pkg, next),
                    )?;
                } else {
                    txn.conn().execute(
                        "INSERT INTO generation_hwm (workspace_id, package_id, hwm) VALUES (?1,?2,?3)",
                        (ws, pkg, next),
                    )?;
                }
                (next, 0, true)
            }
            (Some(cur), Some(exp)) if exp == (cur.generation, cur.epoch) => {
                let e = cur
                    .epoch
                    .checked_add(1)
                    .ok_or_else(|| LifecycleError::Internal("epoch overflow".into()))?;
                (cur.generation, e, false)
            }
            (Some(cur), _) => {
                return Err(LifecycleError::CasMismatch {
                    current: Some((cur.generation, cur.epoch)),
                })
            }
            (None, Some(_)) => return Err(LifecycleError::CasMismatch { current: None }),
        };
        let token = txn.mint(scope, MintFor::Activation { generation, epoch })?;
        if let Some(min_token) = req.min_token {
            if token.get() < min_token {
                // Dropping `txn` rolls back the mint and the generation bump.
                return Err(LifecycleError::Fenced {
                    minted: token.get(),
                    min_token,
                });
            }
        }
        let tok = to_i64(token.get(), "token")?;
        if is_insert {
            txn.conn().execute(
                "INSERT INTO active_ontology \
                 (workspace_id, package_id, version, fingerprint, generation, epoch, fencing_token) \
                 VALUES (?1,?2,?3,?4,?5,?6,?7)",
                (ws, pkg, req.version, req.fingerprint, generation, epoch, tok),
            )?;
        } else {
            let changed = txn.conn().execute(
                "UPDATE active_ontology SET version=?3, fingerprint=?4, epoch=?5, fencing_token=?6 \
                 WHERE workspace_id=?1 AND package_id=?2 AND generation=?7 AND epoch=?8",
                (
                    ws,
                    pkg,
                    req.version,
                    req.fingerprint,
                    epoch,
                    tok,
                    generation,
                    epoch - 1,
                ),
            )?;
            if changed != 1 {
                return Err(LifecycleError::Internal(format!(
                    "pointer CAS update touched {changed} rows"
                )));
            }
        }
        txn.commit()?;
        Ok(Activation {
            scope: scope.clone(),
            version: req.version.to_string(),
            fingerprint: req.fingerprint.to_string(),
            generation,
            epoch,
            fencing_token: token,
        })
    }
}
