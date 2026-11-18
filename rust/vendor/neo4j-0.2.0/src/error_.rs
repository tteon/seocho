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
// use std::backtrace::Backtrace;
use std::fmt::{Display, Formatter};
use std::io;

use log::info;
use thiserror::Error;

use crate::driver::io::bolt::BoltMeta;
use crate::util::concat_str;
use crate::value::ValueReceive;

// imports for docs
#[allow(unused)]
use crate::address_::resolution::AddressResolver;
#[allow(unused)]
use crate::driver::auth::AuthManager;
#[allow(unused)]
use crate::driver::session::bookmarks::BookmarkManager;
#[allow(unused)]
use crate::driver::{DriverConfig, ExecuteQueryBuilder};
#[allow(unused)]
use crate::session::SessionConfig;
#[allow(unused)]
use crate::value::ValueSend;

type BoxError = Box<dyn StdError + Send + Sync>;

#[derive(Error, Debug)]
// #[non_exhaustive]
/// Errors that can occur while using the driver.
///
/// **Important Notes on Usage:**
///  * Error messages are *not* considered part of the driver's API.
///    They may change at any time and don't follow semantic versioning.
///  * The only string in errors that can be (somewhat<sup>1</sup>) reliably used is
///    [`ServerError::code()`].
///
/// <sup>1</sup>The code is received from the server and therefore might still change depending on
/// the server version.
pub enum Neo4jError {
    /// used when
    ///  * experiencing a connectivity error.  
    ///    E.g., not able to connect, a broken socket,
    ///    not able to fetch routing information
    #[error("connection failed: {message} (during commit: {during_commit}){}",
            source.as_ref().map(|err| format!(" caused by: {err}")).unwrap_or_default())]
    #[non_exhaustive]
    Disconnect {
        message: String,
        // #[backtrace]
        // #[from]
        // #[source]
        source: Option<io::Error>,
        /// Will be true when connection was lost while the driver cannot be
        /// sure whether the ongoing transaction has been committed or not.
        /// To recover from this situation, business logic is required to check
        /// whether the transaction should or shouldn't be retried.
        during_commit: bool,
    },

    /// Used when the driver encounters an error caused by user input.
    /// For example:
    ///  * Trying to send a [`ValueSend`] not supported by the DBMS.
    ///    * A too large collection ([`ValueSend::List`], [`ValueSend::Map`])
    ///      (max. [`i64::MAX`] elements).
    ///    * A temporal type representing a leap-second.
    ///    * A temporal value that is out of range.
    ///      The exact conditions for this depend on the protocol version negotiated with the
    ///      server.
    ///  * Connecting with an incompatible server/using a feature that's not
    ///    supported over the negotiated protocol version.
    ///  * Can be generated using [`Neo4jError::from::<GetSingleRecordError>`]
    ///    (the driver itself won't perform this conversion)
    ///  * Trying to use an address resolver ([`DriverConfig::with_resolver()`]) that returns no
    ///    addresses.
    ///  * Configuring the driver's sockets according to [`DriverConfig`] failed.
    ///    * TLS is enabled, but establishing a TLS connection failed.
    ///    * Socket timeouts cannot be set/read.
    ///    * Socket keepalive cannot be set.
    #[error("invalid configuration: {message}")]
    #[non_exhaustive]
    InvalidConfig {
        message: String,
        // backtrace: Backtrace,
    },

    /// Used when:
    ///  * the server returns an error.
    #[error("{error}")]
    #[non_exhaustive]
    ServerError { error: ServerError },

    /// Used when
    ///  * connection acquisition timed out
    ///    ([`DriverConfig::with_connection_acquisition_timeout()`]).
    #[error("{message}")]
    #[non_exhaustive]
    Timeout { message: String },

    /// Used when a user-provided callback failed.
    ///
    /// See [`UserCallbackError`] for more information.
    #[error("{error}")]
    #[non_exhaustive]
    UserCallback { error: UserCallbackError },

    /// If you encounter this error, there's either a bug in the driver or the server.
    /// An unexpected message or message content was received from the server.
    ///
    /// Please consider opening an issue at
    /// <https://github.com/robsdedude/neo4j-rust-driver/issues>.  
    /// Please also **include driver debug logs** (see [Logging](crate#logging)).
    #[error(
        "the driver encountered a protocol violation, \
        this is likely a bug in the driver or the server: {message}"
    )]
    #[non_exhaustive]
    ProtocolError {
        message: String,
        // backtrace: Backtrace
    },
}

