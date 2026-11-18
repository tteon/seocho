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

#![allow(clippy::option_map_unit_fn)]

//! # Neo4j Bolt Driver
//!
//! This crate provides a driver for the Neo4j graph database.
//! It's designed to mirror many concepts of the official drivers while leveraging Rust's expressive
//! type system and lifetime management to provide a safer API that prevents many common pitfalls
//! already at compile time.
//!
//! ## Compatibility
// [bolt-version-bump] search tag when changing bolt version support
//! This driver supports bolt protocol version 4.4, and 5.0 - 5.8.
//! This corresponds to Neo4j versions 4.4, and the whole 5.x series.
//! Newer versions of Neo4j are supported as long as they keep support for at least one of the
//! protocol versions mentioned above.
//! For details of bolt protocol compatibility, see the
//! [official Neo4j documentation](https://7687.org/bolt-compatibility/).
//!
//! ## Basic Example
//! ```
//! use std::sync::Arc;
//!
//! use neo4j::address::Address;
//! use neo4j::driver::auth::AuthToken;
//! use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, RoutingControl};
//! use neo4j::retry::ExponentialBackoff;
//! use neo4j::{value_map, ValueReceive};
//!
//! let host = "localhost";
//! # let host = doc_test_utils::get_host();
//! let port = 7687;
//! # let port = doc_test_utils::get_port();
//! let user = "neo4j";
//! # let user = doc_test_utils::get_user();
//! let password = "pass";
//! # let password = doc_test_utils::get_password();
//! let database = "neo4j";
//!
//! let database = Arc::new(String::from(database));
//! let address = Address::from((host, port));
//! let auth_token = AuthToken::new_basic_auth(user, password);
//! let driver = Driver::new(
//!     // tell the driver where to connect to
//!     ConnectionConfig::new(address),
//!     // configure how the driver works locally (e.g., authentication)
//!     DriverConfig::new().with_auth(Arc::new(auth_token)),
//! );
//!
//! // Driver::execute_query() is the easiest way to run a query.
//! // It will be sufficient for most use-cases and allows the driver to apply some optimizations.
//! // So it's recommended to use it whenever possible.
//! // For more control, see sessions and transactions.
//! let result = driver
//!     // Run a CYPHER query against the DBMS.
//!     .execute_query("RETURN $x AS x")
//!     // Always specify the database when you can (also applies to using sessions).
//!     // This will let the driver work more efficiently.
//!     .with_database(database)
//!     // Tell the driver to send the query to a read server.
//!     // In a clustered environment, this will make sure that read queries don't overload
//!     // the single write server.
//!     .with_routing_control(RoutingControl::Read)
//!     // Use query parameters (instead of string interpolation) to avoid injection attacks and
//!     // improve performance.
//!     .with_parameters(value_map!({"x": 123}))
//!     // For more resilience, use retry policies.
//!     // Especially in a clustered environment, this will help to recover from transient errors
//!     // like those caused by leader elections, which are to be expected.
//!     .run_with_retry(ExponentialBackoff::default());
//! println!("{:?}", result);
//!
//! let result = result.unwrap();
//! assert_eq!(result.records.len(), 1);
//! for mut record in result.records {
//!     assert_eq!(record.values().count(), 1);
//!     assert_eq!(record.take_value("x"), Some(ValueReceive::Integer(123)));
//! }
//! ```
//!
//! ## Concepts
//!
//! ### The Driver
//! The fundamental type of this crate is the [`Driver`].
//! Through it, all database interactions are performed.
//! See [`Driver::new()`].
//! The driver manages a connection pool. So there is no need to pool driver objects.
//! Usually, each application will use one global driver.
//!
//! ### Sessions
//! Sessions are spawned from the driver.
//! See [`Driver::session()`].
//! Session creation is cheap, it's recommended to create a new session for each piece of work
//! (unless when using [`Driver::execute_query()`]).
//! Sessions will borrow connections from the driver's pool as needed.
//!
//! ### Main Mechanisms for Query Execution
//! There are three main ways to execute queries:
//! - [`Driver::execute_query()`] is the easiest way to run a query.
//!   Prefer it whenever possible as it most efficient.
//! - [`Session::transaction()`] gives you full control over the transaction.
//! - [`Session::auto_commit()`] is a special method for running queries that manage their own
//!   transactions, such as `CALL {...} IN TRANSACTION`.
//!
//! ### Causal Consistency
//! By default, Neo4j clusters are eventually consistent:
//! a write transaction executed on the leader (write node) will sooner or later be visible to read
//! transactions on all followers (read nodes).
//! To provide stronger guarantees, the server sends a bookmark to the client after every
//! successful transaction that applies a write.
//! These bookmarks are abstract tokens that represent some state of the database.
//! By passing them back to the server along with a transaction, the client requests the server to
//! wait until the state(s) represented by the bookmark(s) have been established before executing
//! the transaction.
//!
//! To point out the obvious: relying on bookmarks can be slow because of the wait described above.
//! Not using them, however, can lead to stale reads which will be unacceptable in some cases.
//!
//! See also [`Bookmarks`].
//!
//! #### Methods for Managing Bookmarks
//!  * The easiest way is to rely on the fact that [`Session`]s will automatically manage
//!    bookmarks for you.
//!    All work run in the same session will be part of the same causal chain.
//!  * Manually passing [`Bookmarks`] between sessions.
//!    See [`Session::last_bookmarks()`] for an example.
//!  * Using a [`BookmarkManager`], which [`Driver::execute_query`] does by default.
//!    See [`SessionConfig::with_bookmark_manager()`], [`Driver::execute_query_bookmark_manager()`].
//!
//! ## Logging
//! The driver uses the [`log`] crate for logging.
//!
//! **Important Notes on Usage:**
//!  * Log messages are *not* considered part of the driver's API.
//!    They may change at any time and don't follow semantic versioning.
//!  * The driver's logs are meant for debugging the driver itself.
//!    Log levels `ERROR` and `WARN` are used liberally to indicate (potential) problems within
//!    abstraction layers of the driver.
//!    If there are problems the user-code needs to be aware of, they will be reported via
//!    [`Result`]s, not log messages.
//!
//! ### Logging Example
//! ```
//! use std::sync::Arc;
//!
//! use env_logger; // example using the env_logger crate
//! use log;
//! use neo4j::driver::{Driver, RoutingControl};
//! use neo4j::retry::ExponentialBackoff;
//!
//! # use doc_test_utils::get_driver;
//!
//! env_logger::builder()
//!     .filter_level(log::LevelFilter::Debug)
//!     .init();
//!
//! let driver: Driver = get_driver();
//! driver
//!     .execute_query("RETURN 1")
//!     .with_database(Arc::new(String::from("neo4j")))
//!     .with_routing_control(RoutingControl::Read)
//!     .run_with_retry(ExponentialBackoff::new())
//!     .unwrap();
//! ```

