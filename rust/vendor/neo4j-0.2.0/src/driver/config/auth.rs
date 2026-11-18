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

use std::collections::HashMap;
use std::error::Error as StdError;
use std::fmt::{Debug, Formatter};
use std::result::Result as StdResult;
use std::sync::Arc;
use std::time::Instant as StdInstant;

use parking_lot::Mutex;

use crate::error_::{Neo4jError, Result, ServerError, UserCallbackError};
use crate::time::Instant;
use crate::value::ValueSend;
use crate::value_map;

// imports for docs
#[allow(unused)]
use crate::driver::session::SessionConfig;
#[allow(unused)]
use crate::driver::{DriverConfig, ExecuteQueryBuilder};

type BoxError = Box<dyn StdError + Send + Sync>;
pub type ManagerGetAuthReturn = StdResult<Arc<AuthToken>, BoxError>;
pub type ManagerHandleErrReturn = StdResult<bool, BoxError>;
pub type BasicProviderReturn = StdResult<AuthToken, BoxError>;
pub type BearerProviderReturn = StdResult<(AuthToken, Option<StdInstant>), BoxError>;

/// Contains authentication information for a Neo4j server.
///
/// Can be used with [`DriverConfig::with_auth()`], [`ExecuteQueryBuilder::with_session_auth()`],
/// [`SessionConfig::with_session_auth()`], as well as [`AuthManager`].
#[derive(Debug, Clone, PartialEq)]
pub struct AuthToken {
    pub(crate) data: HashMap<String, ValueSend>,
}

impl AuthToken {
    /// Create a new [`AuthToken`] to be used against servers with disabled authentication.
    pub fn new_none_auth() -> Self {
        Self {
            data: value_map!({
                "scheme": "none",
            }),
        }
    }

    /// Create a new [`AuthToken`] to be used against servers with basic authentication.
    pub fn new_basic_auth(username: impl Into<String>, password: impl Into<String>) -> Self {
        Self {
            data: value_map!({
                "scheme": "basic",
                "principal": username.into(),
                "credentials": password.into(),
            }),
        }
    }

    /// Create a new [`AuthToken`] to be used against servers with basic authentication.
    /// This variant allows to specify a realm.
    pub fn new_basic_auth_with_realm(
        username: impl Into<String>,
        password: impl Into<String>,
        realm: impl Into<String>,
    ) -> Self {
        let mut token = Self::new_basic_auth(username, password);
        token.data.insert("realm".into(), realm.into().into());
        token
    }

    /// Create a new [`AuthToken`] to be used against servers with kerberos authentication.
    pub fn new_kerberos_auth(base64_encoded_ticket: impl Into<String>) -> Self {
        Self {
            data: value_map!({
                "scheme": "kerberos",
                "principal": "",
                "credentials": base64_encoded_ticket.into(),
            }),
        }
    }

    /// Create a new [`AuthToken`] to be used against servers with bearer authentication, e.g., JWT
    /// tokens as often used with SSO providers.
    pub fn new_bearer_auth(base64_encoded_token: impl Into<String>) -> Self {
        Self {
            data: value_map!({
                "scheme": "bearer",
                "credentials": base64_encoded_token.into(),
            }),
        }
    }

    /// Create a new [`AuthToken`] to be used against servers with custom authentication plugins.
    pub fn new_custom_auth(
        principal: Option<String>,
        credentials: Option<String>,
        realm: Option<String>,
        scheme: Option<String>,
        parameters: Option<HashMap<String, ValueSend>>,
    ) -> Self {
        #[inline]
        fn used(arg: &Option<String>) -> bool {
            arg.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
        }

        let mut data = HashMap::with_capacity(
            <bool as Into<usize>>::into(principal.is_some())
                + <bool as Into<usize>>::into(used(&credentials))
                + <bool as Into<usize>>::into(used(&realm))
                + <bool as Into<usize>>::into(used(&scheme))
                + 1
                + <bool as Into<usize>>::into(
                    parameters.as_ref().map(|s| !s.is_empty()).unwrap_or(false),
                ),
        );
        if let Some(principal) = principal {
            data.insert("principal".into(), principal.into());
        }
        if let Some(credentials) = credentials {
            if !credentials.is_empty() {
                data.insert("credentials".into(), credentials.into());
            }
        }
        if let Some(realm) = realm {
            if !realm.is_empty() {
                data.insert("realm".into(), realm.into());
            }
        }
        data.insert("scheme".into(), scheme.into());
        if let Some(parameters) = parameters {
            if !parameters.is_empty() {
                data.insert("parameters".into(), parameters.into());
            }
        }
        Self { data }
    }

