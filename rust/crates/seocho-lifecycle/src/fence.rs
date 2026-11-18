//! Fencing tokens and the single place that mints them.
//!
//! [`FencingToken`] has a private field, no public constructor, no `From`
//! impls, no `Default` and no `Deserialize`. The only expression in the whole
//! code base that builds one is inside [`WriteTxn::mint`], which lives in this
//! module so that no other module of this crate can bypass it either.

use crate::clock::{Clock, Millis};
use crate::error::LifecycleError;
use crate::schema::last_wall_ms;
use rusqlite::{OptionalExtension, Transaction, TransactionBehavior};
use seocho_lifecycle_proto::ObservedToken;
use std::fmt;
use std::num::NonZeroU64;

/// A `(workspace_id, package_id)` token scope. Tokens are only comparable
/// within one scope (design I10).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Scope {
    workspace_id: String,
    package_id: String,
}

impl Scope {
    pub fn new(workspace_id: &str, package_id: &str) -> Result<Self, LifecycleError> {
        if workspace_id.is_empty() || package_id.is_empty() {
            return Err(LifecycleError::InvalidRequest(
                "workspace_id and package_id must be non-empty".into(),
            ));
        }
        Ok(Self {
            workspace_id: workspace_id.to_string(),
            package_id: package_id.to_string(),
        })
    }

    pub fn workspace_id(&self) -> &str {
        &self.workspace_id
    }

    pub fn package_id(&self) -> &str {
        &self.package_id
    }
}

impl fmt::Display for Scope {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}/{}", self.workspace_id, self.package_id)
    }
}

/// A fencing token minted by the authority. Strictly increasing and never
/// reused per [`Scope`] (design I1).
///
/// It cannot be built outside this crate:
///
/// ```compile_fail,E0603
/// use std::num::NonZeroU64;
/// let _t = seocho_lifecycle::FencingToken(NonZeroU64::MIN);
/// ```
///
/// ```compile_fail,E0277
/// let _t: seocho_lifecycle::FencingToken = 7u64.into();
/// ```
///
/// ```compile_fail,E0277
/// let _t: seocho_lifecycle::FencingToken = Default::default();
/// ```
///
/// ```compile_fail,E0277
/// let o = seocho_lifecycle_proto::ObservedToken::new(7);
/// let _t: seocho_lifecycle::FencingToken = o.into();
/// ```
///
/// ```compile_fail,E0277
/// let _t: Result<seocho_lifecycle::FencingToken, _> = serde_json::from_str("7");
/// ```
///
/// The same items do compile for types that allow it, which shows the
/// failures above are about `FencingToken` and not about the setup:
///
/// ```
/// use std::num::NonZeroU64;
/// let o = seocho_lifecycle_proto::ObservedToken::new(7);
/// let n: Result<u64, _> = serde_json::from_str("7");
/// let _x: Option<NonZeroU64> = NonZeroU64::new(o.get());
/// assert!(n.is_ok());
/// fn takes(t: seocho_lifecycle::FencingToken) -> u64 { t.get() }
/// let _ = takes;
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct FencingToken(NonZeroU64);

impl FencingToken {
    pub const fn get(self) -> u64 {
        self.0.get()
    }

    /// The verifier-side view of this token.
    pub const fn observed(self) -> ObservedToken {
        ObservedToken::new(self.0.get())
    }
}

impl fmt::Display for FencingToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}

/// What a token is minted for. Recorded in
/// `ontology_fence_high_water.minted_for`, which triggers T3/T5/T6 bind to
/// the row that is allowed to carry the token.
#[derive(Clone, Copy, Debug)]
pub(crate) enum MintFor<'a> {
    /// `minted_for = <lease_id>`.
    Lease(&'a str),
    /// `minted_for = 'activate:<generation>:<epoch>'` of the pointer row that
    /// will carry the token.
    Activation { generation: i64, epoch: i64 },
}

impl MintFor<'_> {
    fn label(&self) -> Result<String, LifecycleError> {
        match self {
            Self::Lease(id) => {
                let lower = id.to_ascii_lowercase();
                if id.is_empty() || lower.starts_with("activate:") || lower.starts_with("consumed:") {
                    return Err(LifecycleError::InvalidRequest(format!(
                        "invalid lease id {id:?}"
                    )));
                }
                Ok((*id).to_string())
            }
            Self::Activation { generation, epoch } => Ok(format!("activate:{generation}:{epoch}")),
        }
    }
}

