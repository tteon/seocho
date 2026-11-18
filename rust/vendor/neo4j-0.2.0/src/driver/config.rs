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

pub(crate) mod auth;
pub(crate) mod notification;

use std::collections::HashMap;
use std::fmt::Debug;
use std::path::Path;
use std::result::Result as StdResult;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use mockall_double::double;
use rustls::ClientConfig;
use thiserror::Error;
use uriparse::{Query, URI};

use crate::address_::resolution::AddressResolver;
use crate::address_::Address;
use crate::address_::DEFAULT_PORT;
use crate::value::ValueSend;
use auth::{AuthManager, AuthToken};
use notification::NotificationFilter;

// imports for docs
#[allow(unused)]
use super::session::{AutoCommitBuilder, SessionConfig, TransactionBuilder};
#[allow(unused)]
use super::ExecuteQueryBuilder;
#[allow(unused)]
use crate::error_::Neo4jError;

const DEFAULT_USER_AGENT: &str = env!("NEO4J_DEFAULT_USER_AGENT");
pub(crate) const DEFAULT_FETCH_SIZE: i64 = 1000;
pub(crate) const DEFAULT_CONNECTION_TIMEOUT: Duration = Duration::from_secs(30);
pub(crate) const DEFAULT_CONNECTION_ACQUISITION_TIMEOUT: Duration = Duration::from_secs(60);
pub(crate) const DEFAULT_MAX_CONNECTION_LIFETIME: Duration = Duration::from_secs(3600);

/// Configure how the driver should behave.
#[derive(Debug)]
pub struct DriverConfig {
    pub(crate) user_agent: String,
    pub(crate) auth: AuthConfig,
    pub(crate) max_connection_lifetime: Option<Duration>,
    pub(crate) idle_time_before_connection_test: Option<Duration>,
    pub(crate) max_connection_pool_size: usize,
    pub(crate) fetch_size: i64,
    pub(crate) connection_timeout: Option<Duration>,
    pub(crate) connection_acquisition_timeout: Option<Duration>,
    pub(crate) resolver: Option<Box<dyn AddressResolver>>,
    pub(crate) notification_filter: NotificationFilter,
    pub(crate) keep_alive: Option<KeepAliveConfig>,
    pub(crate) telemetry: bool,
}

#[derive(Debug)]
pub(crate) enum AuthConfig {
    Static(Arc<AuthToken>),
    Manager(Arc<dyn AuthManager>),
}

/// Options to configure the TCP keep alive of the driver's sockets.
///
/// See also [`DriverConfig::with_keep_alive()`]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum KeepAliveConfig {
    /// Enable TCP keep alive with the default settings of the OS.
    Default,
    /// Enable TCP keep alive, setting the time to `Duration`.
    ///
    /// See also [`socket2::TcpKeepalive::with_time()`].
    CustomTime(Duration),
}

/// Tell the driver where the DBMS it be found and how to connect to it.
///
/// ## From a URI
/// Most official drivers only accept a URI string to configure this aspect of the driver.
/// This crate supports the same mechanism by implementing `FromStr` for `ConnectionConfig`.
/// The string is expected to follow the form:
/// ```text
/// scheme://host[:port[?routing_context]]
/// ```
/// Where scheme must be one of:
///
/// | scheme      | encryption                                       | routing |
/// | ----------- | ------------------------------------------------ | ------- |
/// | `neo4j`     | none                                             | yes     |
/// | `neo4j+s`   | yes                                              | yes     |
/// | `neo4j+scc` | yes, *but every certificate is accepted*.        | yes     |
/// | `bolt`      | none                                             | no      |
/// | `bolt+s`    | yes                                              | no      |
/// | `bolt+scc`  | yes, *but every certificate is accepted*.        | no      |
///
/// **⚠️ WARNING**:  
/// The `...+ssc` schemes are not secure and provided for testing purposes only.
///
/// The routing context may only be present for schemes that support routing.
///
/// ```
/// use neo4j::driver::ConnectionConfig;
///
/// let conf: ConnectionConfig = "neo4j+s://localhost:7687?foo=bar".parse().unwrap();
/// ```
///
/// ## Programmatically
/// To get better type safety and avoid parsing errors at runtime, this crate also provides a
/// builder API.
///
/// ```
/// use std::collections::HashMap;
///
/// use neo4j::driver::ConnectionConfig;
///
/// let routing_context = {
///     let mut map = HashMap::with_capacity(1);
///     map.insert("foo".to_string(), "bar".to_string());
///     map
/// };
/// let conf = ConnectionConfig::new(("localhost", 7687).into())
///     .with_encryption_trust_default_cas()
///     .unwrap()
///     .with_routing_context(routing_context);
/// ```
///
/// ## TLS
/// The driver utilizes [`rustls`] for TLS concerns.
/// Make sure to have a look at the requirements, for building and using it.
/// In particular:
///  * Consider the
///    [platform support](https://docs.rs/rustls/latest/rustls/index.html#platform-support)
///  * Make sure a crypto provider is installed (e.g., by calling
///    [`rustls::crypto::CryptoProvider::install_default()`] when starting the process).
#[derive(Debug)]
pub struct ConnectionConfig {
    pub(crate) address: Address,
    pub(crate) routing_context: Option<HashMap<String, ValueSend>>,
    pub(crate) tls_config: Option<ClientConfig>,
}

impl Default for DriverConfig {
    fn default() -> Self {
        Self {
            user_agent: String::from(DEFAULT_USER_AGENT),
            auth: AuthConfig::Static(Default::default()),
            max_connection_lifetime: Some(DEFAULT_MAX_CONNECTION_LIFETIME),
            idle_time_before_connection_test: None,
            max_connection_pool_size: 100,
            fetch_size: DEFAULT_FETCH_SIZE,
            connection_timeout: Some(DEFAULT_CONNECTION_TIMEOUT),
            connection_acquisition_timeout: Some(DEFAULT_CONNECTION_ACQUISITION_TIMEOUT),
            resolver: None,
            notification_filter: Default::default(),
            keep_alive: None,
            telemetry: true,
        }
    }
}