    /// Compare the data contained in this [`AuthToken`] with the data contained in another one.
    ///
    /// Data equality is defined like the regular equality ([`PartialEq`]), except for floats
    /// ([`f64`]), which are compared by their bit representation.
    /// Therefore (among other differences), `NaN` == `NaN` and `-0.0` != `0.0`.
    ///
    /// # Example
    /// ```
    /// use neo4j::{ValueSend, value_map};
    /// use neo4j::driver::auth::AuthToken;
    ///
    /// fn eq(a: &ValueSend, b: &ValueSend) -> bool {
    ///     a == b
    /// }
    /// fn data_eq(a: &ValueSend, b: &ValueSend) -> bool {
    ///     fn wrap_in_token(value: &ValueSend) -> AuthToken {
    ///         AuthToken::new_custom_auth(
    ///             None,
    ///             None,
    ///             None,
    ///             None,
    ///             Some(value_map!({"key": value.clone()}))
    ///         )
    ///     }
    ///
    ///     let token1 = wrap_in_token(a);
    ///     let token2 = wrap_in_token(b);
    ///     token1.eq_data(&token2)
    /// }
    ///
    /// let one = ValueSend::Float(1. );
    /// let zero = ValueSend::Float(0.);
    /// let neg_zero = ValueSend::Float(-0.);
    /// let nan = ValueSend::Float(f64::NAN);
    ///
    /// assert!(eq(&one, &one));
    /// assert!(data_eq(&one, &one));
    ///
    /// assert!(eq(&zero, &zero));
    /// assert!(data_eq(&zero, &zero));
    ///
    /// assert!(eq(&zero, &neg_zero));
    /// assert!(!data_eq(&zero, &neg_zero));
    ///
    /// assert!(!eq(&nan, &nan));
    /// assert!(data_eq(&nan, &nan));
    /// ```
    pub fn eq_data(&self, other: &Self) -> bool {
        if std::ptr::eq(self, other) {
            return true;
        }
        if self.data.len() != other.data.len() {
            return false;
        }
        self.data
            .iter()
            .all(|(k1, v2)| other.data.get(k1).is_some_and(|v1| v1.eq_data(v2)))
    }

    /// Get the raw data contained in this [`AuthToken`].
    #[inline]
    pub fn data(&self) -> &HashMap<String, ValueSend> {
        &self.data
    }
}

impl Default for AuthToken {
    fn default() -> Self {
        Self::new_none_auth()
    }
}

/// The `AuthManager` trait allows to implement custom authentication strategies that go beyond
/// configuring a static [`AuthToken`].
///
/// **⚠️ WARNING**:  
///  * Any auth manager implementation must not interact with the driver it is used with to avoid
///    deadlocks.
///  * The [`AuthToken`]s returned by [`AuthManager::get_auth`] must always belong to the same
///    identity.
///    Trying to switch users using an auth manager will result in undefined behavior.
///    Use [`ExecuteQueryBuilder::with_session_auth`] or [`SessionConfig::with_session_auth`] for
///    such use-cases.
///
/// Pre-defined auth manager implementations are available in [`auth_managers#functions`].
pub trait AuthManager: Send + Sync + Debug {
    /// Get the [`AuthToken`] to be used for authentication.
    ///
    /// The driver will call this method whenever it picks up a connection from the pool.
    /// This is expected to happen frequently, so this method should be fast.
    /// A caching strategy should be implemented in the auth manager.
    ///
    /// If the method fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::AuthManager`].
    fn get_auth(&self) -> ManagerGetAuthReturn;

