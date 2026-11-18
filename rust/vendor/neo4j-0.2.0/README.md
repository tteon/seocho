Neo4j Driver for Rust
=====================

Disclaimer:
 * While I'm currently affiliated with Neo4j, this is my private hobby project.
   It's not an officially supported piece of software by Neo4j.
 * This is a work in progress, and it's mostly developed in my spare time.
 * This project is primarily meant for private studies.
   Yet, I decided to publish it as it might be useful for others as well.


## Driver
This is a driver for Neo4j using the [Bolt protocol](https://7687.org) written in and for Rust.
It’s designed to mirror many concepts of the official drivers while leveraging Rust’s expressive type system and lifetime management to provide a safer API that prevents many common pitfalls already at compile time.

### Compatibility
This driver supports bolt protocol version 4.4, and 5.0 - 5.8.
This corresponds to Neo4j versions 4.4, and the whole 5.x series.
Newer versions of Neo4j are supported as long as they keep support for at least one of the protocol versions mentioned above.
For details of bolt protocol compatibility, see the [official Neo4j documentation](https://7687.org/bolt-compatibility/).

### Basic Example

> [!NOTE]
> You can find the full documentation on [docs.rs](https://docs.rs/neo4j/) as well as more examples in the [examples folder](neo4j/examples).

```rust
use std::sync::Arc;

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, RoutingControl};
use neo4j::retry::ExponentialBackoff;
use neo4j::{value_map, ValueReceive};

let host = "localhost";
let port = 7687;
let user = "neo4j";
let password = "pass";
let database = "neo4j";

let database = Arc::new(String::from(database));
let address = Address::from((host, port));
let auth_token = AuthToken::new_basic_auth(user, password);
let driver = Driver::new(
    // tell the driver where to connect to
    ConnectionConfig::new(address),
    // configure how the driver works locally (e.g., authentication)
    DriverConfig::new().with_auth(Arc::new(auth_token)),
);

// Driver::execute_query() is the easiest way to run a query.
// It will be sufficient for most use-cases and allows the driver to apply some optimizations.
// So it's recommended to use it whenever possible.
// For more control, see sessions and transactions.
let result = driver
    // Run a CYPHER query against the DBMS.
    .execute_query("RETURN $x AS x")
    // Always specify the database when you can (also applies to using sessions).
    // This will let the driver work more efficiently.
    .with_database(database)
    // Tell the driver to send the query to a read server.
    // In a clustered environment, this will make sure that read queries don't overload
    // the single write server.
    .with_routing_control(RoutingControl::Read)
    // Use query parameters (instead of string interpolation) to avoid injection attacks and
    // improve performance.
    .with_parameters(value_map!({"x": 123}))
    // For more resilience, use retry policies.
    // Especially in a clustered environment, this will help to recover from transient errors
    // like those caused by leader elections, which are to be expected.
    .run_with_retry(ExponentialBackoff::default());
println!("{:?}", result);

let result = result.unwrap();
assert_eq!(result.records.len(), 1);
for mut record in result.records {
    assert_eq!(record.values().count(), 1);
    assert_eq!(record.take_value("x"), Some(ValueReceive::Integer(123)));
}
```


## MSRV
Currently, this crate's minimum supported Rust version is `1.70`.
A bump in MSRV is considered a minor breaking change.


## Feature Progress
 * [x] (Well-Written) Docs and Examples
 * [ ] Driver
   * [ ] Config
     * [x] `user_agent`
     * [x] `auth`
       * [x] basic
       * [x] kerberos
       * [x] bearer
       * [x] custom
     * [x] `max_connection_pool_size`
     * [x] `fetch_size`
     * [x] `connection_timeout`
     * [x] `connection_acquisition_timeout`
     * [x] `resolver`
     * [x] `max_connection_lifetime`
     * [x] `liveness_check_timeout` (called `idle_time_before_connection_test`)
     * [x] routing and direct connections
     * [x] `keep_alive`
     * [x] `TLS`
     * [ ] mTLS for 2FA
   * [x] `.session`
   * [x] `.supports_multi_db`
   * [x] `.supports_session_auth`
   * [x] `.execute_query`
   * [x] `.verify_connectivity`
   * [x] `.verify_authentication`
   * [x] `.get_server_info`
   * [x] `.is_encrypted`
 * [x] Session
   * [x] Config
     * [x] database
     * [x] bookmarks
     * [x] impersonated_user
     * [x] fetch_size
     * [x] session_auth
   * [x] Auto Commit
   * [x] Transaction
     * [x] Config
       * [x] Metadata
       * [x] Timeout
     * [x] Unmanaged
     * [x] With Retry
 * [ ] Result (`RecordStream`)
   * [x] `.keys`
   * [x] `.consume` (Summary)
     * [x] `.server_info`
       * [x] `.address`
       * [x] `.server_agent`
       * [x] `.protocol_version`
     * [x] `.database`
     * [x] `.query`, `.parameters` (won't implement)
     * [x] `.query_type`
     * [x] `.plan`
     * [x] `.profile`
     * [x] `.notifications`
     * [x] `.counters`
     * [x] `.result_available_after`
     * [x] `.result_consumed_after`
   * [x] `.single`
   * [ ] `.into::<EagerResult>()`
   * [ ] `.closed` (not sure if needed?)
   * [ ] Record
     * [x] most basic functionality
     * [ ] ergonomic way to access by key
 * [x] Bookmark Management
 * [x] Protocol Versions (including features introduced to the official drivers with each version)
   * [x] 4.4
   * [x] 5.0 (utc fix)
   * [x] 5.1 (re-auth)
   * [x] 5.2 (notification filtering)
   * [x] 5.3 (bolt agent)
   * [x] 5.4 (telemetry)
   * [x] 5.5 (never released)
   * [x] 5.6 (GQL notifications)
   * [x] 5.7
     * [x] (GQL errors)
     * [x] (new bolt version handshake)
   * [x] 5.8 (home db resolution cache)
 * [x] Types
   * [x] `Null`
   * [x] `Integer`
   * [x] `Float`
   * [x] `String`
   * [x] `Boolean`
   * [x] `Bytes`
   * [x] `List`
   * [x] `Map`
   * [x] `Node`
   * [x] `Relationship`
   * [x] `Path`
   * [x] `Spatial` (point)
   * [x] `Temporal` (date, time, datetime, duration)
 * [ ] Proper Unit Tests
 * [x] CI
   * [x] rustfmt
   * [x] clippy
   * [x] Unit Tests
     * [x] Public API SemVer Compatibility Tests ([cargo-public-api](https://github.com/enselic/cargo-public-api))
   * [x] Exposed Dependency Types Check ([cargo-check-external-types](https://github.com/awslabs/cargo-check-external-types))
   * [x] TestKit


## Note on async
Currently, there are no plans to add async support until a proper abstraction over multiple runtimes is available so that users can choose their preferred runtime.
As it stands, the async ecosystem would either force this crate to dictate a runtime or do an ugly dance to support multiple runtimes.
Even then, the supported runtimes would be limited to the ones chosen by this crate.