impl DriverConfig {
    /// Create a new driver configuration with default values.
    ///
    /// This is the same as calling [`DriverConfig::default()`].
    #[inline]
    pub fn new() -> Self {
        Self::default()
    }

    /// Configure a custom user agent the driver should send to the DBMS.
    ///
    /// The user agent should follow the form `<app-name>/<version>[ <further information>]`.
    /// For example, `"my-app/1.0.0"` or `"my-app/1.0.0 linux emea-prod-1"`.
    ///
    /// If omitted, the driver chooses a *default* user agent.
    ///
    /// # Example
    /// ```
    /// use neo4j::driver::DriverConfig;
    ///
    /// let config = DriverConfig::new().with_user_agent(String::from("my-app/1.0.0"));
    /// ```
    #[inline]
    pub fn with_user_agent(mut self, user_agent: String) -> Self {
        self.user_agent = user_agent;
        self
    }

    /// Configure a static auth token the driver should use to authenticate with the DBMS.
    ///
    /// This will overwrite any auth manager previously configured with
    /// [`DriverConfig::with_auth_manager()`].
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::driver::auth::AuthToken;
    /// use neo4j::driver::DriverConfig;
    ///
    /// let auth = Arc::new(AuthToken::new_basic_auth("neo4j", "pass"));
    /// let config = DriverConfig::new().with_auth(auth);
    /// ```
    #[inline]
    pub fn with_auth(mut self, auth: Arc<AuthToken>) -> Self {
        self.auth = AuthConfig::Static(auth);
        self
    }

    /// Configure an auth manager the driver should use to authenticate with the DBMS.
    ///
    /// This will overwrite any auth token previously configured with
    /// [`DriverConfig::with_auth()`].
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::driver::auth::{auth_managers, AuthToken};
    /// use neo4j::driver::DriverConfig;
    ///
    /// let manager = Arc::new(auth_managers::new_static(AuthToken::new_basic_auth(
    ///     "neo4j", "pass",
    /// )));
    /// let config = DriverConfig::new().with_auth_manager(manager);
    /// ```
    #[inline]
    pub fn with_auth_manager(mut self, manager: Arc<dyn AuthManager>) -> Self {
        self.auth = AuthConfig::Manager(manager);
        self
    }

    /// Limit the maximum lifetime of any connection.
    ///
    /// When a connection is attempted to be picked up from the connection pool, it will be closed
    /// if it has been created longer than this duration ago.
    #[inline]
    pub fn with_max_connection_lifetime(mut self, max_connection_lifetime: Duration) -> Self {
        self.max_connection_lifetime = Some(max_connection_lifetime);
        self
    }

    /// Disable closing connections based on their age.
    ///
    /// See also [`DriverConfig::with_max_connection_lifetime()`].
    #[inline]
    pub fn without_max_connection_lifetime(mut self) -> Self {
        self.max_connection_lifetime = None;
        self
    }

    /// Use the default maximum connection lifetime.
    ///
    /// Currently, this is `3600` seconds.
    /// This is an implementation detail and may change in the future.
    ///
    /// See also [`DriverConfig::with_max_connection_lifetime()`].
    #[inline]
    pub fn with_default_max_connection_lifetime(mut self) -> Self {
        self.max_connection_lifetime = Some(DEFAULT_MAX_CONNECTION_LIFETIME);
        self
    }

    /// Configure connections that have been idle for longer than this duration whenever they are
    /// pulled from the connection pool to be tested before being used.
    ///
    /// The test will cause an extra round-trip.
    /// However, it will help to avoid using broken connections that, when used with a retry policy,
    /// would cause a retry which usually takes much longer than the test.
    /// Therefore, this configuration is a trade-off.
    /// For especially unstable network configurations, it might be useful to set this to a low
    /// value.
    ///
    /// Set the timeout to [`Duration::ZERO`] to make the driver always perform the liveness check
    /// when picking up a connection from the pool.
    ///
    /// Usually, this parameter does not need tweaking.
    ///
    /// # Example
    /// ```
    /// use std::time::Duration;
    ///
    /// use neo4j::driver::DriverConfig;
    ///
    /// let config = DriverConfig::new().with_idle_time_before_connection_test(Duration::from_secs(15));
    /// ```
    #[inline]
    pub fn with_idle_time_before_connection_test(mut self, idle_time: Duration) -> Self {
        self.idle_time_before_connection_test = Some(idle_time);
        self
    }

    /// Disable the liveness check for idle connections.
    ///
    /// This is the *default*.
    ///
    /// See [`DriverConfig::with_idle_time_before_connection_test()`].
    #[inline]
    pub fn without_idle_time_before_connection_test(mut self) -> Self {
        self.idle_time_before_connection_test = None;
        self
    }

    /// Configure the maximum number of connections the driver should keep per connection pool.
    ///
    /// The driver maintains multiple connection pools, one for each remote address in the cluster.
    /// For single instance databases, there is only one connection pool.
    ///
    /// Currently, this is `100`.
    /// This is an implementation detail and may change in the future.
    #[inline]
    pub fn with_max_connection_pool_size(mut self, max_connection_pool_size: usize) -> Self {
        self.max_connection_pool_size = max_connection_pool_size;
        self
    }