/// An open `BEGIN IMMEDIATE` transaction on the control DB, with the
/// authority's clamped clock reading taken inside it.
pub(crate) struct WriteTxn<'c> {
    tx: Transaction<'c>,
    now: Millis,
}

fn to_i64(v: u64, what: &str) -> Result<i64, LifecycleError> {
    i64::try_from(v).map_err(|_| LifecycleError::Internal(format!("{what} {v} exceeds i64")))
}

impl<'c> WriteTxn<'c> {
    /// `BEGIN IMMEDIATE`, then `now = max(clock, lifecycle_meta.last_wall_ms)`
    /// persisted back (design I9).
    pub(crate) fn begin<C: Clock>(
        conn: &'c mut rusqlite::Connection,
        clock: &C,
    ) -> Result<Self, LifecycleError> {
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let last = last_wall_ms(&tx)?;
        let now = Millis::new(clock.now_ms().get().max(last));
        let changed = tx.execute(
            "UPDATE lifecycle_meta SET value=?1 WHERE key='last_wall_ms'",
            [now.get().to_string()],
        )?;
        if changed != 1 {
            return Err(LifecycleError::SchemaTampered {
                expected: "one lifecycle_meta.last_wall_ms row".into(),
                found: format!("{changed} rows updated"),
            });
        }
        Ok(Self { tx, now })
    }

    pub(crate) fn now(&self) -> Millis {
        self.now
    }

    pub(crate) fn now_i64(&self) -> Result<i64, LifecycleError> {
        to_i64(self.now.get(), "clock")
    }

    pub(crate) fn conn(&self) -> &rusqlite::Connection {
        &self.tx
    }

    pub(crate) fn commit(self) -> Result<(), LifecycleError> {
        self.tx.commit()?;
        Ok(())
    }

    /// Mint the next token for `scope`: `max(high_water, MAX(live lease),
    /// MAX(tombstone), pointer) + 1`, then write the high-water (UPDATE, or INSERT for a new scope) with
    /// `minted_for`. This is the only token source (design §4.3, I1).
    ///
    /// Including the tombstone maximum is belt and braces: in a healthy v2 DB
    /// the high-water already dominates it.
    pub(crate) fn mint(
        &mut self,
        scope: &Scope,
        minted_for: MintFor<'_>,
    ) -> Result<FencingToken, LifecycleError> {
        let label = minted_for.label()?;
        let ws = scope.workspace_id();
        let pkg = scope.package_id();
        let high_water: i64 = self
            .tx
            .query_row(
                "SELECT high_water FROM ontology_fence_high_water WHERE workspace_id=?1 AND package_id=?2",
                (ws, pkg),
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(0);
        let live: i64 = self.tx.query_row(
            "SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease WHERE workspace_id=?1 AND package_id=?2",
            (ws, pkg),
            |r| r.get(0),
        )?;
        let tomb: i64 = self.tx.query_row(
            "SELECT COALESCE(MAX(fencing_token), 0) FROM ontology_lease_tombstone WHERE workspace_id=?1 AND package_id=?2",
            (ws, pkg),
            |r| r.get(0),
        )?;
        let pointer: i64 = self
            .tx
            .query_row(
                "SELECT COALESCE(fencing_token, 0) FROM active_ontology WHERE workspace_id=?1 AND package_id=?2",
                (ws, pkg),
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(0);
        let floor = high_water.max(live).max(tomb).max(pointer).max(0);
        let next = floor
            .checked_add(1)
            .ok_or_else(|| LifecycleError::Internal(format!("fencing token overflow in {scope}")))?;
        let value = u64::try_from(next)
            .ok()
            .and_then(NonZeroU64::new)
            .ok_or_else(|| LifecycleError::Internal(format!("non-positive token {next}")))?;
        // UPDATE-then-INSERT, never UPSERT/REPLACE: the T1 insert trigger
        // refuses any INSERT over an existing high-water row.
        let updated = self.tx.execute(
            "UPDATE ontology_fence_high_water SET high_water=?3, minted_for=?4 \
             WHERE workspace_id=?1 AND package_id=?2",
            (ws, pkg, next, &label),
        )?;
        if updated == 0 {
            self.tx.execute(
                "INSERT INTO ontology_fence_high_water (workspace_id, package_id, high_water, minted_for) \
                 VALUES (?1, ?2, ?3, ?4)",
                (ws, pkg, next, &label),
            )?;
        }
        Ok(FencingToken(value))
    }
}