impl Neo4jError {
    pub fn is_retryable(&self) -> bool {
        match self {
            Neo4jError::ServerError { error } => error.is_retryable(),
            Neo4jError::Disconnect { during_commit, .. } => !during_commit,
            _ => false,
        }
    }

    pub(crate) fn wrap_read<T>(res: io::Result<T>) -> Result<T> {
        match res {
            Ok(t) => Ok(t),
            Err(err) => Err(Self::read_err(err)),
        }
    }

    pub(crate) fn read_err(err: io::Error) -> Self {
        info!("read error: {}", err);
        Self::Disconnect {
            message: String::from("failed to read"),
            source: Some(err),
            during_commit: false,
        }
    }

    pub(crate) fn wrap_write<T>(res: io::Result<T>) -> Result<T> {
        match res {
            Ok(t) => Ok(t),
            Err(err) => Err(Self::write_error(err)),
        }
    }

    pub(crate) fn write_error(err: io::Error) -> Neo4jError {
        info!("write error: {}", err);
        Self::Disconnect {
            message: String::from("failed to write"),
            source: Some(err),
            during_commit: false,
        }
    }

    pub(crate) fn connect_error(err: io::Error) -> Neo4jError {
        Self::Disconnect {
            message: String::from("failed to open connection"),
            source: Some(err),
            during_commit: false,
        }
    }

    pub(crate) fn wrap_connect<T>(res: io::Result<T>) -> Result<T> {
        match res {
            Ok(t) => Ok(t),
            Err(err) => Err(Self::connect_error(err)),
        }
    }

    pub(crate) fn disconnect<S: Into<String>>(message: S) -> Self {
        Self::Disconnect {
            message: message.into(),
            source: None,
            during_commit: false,
        }
    }

    pub(crate) fn protocol_error<S: Into<String>>(message: S) -> Self {
        Self::ProtocolError {
            message: message.into(),
        }
    }

    pub(crate) fn failed_commit(mut self) -> Self {
        if let Self::Disconnect { during_commit, .. } = &mut self {
            *during_commit = true;
        }
        self
    }

    pub(crate) fn wrap_commit<T>(res: Result<T>) -> Result<T> {
        match res {
            Ok(t) => Ok(t),
            Err(err) => Err(err.failed_commit()),
        }
    }

    pub(crate) fn connection_acquisition_timeout<S: AsRef<str>>(during: S) -> Self {
        Self::Timeout {
            message: format!("connection acquisition timed out while {}", during.as_ref()),
        }
    }

    pub(crate) fn fatal_during_discovery(&self) -> bool {
        match self {
            Neo4jError::ServerError { error } => error.fatal_during_discovery(),
            Neo4jError::InvalidConfig { .. } => true,
            Neo4jError::UserCallback { .. } => true,
            _ => false,
        }
    }
}
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct ServerError {
    pub code: String,
    pub gql_status: String,
    pub message: String,
    pub gql_status_description: String,
    pub gql_raw_classification: Option<String>,
    pub gql_classification: GqlErrorClassification,
    pub diagnostic_record: HashMap<String, ValueReceive>,
    pub cause: Option<Box<GqlErrorCause>>,
    retryable_overwrite: bool,
}

const UNKNOWN_NEO4J_CODE: &str = "Neo.DatabaseError.General.UnknownError";
const UNKNOWN_NEO4J_MESSAGE: &str = "An unknown error occurred.";
const UNKNOWN_GQL_STATUS: &str = "50N42";
const UNKNOWN_GQL_STATUS_DESCRIPTION: &str =
    "error: general processing exception - unexpected error";