    /// Change the fetch size to fetch `fetch_size` records at once.
    ///
    /// See also [`SessionConfig::with_fetch_size()`] which is the same setting but per session.
    ///
    /// # Errors
    /// A [`ConfigureFetchSizeError`] is returned if `fetch_size` is greater than [`i64::MAX`].
    ///
    /// # Example
    /// ```
    /// use neo4j::session::SessionConfig;
    ///
    /// let mut config = SessionConfig::new().with_fetch_size(100).unwrap();
    ///
    /// config = config
    ///     .with_fetch_size(i64::MAX as u64 + 1)
    ///     .unwrap_err()
    ///     .builder;
    ///
    /// config = config.with_fetch_size(i64::MAX as u64).unwrap();
    /// ```
    #[allow(clippy::result_large_err)]
    #[inline]
    pub fn with_fetch_size(
        mut self,
        fetch_size: u64,
    ) -> StdResult<Self, ConfigureFetchSizeError<Self>> {
        match i64::try_from(fetch_size) {
            Ok(fetch_size) => {
                self.fetch_size = fetch_size;
                Ok(self)
            }
            Err(_) => Err(ConfigureFetchSizeError { builder: self }),
        }
    }

    /// Fetch all records at once.
    ///
    /// See also [`SessionConfig::with_fetch_size()`] which is the same setting but per session.
    #[inline]
    pub fn with_fetch_all(mut self) -> Self {
        self.fetch_size = -1;
        self
    }

    /// Use the default fetch size.
    ///
    /// Currently, this is `1000`.
    /// This is an implementation detail and may change in the future.
    ///
    /// See also [`SessionConfig::with_fetch_size()`] which is the same setting but per session.
    #[inline]
    pub fn with_default_fetch_size(mut self) -> Self {
        self.fetch_size = DEFAULT_FETCH_SIZE;
        self
    }

    /// Configure the timeout for establishing a connection.
    ///
    /// The timeout only applies to the initial TCP connection establishment.
    #[inline]
    pub fn with_connection_timeout(mut self, timeout: Duration) -> Self {
        self.connection_timeout = Some(timeout);
        self
    }

    /// Disable the connection timeout.
    ///
    /// This setting could lead to the driver waiting for an inappropriately long time.
    ///
    /// See also [`DriverConfig::with_default_connection_timeout()`].
    #[inline]
    pub fn without_connection_timeout(mut self) -> Self {
        self.connection_timeout = None;
        self
    }

    /// Use the default connection timeout.
    ///
    /// Currently, this is `30` seconds.
    /// This is an implementation detail and may change in the future.
    ///
    /// See also [`DriverConfig::with_connection_timeout()`].
    #[inline]
    pub fn with_default_connection_timeout(mut self) -> Self {
        self.connection_timeout = Some(DEFAULT_CONNECTION_TIMEOUT);
        self
    }

    /// Configure the timeout for acquiring a connection from the pool.
    ///
    /// This timeout spans everything needed to acquire a connection from the pool, including
    ///  * waiting for mutexes,
    ///  * fetching routing information if necessary,
    ///  * potential liveness probes
    ///    (see [`DriverConfig::with_idle_time_before_connection_test()`]),
    ///  * establishing a new connection if necessary,
    ///  * etc.
    ///
    /// See also [`DriverConfig::with_default_connection_acquisition_timeout()`].
    #[inline]
    pub fn with_connection_acquisition_timeout(mut self, timeout: Duration) -> Self {
        self.connection_acquisition_timeout = Some(timeout);
        self
    }

    /// Disable the connection acquisition timeout.
    ///
    /// This setting could lead to the driver waiting for an inappropriately long time.
    ///
    /// See also [`DriverConfig::with_default_connection_acquisition_timeout()`].
    #[inline]
    pub fn without_connection_acquisition_timeout(mut self) -> Self {
        self.connection_acquisition_timeout = None;
        self
    }

    /// Use the default connection acquisition timeout.
    ///
    /// Currently, this is `60` seconds.
    /// This is an implementation detail and may change in the future.
    ///
    /// See also [`DriverConfig::with_connection_acquisition_timeout()`].
    #[inline]
    pub fn with_default_connection_acquisition_timeout(mut self) -> Self {
        self.connection_acquisition_timeout = Some(DEFAULT_CONNECTION_ACQUISITION_TIMEOUT);
        self
    }

    /// Register an address resolver.
    ///
    /// The resolver will be called for every address coming into the driver.
    /// Either through the initial [`ConnectionConfig`] or as part of a routing table the driver
    /// fetches from the DBMS.
    /// All addresses will still be DNS resolved after the resolver has been called.
    #[inline]
    pub fn with_resolver(mut self, resolver: Box<dyn AddressResolver>) -> Self {
        self.resolver = Some(resolver);
        self
    }

    /// Don't use an address resolver.
    ///
    /// This is the *default*.
    ///
    /// See also [`DriverConfig::with_resolver()`].
    #[inline]
    pub fn without_resolver(mut self) -> Self {
        self.resolver = None;
        self
    }

    /// Configure what notifications the driver should receive from the DBMS.
    ///
    /// This requires Neo4j 5.7 or newer.
    /// For older versions, this will result in a [`Neo4jError::InvalidConfig`].
    ///
    /// Disabling all irrelevant notifications can improve performance.
    /// Especially when deploying an application to production, it is recommended to disable
    /// notifications that are not relevant to the application, or even all, and only using
    /// notifications in development, testing, and possibly staging environments.
    ///
    /// This setting may be overwritten per session.
    /// See [`SessionConfig::with_notification_filter()`] and
    /// [`ExecuteQueryBuilder::with_notification_filter()`].
    ///
    /// See [`NotificationFilter`] for configuration options.
    ///
    /// # Example
    /// ```
    /// use neo4j::driver::notification::{
    ///     DisabledClassification, MinimumSeverity, NotificationFilter,
    /// };
    /// use neo4j::driver::DriverConfig;
    ///
    /// // filter to only receive warnings (and above), but no performance notifications
    /// let filter = NotificationFilter::new()
    ///     .with_minimum_severity(MinimumSeverity::Warning)
    ///     .with_disabled_classifications(vec![DisabledClassification::Performance]);
    /// let config = DriverConfig::new().with_notification_filter(filter);
    /// ```
    #[inline]
    pub fn with_notification_filter(mut self, notification_filter: NotificationFilter) -> Self {
        self.notification_filter = notification_filter;
        self
    }

