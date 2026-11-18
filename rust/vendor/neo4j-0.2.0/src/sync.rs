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

use std::cell::RefCell;
use std::fmt::Debug;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use parking_lot::{RwLock, RwLockReadGuard, RwLockWriteGuard};

use crate::{Neo4jError, Result};

#[derive(Debug)]
pub(crate) struct MostlyRLock<T: Debug> {
    inner: RwLock<T>,
    updating: AtomicBool,
}

#[allow(dead_code)] // unused methods for symetry between read and write ops
impl<T: Debug> MostlyRLock<T> {
    pub(crate) fn new(inner: T) -> Self {
        let inner = RwLock::new(inner);
        Self {
            inner,
            updating: AtomicBool::new(false),
        }
    }

    pub(crate) fn read(&self) -> RwLockReadGuard<T> {
        self.inner.read()
    }

    pub(crate) fn try_read_until(
        &self,
        timeout: Option<Instant>,
        during: &'static str,
    ) -> Result<RwLockReadGuard<T>> {
        let Some(timeout) = timeout else {
            return Ok(self.read());
        };
        match self.inner.try_read_until(timeout) {
            Some(r_lock) => Ok(r_lock),
            None => Err(Neo4jError::connection_acquisition_timeout(during)),
        }
    }

    pub(crate) fn update<'a, UPDATE: FnMut(RwLockWriteGuard<'a, T>) -> Result<()>>(
        &'a self,
        mut updater: UPDATE,
    ) -> Result<RwLockReadGuard<'a, T>> {
        let done = RefCell::new(false);
        self.maybe_write(
            {
                let done = &done;
                |_| !*done.borrow()
            },
            {
                let done = &done;
                |lock| {
                    *done.borrow_mut() = true;
                    updater(lock)
                }
            },
        )
    }

    pub(crate) fn try_update_until<'a, UPDATE: FnMut(RwLockWriteGuard<'a, T>) -> Result<()>>(
        &'a self,
        timeout: Option<Instant>,
        during: &'static str,
        mut updater: UPDATE,
    ) -> Result<RwLockReadGuard<'a, T>> {
        let done = RefCell::new(false);
        self.try_maybe_write_until(
            timeout,
            during,
            {
                let done = &done;
                |_| !*done.borrow()
            },
            {
                let done = &done;
                |lock| {
                    *done.borrow_mut() = true;
                    updater(lock)
                }
            },
        )
    }

    pub(crate) fn maybe_write<
        'a,
        CHECK: FnMut(&RwLockReadGuard<'a, T>) -> bool,
        UPDATE: FnMut(RwLockWriteGuard<'a, T>) -> Result<()>,
    >(
        &'a self,
        mut needs_update: CHECK,
        mut updater: UPDATE,
    ) -> Result<RwLockReadGuard<'a, T>> {
        loop {
            {
                let r_lock = self.inner.read();
                if !needs_update(&r_lock) {
                    return Ok(r_lock);
                }
                // avoid drowning the writer
                RwLockReadGuard::unlock_fair(r_lock);
            }
            let already_updating = self.updating.swap(true, Ordering::SeqCst);
            if !already_updating {
                let w_lock = self.inner.write();
                self.updating.store(false, Ordering::SeqCst);
                updater(w_lock)?;
                return Ok(self.inner.read());
            }
        }
    }

    pub(crate) fn try_maybe_write_until<
        'a,
        CHECK: FnMut(&RwLockReadGuard<'a, T>) -> bool,
        UPDATE: FnMut(RwLockWriteGuard<'a, T>) -> Result<()>,
    >(
        &'a self,
        timeout: Option<Instant>,
        during: &'static str,
        mut needs_update: CHECK,
        mut updater: UPDATE,
    ) -> Result<RwLockReadGuard<'a, T>> {
        let Some(timeout) = timeout else {
            return self.maybe_write(needs_update, updater);
        };
        loop {
            {
                let Some(r_lock) = self.inner.try_read_until(timeout) else {
                    return Err(Neo4jError::connection_acquisition_timeout(during));
                };
                if !needs_update(&r_lock) {
                    return Ok(r_lock);
                }
                // avoid drowning the writer
                RwLockReadGuard::unlock_fair(r_lock);
            }
            let already_updating = self.updating.swap(true, Ordering::SeqCst);
            if !already_updating {
                let maybe_w_lock = self.inner.try_write_until(timeout);
                self.updating.store(false, Ordering::SeqCst);
                let Some(w_lock) = maybe_w_lock else {
                    return Err(Neo4jError::connection_acquisition_timeout(during));
                };
                updater(w_lock)?;
                return Ok(self.inner.read());
            }
        }
    }
}
