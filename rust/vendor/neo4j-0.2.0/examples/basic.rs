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

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, RoutingControl};
use neo4j::retry::ExponentialBackoff;
use neo4j::{value_map, ValueReceive};

const HOST: &str = "localhost";
const PORT: u16 = 7687;
const USER: &str = "neo4j";
const PASSWORD: &str = "pass";
const DATABASE: &str = "neo4j";

fn main() {
    let database = Arc::new(String::from(DATABASE));
    let address = Address::from((HOST, PORT));
    let auth_token = AuthToken::new_basic_auth(USER, PASSWORD);
    let driver = Driver::new(
        // tell the driver where to connect to
        ConnectionConfig::new(address),
        // configure how the driver works locally (e.g., authentication)
        DriverConfig::new().with_auth(Arc::new(auth_token)),
    );

    // Driver::execute_query() is the easiest way to run a query.
    // It will be sufficient for most use cases and allows the driver to apply some optimizations.
    // So it's recommended to use it whenever possible.
    // For more control, see sessions and transactions.
    let result = driver
        // Run a CYPHER query against the DBMS.
        .execute_query("RETURN $x AS x")
        // Always specify the database when you can (also applies to using sessions).
        // This will let the driver work more efficiently.
        .with_database(database)
        // Tell the driver to send the query to a read server.
        // In a clustered environment, this will allow make sure that read queries don't overload
        // the write single server.
        .with_routing_control(RoutingControl::Read)
        // Use query parameters (instead of string interpolation) to avoid injection attacks and
        // improve performance.
        .with_parameters(value_map!({"x": 123}))
        // For more resilience, use retry policies.
        .run_with_retry(ExponentialBackoff::default());
    println!("{:?}", result);

    let result = result.unwrap();
    assert_eq!(result.records.len(), 1);
    for mut record in result.records {
        assert_eq!(record.values().count(), 1);
        assert_eq!(record.take_value("x"), Some(ValueReceive::Integer(123)));
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn test_main() {
        main();
    }
}
