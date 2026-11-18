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

use std::borrow::Cow;
use std::collections::{HashSet, VecDeque};
use std::ops::{Deref, DerefMut};
use std::sync::Arc;
use std::time::Duration;

use log::{info, log_enabled, Level};
use parking_lot::lock_api::MutexGuard;
use parking_lot::{Condvar, Mutex, RawMutex};

use super::super::bolt::message_parameters::{HelloParameters, ReauthParameters};
use super::super::bolt::{self, AuthResetHandle, OnServerErrorCb, TcpBolt, TcpRW};
use super::super::pool::ssr_tracker::SsrTracker;
use super::PoolConfig;
use crate::address_::Address;
use crate::driver::config::auth::{auth_managers, AuthToken};
use crate::driver::config::AuthConfig;
use crate::error_::{Neo4jError, Result};
use crate::time::Instant;

type PoolElement = TcpBolt;

#[derive(Debug)]
pub(crate) struct InnerPool {
    address: Arc<Address>,
    config: Arc<PoolConfig>,
    ssr_tracker: Arc<SsrTracker>,
    synced: Mutex<InnerPoolSyncedData>,
    made_room_condition: Condvar,
}

#[derive(Debug)]
struct InnerPoolSyncedData {
    raw_pool: VecDeque<PoolElement>,
    reservations: usize,
    borrowed: usize,
    borrowed_auth_reset: HashSet<AuthResetHandle>,
}

impl InnerPool {
    fn new(address: Arc<Address>, config: Arc<PoolConfig>, ssr_tracker: Arc<SsrTracker>) -> Self {
        let raw_pool = VecDeque::with_capacity(config.max_connection_pool_size);
        // allow: `AuthResetHandle::hash` hashes by pointer address, not value
        #[allow(clippy::mutable_key_type)]
        let borrowed_auth_reset = HashSet::with_capacity(config.max_connection_pool_size);
        let synced = Mutex::new(InnerPoolSyncedData {
            raw_pool,
            reservations: 0,
            borrowed: 0,
            borrowed_auth_reset,
        });
        Self {
            address,
            config,
            ssr_tracker,
            synced,
            made_room_condition: Condvar::new(),
        }
    }

    fn acquire_new(
        &self,
        deadline: Option<Instant>,
        session_auth: SessionAuth,
    ) -> Result<PoolElement> {
        let connection = self.open_new(deadline, session_auth);
        let mut sync = self.synced.lock();
        sync.reservations -= 1;
        let connection = connection?;
        sync.borrowed += 1;
        assert!(sync
            .borrowed_auth_reset
            .insert(connection.auth_reset_handler()));
        Ok(connection)
    }

    fn open_new(
        &self,
        deadline: Option<Instant>,
        session_auth: SessionAuth,
    ) -> Result<PoolElement> {
        let auth = match session_auth {
            SessionAuth::None => match &self.config.auth {
                AuthConfig::Static(auth) => Cow::Borrowed(auth),
                AuthConfig::Manager(manager) => {
                    Cow::Owned(auth_managers::get_auth(manager.as_ref())?)
                }
            },
            SessionAuth::Reauth(auth) => Cow::Borrowed(auth),
            SessionAuth::Forced(auth) => Cow::Borrowed(auth),
        };

        let address = Arc::clone(&self.address);
        let mut connection = self.open_socket(address, deadline)?;
        connection.set_telemetry_enabled(self.config.telemetry);

        connection.hello(HelloParameters::new(
            &self.config.user_agent,
            auth.as_ref(),
            self.config.routing_context.as_ref(),
            &self.config.notification_filters,
        ))?;
        let is_session_auth = !matches!(session_auth, SessionAuth::None);
        let supports_reauth = connection.supports_reauth();
        if is_session_auth || supports_reauth {
            if log_enabled!(Level::Debug) && is_session_auth && !supports_reauth {
                connection.debug_log(|| "lacking session auth support".into());
            };
            connection.reauth(ReauthParameters::new(auth.as_ref(), is_session_auth))?;
        }
        connection.write_all(deadline)?;
        connection.read_all(deadline, None)?;
        self.ssr_tracker.add_connection(&connection);
        Ok(connection)
    }

    fn open_socket(&self, address: Arc<Address>, deadline: Option<Instant>) -> Result<TcpBolt> {
        let mut last_err = None;
        for address in address.fully_resolve(self.config.resolver.as_deref())? {
            last_err = match address {
                Ok(address) => {
                    match bolt::open(
                        bolt::TcpConnector,
                        address,
                        deadline,
                        self.config.connection_timeout,
                        self.config.keep_alive,
                        self.config.tls_config.clone(),
                    ) {
                        Ok(connection) => return Ok(connection),
                        Err(err) => {
                            info!("failed to open connection: {}", err);
                            Some(Err(err))
                        }
                    }
                }
                Err(err) => {
                    info!("failed to resolve address: {}", err);
                    Some(Err(Neo4jError::connect_error(err)))
                }
            }
        }
        last_err.expect("resolve_address_fully returned empty iterator")
    }

