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

pub(crate) mod config;
pub(crate) mod eager_result;
mod home_db_cache;
pub(crate) mod io;
pub(crate) mod record;
pub mod record_stream;
pub(crate) mod session;
pub(crate) mod summary;
pub mod transaction;

use std::borrow::Borrow;
use std::collections::HashMap;
use std::fmt::Debug;
use std::marker::PhantomData;
use std::result::Result as StdResult;
use std::sync::Arc;
use std::time::Duration;

#[cfg(feature = "_internal_testkit_backend")]
use crate::address_::Address;
use crate::bookmarks::{bookmark_managers, BookmarkManager};
use crate::error_::Result;
use crate::value::ValueSend;
use config::auth::AuthToken;
pub use config::{
    ConfigureFetchSizeError, ConnectionConfig, ConnectionConfigParseError, DriverConfig,
    InvalidRoutingContextError, KeepAliveConfig, TlsConfigError,
};
pub use eager_result::{EagerResult, ScalarError};
use home_db_cache::HomeDbCache;
use io::bolt::message_parameters::TelemetryAPI;
#[cfg(feature = "_internal_testkit_backend")]
pub use io::ConnectionPoolMetrics;
use io::{AcquireConfig, Pool, PoolConfig, PooledBolt, SessionAuth, UpdateRtArgs, UpdateRtDb};
use notification::NotificationFilter;
pub use record::Record;
use record_stream::RecordStream;
use session::config::InternalSessionConfig;
use session::retry::RetryPolicy;
use session::{
    default_receiver, DefaultMeta, DefaultMetaKey, DefaultParam, DefaultParamKey, DefaultReceiver,
    Session, SessionConfig,
};
use summary::ServerInfo;
use transaction::TransactionTimeout;

// imports for docs
#[allow(unused)]
use crate::error_::Neo4jError;
#[allow(unused)]
use session::TransactionBuilder;

pub mod auth {
    pub use super::config::auth::*;
}
pub mod notification {
    pub use super::config::notification::*;
}

/// The driver hold the configuration and connection pool to your Neo4j DBMS.
///
/// Main ways to run work against the DBMS through the driver are:
///  * [`Driver::execute_query()`] for running a single query inside a transaction.
///  * [`Driver::session()`] for several mechanisms offering more advance patterns.
#[derive(Debug)]
pub struct Driver {
    config: ReducedDriverConfig,
    pool: Pool,
    home_db_cache: Arc<HomeDbCache>,
    capability_check_config: SessionConfig,
    execute_query_bookmark_manager: Arc<dyn BookmarkManager>,
}

impl Driver {
    /// Create a new driver.
    ///
    /// **Note:**  
    /// Driver creation is *lazy*.
    /// No connections are established until work is performed.
    /// If you want to verify connectivity, use [`Driver::verify_connectivity`].
    pub fn new(mut connection_config: ConnectionConfig, config: DriverConfig) -> Self {
        if let Some(routing_context) = &mut connection_config.routing_context {
            let before = routing_context.insert(
                String::from("address"),
                connection_config.address.to_string().into(),
            );
            assert!(
                before.is_none(),
                "address was already set in routing context"
            );
        }
        let pool_config = PoolConfig {
            routing_context: connection_config.routing_context,
            tls_config: connection_config.tls_config.map(Arc::new),
            user_agent: config.user_agent,
            auth: config.auth,
            max_connection_lifetime: config.max_connection_lifetime,
            max_connection_pool_size: config.max_connection_pool_size,
            connection_timeout: config.connection_timeout,
            keep_alive: config.keep_alive,
            connection_acquisition_timeout: config.connection_acquisition_timeout,
            resolver: config.resolver,
            notification_filters: Arc::new(config.notification_filter),
            telemetry: config.telemetry,
        };
        Driver {
            config: ReducedDriverConfig {
                fetch_size: config.fetch_size,
                idle_time_before_connection_test: config.idle_time_before_connection_test,
            },
            pool: Pool::new(Arc::new(connection_config.address), pool_config),
            home_db_cache: Default::default(),
            capability_check_config: SessionConfig::default()
                .with_database(Arc::new(String::from("system"))),
            execute_query_bookmark_manager: Arc::new(bookmark_managers::simple(None)),
        }
    }

