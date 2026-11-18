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

use std::sync::Arc;
use std::time::Duration;

use super::super::config::auth::AuthToken;
use super::super::config::notification::NotificationFilter;
use super::super::config::ConfigureFetchSizeError;
use super::super::session::bookmarks::{BookmarkManager, Bookmarks};

// imports for docs
#[allow(unused)]
use super::super::config::DriverConfig;
#[allow(unused)]
use super::super::Driver;
#[allow(unused)]
use super::Session;
#[allow(unused)]
use crate::error_::Neo4jError;

/// Configuration for a session.
///
/// # Example
/// ```
/// use std::sync::Arc;
///
/// use neo4j::bookmarks::{bookmark_managers, Bookmarks};
/// use neo4j::driver::auth::AuthToken;
/// use neo4j::driver::{ConnectionConfig, Driver, DriverConfig};
/// use neo4j::session::SessionConfig;
///
/// let database = Arc::new(String::from("neo4j"));
/// let bookmarks = Arc::new(Bookmarks::default());
/// let bookmark_manager = Arc::new(bookmark_managers::simple(None));
/// let impersonated_user = Arc::new(String::from("robsdedude"));
/// let auth_token = Arc::new(AuthToken::new_basic_auth(
///     "someone_who_can_impersonate_robsdedude",
///     "super secret password",
/// ));
///
/// let config = SessionConfig::new()
///     // Chose which database to run the query on.
///     // If omitted, the default or home database is used.
///     // Always specify this, if possible, to allow the driver to run more efficiently.
///     .with_database(database)
///     // Use these bookmarks to build a causal chain.
///     .with_bookmarks(bookmarks)
///     // Use this bookmark manager to build a causal chain.
///     .with_bookmark_manager(bookmark_manager)
///     // Impersonate the specified user.
///     // This means all queries will be run in the security context of that user.
///     // The current user must have the appropriate privileges to impersonate the specified user.
///     .with_impersonated_user(impersonated_user)
///     // Change the fetch size to fetch all records at once.
///     .with_fetch_all()
///     // Use the given authentication token for the duration of the session.
///     // This requires Neo4j 5.5 or newer.
///     .with_session_auth(auth_token);
/// ```
///
/// See also [`Driver::session()`].
#[derive(Debug, Clone, Default)]
pub struct SessionConfig {
    pub(crate) database: Option<Arc<String>>,
    pub(crate) bookmarks: Option<Arc<Bookmarks>>,
    pub(crate) impersonated_user: Option<Arc<String>>,
    pub(crate) fetch_size: Option<i64>,
    pub(crate) auth: Option<Arc<AuthToken>>,
    pub(crate) bookmark_manager: Option<Arc<dyn BookmarkManager>>,
    pub(crate) notification_filter: NotificationFilter,
}

impl SessionConfig {
    /// Create a new session config with default settings.
    ///
    /// This is equivalent to [`SessionConfig::default()`].
    #[inline]
    pub fn new() -> SessionConfig {
        Self::default()
    }

    /// Choose which database to run the transactions on.
    ///
    /// Always specify this, if possible, to allow the driver to run more efficiently.
    #[inline]
    pub fn with_database(mut self, database: Arc<String>) -> Self {
        self.database = Some(database);
        self
    }

    /// Use the default or home database as configured on the server.
    #[inline]
    pub fn with_default_database(mut self) -> Self {
        self.database = None;
        self
    }

    /// Use these bookmarks to build a [causal chain](crate#causal-consistency).
    ///
    /// See [`Session::last_bookmarks`] for a full example.
    ///
    /// If [`SessionConfig::with_bookmark_manager`] is also used, both will be used to build the
    /// causal chain.  
    /// N.B., the bookmark manager will not be updated with the bookmarks from this method.
    #[inline]
    pub fn with_bookmarks(mut self, bookmarks: Arc<Bookmarks>) -> Self {
        self.bookmarks = Some(bookmarks);
        self
    }