    #[cfg(feature = "_internal_testkit_backend")]
    pub(crate) fn address(&self) -> &Arc<Address> {
        &self.address
    }
}

#[derive(Debug)]
pub(crate) struct SimplePool(Arc<InnerPool>);

impl SimplePool {
    pub(crate) fn new(
        address: Arc<Address>,
        config: Arc<PoolConfig>,
        ssr_tracker: Arc<SsrTracker>,
    ) -> Self {
        Self(Arc::new(InnerPool::new(address, config, ssr_tracker)))
    }

    pub(crate) fn acquire(&self, deadline: Option<Instant>) -> Result<UnpreparedSinglePooledBolt> {
        {
            let mut synced = self.synced.lock();
            loop {
                if let Some(connection) = self.acquire_existing(&mut synced) {
                    return Ok(UnpreparedSinglePooledBolt::new(
                        Some(connection),
                        Arc::clone(&self.0),
                    ));
                }
                if self.has_room(&synced) {
                    synced.reservations += 1;
                    break;
                } else {
                    self.wait_for_room(deadline, &mut synced)?;
                }
            }
        }
        Ok(UnpreparedSinglePooledBolt::new(None, Arc::clone(&self.0)))
    }

    fn wait_for_room(
        &self,
        deadline: Option<Instant>,
        synced: &mut MutexGuard<RawMutex, InnerPoolSyncedData>,
    ) -> Result<()> {
        match deadline {
            None => self.made_room_condition.wait(synced),
            Some(deadline) => {
                if self
                    .made_room_condition
                    .wait_until(synced, deadline.raw())
                    .timed_out()
                {
                    return Err(Neo4jError::connection_acquisition_timeout(
                        "waiting for room in the connection pool",
                    ));
                }
            }
        }
        Ok(())
    }

    pub(crate) fn acquire_no_wait(&self) -> Option<UnpreparedSinglePooledBolt> {
        {
            let mut synced = self.synced.lock();
            if let Some(connection) = self.acquire_existing(&mut synced) {
                return Some(UnpreparedSinglePooledBolt::new(
                    Some(connection),
                    Arc::clone(&self.0),
                ));
            }
            if self.has_room(&synced) {
                synced.reservations += 1;
            } else {
                return None;
            }
        }
        Some(UnpreparedSinglePooledBolt::new(None, Arc::clone(&self.0)))
    }

    pub(crate) fn in_use(&self) -> usize {
        let synced = self.synced.lock();
        synced.borrowed + synced.reservations
    }

    pub(crate) fn reset_all_auth(&self) {
        let synced = self.synced.lock();
        for connection in &synced.raw_pool {
            connection.auth_reset_handler().mark_for_reset();
        }
        for reset_handle in &synced.borrowed_auth_reset {
            reset_handle.mark_for_reset();
        }
    }

    fn has_room(&self, synced: &InnerPoolSyncedData) -> bool {
        synced.raw_pool.len() + synced.borrowed + synced.reservations
            < self.config.max_connection_pool_size
    }

    fn acquire_existing(&self, synced: &mut InnerPoolSyncedData) -> Option<PoolElement> {
        let connection = synced.raw_pool.pop_front();
        if let Some(connection) = connection.as_ref() {
            synced.borrowed += 1;
            assert!(synced
                .borrowed_auth_reset
                .insert(connection.auth_reset_handler()));
        }
        connection
    }

    fn release(inner_pool: &Arc<InnerPool>, mut connection: PoolElement) {
        if connection.needs_reset() {
            let res = connection
                .reset()
                .and_then(|_| connection.write_all(None))
                .and_then(|_| connection.read_all(None, None));
            if res.is_err() {
                info!("ignoring failure during reset, dropping connection");
            }
        }
        let mut lock = inner_pool.synced.lock();
        assert!(lock
            .borrowed_auth_reset
            .remove(&connection.auth_reset_handler()));
        lock.borrowed -= 1;
        if connection.closed() {
            inner_pool.made_room_condition.notify_one();
            drop(lock);
            inner_pool.ssr_tracker.remove_connection(&connection);
        } else {
            lock.raw_pool.push_back(connection);
            inner_pool.made_room_condition.notify_one();
        }
    }

    #[cfg(feature = "_internal_testkit_backend")]
    pub(crate) fn get_metrics(&self) -> ConnectionPoolMetrics {
        let lock = self.synced.lock();
        ConnectionPoolMetrics {
            in_use: lock.borrowed + lock.reservations,
            idle: lock.raw_pool.len(),
        }
    }
}

impl Deref for SimplePool {
    type Target = InnerPool;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

#[derive(Debug)]
pub(crate) struct UnpreparedSinglePooledBolt {
    pool: Arc<InnerPool>,
    bolt: Option<PoolElement>,
}

impl UnpreparedSinglePooledBolt {
    fn new(bolt: Option<PoolElement>, pool: Arc<InnerPool>) -> Self {
        Self { pool, bolt }
    }