    /// Spawn a new [`Session`] with the given [`SessionConfig`].
    ///
    /// This is the main entry point for advanced usage of the driver, when
    /// [`Driver::execute_query()`] is not sufficient.
    /// Else, prefer the latter.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::driver::{EagerResult, RoutingControl};
    /// use neo4j::retry::ExponentialBackoff;
    /// use neo4j::session::SessionConfig;
    /// use neo4j::Result as Neo4jResult;
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// // Always specify the database when possible, to allow the driver to work more efficiently.
    /// let database = Arc::new(String::from("neo4j"));
    /// let mut session = driver.session(SessionConfig::new().with_database(Arc::clone(&database)));
    ///
    /// // do work with the session
    /// let mut scalar = session
    ///     .transaction()
    ///     .with_routing_control(RoutingControl::Read)
    ///     .run_with_retry(ExponentialBackoff::new(), |tx| {
    ///         Ok(tx
    ///             .query("RETURN 1 AS n")
    ///             .run()?
    ///             .try_as_eager_result()?
    ///             .expect("result stream not consumed before")
    ///             .into_scalar()
    ///             .unwrap())
    ///     })
    ///     .unwrap();
    /// assert_eq!(scalar, ValueReceive::Integer(1));
    /// ```
    pub fn session(&self, config: SessionConfig) -> Session {
        let config = InternalSessionConfig {
            config,
            idle_time_before_connection_test: self.config.idle_time_before_connection_test,
            eager_begin: true,
        };
        Session::new(
            config,
            &self.pool,
            Arc::clone(&self.home_db_cache),
            &self.config,
        )
    }

    fn execute_query_session(
        &self,
        database: Option<Arc<String>>,
        impersonated_user: Option<Arc<String>>,
        auth: Option<Arc<AuthToken>>,
        bookmark_manager: ExecuteQueryBookmarkManager,
        notification_filter: NotificationFilter,
    ) -> Session {
        let mut session_config = SessionConfig::new();
        session_config.database = database;
        session_config.impersonated_user = impersonated_user;
        session_config.auth = auth;
        session_config.bookmark_manager = match &bookmark_manager {
            ExecuteQueryBookmarkManager::None => None,
            ExecuteQueryBookmarkManager::DriverDefault => {
                Some(Arc::clone(&self.execute_query_bookmark_manager))
            }
            ExecuteQueryBookmarkManager::Custom(manager) => Some(Arc::clone(manager)),
        };
        session_config.notification_filter = notification_filter;
        let config = InternalSessionConfig {
            config: session_config,
            idle_time_before_connection_test: self.config.idle_time_before_connection_test,
            eager_begin: false,
        };
        Session::new(
            config,
            &self.pool,
            Arc::clone(&self.home_db_cache),
            &self.config,
        )
    }

    /// Execute a single query inside a transaction.
    /// Use the returned [`ExecuteQueryBuilder`] to configure the transaction and run it.
    ///
    /// This is the preferred way run work against the DBMS.
    /// The single query nature allows the driver to optimize round trips to the DBMS.
    ///
    /// By default, all work executed over multiple `execute_query()` calls is
    /// [causally chained](crate#causal-consistency) (i.e., can read previous writes).
    /// This is achieved by using a common bookmark manager for all `execute_query()` calls:
    /// [`Driver::execute_query_bookmark_manager`].
    /// Causal chaining can cause the DBMS to wait for previous writes to be visible to the
    /// transaction, which can increase latency.
    /// If no causal chaining is desired, use [`ExecuteQueryBuilder::without_bookmark_manager()`]
    /// or supply different bookmark managers to different `execute_query()` calls to choose exactly
    /// which queries should be causally chained.
    ///
    /// `execute_query().run()` is equivalent (except for performance optimizations) to
    /// spawning a new [`Session`] with [`Driver::session()`], calling [`Session::transaction()`],
    /// executing the query and committing the transaction.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::driver::RoutingControl;
    /// use neo4j::retry::ExponentialBackoff;
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// // Always specify the database when possible, to allow the driver to work more efficiently.
    /// let database = Arc::new(String::from("neo4j"));
    /// let mut result = driver
    ///     .execute_query("RETURN 1 AS n")
    ///     .with_database(Arc::clone(&database))
    ///     .with_routing_control(RoutingControl::Read)
    ///     // For more resilience, use retry policies, especially when running against a cluster
    ///     // or cloud-hosted DBMS.
    ///     .run_with_retry(ExponentialBackoff::default())
    ///     .unwrap();
    ///
    /// assert_eq!(result.into_scalar().unwrap(), ValueReceive::Integer(1));
    /// ```
    pub fn execute_query<Q: AsRef<str>>(
        &self,
        query: Q,
    ) -> ExecuteQueryBuilder<
        Q,
        DefaultParamKey,
        DefaultParam,
        DefaultMetaKey,
        DefaultMeta,
        DefaultReceiver,
    > {
        ExecuteQueryBuilder::new(self, query)
    }