    /// Don't use any bookmarks.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_bookmarks(mut self) -> Self {
        self.bookmarks = None;
        self
    }

    /// Use this bookmark manager to build a [causal chain](crate#causal-consistency).
    #[inline]
    pub fn with_bookmark_manager(mut self, manager: Arc<dyn BookmarkManager>) -> Self {
        self.bookmark_manager = Some(manager);
        self
    }

    /// Don't use any bookmark manager.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_bookmark_manager(mut self) -> Self {
        self.bookmark_manager = None;
        self
    }

    /// Impersonate the specified user.
    ///
    /// This means all queries will be run in the security context of that user.
    /// The current user must have the appropriate privileges to impersonate the specified user.
    #[inline]
    pub fn with_impersonated_user(mut self, user: Arc<String>) -> Self {
        self.impersonated_user = Some(user);
        self
    }

    /// Don't impersonate any user.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_impersonated_user(mut self) -> Self {
        self.impersonated_user = None;
        self
    }

    /// Change the fetch size to fetch `fetch_size` records at once.
    ///
    /// The driver pulls records from the server in batches.
    /// This is the number of records in each batch.
    ///
    /// Choose a higher value to reduce the number of round trips to the server.
    /// Choose a lower value to avoid buffer overflows on server and client.
    ///
    /// The driver will not process records in advance, even if they are already fetched.
    /// Therefore, latency is not affected by this setting (except for the extra round trips
    /// explained above).
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
    ///
    /// See also [`SessionConfig::with_fetch_all()`] and
    /// [`SessionConfig::with_default_fetch_size()`].
    #[inline]
    pub fn with_fetch_size(
        mut self,
        fetch_size: u64,
    ) -> Result<Self, ConfigureFetchSizeError<Self>> {
        match i64::try_from(fetch_size) {
            Ok(fetch_size) => {
                self.fetch_size = Some(fetch_size);
                Ok(self)
            }
            Err(_) => Err(ConfigureFetchSizeError { builder: self }),
        }
    }

    /// Fetch all records at once.
    ///
    /// See also [`SessionConfig::with_fetch_size()`].
    #[inline]
    pub fn with_fetch_all(mut self) -> Self {
        self.fetch_size = Some(-1);
        self
    }

    /// Use the default fetch size.
    ///
    /// Currently, this is `1000`.
    /// This is an implementation detail and may change in the future.
    ///
    /// See also [`SessionConfig::with_fetch_size()`].
    #[inline]
    pub fn with_default_fetch_size(mut self) -> Self {
        self.fetch_size = None;
        self
    }

    /// Use the given authentication token for the duration of the session.
    ///
    /// This requires Neo4j 5.5 or newer.
    /// For older versions, this will result in a [`Neo4jError::InvalidConfig`].
    #[inline]
    pub fn with_session_auth(mut self, auth: Arc<AuthToken>) -> Self {
        self.auth = Some(auth);
        self
    }

    /// Use the authentication token from the driver config.
    ///
    /// This is the *default*.
    #[inline]
    pub fn without_session_auth(mut self) -> Self {
        self.auth = None;
        self
    }

    /// Configure what notifications the driver should receive from the DBMS for this session.
    ///
    /// This requires Neo4j 5.7 or newer.
    /// For older versions, this will result in a [`Neo4jError::InvalidConfig`].
    ///
    /// See also [`DriverConfig::with_notification_filter()`].
    #[inline]
    pub fn with_notification_filter(mut self, notification_filter: NotificationFilter) -> Self {
        self.notification_filter = notification_filter;
        self
    }

    /// Use the default notification filters.
    ///
    /// This means using the filter configured in the driver.
    ///
    /// See also [`DriverConfig::with_notification_filter()`].
    #[inline]
    pub fn with_default_notification_filter(mut self) -> Self {
        self.notification_filter = Default::default();
        self
    }
}

impl AsRef<SessionConfig> for SessionConfig {
    #[inline]
    fn as_ref(&self) -> &SessionConfig {
        self
    }
}

#[derive(Debug)]
pub(crate) struct InternalSessionConfig {
    pub(crate) config: SessionConfig,
    pub(crate) idle_time_before_connection_test: Option<Duration>,
    pub(crate) eager_begin: bool,
}