    /// Handle a security error.
    ///
    /// The driver will call this method whenever it receives a security error from the server.
    /// The method returns a boolean indicating whether the error got handled or not.
    /// Handled errors will be marked retryable (see [`Neo4jError::is_retryable()`]).
    /// Therefore, `true` should only be returned if there's hope that the auth manager will resolve
    /// the issue by providing an updated [`AuthToken`] via [`AuthManager::get_auth`] on the next
    /// call.
    ///
    /// If the method fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::AuthManager`].
    fn handle_security_error(
        &self,
        _auth: &Arc<AuthToken>,
        _error: &ServerError,
    ) -> ManagerHandleErrReturn {
        Ok(false)
    }
}

/// Contains pre-defined [`AuthManager`] implementations.
pub mod auth_managers {
    use super::*;

    /// Create a new [`AuthManager`] that always returns the same [`AuthToken`].
    pub fn new_static(auth: AuthToken) -> impl AuthManager {
        StaticAuthManager {
            auth: Arc::new(auth),
        }
    }

    /// Create a new [`AuthManager`] designed for password rotation.
    ///
    /// The provider function is called whenever the server indicates the current auth token is
    /// invalid (`"Neo.ClientError.Security.Unauthorized"` code).
    /// It's supposed to return a new [`AuthToken`] to be used for authentication.  
    /// N.B., this will make the driver retry (when using a retry policy), when configured with
    /// wrong credentials.
    /// Thus, this has the potential to debug authentication issues harder.
    ///
    /// **⚠️ WARNING**:  
    ///  * The `provider` must not interact with the driver it is used with to avoid deadlocks.
    ///  * The [`AuthToken`]s returned by [`AuthManager::get_auth`] must always belong to the same
    ///    identity.
    ///    Trying to switch users using an auth manager will result in undefined behavior.
    ///    Use [`ExecuteQueryBuilder::with_session_auth`] or [`SessionConfig::with_session_auth`]
    ///    for such use-cases.
    ///
    /// If the provider function fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::AuthManager`].
    ///
    /// # Example
    /// TODO
    pub fn new_basic<P: Fn() -> BasicProviderReturn + Sync + Send>(
        provider: P,
    ) -> impl AuthManager {
        BasicAuthManager(Neo4jAuthManager {
            provider,
            handled_codes: ["Neo.ClientError.Security.Unauthorized"],
            cached_auth: Default::default(),
        })
    }

    /// Create a new [`AuthManager`] designed for expiring bearer tokens (SSO).
    ///
    /// The provider function is called whenever the server indicates the current auth token is
    /// invalid (`"Neo.ClientError.Security.Unauthorized"` code) or the has expired
    /// (`"Neo.ClientError.Security.TokenExpired"` code).
    /// It's supposed to return a new [`AuthToken`] to be used for authentication as well as,
    /// optionally, a Duration for how long the token should be considered valid.
    /// If the duration is over, the provider function will be called again on the next occasion
    /// (i.e., when the driver picks up a connection from the pool).  
    /// N.B., this will make the driver retry (when using a retry policy), when configured with
    /// wrong credentials.
    /// Thus, this has the potential to debug authentication issues harder.
    ///
    /// **⚠️ WARNING**:  
    ///  * The `provider` must not interact with the driver it is used with to avoid deadlocks.
    ///  * The [`AuthToken`]s returned by [`AuthManager::get_auth`] must always belong to the same
    ///    identity.
    ///    Trying to switch users using an auth manager will result in undefined behavior.
    ///    Use [`ExecuteQueryBuilder::with_session_auth`] or [`SessionConfig::with_session_auth`]
    ///    for such use-cases.
    ///
    /// If the provider function fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::AuthManager`].
    ///
    /// # Example
    /// TODO
    pub fn new_bearer<P: Fn() -> BearerProviderReturn + Send + Sync>(
        provider: P,
    ) -> impl AuthManager {
        BearerAuthManager(Neo4jAuthManager {
            provider,
            handled_codes: [
                "Neo.ClientError.Security.TokenExpired",
                "Neo.ClientError.Security.Unauthorized",
            ],
            cached_auth: Default::default(),
        })
    }

    pub(crate) fn get_auth(manager: &'_ dyn AuthManager) -> Result<Arc<AuthToken>> {
        manager.get_auth().map_err(|err| Neo4jError::UserCallback {
            error: UserCallbackError::AuthManager(err),
        })
    }