    /// Use the default notification filters.
    ///
    /// This means leaving it up to the DBMS to decide what notifications to send.
    #[inline]
    pub fn with_default_notification_filter(mut self) -> Self {
        self.notification_filter = Default::default();
        self
    }

    /// Configure the TCP keep alive of the driver's sockets.
    ///
    /// # Example
    /// ```
    /// use std::time::Duration;
    ///
    /// use neo4j::driver::{DriverConfig, KeepAliveConfig};
    ///
    /// let config =
    ///     DriverConfig::new().with_keep_alive(KeepAliveConfig::CustomTime(Duration::from_secs(10)));
    /// ```
    #[inline]
    pub fn with_keep_alive(mut self, keep_alive: KeepAliveConfig) -> Self {
        self.keep_alive = Some(keep_alive);
        self
    }

    /// Disable TCP keep alive.
    #[inline]
    pub fn without_keep_alive(mut self) -> Self {
        self.keep_alive = None;
        self
    }

    /// Enable or disable telemetry.
    ///
    /// If enabled (default) and the server requests is, the driver will send anonymous API usage
    /// statistics to the server.
    /// Currently, everytime one of the following APIs is used to execute a query
    /// (for the first time), the server is informed of this
    /// (without any further information like arguments, client identifiers, etc.):
    ///
    /// * [`ExecuteQueryBuilder::run()`] / [`ExecuteQueryBuilder::run_with_retry()`]
    /// * [`TransactionBuilder::run()`]
    /// * [`TransactionBuilder::run_with_retry()`]
    /// * [`AutoCommitBuilder::run()`]
    ///
    /// # Example
    /// ```
    /// use neo4j::driver::DriverConfig;
    ///
    /// let config = DriverConfig::new().with_telemetry(false);
    /// ```
    #[inline]
    pub fn with_telemetry(mut self, telemetry: bool) -> Self {
        self.telemetry = telemetry;
        self
    }
}

impl ConnectionConfig {
    /// Create a new connection configuration with default values.
    ///
    /// Besides the required address, no TLS encryption will be used and routing with an empty
    /// routing context is the default.
    pub fn new(address: Address) -> Self {
        Self {
            address,
            routing_context: Some(HashMap::new()),
            tls_config: None,
        }
    }

    /// Change the address the driver should connect to.
    pub fn with_address(mut self, address: Address) -> Self {
        self.address = address;
        self
    }

    /// Choose whether the driver should perform routing [`true`] or not [`false`].
    ///
    /// Routing is enabled by *default*.
    ///
    /// Routing should be used and also works with single instance DBMS setups.
    /// Only when specifically needing to connect to a single cluster node (e.g., for maintenance),
    /// should routing be disabled.
    ///
    /// When disabling routing (`with_routing(false)`), after a routing context has been configured
    /// ([`ConnectionConfig::with_routing_context()`]), the routing context will be dropped.
    /// When enabling it (`with_routing(true)`), an empty routing context will be configured.
    /// If you want to enable routing with a routing context, calling
    /// [`ConnectionConfig::with_routing_context()`] is sufficient.
    pub fn with_routing(mut self, routing: bool) -> Self {
        if !routing {
            self.routing_context = None
        } else if self.routing_context.is_none() {
            self.routing_context = Some(HashMap::new());
        }
        self
    }

    /// Enable routing with a specific routing context.
    ///
    /// The routing context is a set of key-value pairs that will be sent to the DBMS and used can
    /// be used for routing policies (e.g., choosing a region).
    ///
    /// # Errors
    /// An [`InvalidRoutingContextError`] is returned if the routing context contains the *reserved*
    /// key `"address"`.
    #[allow(clippy::result_large_err)]
    pub fn with_routing_context(
        mut self,
        routing_context: HashMap<String, String>,
    ) -> StdResult<Self, InvalidRoutingContextError<Self>> {
        if routing_context.contains_key("address") {
            return Err(InvalidRoutingContextError {
                builder: self,
                it: "cannot contain key 'address'",
            });
        }
        self.routing_context = Some(
            routing_context
                .into_iter()
                .map(|(k, v)| (k, v.into()))
                .collect(),
        );
        Ok(self)
    }

    /// Enforce TLS encryption, verifying the server's certificate against the system's root CA
    /// certificate store.
    ///
    /// Returns an error if the system's root CA certificate store could not be loaded.
    ///
    /// To use TLS, see the notes about [TLS requirements](Self#tls).
    #[allow(clippy::result_large_err)]
    pub fn with_encryption_trust_default_cas(mut self) -> StdResult<Self, TlsConfigError> {
        self.tls_config = Some(match tls_helper::secure_tls_config() {
            Ok(config) => config,
            Err(message) => {
                return Err(TlsConfigError {
                    message,
                    config: self,
                })
            }
        });
        Ok(self)
    }