#[allow(dead_code)] // will be the error message in a future version of the driver
const UNKNOWN_GQL_MESSAGE: &str = concat_str!(
    UNKNOWN_GQL_STATUS,
    ": Unexpected error has occurred. See debug log for details."
);

impl ServerError {
    fn map_legacy_codes(code: String) -> String {
        match code.as_str() {
            // In 5.0, these errors have been re-classified as ClientError.
            // For backwards compatibility with Neo4j 4.4 and earlier, we re-map
            // them in the driver, too.
            "Neo.TransientError.Transaction.Terminated" => {
                String::from("Neo.ClientError.Transaction.Terminated")
            }
            "Neo.TransientError.Transaction.LockClientStopped" => {
                String::from("Neo.ClientError.Transaction.LockClientStopped")
            }
            _ => code,
        }
    }

    pub(crate) fn from_meta(mut meta: BoltMeta) -> Self {
        let code = match meta.remove("code") {
            Some(ValueReceive::String(code)) => code,
            _ => UNKNOWN_NEO4J_CODE.into(),
        };
        let message = match meta.remove("message") {
            Some(ValueReceive::String(message)) => message,
            _ => UNKNOWN_NEO4J_MESSAGE.into(),
        };
        let gql_status_description = format!("{UNKNOWN_GQL_STATUS_DESCRIPTION}. {message}");
        Self {
            code: Self::map_legacy_codes(code),
            message,
            gql_status: String::from(UNKNOWN_GQL_STATUS),
            gql_status_description,
            gql_raw_classification: None,
            gql_classification: GqlErrorClassification::Unknown,
            diagnostic_record: GqlErrorCause::default_diagnostic_record(),
            cause: None,
            retryable_overwrite: false,
        }
    }

    pub(crate) fn from_meta_gql(mut meta: BoltMeta) -> Self {
        let code = match meta.remove("neo4j_code") {
            Some(ValueReceive::String(code)) => code,
            _ => UNKNOWN_NEO4J_CODE.into(),
        };

        let gql_data = GqlErrorCause::from_meta(meta);

        Self {
            code: Self::map_legacy_codes(code),
            message: gql_data.message,
            gql_status: gql_data.gql_status,
            gql_status_description: gql_data.gql_status_description,
            gql_raw_classification: gql_data.gql_raw_classification,
            gql_classification: gql_data.gql_classification,
            diagnostic_record: gql_data.diagnostic_record,
            cause: gql_data.cause,
            retryable_overwrite: false,
        }
    }

    pub fn code(&self) -> &str {
        &self.code
    }

    pub fn message(&self) -> &str {
        &self.message
    }

    pub fn classification(&self) -> &str {
        self.code.split('.').nth(1).unwrap_or("")
    }

    pub fn category(&self) -> &str {
        self.code.split('.').nth(2).unwrap_or("")
    }

    pub fn title(&self) -> &str {
        self.code.split('.').nth(3).unwrap_or("")
    }

    pub(crate) fn is_retryable(&self) -> bool {
        self.retryable_overwrite
            || match self.code() {
                "Neo.ClientError.Security.AuthorizationExpired"
                | "Neo.ClientError.Cluster.NotALeader"
                | "Neo.ClientError.General.ForbiddenOnReadOnlyDatabase" => true,
                _ => self.classification() == "TransientError",
            }
    }

    pub(crate) fn fatal_during_discovery(&self) -> bool {
        match self.code() {
            "Neo.ClientError.Database.DatabaseNotFound"
            | "Neo.ClientError.Transaction.InvalidBookmark"
            | "Neo.ClientError.Transaction.InvalidBookmarkMixture"
            | "Neo.ClientError.Statement.TypeError"
            | "Neo.ClientError.Statement.ArgumentError"
            | "Neo.ClientError.Request.Invalid" => true,
            code => {
                code.starts_with("Neo.ClientError.Security.")
                    && code != "Neo.ClientError.Security.AuthorizationExpired"
            }
        }
    }