    /// Return the bookmark manager the driver uses by default for
    /// [causally chaining](crate#causal-consistency) [`Driver::execute_query()`] calls.
    ///
    /// This is for instance useful to extend the causal chain to any session.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::session::SessionConfig;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// // Always specify the database when possible, to allow the driver to work more efficiently.
    /// let database = Arc::new(String::from("neo4j"));
    /// driver
    ///     .execute_query(
    ///         // some write query
    ///         // ...
    ///         # "RETURN 1 AS n",
    ///     )
    ///     .with_database(Arc::clone(&database))
    ///     .run()
    ///     .unwrap();
    ///
    /// let mut session = driver.session(
    ///     SessionConfig::new()
    ///         .with_database(Arc::clone(&database))
    ///         // extend the causal chain to the session
    ///         .with_bookmark_manager(driver.execute_query_bookmark_manager()),
    /// );
    /// // session can now read the result of the write query
    /// // ...
    /// ```
    pub fn execute_query_bookmark_manager(&self) -> Arc<dyn BookmarkManager> {
        Arc::clone(&self.execute_query_bookmark_manager)
    }

    /// Make sure the driver can connect to the DBMS.
    ///
    /// This is equivalent to calling [`Driver::get_server_info()`], but ignoring the returned
    /// `ServerInfo`.
    pub fn verify_connectivity(&self) -> Result<()> {
        self.acquire_connectivity_checked().map(drop)
    }

    /// Get information about the DBMS the driver is connected to.
    ///
    /// When connecting to a cluster, this method makes no guarantees about which cluster member
    /// the returned [`ServerInfo`] refers to.
    pub fn get_server_info(&self) -> Result<ServerInfo> {
        self.acquire_connectivity_checked()
            .map(|connection| ServerInfo::new(&connection))
    }

    /// Verify that the configured authentication is valid.
    ///
    /// The driver will attempt to connect to the server.
    /// On success, `Ok(true)` is returned.
    /// On failure, the error is compared to a pre-defined list of errors that indicate invalid
    /// authentication.
    /// On match, `Ok(false)` is returned.
    /// All other errors are returned as `Err(...)`.
    pub fn verify_authentication(&self, auth: Arc<AuthToken>) -> Result<bool> {
        self.session(self.capability_check_config.clone())
            .verify_authentication(&auth)
    }

    fn acquire_connectivity_checked(&self) -> Result<PooledBolt> {
        let config = InternalSessionConfig {
            config: SessionConfig::default(),
            idle_time_before_connection_test: Some(Duration::ZERO),
            eager_begin: true,
        };
        let mut session = Session::new(
            config,
            &self.pool,
            Arc::clone(&self.home_db_cache),
            &self.config,
        );
        session
            .acquire_connection(RoutingControl::Read)
            .and_then(|mut con| {
                con.write_all(None)?;
                con.read_all(None)?;
                Ok(con)
            })
    }

    /// Check whether the protocol version negotiated with the DBMS supports multi-database.
    ///
    /// Support was added in Bolt protocol version 4.0, corresponding to Neo4j 4.0.
    ///
    /// **Note:**  
    /// The protocol supporting multi-database does not necessarily mean that the DBMS supports it
    /// as well.
    /// For example, Neo4j community edition does not support multi-database.
    pub fn supports_multi_db(&self) -> Result<bool> {
        self.acquire_capability_check_connection()
            .map(|connection| connection.protocol_version() >= (4, 0))
    }