    /// Enforce TLS encryption, verifying the server's certificate against root CA certificates
    /// loaded from the given file(s).
    ///
    /// Returns an error if loading the root CA certificates failed.
    ///
    /// To use TLS, see the notes about [TLS requirements](Self#tls).
    #[allow(clippy::result_large_err)]
    pub fn with_encryption_trust_custom_cas<P: AsRef<Path>>(
        self,
        paths: &[P],
    ) -> StdResult<Self, TlsConfigError> {
        fn inner(
            mut config: ConnectionConfig,
            paths: &[&Path],
        ) -> StdResult<ConnectionConfig, TlsConfigError> {
            config.tls_config = Some(match tls_helper::custom_ca_tls_config(paths) {
                Ok(config) => config,
                Err(message) => return Err(TlsConfigError { message, config }),
            });
            Ok(config)
        }
        let paths = paths.iter().map(|path| path.as_ref()).collect::<Vec<_>>();
        inner(self, &paths)
    }

    /// Enforce TLS encryption, without verifying the server's certificate.
    ///
    /// **⚠️ WARNING**:  
    /// This is not secure and should only be used for testing purposes.
    ///
    /// To use TLS, see the notes about [TLS requirements](Self#tls).
    pub fn with_encryption_trust_any_certificate(mut self) -> Self {
        self.tls_config = Some(tls_helper::self_signed_tls_config());
        self
    }

    /// Enforce TLS encryption, using a custom TLS configuration.
    ///
    /// **⚠️ WARNING**:  
    /// Depending on the passed TLS configuration, this might not be secure.
    ///
    /// # Example
    /// ```
    /// use neo4j::driver::ConnectionConfig;
    /// use rustls::client::ClientConfig;
    /// # use rustls::RootCertStore;
    /// # use rustls_pki_types::{TrustAnchor, Der};
    ///
    /// # fn get_custom_client_config() -> ClientConfig {
    /// #     // Fails if a provider is already installed. That's fine, too.
    /// #     let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    /// #     ClientConfig::builder().with_root_certificates(
    /// #         RootCertStore {
    /// #             roots: vec![
    /// #                 TrustAnchor {
    /// #                     subject: Der::from_slice(b""),
    /// #                     subject_public_key_info: Der::from_slice(b""),
    /// #                     name_constraints: None,
    /// #                 },
    /// #             ],
    /// #         },
    /// #     ).with_no_client_auth()
    /// # }
    /// #
    /// let client_config: ClientConfig = get_custom_client_config();
    /// let config = ConnectionConfig::new(("localhost", 7687).into())
    ///     .with_encryption_custom_tls_config(client_config);
    /// ```
    ///
    /// To use TLS, see the notes about [TLS requirements](Self#tls).
    pub fn with_encryption_custom_tls_config(mut self, tls_config: ClientConfig) -> Self {
        self.tls_config = Some(tls_config);
        self
    }

    /// Disable TLS encryption.
    pub fn with_encryption_disabled(mut self) -> Self {
        self.tls_config = None;
        self
    }

    fn parse_uri(uri: &str) -> StdResult<ConnectionConfig, ConnectionConfigParseError> {
        let uri = URI::try_from(uri).map_err(URIError)?;

        let (routing, tls_config) = match uri.scheme().as_str() {
            "neo4j" => (true, None),
            "neo4j+s" => (true, Some(tls_helper::secure_tls_config()?)),
            "neo4j+ssc" => (true, Some(tls_helper::self_signed_tls_config())),
            "bolt" => (false, None),
            "bolt+s" => (false, Some(tls_helper::secure_tls_config()?)),
            "bolt+ssc" => (false, Some(tls_helper::self_signed_tls_config())),
            scheme => {
                return Err(ConnectionConfigParseError(format!(
                    "unknown scheme in URI {} expected `neo4j`, `neo4j`, `neo4j+s`, `neo4j+ssc`, \
                         `bolt`, `bolt+s`, or `bolt+ssc`",
                    scheme
                )))
            }
        };

        let authority = uri
            .authority()
            .ok_or(ConnectionConfigParseError(String::from(
                "missing host in URI",
            )))?;
        if authority.has_username() {
            return Err(ConnectionConfigParseError(format!(
                "URI cannot contain a username, found: {}",
                authority.username().unwrap()
            )));
        }
        if authority.has_password() {
            return Err(ConnectionConfigParseError(String::from(
                "URI cannot contain a password",
            )));
        }
        let host = authority.host().to_string();
        let port = authority.port().unwrap_or(DEFAULT_PORT);

        if uri.path() != "/" {
            return Err(ConnectionConfigParseError(format!(
                "URI cannot contain a path, found: {}",
                uri.path()
            )));
        }

        let routing_context = match uri.query() {
            None => {
                if routing {
                    Some(HashMap::new())
                } else {
                    None
                }
            }
            Some(query) => {
                if query.is_empty() {
                    Some(HashMap::new())
                } else {
                    if !routing {
                        return Err(ConnectionConfigParseError(format!(
                            "URI with bolt scheme cannot contain a query \
                                                  (routing context), found: {}",
                            query,
                        )));
                    }
                    Some(Self::parse_query(query)?)
                }
            }
        };

        if let Some(fragment) = uri.fragment() {
            return Err(ConnectionConfigParseError(format!(
                "URI cannot contain a fragment, found: {}",
                fragment
            )));
        }

        Ok(ConnectionConfig {
            address: (host, port).into(),
            routing_context,
            tls_config,
        })
    }

    fn parse_query(
        query: &Query,
    ) -> StdResult<HashMap<String, ValueSend>, ConnectionConfigParseError> {
        let mut result = HashMap::new();
        let mut query = query.to_owned();
        query.normalize();
        for key_value in query.split('&') {
            let mut elements: Vec<_> = key_value.split('=').take(3).collect();
            if elements.len() != 2 {
                return Err(ConnectionConfigParseError(format!(
                    "couldn't parse key=value pair '{}' in '{}'",
                    key_value, query
                )));
            }
            let value = elements.pop().unwrap();
            let key = elements.pop().unwrap();
            if key == "address" {
                return Err(ConnectionConfigParseError(format!(
                    "routing context cannot contain key 'address', found: {}",
                    value
                )));
            }
            result.insert(key.into(), value.into());
        }
        Ok(result)
    }
}