mod address_;
pub mod driver;
mod error_;
mod macros;
mod sync;
#[cfg(feature = "_internal_testkit_backend")]
pub mod time;
#[cfg(not(feature = "_internal_testkit_backend"))]
mod time;
mod util;
pub mod value;

// imports for docs
#[allow(unused)]
use bookmarks::{BookmarkManager, Bookmarks};
#[allow(unused)]
use driver::record_stream::RecordStream;
#[allow(unused)]
use driver::Driver;
#[allow(unused)]
use session::{Session, SessionConfig};

pub use error_::{Neo4jError, Result};
pub use value::ValueReceive;
pub use value::ValueSend;

/// Address and address resolution.
pub mod address {
    pub use super::address_::resolution::*;
    pub use super::address_::*;
}
/// Bookmarks for [causal consistency](crate#causal-consistency).
pub mod bookmarks {
    pub use super::driver::session::bookmarks::*;
}
/// Error and result types.
pub mod error {
    pub use super::error_::{
        GqlErrorCause, GqlErrorClassification, ServerError, UserCallbackError,
    };
}
/// Retry policies.
pub mod retry {
    pub use super::driver::session::retry::*;
}
/// Session and session configuration.
pub mod session {
    pub use super::driver::session::*;
}
/// Query summary structs (metadata) received via [`RecordStream::consume()`].
pub mod summary {
    pub use super::driver::summary::*;
}
/// Transactions and associated types.
pub mod transaction {
    pub use super::driver::transaction::*;
}

mod private {
    // Trait to prevent traits from being implemented outside of this crate.
    #[allow(dead_code)]
    pub trait Sealed {}
}

#[cfg(test)]
mod test {
    #[cfg(feature = "public-api")]
    #[test]
    fn public_api() {
        // Install a compatible nightly toolchain if it is missing
        rustup_toolchain::install(public_api::MINIMUM_NIGHTLY_RUST_VERSION).unwrap();

        // Build rustdoc JSON
        let rustdoc_json = rustdoc_json::Builder::default()
            .toolchain(public_api::MINIMUM_NIGHTLY_RUST_VERSION)
            .build()
            .unwrap();

        // Derive the public API from the rustdoc JSON
        let public_api = public_api::Builder::from_rustdoc_json(rustdoc_json)
            .omit_blanket_impls(true)
            .build()
            .unwrap();

        // Assert that the public API looks correct
        expect_test::expect_file!["test_data/public-api.txt"].assert_eq(&public_api.to_string());
    }
}