    pub(crate) fn deactivates_server(&self) -> bool {
        self.code.as_str() == "Neo.TransientError.General.DatabaseUnavailable"
    }

    pub(crate) fn invalidates_writer(&self) -> bool {
        matches!(
            self.code(),
            "Neo.ClientError.Cluster.NotALeader"
                | "Neo.ClientError.General.ForbiddenOnReadOnlyDatabase"
        )
    }

    pub(crate) fn is_security_error(&self) -> bool {
        self.code.starts_with("Neo.ClientError.Security.")
    }

    pub(crate) fn unauthenticates_all_connections(&self) -> bool {
        self.code == "Neo.ClientError.Security.AuthorizationExpired"
    }

    pub(crate) fn overwrite_retryable(&mut self) {
        self.retryable_overwrite = true;
    }

    pub(crate) fn clone(&self) -> Self {
        Self {
            code: self.code.clone(),
            gql_status: self.gql_status.clone(),
            message: self.message.clone(),
            gql_status_description: self.gql_status_description.clone(),
            gql_raw_classification: self.gql_raw_classification.clone(),
            gql_classification: self.gql_classification,
            diagnostic_record: self.diagnostic_record.clone(),
            cause: self.cause.clone(),
            retryable_overwrite: self.retryable_overwrite,
        }
    }

    pub(crate) fn clone_with_reason(&self, reason: &str) -> Self {
        Self {
            code: self.code.clone(),
            gql_status: self.gql_status.clone(),
            message: format!("{}: {}", reason, self.message),
            gql_status_description: self.gql_status_description.clone(),
            gql_raw_classification: self.gql_raw_classification.clone(),
            gql_classification: self.gql_classification,
            diagnostic_record: self.diagnostic_record.clone(),
            cause: self.cause.clone(),
            retryable_overwrite: self.retryable_overwrite,
        }
    }
}

impl Display for ServerError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "server error: {} (code: {}, gql_status: {})",
            self.message, self.code, self.gql_status,
        )?;
        if let Some(cause) = &self.cause {
            write!(f, "\ncaused by: {}", cause)?;
        }
        Ok(())
    }
}

#[derive(Debug, Copy, Clone)]
#[non_exhaustive]
/// See [`ServerError::gql_classification`].
pub enum GqlErrorClassification {
    ClientError,
    DatabaseError,
    TransientError,
    /// Used when the server provides a Classification which the driver is unaware of.
    /// This can happen when connecting to a server newer than the driver or before GQL errors were
    /// introduced.
    Unknown,
}

impl GqlErrorClassification {
    fn from_str(s: &str) -> Self {
        match s {
            "CLIENT_ERROR" => Self::ClientError,
            "DATABASE_ERROR" => Self::DatabaseError,
            "TRANSIENT_ERROR" => Self::TransientError,
            _ => Self::Unknown,
        }
    }
}

#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct GqlErrorCause {
    pub gql_status: String,
    pub message: String,
    pub gql_status_description: String,
    pub gql_raw_classification: Option<String>,
    pub gql_classification: GqlErrorClassification,
    pub diagnostic_record: HashMap<String, ValueReceive>,
    pub cause: Option<Box<GqlErrorCause>>,
}

