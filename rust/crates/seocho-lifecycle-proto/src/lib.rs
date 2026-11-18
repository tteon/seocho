//! Wire types for the SEOCHO lifecycle authority, protocol `seocho.lifecycle.v1`.
//!
//! This crate is deliberately tiny: it is the only lifecycle crate that a
//! DozerDB writer (`seochod`) may depend on (design invariant I11). It carries
//! no way to *mint* a fencing token. A token observed on the wire or read from
//! a database is an [`ObservedToken`]: it can be compared, never converted into
//! the authority-only `seocho_lifecycle::FencingToken`.
//!
//! Design: `docs/design/rust-lifecycle-authority.md` §4.3.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Protocol identifier reported by `hello`.
pub const PROTOCOL: &str = "seocho.lifecycle.v1";

/// The only lease purpose that may authorise a canonical projection write.
pub const PROJECTION_PURPOSE: &str = "projection";

/// Stable machine-readable error codes (design §4.3).
///
/// The serialized form (`snake_case`) is part of the wire contract and must
/// not change within protocol major version 1.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    LeaseAbsent,
    LeaseExpired,
    LeaseHeld,
    OwnerMismatch,
    NotProjectionPurpose,
    StalePointer,
    Fenced,
    CasMismatch,
    TtlOutOfRange,
    SchemaMismatch,
    SchemaTampered,
    Busy,
    Internal,
    /// An op arrived on a socket that does not dispatch it, or a malformed
    /// request. Used by §4.3 "socket별 dispatch" but missing from the §4.3
    /// code list; added here.
    InvalidRequest,
    /// `acquire` on a scope that has no active ontology pointer. The legacy
    /// Python store raises `ValueError("no active ontology ...")` for this;
    /// the §4.3 code list has no code for it, so one is added.
    NoActive,
}

impl ErrorCode {
    /// The wire spelling, e.g. `"lease_held"`.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::LeaseAbsent => "lease_absent",
            Self::LeaseExpired => "lease_expired",
            Self::LeaseHeld => "lease_held",
            Self::OwnerMismatch => "owner_mismatch",
            Self::NotProjectionPurpose => "not_projection_purpose",
            Self::StalePointer => "stale_pointer",
            Self::Fenced => "fenced",
            Self::CasMismatch => "cas_mismatch",
            Self::TtlOutOfRange => "ttl_out_of_range",
            Self::SchemaMismatch => "schema_mismatch",
            Self::SchemaTampered => "schema_tampered",
            Self::Busy => "busy",
            Self::Internal => "internal",
            Self::InvalidRequest => "invalid_request",
            Self::NoActive => "no_active",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A fencing token as seen by a verifier: read from a database row or from a
/// capability on the wire.
///
/// It supports ordering only. There is intentionally no conversion into the
/// authority's `FencingToken`, so holding an `ObservedToken` never lets a
/// writer issue one.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ObservedToken(u64);

impl ObservedToken {
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for ObservedToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}

/// Projection capability handed to a writer.
///
/// The first five fields are exactly the Python admission dict
/// `{lease_id, fingerprint, generation, epoch, fencing_token}`
/// (pre-#783 src/seocho/ontology/lifecycle.py:257, design G4). `purpose` and
/// `expires_at_ms` are the `admit` additions from §4.3 and are omitted from
/// the JSON when absent so the legacy 5-field shape round-trips unchanged.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Capability {
    pub lease_id: String,
    pub fingerprint: String,
    pub generation: i64,
    pub epoch: i64,
    pub fencing_token: ObservedToken,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub purpose: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at_ms: Option<u64>,
}

impl Capability {
    /// True when the capability names the projection purpose. A capability
    /// without `purpose` (legacy 5-field dict) is *not* a projection
    /// capability: fail closed.
    pub fn is_projection(&self) -> bool {
        self.purpose.as_deref() == Some(PROJECTION_PURPOSE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_code_wire_spelling_matches_as_str() -> Result<(), serde_json::Error> {
        let all = [
            ErrorCode::LeaseAbsent,
            ErrorCode::LeaseExpired,
            ErrorCode::LeaseHeld,
            ErrorCode::OwnerMismatch,
            ErrorCode::NotProjectionPurpose,
            ErrorCode::StalePointer,
            ErrorCode::Fenced,
            ErrorCode::CasMismatch,
            ErrorCode::TtlOutOfRange,
            ErrorCode::SchemaMismatch,
            ErrorCode::SchemaTampered,
            ErrorCode::Busy,
            ErrorCode::Internal,
            ErrorCode::InvalidRequest,
            ErrorCode::NoActive,
        ];
        for code in all {
            assert_eq!(serde_json::to_string(&code)?, format!("\"{}\"", code.as_str()));
        }
        Ok(())
    }

    #[test]
    fn observed_token_is_transparent_and_ordered() -> Result<(), serde_json::Error> {
        let a: ObservedToken = serde_json::from_str("7")?;
        assert_eq!(a.get(), 7);
        assert!(ObservedToken::new(8) > a);
        assert!(serde_json::from_str::<ObservedToken>("-1").is_err());
        Ok(())
    }
}
