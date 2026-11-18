// Copyright Rouven Bauer
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Internal module to enable mocking of time.

use std::fmt::Debug;
use std::ops::{Add, AddAssign};
use std::time::{Duration, Instant as StdInstant};

#[cfg(feature = "_internal_testkit_backend")]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Instant(StdInstant);
#[cfg(not(feature = "_internal_testkit_backend"))]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Instant(StdInstant);

impl Debug for Instant {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl Add<Duration> for Instant {
    type Output = Self;

    fn add(self, other: Duration) -> Self::Output {
        Self(self.0 + other)
    }
}

impl AddAssign<Duration> for Instant {
    fn add_assign(&mut self, other: Duration) {
        self.0 += other;
    }
}

#[allow(dead_code)] // make API more consistent between feature flags
impl Instant {
    #[inline]
    pub fn new(inner: StdInstant) -> Self {
        Self(inner)
    }

    #[inline]
    pub fn unmockable_now() -> Self {
        Self(StdInstant::now())
    }

    #[inline]
    pub fn raw(&self) -> StdInstant {
        self.0
    }

    #[inline]
    pub fn saturating_duration_since(&self, earlier: Self) -> Duration {
        self.0.saturating_duration_since(earlier.0)
    }

    #[inline]
    pub fn checked_duration_since(&self, earlier: Self) -> Option<Duration> {
        self.0.checked_duration_since(earlier.0)
    }

    #[inline]
    pub fn checked_add(&self, duration: Duration) -> Option<Self> {
        self.0.checked_add(duration).map(Self)
    }

    #[inline]
    pub fn checked_sub(&self, duration: Duration) -> Option<Self> {
        self.0.checked_sub(duration).map(Self)
    }
}

#[cfg(not(feature = "_internal_testkit_backend"))]
impl Instant {
    #[inline]
    pub fn now() -> Self {
        Self(StdInstant::now())
    }

    #[inline]
    pub fn elapsed(&self) -> Duration {
        self.0.elapsed()
    }
}

#[cfg(feature = "_internal_testkit_backend")]
mod mockable_time {
    use super::*;
    use std::sync::OnceLock;

    use parking_lot::RwLock;

    static MOCKED_TIME: OnceLock<RwLock<Option<StdInstant>>> = OnceLock::new();

    #[inline]
    fn mocked_time() -> &'static RwLock<Option<StdInstant>> {
        MOCKED_TIME.get_or_init(Default::default)
    }

    impl Instant {
        pub fn now() -> Self {
            Self(mocked_time().read().unwrap_or_else(StdInstant::now))
        }

        #[inline]
        pub fn elapsed(&self) -> Duration {
            Self::now().0 - self.0
        }
    }

    pub fn freeze_time() -> StdInstant {
        let mut mocked_time = mocked_time().write();
        mocked_time.replace(StdInstant::now());
        mocked_time.unwrap()
    }

    pub fn tick(duration: Duration) -> Result<StdInstant, String> {
        let mut mocked_time = mocked_time().write();
        *mocked_time = mocked_time.map(|mocked_time| mocked_time + duration);
        mocked_time
            .as_ref()
            .copied()
            .ok_or_else(|| String::from("cannot tick time if not frozen"))
    }

    pub fn unfreeze_time() -> Result<(), String> {
        let mut mocked_time = mocked_time().write();
        mocked_time
            .take()
            .map(drop)
            .ok_or_else(|| String::from("cannot unfreeze time if not frozen"))
    }
}

#[cfg(feature = "_internal_testkit_backend")]
pub use mockable_time::*;