impl GqlErrorCause {
    pub(crate) fn from_meta(mut meta: BoltMeta) -> Self {
        let mut message = meta
            .remove("message")
            .and_then(|v| v.try_into_string().ok());
        let mut gql_status = meta
            .remove("gql_status")
            .and_then(|v| v.try_into_string().ok());
        let mut description = meta
            .remove("description")
            .and_then(|v| v.try_into_string().ok());
        let diagnostic_record = meta
            .remove("diagnostic_record")
            .and_then(|v| v.try_into_map().ok())
            .unwrap_or_else(Self::default_diagnostic_record);
        let cause = meta
            .remove("cause")
            .and_then(|v| v.try_into_map().ok())
            .map(GqlErrorCause::from_meta)
            .map(Box::new);
        let gql_raw_classification = diagnostic_record
            .get("_classification")
            .and_then(ValueReceive::as_string)
            .cloned();
        let gql_classification = gql_raw_classification
            .as_deref()
            .map(GqlErrorClassification::from_str)
            .unwrap_or(GqlErrorClassification::Unknown);

        if gql_status.is_none() || message.is_none() || description.is_none() {
            gql_status = Some(String::from(UNKNOWN_GQL_STATUS));
            message = Some(String::from(UNKNOWN_GQL_MESSAGE));
            description = Some(String::from(UNKNOWN_GQL_STATUS_DESCRIPTION));
        }
        let gql_status = gql_status.expect("cannot be None because of code above");
        let message = message.expect("cannot be None because of code above");
        let description = description.expect("cannot be None because of code above");

        Self {
            message,
            gql_status,
            gql_status_description: description,
            gql_raw_classification,
            gql_classification,
            diagnostic_record,
            cause,
        }
    }

    fn default_diagnostic_record() -> HashMap<String, ValueReceive> {
        let mut map = HashMap::with_capacity(3);
        map.insert(
            String::from("OPERATION"),
            ValueReceive::String(String::from("")),
        );
        map.insert(
            String::from("OPERATION_CODE"),
            ValueReceive::String(String::from("0")),
        );
        map.insert(
            String::from("CURRENT_SCHEMA"),
            ValueReceive::String(String::from("/")),
        );
        map
    }
}

impl Display for GqlErrorCause {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)?;
        if let Some(cause) = &self.cause {
            write!(f, "\ncaused by: {}", cause)?;
        }
        Ok(())
    }
}

#[derive(Debug, Error)]
#[non_exhaustive]
pub enum UserCallbackError {
    /// The configured [`AddressResolver`] ([`DriverConfig::with_resolver()`]) returned an error.
    #[error("resolver callback failed: {0}")]
    Resolver(BoxError),
    /// The configured [`AuthManager`] ([`DriverConfig::with_auth_manager()`]) returned an error.
    #[error("AuthManager failed: {0}")]
    AuthManager(BoxError),
    /// The configured [`BookmarkManager`]'s [`get_bookmarks()`]([`BookmarkManager::get_bookmarks`])
    /// ([`SessionConfig::with_bookmark_manager()`], [`ExecuteQueryBuilder::with_bookmark_manager`])
    /// returned an error.
    /// In this case, the transaction will not have taken place.
    #[error("BookmarkManager get_bookmarks failed: {0}")]
    BookmarkManagerGet(BoxError),
    /// The configured [`BookmarkManager`]'s
    /// [`update_bookmarks()`]([`BookmarkManager::update_bookmarks`])
    /// ([`SessionConfig::with_bookmark_manager()`], [`ExecuteQueryBuilder::with_bookmark_manager`])
    /// returned an error.
    /// In this case, the transaction will have taken place already.
    #[error("BookmarkManager update_bookmarks failed: {0}")]
    BookmarkManagerUpdate(BoxError),
}

impl UserCallbackError {
    pub fn user_error(&self) -> &dyn StdError {
        match self {
            UserCallbackError::Resolver(err)
            | UserCallbackError::AuthManager(err)
            | UserCallbackError::BookmarkManagerGet(err)
            | UserCallbackError::BookmarkManagerUpdate(err) => err.as_ref(),
        }
    }

    pub fn into_user_error(self) -> BoxError {
        match self {
            UserCallbackError::Resolver(err)
            | UserCallbackError::AuthManager(err)
            | UserCallbackError::BookmarkManagerGet(err)
            | UserCallbackError::BookmarkManagerUpdate(err) => err,
        }
    }
}

pub type Result<T> = std::result::Result<T, Neo4jError>;
