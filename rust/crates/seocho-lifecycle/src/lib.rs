//! SEOCHO ontology lifecycle authority (design
//! `docs/design/rust-lifecycle-authority.md`, PR-1 scope).
//!
//! The #783 contract, "tokens are strictly increasing and never reused per
//! `(workspace_id, package_id)`", is enforced in three layers here:
//!
//! 1. **type**: [`FencingToken`] can only be produced by the crate-private
//!    `WriteTxn::mint` (`max(high_water, live, pointer) + 1`);
//! 2. **database**: schema v2 triggers T1-T7 + M1 ([`schema::DDL_V2`]) apply to every
//!    writer in every language, so a legacy Python writer cannot mint;
//! 3. **tests**: a seeded model test, legacy-writer replay and a
//!    multi-process test (`tests/`).
//!
//! [`audit`] inspects any v0/v1/v2 control DB read-only.

pub mod audit;
mod clock;
mod error;
mod fence;
pub mod schema;
mod sqlite;

pub use clock::{Clock, ManualClock, Millis, SystemClock};
pub use error::LifecycleError;
pub use fence::{FencingToken, Scope};
pub use seocho_lifecycle_proto::{ErrorCode, ObservedToken};
pub use sqlite::{
    AcquireRequest, ActivateRequest, Activation, LeaseGrant, LifecycleBackend, PointerView,
    SqliteBackend, DEFAULT_MAX_TTL_MS,
};