impl TryFrom<&str> for ConnectionConfig {
    type Error = ConnectionConfigParseError;

    fn try_from(value: &str) -> StdResult<Self, Self::Error> {
        Self::parse_uri(value)
    }
}

impl FromStr for ConnectionConfig {
    type Err = ConnectionConfigParseError;

    fn from_str(s: &str) -> StdResult<Self, Self::Err> {
        Self::parse_uri(s)
    }
}

/// Used when an attempt to configure TLS failed.
///
/// See also [`ConnectionConfig::with_encryption_trust_default_cas()`],
/// [`ConnectionConfig::with_encryption_trust_custom_cas()`],
/// [`ConnectionConfig::with_encryption_trust_any_certificate()`].
#[derive(Debug, Error)]
#[non_exhaustive]
#[error("{message}")]
pub struct TlsConfigError {
    pub message: String,
    pub config: ConnectionConfig,
}

/// Used when an attempt to parse a URL into a [`ConnectionConfig`] failed.
#[derive(Debug, Error)]
#[error("{0}")]
pub struct ConnectionConfigParseError(String);

impl ConnectionConfigParseError {}

#[derive(Debug)]
struct URIError(uriparse::URIError);

impl From<URIError> for ConnectionConfigParseError {
    fn from(e: URIError) -> Self {
        ConnectionConfigParseError(format!("couldn't parse URI {}", e.0))
    }
}

impl From<TlsConfigError> for ConnectionConfigParseError {
    fn from(e: TlsConfigError) -> Self {
        ConnectionConfigParseError(format!("couldn't configure TLS {e}"))
    }
}

impl From<String> for ConnectionConfigParseError {
    fn from(e: String) -> Self {
        ConnectionConfigParseError(e)
    }
}

/// Used when configuring a fetch size out of bounds.
///
/// See also [`DriverConfig::with_fetch_size()`], [`SessionConfig::with_fetch_size()`].
#[derive(Debug, Error)]
#[error("fetch size must be <= i64::MAX")]
pub struct ConfigureFetchSizeError<Builder> {
    pub builder: Builder,
}

/// Used when configuring a routing context that is invalid.
///
/// See also [`ConnectionConfig::with_routing_context()`].
#[derive(Debug, Error)]
#[error("routing context invalid because it {it}")]
pub struct InvalidRoutingContextError<Builder> {
    pub builder: Builder,
    it: &'static str,
}

#[double]
use mockable::tls_helper;

#[cfg_attr(test, allow(dead_code))]
mod mockable {
    #[cfg(test)]
    use mockall::automock;

    #[cfg_attr(test, automock)]
    pub(super) mod tls_helper {
        use std::fs::File;
        use std::io::BufReader;
        use std::path::Path;
        use std::result::Result as StdResult;
        use std::sync::Arc;
        use std::sync::OnceLock;

        use rustls::ClientConfig;
        use rustls::RootCertStore;

        use super::NonVerifyingVerifier;

        static SYSTEM_CERTIFICATES: OnceLock<StdResult<Arc<RootCertStore>, String>> =
            OnceLock::new();

        pub fn secure_tls_config() -> StdResult<ClientConfig, String> {
            // Fails if a provider is already installed. That's fine, too.
            let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

            let root_store = SYSTEM_CERTIFICATES.get_or_init(|| {
                // Fails if a provider is already installed. That's fine, too.
                let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

                let mut root_store = RootCertStore::empty();
                // root_store.add_trust_anchors(webpki_roots::TLS_SERVER_ROOTS.iter().map(|ta| {
                //     rustls::OwnedTrustAnchor::from_subject_spki_name_constraints(
                //         ta.subject,
                //         ta.spki,
                //         ta.name_constraints,
                //     )
                // }));
                let native_certs = rustls_native_certs::load_native_certs()
                    .map_err(|e| format!("failed to load system certificates: {e}"))?;
                let (_, _) = root_store.add_parsable_certificates(native_certs);
                Ok(Arc::new(root_store))
            });
            let root_store = Arc::clone(root_store.as_ref().map_err(Clone::clone)?);
            Ok(ClientConfig::builder()
                .with_root_certificates(root_store)
                .with_no_client_auth())
        }

        #[allow(clippy::needless_lifetimes)] // explicit lifetimes required for automock
        pub fn custom_ca_tls_config<'a, 'b>(
            paths: &'a [&'b Path],
        ) -> StdResult<ClientConfig, String> {
            // Fails if a provider is already installed. That's fine, too.
            let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

            let mut root_store = RootCertStore::empty();
            for path in paths {
                let file = File::open(path)
                    .map_err(|e| format!("failed to open certificate(s) path {path:?}: {e}"))?;
                let mut reader = BufReader::new(file);
                for cert_res in rustls_pemfile::certs(&mut reader) {
                    let cert = cert_res
                        .map_err(|e| format!("failed to load certificate(s) from {path:?}: {e}"))?;
                    root_store.add(cert).map_err(|e| {
                        format!("failed to add certificate(s) from {path:?} to root store: {e}")
                    })?;
                }
            }
            Ok(ClientConfig::builder()
                .with_root_certificates(root_store)
                .with_no_client_auth())
        }

