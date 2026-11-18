//! Error type of the authority, mapped onto the stable wire [`ErrorCode`].

use seocho_lifecycle_proto::ErrorCode;
use std::fmt;

/// Every way an authority operation can fail.
#[derive(Debug)]
pub enum LifecycleError {
    /// No lease row with that id (released, moved to tombstone, or never existed).
    LeaseAbsent,
    /// The lease exists but has expired by the authority clock.
    LeaseExpired,
    /// A live lease for the same `(workspace, package, purpose)` exists.
    LeaseHeld {
        lease_id: String,
        owner: String,
        expires_at_ms: i64,
    },
    /// The caller is not the lease owner.
    OwnerMismatch,
    /// `acquire` on a scope without an active pointer.
    NoActive,
    /// The pointer `(generation, epoch)` CAS failed; `current` is the pointer
    /// that won, if any.
    CasMismatch { current: Option<(i64, i64)> },
    /// The authority would mint `minted`, below the caller's `min_token`
    /// assertion. Nothing was written.
    Fenced { minted: u64, min_token: u64 },
    /// `ttl_ms` is zero or above the configured maximum.
    TtlOutOfRange { ttl_ms: u64, max_ttl_ms: u64 },
    /// The database is not an exact schema v2 database (or is not a lifecycle
    /// database at all).
    SchemaMismatch(String),
    /// Schema v2 is declared but the owned DDL differs from the compiled-in
    /// DDL (for example a trigger was dropped).
    SchemaTampered { expected: String, found: String },
    /// SQLite reported `SQLITE_BUSY`/`SQLITE_LOCKED` past the busy timeout.
    Busy,
    /// A request field is invalid (empty scope component etc.).
    InvalidRequest(String),
    /// Any other storage failure, including a trigger abort.
    Sqlite(rusqlite::Error),
    /// Internal invariant failure (token overflow, randomness failure, ...).
    Internal(String),
}

impl LifecycleError {
    /// Stable wire code for this error.
    pub fn code(&self) -> ErrorCode {
        match self {
            Self::LeaseAbsent => ErrorCode::LeaseAbsent,
            Self::LeaseExpired => ErrorCode::LeaseExpired,
            Self::LeaseHeld { .. } => ErrorCode::LeaseHeld,
            Self::OwnerMismatch => ErrorCode::OwnerMismatch,
            Self::NoActive => ErrorCode::NoActive,
            Self::CasMismatch { .. } => ErrorCode::CasMismatch,
            Self::Fenced { .. } => ErrorCode::Fenced,
            Self::TtlOutOfRange { .. } => ErrorCode::TtlOutOfRange,
            Self::SchemaMismatch(_) => ErrorCode::SchemaMismatch,
            Self::SchemaTampered { .. } => ErrorCode::SchemaTampered,
            Self::Busy => ErrorCode::Busy,
            Self::InvalidRequest(_) => ErrorCode::InvalidRequest,
            Self::Sqlite(_) | Self::Internal(_) => ErrorCode::Internal,
        }
    }

    /// The trigger message if this error is a lifecycle trigger abort
    /// (`RAISE(ABORT, 'lifecycle Tn: ...')`).
    pub fn trigger_message(&self) -> Option<&str> {
        match self {
            Self::Sqlite(rusqlite::Error::SqliteFailure(e, Some(msg)))
                if e.extended_code == rusqlite::ffi::SQLITE_CONSTRAINT_TRIGGER =>
            {
                Some(msg.as_str())
            }
            _ => None,
        }
    }
}

impl fmt::Display for LifecycleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::LeaseAbsent => f.write_str("lifecycle lease is absent"),
            Self::LeaseExpired => f.write_str("lifecycle lease has expired"),
            Self::LeaseHeld {
                lease_id,
                owner,
                expires_at_ms,
            } => write!(
                f,
                "live lease held by {owner} until {expires_at_ms} (lease {lease_id})"
            ),
            Self::OwnerMismatch => f.write_str("lease is owned by another process"),
            Self::NoActive => f.write_str("no active ontology for workspace/package"),
            Self::CasMismatch { current } => match current {
                Some((g, e)) => write!(f, "pointer CAS mismatch; current generation={g} epoch={e}"),
                None => f.write_str("pointer CAS mismatch; no current pointer"),
            },
            Self::Fenced { minted, min_token } => write!(
                f,
                "fenced: authority would mint {minted}, below asserted min_token {min_token}"
            ),
            Self::TtlOutOfRange { ttl_ms, max_ttl_ms } => {
                write!(f, "ttl_ms {ttl_ms} outside 0 < ttl_ms <= {max_ttl_ms}")
            }
            Self::SchemaMismatch(msg) => write!(f, "schema mismatch: {msg}"),
            Self::SchemaTampered { expected, found } => write!(
                f,
                "schema tampered: ddl_sha256 expected {expected}, found {found}"
            ),
            Self::Busy => f.write_str("control database busy"),
            Self::InvalidRequest(msg) => write!(f, "invalid request: {msg}"),
            Self::Sqlite(e) => write!(f, "sqlite: {e}"),
            Self::Internal(msg) => write!(f, "internal: {msg}"),
        }
    }
}

impl std::error::Error for LifecycleError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Sqlite(e) => Some(e),
            _ => None,
        }
    }
}

impl From<rusqlite::Error> for LifecycleError {
    fn from(e: rusqlite::Error) -> Self {
        match e.sqlite_error_code() {
            Some(rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked) => {
                Self::Busy
            }
            _ => Self::Sqlite(e),
        }
    }
}