    pub(crate) fn handle_security_error(
        manager: &'_ dyn AuthManager,
        auth: &Arc<AuthToken>,
        error: &ServerError,
    ) -> Result<bool> {
        manager
            .handle_security_error(auth, error)
            .map_err(|err| Neo4jError::UserCallback {
                error: UserCallbackError::AuthManager(err),
            })
    }

    #[derive(Debug)]
    struct StaticAuthManager {
        auth: Arc<AuthToken>,
    }

    impl AuthManager for StaticAuthManager {
        fn get_auth(&self) -> ManagerGetAuthReturn {
            Ok(Arc::clone(&self.auth))
        }
    }

    #[derive(Debug)]
    struct Neo4jAuthCache {
        auth: Arc<AuthToken>,
        expiry: Option<Instant>,
    }

    struct Neo4jAuthManager<P, const N: usize> {
        provider: P,
        handled_codes: [&'static str; N],
        cached_auth: Mutex<Option<Neo4jAuthCache>>,
    }

    impl<P, const N: usize> Neo4jAuthManager<P, N> {
        fn handle_security_error(
            &self,
            auth: &AuthToken,
            error: &ServerError,
        ) -> ManagerHandleErrReturn {
            if !self.handled_codes.contains(&error.code()) {
                return Ok(false);
            }
            let mut cache_guard = self.cached_auth.lock();
            let Some(cached_auth) = &*cache_guard else {
                return Ok(true);
            };
            if auth.eq_data(&cached_auth.auth) {
                *cache_guard = None;
            }
            Ok(true)
        }
    }

    struct BasicAuthManager<P, const N: usize>(Neo4jAuthManager<P, N>);
    impl<P, const N: usize> Debug for BasicAuthManager<P, N> {
        fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
            f.debug_struct("Neo4jBasicAuthManager")
                .field("handled_codes", &self.0.handled_codes)
                .field("cached_auth", &self.0.cached_auth)
                .finish()
        }
    }

    struct BearerAuthManager<P, const N: usize>(Neo4jAuthManager<P, N>);
    impl<P, const N: usize> Debug for BearerAuthManager<P, N> {
        fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
            f.debug_struct("Neo4jBearerAuthManager")
                .field("handled_codes", &self.0.handled_codes)
                .field("cached_auth", &self.0.cached_auth)
                .finish()
        }
    }

    impl<P: Fn() -> BasicProviderReturn + Sync + Send, const N: usize> AuthManager
        for BasicAuthManager<P, N>
    {
        fn get_auth(&self) -> ManagerGetAuthReturn {
            let mut cache_guard = self.0.cached_auth.lock();
            if let Some(cache) = &*cache_guard {
                return Ok(Arc::clone(&cache.auth));
            }
            let auth = Arc::new((self.0.provider)()?);
            *cache_guard = Some(Neo4jAuthCache {
                auth: Arc::clone(&auth),
                expiry: None,
            });
            Ok(auth)
        }

        #[inline]
        fn handle_security_error(
            &self,
            auth: &Arc<AuthToken>,
            error: &ServerError,
        ) -> ManagerHandleErrReturn {
            self.0.handle_security_error(auth, error)
        }
    }

    impl<P: Fn() -> BearerProviderReturn + Sync + Send, const N: usize> AuthManager
        for BearerAuthManager<P, N>
    {
        fn get_auth(&self) -> ManagerGetAuthReturn {
            let mut cache_guard = self.0.cached_auth.lock();
            if let Some(cache) = &*cache_guard {
                let expired = match cache.expiry {
                    Some(expiry) => expiry <= Instant::now(),
                    None => false,
                };
                if !expired {
                    return Ok(Arc::clone(&cache.auth));
                }
                *cache_guard = None;
            }
            let (auth, expiry) = (self.0.provider)()?;
            let auth = Arc::new(auth);
            *cache_guard = Some(Neo4jAuthCache {
                auth: Arc::clone(&auth),
                expiry: expiry.map(Instant::new),
            });
            Ok(auth)
        }

        #[inline]
        fn handle_security_error(
            &self,
            auth: &Arc<AuthToken>,
            error: &ServerError,
        ) -> ManagerHandleErrReturn {
            self.0.handle_security_error(auth, error)
        }
    }
}