        pub fn self_signed_tls_config() -> ClientConfig {
            // Fails if a provider is already installed. That's fine, too.
            let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

            let root_store = RootCertStore::empty();
            let mut config = ClientConfig::builder()
                .with_root_certificates(root_store)
                .with_no_client_auth();
            config
                .dangerous()
                .set_certificate_verifier(Arc::new(NonVerifyingVerifier::new()));
            config
        }
    }

    mod dangerous {
        use std::result::Result as StdResult;
        use std::sync::Arc;

        use rustls::client::danger::{
            HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier,
        };
        use rustls::client::WebPkiServerVerifier;
        use rustls::Error as RustlsError;
        use rustls::{DigitallySignedStruct, RootCertStore, SignatureScheme};
        use rustls_pki_types::{CertificateDer, Der, ServerName, TrustAnchor, UnixTime};

        /// As the name suggests, this verifier happily accepts any certificate.
        /// This is not secure and should only be used for testing.
        #[derive(Debug)]
        pub(super) struct NonVerifyingVerifier {
            default_verifier: WebPkiServerVerifier,
        }

        impl NonVerifyingVerifier {
            pub fn new() -> Self {
                let default_verifier = WebPkiServerVerifier::builder(Arc::new(RootCertStore {
                    roots: vec![
                        // any certificate will do as we only forward those methods to the default
                        // verifier which do not care about the certificate
                        TrustAnchor {
                            subject: Der::from_slice(b""),
                            subject_public_key_info: Der::from_slice(b""),
                            name_constraints: None,
                        },
                    ],
                }))
                .build()
                .unwrap();
                let default_verifier = Arc::into_inner(default_verifier).unwrap();
                Self { default_verifier }
            }
        }

        impl ServerCertVerifier for NonVerifyingVerifier {
            fn verify_server_cert(
                &self,
                _end_entity: &CertificateDer<'_>,
                _intermediates: &[CertificateDer<'_>],
                _server_name: &ServerName<'_>,
                _ocsp_response: &[u8],
                _now: UnixTime,
            ) -> StdResult<ServerCertVerified, RustlsError> {
                Ok(ServerCertVerified::assertion())
            }

            fn verify_tls12_signature(
                &self,
                message: &[u8],
                cert: &CertificateDer<'_>,
                dss: &DigitallySignedStruct,
            ) -> StdResult<HandshakeSignatureValid, RustlsError> {
                self.default_verifier
                    .verify_tls12_signature(message, cert, dss)
            }

            fn verify_tls13_signature(
                &self,
                message: &[u8],
                cert: &CertificateDer<'_>,
                dss: &DigitallySignedStruct,
            ) -> StdResult<HandshakeSignatureValid, RustlsError> {
                self.default_verifier
                    .verify_tls13_signature(message, cert, dss)
            }

            fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
                self.default_verifier.supported_verify_schemes()
            }
        }
    }

    use dangerous::NonVerifyingVerifier;
}

#[cfg(test)]
mod tests {
    use std::ops::Deref;
    use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

    use rustls::RootCertStore;

    use rstest::*;

    use crate::macros::hash_map;

    use super::*;

    static TLS_HELPER_MTX: OnceLock<Mutex<()>> = OnceLock::new();
    // When a test panics, it will poison the Mutex. Since we don't actually
    // care about the state of the data we ignore that it is poisoned and grab
    // the lock regardless.  If you just do `let _m = &MTX.lock().unwrap()`, one
    // test panicking will cause all other tests that try and acquire a lock on
    // that Mutex to also panic.
    fn get_tls_helper_lock() -> MutexGuard<'static, ()> {
        let mutex = TLS_HELPER_MTX.get_or_init(Default::default);
        mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn get_test_client_config() -> ClientConfig {
        // Fails if a provider is already installed. That's fine, too.
        let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

        let root_store = RootCertStore::empty();
        ClientConfig::builder()
            .with_root_certificates(root_store)
            .with_no_client_auth()
    }

    #[rstest]
    fn test_no_tls_by_default() {
        let address = ("localhost", 7687).into();
        let connection_config = ConnectionConfig::new(address);

        assert!(connection_config.tls_config.is_none());
    }

    #[rstest]
    #[case(None)]
    #[case(Some("bolt://localhost:7687"))]
    #[case(Some("neo4j://localhost:7687"))]
    fn test_no_tls(#[case] uri: Option<&str>) {
        let address = ("localhost", 7687).into();

        let connection_config = match uri {
            None => ConnectionConfig::new(address).with_encryption_disabled(),
            Some(uri) => ConnectionConfig::try_from(uri).unwrap(),
        };

        assert!(connection_config.tls_config.is_none());
    }

    #[rstest]
    #[case(None)]
    #[case(Some("bolt+s://localhost:7687"))]
    #[case(Some("neo4j+s://localhost:7687"))]
    fn test_tls(#[case] uri: Option<&str>) {
        let _m = get_tls_helper_lock();
        let ctx = tls_helper::secure_tls_config_context();
        ctx.expect().returning(|| Ok(get_test_client_config()));

        let address = ("localhost", 7687).into();

        let connection_config = match uri {
            None => ConnectionConfig::new(address)
                .with_encryption_trust_default_cas()
                .unwrap(),
            Some(uri) => ConnectionConfig::try_from(uri).unwrap(),
        };

        connection_config.tls_config.unwrap();
        // testing the tls_config to be the right on, would require mocking rustls::ClientConfig
    }

    #[rstest]
    #[case(Some("bolt+ssc://localhost:7687"))]
    #[case(Some("neo4j+ssc://localhost:7687"))]
    fn test_self_signed_tls(#[case] uri: Option<&str>) {
        let _m = get_tls_helper_lock();
        let ctx = tls_helper::self_signed_tls_config_context();
        ctx.expect().returning(get_test_client_config);

        let address = ("localhost", 7687).into();
        let connection_config = match uri {
            None => ConnectionConfig::new(address).with_encryption_trust_any_certificate(),
            Some(uri) => ConnectionConfig::try_from(uri).unwrap(),
        };

        connection_config.tls_config.unwrap();
    }