    /// Check whether the protocol version negotiated with the DBMS supports session authentication.
    ///
    /// Support was added in Bolt protocol version 5.1, corresponding to Neo4j 5.5.
    pub fn supports_session_auth(&self) -> Result<bool> {
        self.acquire_capability_check_connection()
            .map(|connection| connection.protocol_version() >= (5, 1))
    }

    /// Check whether the driver was configured with TLS.
    ///
    /// See also [`ConnectionConfig::with_encryption_disabled()`] and similar.
    #[inline]
    pub fn is_encrypted(&self) -> bool {
        self.pool.is_encrypted()
    }

    #[cfg(feature = "_internal_testkit_backend")]
    #[inline]
    pub fn get_connection_pool_metrics(
        &self,
        address: Arc<Address>,
    ) -> Option<ConnectionPoolMetrics> {
        self.pool.get_metrics(address)
    }

    fn acquire_capability_check_connection(&self) -> Result<PooledBolt> {
        self.pool.acquire(AcquireConfig {
            mode: RoutingControl::Read,
            update_rt_args: UpdateRtArgs {
                db: self
                    .capability_check_config
                    .database
                    .as_ref()
                    .map(|db| UpdateRtDb {
                        db: Arc::clone(db),
                        guess: false,
                    })
                    .as_ref(),
                bookmarks: None,
                imp_user: None,
                deadline: self.pool.config.connection_acquisition_deadline(),
                session_auth: SessionAuth::None,
                idle_time_before_connection_test: None,
                db_resolution_cb: None,
            },
        })
    }
}

#[derive(Debug)]
enum ExecuteQueryBookmarkManager {
    None,
    DriverDefault,
    Custom(Arc<dyn BookmarkManager>),
}

/// Builder for [`Driver::execute_query()`].
pub struct ExecuteQueryBuilder<'driver, Q, KP, P, KM, M, FRes> {
    driver: &'driver Driver,
    query: Q,
    _kp: PhantomData<KP>,
    param: P,
    _km: PhantomData<KM>,
    meta: M,
    timeout: TransactionTimeout,
    mode: RoutingControl,
    database: Option<Arc<String>>,
    impersonated_user: Option<Arc<String>>,
    auth: Option<Arc<AuthToken>>,
    bookmark_manager: ExecuteQueryBookmarkManager,
    notification_filter: NotificationFilter,
    receiver: FRes,
}

impl<'driver, Q: AsRef<str>>
    ExecuteQueryBuilder<
        'driver,
        Q,
        DefaultParamKey,
        DefaultParam,
        DefaultMetaKey,
        DefaultMeta,
        DefaultReceiver,
    >
{
    fn new(driver: &'driver Driver, query: Q) -> Self {
        Self {
            driver,
            query,
            _kp: PhantomData,
            param: Default::default(),
            _km: PhantomData,
            meta: Default::default(),
            timeout: Default::default(),
            mode: RoutingControl::Write,
            database: None,
            impersonated_user: None,
            bookmark_manager: ExecuteQueryBookmarkManager::DriverDefault,
            auth: None,
            receiver: default_receiver,
            notification_filter: Default::default(),
        }
    }
}

