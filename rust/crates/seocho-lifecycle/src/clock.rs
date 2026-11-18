//! Authority clock (design I5, I9).
//!
//! The authority is the only party that decides "now" for lease expiry. A
//! [`Clock`] supplies wall time; the backend clamps it to the persisted
//! `lifecycle_meta.last_wall_ms` on every write transaction, so the effective
//! clock never goes backwards across restarts.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// Milliseconds since the Unix epoch.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Millis(u64);

impl Millis {
    pub const fn new(ms: u64) -> Self {
        Self(ms)
    }

    pub const fn get(self) -> u64 {
        self.0
    }

    pub const fn saturating_add(self, ms: u64) -> Self {
        Self(self.0.saturating_add(ms))
    }
}

/// Source of wall time for the authority.
pub trait Clock {
    fn now_ms(&self) -> Millis;
}

/// `SystemTime`-backed clock used in production.
#[derive(Clone, Copy, Debug, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_ms(&self) -> Millis {
        let ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
            .unwrap_or(0);
        Millis(ms)
    }
}

/// Manually driven clock for tests. Clones share the same time.
#[derive(Clone, Debug, Default)]
pub struct ManualClock(Arc<AtomicU64>);

impl ManualClock {
    pub fn new(start: Millis) -> Self {
        Self(Arc::new(AtomicU64::new(start.get())))
    }

    pub fn set(&self, t: Millis) {
        self.0.store(t.get(), Ordering::SeqCst);
    }

    pub fn advance(&self, ms: u64) {
        self.0.fetch_add(ms, Ordering::SeqCst);
    }
}

impl Clock for ManualClock {
    fn now_ms(&self) -> Millis {
        Millis(self.0.load(Ordering::SeqCst))
    }
}

impl<C: Clock + ?Sized> Clock for &C {
    fn now_ms(&self) -> Millis {
        (**self).now_ms()
    }
}