    pub(crate) fn prepare(
        mut self,
        deadline: Option<Instant>,
        idle_time_before_connection_test: Option<Duration>,
        session_auth: SessionAuth,
        on_server_error: OnServerErrorCb<TcpRW>,
    ) -> Result<Option<SinglePooledBolt>> {
        let bolt = self.bolt.take();
        let pool = Arc::clone(&self.pool);
        match bolt {
            None => {
                let connection = self.pool.acquire_new(deadline, session_auth)?;
                Ok(Some(SinglePooledBolt::new(connection, pool)))
            }
            Some(mut connection) => {
                // room for health check etc. (return None on failed health check)
                if let Some(max_lifetime) = self.pool.config.max_connection_lifetime {
                    if connection.is_older_than(max_lifetime) {
                        connection.debug_log(|| String::from("connection reached max lifetime"));
                        connection.close();
                        self.bolt = Some(connection);
                        return Ok(None);
                    }
                }
                match idle_time_before_connection_test {
                    None => {}
                    Some(timeout) => {
                        if let Err(err) = Self::liveness_check(
                            &mut connection,
                            timeout,
                            deadline,
                            on_server_error,
                        ) {
                            connection.debug_log(|| format!("liveness check failed: {}", err));
                            self.bolt = Some(connection);
                            return Ok(None);
                        }
                    }
                }
                match self.reauth(&mut connection, session_auth) {
                    Ok(Some(())) => Ok(Some(SinglePooledBolt {
                        pool,
                        bolt: Some(connection),
                    })),
                    Ok(None) => {
                        SimplePool::release(&self.pool, connection);
                        Ok(None)
                    }
                    Err(e) => {
                        SimplePool::release(&self.pool, connection);
                        Err(e)
                    }
                }
            }
        }
    }

    fn reauth(
        &self,
        connection: &mut PoolElement,
        session_auth: SessionAuth,
    ) -> Result<Option<()>> {
        match session_auth {
            SessionAuth::None => {
                let new_auth = match &self.pool.config.auth {
                    AuthConfig::Static(auth) => Cow::Borrowed(auth),
                    AuthConfig::Manager(manager) => {
                        Cow::Owned(auth_managers::get_auth(manager.as_ref())?)
                    }
                };
                let reauth_params = ReauthParameters::new(new_auth.as_ref(), false);
                if connection.needs_reauth(reauth_params) {
                    if !connection.supports_reauth() {
                        connection.debug_log(|| {
                            "backwards compatible auth token refresh: purge connection".into()
                        });
                        connection.close();
                        return Ok(None);
                    }
                    connection.reauth(reauth_params)?;
                }
            }
            SessionAuth::Reauth(auth) => {
                let reauth_params = ReauthParameters::new(auth, true);
                if connection.needs_reauth(reauth_params) {
                    connection.reauth(reauth_params)?;
                }
            }
            SessionAuth::Forced(auth) => {
                connection.reauth(ReauthParameters::new(auth, true))?;
            }
        }
        Ok(Some(()))
    }

    fn liveness_check(
        connection: &mut PoolElement,
        timeout: Duration,
        deadline: Option<Instant>,
        on_server_error: OnServerErrorCb<TcpRW>,
    ) -> Result<()> {
        if connection.is_idle_for(timeout) {
            connection.debug_log(|| String::from("liveness check"));
            connection.reset()?;
            connection.write_all(None)?;
            connection.read_all(deadline, on_server_error)?;
        }
        Ok(())
    }
}

impl Drop for UnpreparedSinglePooledBolt {
    fn drop(&mut self) {
        if self.bolt.is_none() {
            return;
        }
        let bolt = self.bolt.take().expect("checked above that bolt is Some");
        SimplePool::release(&self.pool, bolt);
    }
}

#[derive(Debug, Copy, Clone)]
pub(crate) enum SessionAuth<'a> {
    None,
    Reauth(&'a Arc<AuthToken>),
    Forced(&'a Arc<AuthToken>),
}

#[derive(Debug)]
pub(crate) struct SinglePooledBolt {
    pool: Arc<InnerPool>,
    bolt: Option<PoolElement>,
}

impl SinglePooledBolt {
    fn new(bolt: PoolElement, pool: Arc<InnerPool>) -> Self {
        Self {
            pool,
            bolt: Some(bolt),
        }
    }
}

impl Drop for SinglePooledBolt {
    fn drop(&mut self) {
        let bolt = self
            .bolt
            .take()
            .expect("bolt option should be Some from init to drop");
        SimplePool::release(&self.pool, bolt);
    }
}

impl Deref for SinglePooledBolt {
    type Target = TcpBolt;

    fn deref(&self) -> &Self::Target {
        self.bolt
            .as_ref()
            .expect("bolt option should be Some from init to drop")
    }
}

impl DerefMut for SinglePooledBolt {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.bolt
            .as_mut()
            .expect("bolt option should be Some from init to drop")
    }
}

#[cfg(feature = "_internal_testkit_backend")]
#[derive(Debug, Copy, Clone)]
pub struct ConnectionPoolMetrics {
    pub in_use: usize,
    pub idle: usize,
}