impl<
        'driver,
        Q: AsRef<str>,
        KP: Borrow<str> + Debug,
        P: Borrow<HashMap<KP, ValueSend>>,
        KM: Borrow<str> + Debug,
        M: Borrow<HashMap<KM, ValueSend>>,
        R,
        FRes: FnMut(&mut RecordStream) -> Result<R>,
    > ExecuteQueryBuilder<'driver, Q, KP, P, KM, M, FRes>
{
    /// Configure query parameters.
    ///
    /// # Example
    /// ```
    /// use neo4j::{value_map, ValueReceive};
    /// use neo4j::retry::ExponentialBackoff;
    ///
    /// # doc_test_utils::db_exclusive(|| {
    /// # let driver = doc_test_utils::get_driver();
    /// let mut result = driver
    ///     .execute_query("CREATE (n:Node {id: $id}) RETURN n")
    ///     .with_parameters(value_map!({"id": 1}))
    ///     .run_with_retry(ExponentialBackoff::new())
    ///     .unwrap();
    /// let mut node = result.into_scalar().unwrap().try_into_node().unwrap();
    /// assert_eq!(node.properties.remove("id").unwrap(), ValueReceive::Integer(1));
    /// # });
    /// ```
    ///
    /// Always prefer this over query string manipulation to avoid injection vulnerabilities and to
    /// allow the server to cache the query plan.
    #[inline]
    pub fn with_parameters<KP_: Borrow<str> + Debug, P_: Borrow<HashMap<KP_, ValueSend>>>(
        self,
        param: P_,
    ) -> ExecuteQueryBuilder<'driver, Q, KP_, P_, KM, M, FRes> {
        let Self {
            driver,
            query,
            _kp: _,
            param: _,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp: PhantomData,
            param,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        }
    }

    /// Configure the query to not use any parameters.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_parameters(
        self,
    ) -> ExecuteQueryBuilder<'driver, Q, DefaultParamKey, DefaultParam, KM, M, FRes> {
        let Self {
            driver,
            query,
            _kp: _,
            param: _,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp: PhantomData,
            param: Default::default(),
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        }
    }

    /// Attach transaction metadata to the query.
    ///
    /// See [`TransactionBuilder::with_transaction_meta()`] for more information.
    ///
    /// # Example
    /// ```
    /// use neo4j::value_map;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("MATCH (n:Node) RETURN n")
    ///    .with_transaction_meta(value_map!({"key": "value"}))
    ///    .run()
    ///    .unwrap();
    /// ```
    #[inline]
    pub fn with_transaction_meta<KM_: Borrow<str> + Debug, M_: Borrow<HashMap<KM_, ValueSend>>>(
        self,
        meta: M_,
    ) -> ExecuteQueryBuilder<'driver, Q, KP, P, KM_, M_, FRes> {
        let Self {
            driver,
            query,
            _kp,
            param,
            _km: _,
            meta: _,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp,
            param,
            _km: PhantomData,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        }
    }

    /// Configure the query to not use any transaction metadata.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_transaction_meta(
        self,
    ) -> ExecuteQueryBuilder<'driver, Q, KP, P, DefaultMetaKey, DefaultMeta, FRes> {
        let Self {
            driver,
            query,
            _kp,
            param,
            _km: _,
            meta: _,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp,
            param,
            _km: PhantomData,
            meta: Default::default(),
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        }
    }

    /// Instruct the server to abort the transaction after the given timeout.
    ///
    /// See [`TransactionTimeout`] for options.
    #[inline]
    pub fn with_transaction_timeout(mut self, timeout: TransactionTimeout) -> Self {
        self.timeout = timeout;
        self
    }

    /// Specify whether the query should be send to a reader or writer in the cluster.
    ///
    /// See [`TransactionBuilder::with_routing_control()`] for more information.
    #[inline]
    pub fn with_routing_control(mut self, mode: RoutingControl) -> Self {
        self.mode = mode;
        self
    }

    /// Choose which database to run the query on.
    ///
    /// Always specify this, if possible, to allow the driver to run more efficiently.
    ///
    /// See also [`SessionConfig::with_database()`].
    #[inline]
    pub fn with_database(mut self, database: Arc<String>) -> Self {
        self.database = Some(database);
        self
    }

    /// Use the default or home database as configured on the server.
    ///
    /// See also [`SessionConfig::with_default_database()`].
    #[inline]
    pub fn with_default_database(mut self) -> Self {
        self.database = None;
        self
    }

    /// Impersonate the specified user.
    ///
    /// See also [`SessionConfig::with_impersonated_user()`].
    #[inline]
    pub fn with_impersonated_user(mut self, user: Arc<String>) -> Self {
        self.impersonated_user = Some(user);
        self
    }

    /// Don't impersonate any user.
    ///
    /// This is the *default*.
    ///
    /// See also [`SessionConfig::without_impersonated_user()`].
    #[inline]
    pub fn without_impersonated_user(mut self) -> Self {
        self.impersonated_user = None;
        self
    }

    /// Use the given authentication token for the duration of the session.
    ///
    /// This requires Neo4j 5.5 or newer.
    /// For older versions, this will result in a [`Neo4jError::UserCallback`].
    ///
    /// See also [`SessionConfig::with_session_auth()`].
    #[inline]
    pub fn with_session_auth(mut self, auth: Arc<AuthToken>) -> Self {
        self.auth = Some(auth);
        self
    }

    /// Use the authentication token from the driver config.
    ///
    /// This is the *default*.
    ///
    /// See also [`SessionConfig::without_session_auth()`].
    #[inline]
    pub fn without_session_auth(mut self) -> Self {
        self.auth = None;
        self
    }

    /// Use this bookmark manager to build a [causal chain](crate#causal-consistency).
    #[inline]
    pub fn with_bookmark_manager(mut self, manager: Arc<dyn BookmarkManager>) -> Self {
        self.bookmark_manager = ExecuteQueryBookmarkManager::Custom(manager);
        self
    }

    /// Use the default bookmark manager for the driver to build a
    /// [causal chain](crate#causal-consistency), which is
    /// [`Driver::execute_query_bookmark_manager()`]
    ///
    /// `with_default_bookmark_manager()` is equivalent to
    /// ```
    /// # let driver = doc_test_utils::get_driver();
    /// driver
    ///     .execute_query("...")
    ///     .with_bookmark_manager(driver.execute_query_bookmark_manager());
    /// ```
    #[inline]
    pub fn with_default_bookmark_manager(mut self) -> Self {
        self.bookmark_manager = ExecuteQueryBookmarkManager::DriverDefault;
        self
    }

    /// Don't use any bookmark manager.
    ///
    /// This makes the transaction be part of no [causal chain](crate#causal-consistency).
    #[inline]
    pub fn without_bookmark_manager(mut self) -> Self {
        self.bookmark_manager = ExecuteQueryBookmarkManager::None;
        self
    }

    /// Configure what notifications the driver should receive from the DBMS for this query.
    ///
    /// This requires Neo4j 5.7 or newer.
    /// For older versions, this will result in a [`Neo4jError::InvalidConfig`].
    ///
    /// See also [`SessionConfig::with_notification_filter()`].
    #[inline]
    pub fn with_notification_filter(mut self, notification_filter: NotificationFilter) -> Self {
        self.notification_filter = notification_filter;
        self
    }

    /// Use the default notification filters.
    ///
    /// This means using the filter configured in the driver.
    ///
    /// See also [`SessionConfig::with_default_notification_filter()`].
    #[inline]
    pub fn with_default_notification_filter(mut self) -> Self {
        self.notification_filter = Default::default();
        self
    }

    /// Specify a custom receiver to handle the result stream.
    ///
    /// By default (see [`ExecuteQueryBuilder::with_default_receiver()`]), the result stream will be
    /// collected into memory and returned as [`EagerResult`].
    ///
    /// If the transaction is executed with a retry policy
    /// ([`ExecuteQueryBuilder::run_with_retry()`]), the receiver might be called multiple times if
    /// the transaction is retried.
    ///
    /// # Example
    /// ```
    /// use neo4j::driver::record_stream::RecordStream;
    /// use neo4j::driver::Record;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let sum = driver
    ///     .execute_query("UNWIND range(1, 3) AS x RETURN x")
    ///     .with_receiver(|stream: &mut RecordStream| {
    ///         let mut sum = 0;
    ///         for result in stream {
    ///             let mut record: Record = result?;
    ///             sum += record.into_values().next().unwrap().try_into_int().unwrap();
    ///         }
    ///         Ok(sum)
    ///     })
    ///     .run()
    ///     .unwrap();
    ///
    /// assert_eq!(sum, 6);
    /// ```
    #[inline]
    pub fn with_receiver<R_, FRes_: FnMut(&mut RecordStream) -> Result<R_>>(
        self,
        receiver: FRes_,
    ) -> ExecuteQueryBuilder<'driver, Q, KP, P, KM, M, FRes_> {
        let Self {
            driver,
            query,
            _kp,
            param,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver: _,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp,
            param,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver,
        }
    }

    /// Set the receiver back to the default, which will collect the result stream into memory and
    /// return it as [`EagerResult`].
    #[inline]
    pub fn with_default_receiver(
        self,
    ) -> ExecuteQueryBuilder<'driver, Q, KP, P, KM, M, DefaultReceiver> {
        let Self {
            driver,
            query,
            _kp,
            param,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver: _,
        } = self;
        ExecuteQueryBuilder {
            driver,
            query,
            _kp,
            param,
            _km,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            receiver: default_receiver,
        }
    }

    /// Run the transaction and return the result.
    pub fn run(self) -> Result<R> {
        let Self {
            driver,
            query,
            _kp: _,
            param,
            _km: _,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            mut receiver,
        } = self;
        let mut session = driver.execute_query_session(
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
        );
        let tx_builder = session
            .transaction()
            .with_transaction_meta(meta.borrow())
            .with_transaction_timeout(timeout)
            .with_routing_control(mode)
            .with_api_overwrite(Some(TelemetryAPI::DriverLevel));
        tx_builder.run(move |tx| {
            let mut result_stream = tx.query(query).with_parameters(param).run()?;
            let res = receiver(result_stream.raw_stream_mut())?;
            result_stream.consume()?;
            tx.commit()?;
            Ok(res)
        })
    }

    /// Run the transaction with a retry policy.
    ///
    /// This is pretty much the same as [`ExecuteQueryBuilder::run()`], except that the whole
    /// transaction will be retried if it returns an error deemed retryable by the given
    /// `retry_policy`.
    ///
    /// See also [`RetryPolicy`].
    pub fn run_with_retry<RP: RetryPolicy>(self, retry_policy: RP) -> StdResult<R, RP::Error> {
        let Self {
            driver,
            query,
            _kp: _,
            param,
            _km: _,
            meta,
            timeout,
            mode,
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
            mut receiver,
        } = self;
        let mut session = driver.execute_query_session(
            database,
            impersonated_user,
            auth,
            bookmark_manager,
            notification_filter,
        );
        let tx_builder = session
            .transaction()
            .with_transaction_meta(meta.borrow())
            .with_transaction_timeout(timeout)
            .with_routing_control(mode)
            .with_api_overwrite(Some(TelemetryAPI::DriverLevel));
        tx_builder.run_with_retry(retry_policy, move |tx| {
            let mut result_stream = tx
                .query(query.as_ref())
                .with_parameters(param.borrow())
                .run()?;
            let res = receiver(result_stream.raw_stream_mut())?;
            result_stream.consume()?;
            tx.commit()?;
            Ok(res)
        })
    }
}