    #[rstest]
    fn test_custom_ca_tls() {
        let test_paths = Arc::new(
            ["/foo", "/bar.pem"]
                .into_iter()
                .map(Path::new)
                .collect::<Vec<_>>(),
        );

        let _m = get_tls_helper_lock();
        let ctx = tls_helper::custom_ca_tls_config_context();
        ctx.expect()
            .withf({
                let test_paths = Arc::clone(&test_paths);
                move |paths| paths == test_paths.deref()
            })
            .returning(|_paths| Ok(get_test_client_config()));

        let address = ("localhost", 7687).into();
        let connection_config = ConnectionConfig::new(address)
            .with_encryption_trust_custom_cas(&test_paths)
            .unwrap();

        connection_config.tls_config.unwrap();
    }

    #[rstest]
    #[case("neo4j://example.com", true)]
    #[case("bolt://example.com", false)]
    fn test_parsing_routing(#[case] uri: &str, #[case] routing: bool) {
        let connection_config = ConnectionConfig::try_from(uri);
        let connection_config = connection_config.unwrap();
        assert_eq!(connection_config.routing_context.is_some(), routing);
    }

    #[rstest]
    #[case("neo4j://localhost:7687", "localhost")]
    #[case("neo4j://localhost", "localhost")]
    #[case("neo4j://example.com:7687", "example.com")]
    #[case("neo4j://example.com", "example.com")]
    #[case("neo4j://127.0.0.1:7687", "127.0.0.1")]
    #[case("neo4j://127.0.0.1", "127.0.0.1")]
    #[case("neo4j://[::1]:7687", "[::1]")]
    #[case("neo4j://[::1]", "[::1]")]
    #[case("neo4j://localhost:7687?foo=bar", "localhost")]
    #[case("neo4j://localhost?foo=bar", "localhost")]
    #[case("neo4j://example.com:7687?foo=bar", "example.com")]
    #[case("neo4j://example.com?foo=bar", "example.com")]
    #[case("neo4j://127.0.0.1:7687?foo=bar", "127.0.0.1")]
    #[case("neo4j://127.0.0.1?foo=bar", "127.0.0.1")]
    #[case("neo4j://[::1]:7687?foo=bar", "[::1]")]
    #[case("neo4j://[::1]?foo=bar", "[::1]")]
    #[case("bolt://localhost:7687", "localhost")]
    #[case("bolt://localhost", "localhost")]
    #[case("bolt://example.com:7687", "example.com")]
    #[case("bolt://example.com", "example.com")]
    #[case("bolt://127.0.0.1:7687", "127.0.0.1")]
    #[case("bolt://127.0.0.1", "127.0.0.1")]
    #[case("bolt://[::1]:7687", "[::1]")]
    #[case("bolt://[::1]", "[::1]")]
    fn test_parsing_address(#[case] uri: &str, #[case] host: &str) {
        let connection_config = ConnectionConfig::try_from(uri);
        let connection_config = connection_config.unwrap();
        assert_eq!(connection_config.address.host(), host);
    }

    #[rstest]
    #[case("neo4j://localhost", 7687)]
    #[case("neo4j://localhost:7687", 7687)]
    #[case("neo4j://localhost:1337", 1337)]
    #[case("neo4j://example.com", 7687)]
    #[case("neo4j://example.com:7687", 7687)]
    #[case("neo4j://example.com:1337", 1337)]
    #[case("neo4j://127.0.0.1", 7687)]
    #[case("neo4j://127.0.0.1:7687", 7687)]
    #[case("neo4j://127.0.0.1:1337", 1337)]
    #[case("neo4j://[::1]", 7687)]
    #[case("neo4j://[::1]:7687", 7687)]
    #[case("neo4j://[::1]:1337", 1337)]
    #[case("bolt://localhost", 7687)]
    #[case("bolt://localhost:7687", 7687)]
    #[case("bolt://localhost:1337", 1337)]
    #[case("bolt://example.com", 7687)]
    #[case("bolt://example.com:7687", 7687)]
    #[case("bolt://example.com:1337", 1337)]
    #[case("bolt://127.0.0.1", 7687)]
    #[case("bolt://127.0.0.1:7687", 7687)]
    #[case("bolt://127.0.0.1:1337", 1337)]
    #[case("bolt://[::1]", 7687)]
    #[case("bolt://[::1]:7687", 7687)]
    #[case("bolt://[::1]:1337", 1337)]
    fn test_parsing_port(#[case] uri: &str, #[case] port: u16) {
        let connection_config = ConnectionConfig::try_from(uri);
        let connection_config = connection_config.unwrap();
        assert_eq!(connection_config.address.port(), port);
    }

    #[rstest]
    #[case("", hash_map!())]
    #[case("?", hash_map!())]
    #[case("?foo=bar", hash_map!("foo".into() => "bar".into()))]
    #[case("?n=1", hash_map!("n".into() => "1".into()))]
    #[case("?foo=bar&baz=foobar", hash_map!("foo".into() => "bar".into(), "baz".into() => "foobar".into()))]
    fn test_parsing_routing_context(
        #[values(
            "neo4j://localhost:7687",
            "neo4j://localhost",
            "neo4j://example.com:7687",
            "neo4j://example.com",
            "neo4j://127.0.0.1:7687",
            "neo4j://127.0.0.1",
            "neo4j://[::1]:7687",
            "neo4j://[::1]"
        )]
        uri_base: &str,
        #[case] uri_query: &str,
        #[case] routing_context: HashMap<String, ValueSend>,
    ) {
        let uri: String = format!("{}{}", uri_base, uri_query);
        dbg!(&uri, &routing_context);
        let connection_config = ConnectionConfig::try_from(uri.as_str());
        let connection_config = connection_config.unwrap();
        assert_eq!(connection_config.routing_context, Some(routing_context));
    }
}