impl<
        Q: AsRef<str>,
        KP: Borrow<str> + Debug,
        P: Borrow<HashMap<KP, ValueSend>>,
        KM: Borrow<str> + Debug,
        M: Borrow<HashMap<KM, ValueSend>>,
        FRes,
    > Debug for ExecuteQueryBuilder<'_, Q, KP, P, KM, M, FRes>
{
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExecuteQueryBuilder")
            .field("driver", &"...")
            .field("query", &self.query.as_ref())
            .field("param", &self.param.borrow())
            .field("meta", &self.meta.borrow())
            .field("timeout", &self.timeout)
            .field("mode", &self.mode)
            .field("database", &self.database)
            .field("impersonated_user", &self.impersonated_user)
            .field("auth", &self.auth)
            .field("bookmark_manager", &self.bookmark_manager)
            .finish()
    }
}

#[derive(Debug)]
pub(crate) struct ReducedDriverConfig {
    pub(crate) fetch_size: i64,
    pub(crate) idle_time_before_connection_test: Option<Duration>,
}

#[derive(Debug, Copy, Clone)]
pub enum RoutingControl {
    Read,
    Write,
}

impl RoutingControl {
    pub(crate) fn as_protocol_str(&self) -> Option<&'static str> {
        match self {
            RoutingControl::Read => Some("r"),
            RoutingControl::Write => None,
        }
    }
}
