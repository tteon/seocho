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
use std::mem;
use std::sync::Arc;
use std::time::Duration;

use log::{debug, warn};

use crate::address_::Address;
use crate::driver::RoutingControl;
use crate::time::Instant;
use crate::value::ValueReceive;

use thiserror::Error;

#[derive(Debug)]
pub(crate) struct RoutingTable {
    pub(crate) routers: Vec<Arc<Address>>,
    pub(crate) readers: Vec<Arc<Address>>,
    pub(crate) writers: Vec<Arc<Address>>,
    pub(crate) database: Option<Arc<String>>,
    pub(crate) initialized_without_writers: bool,
    created: Instant,
    ttl: Duration,
}

impl RoutingTable {
    pub(crate) fn new(initial_router: Arc<Address>) -> Self {
        Self {
            routers: vec![initial_router],
            readers: Vec::new(),
            writers: Vec::new(),
            database: None,
            initialized_without_writers: true,
            created: Instant::now(),
            ttl: Duration::new(0, 0),
        }
    }

    pub(crate) fn servers_for_mode(&self, mode: RoutingControl) -> &[Arc<Address>] {
        match mode {
            RoutingControl::Read => &self.readers,
            RoutingControl::Write => &self.writers,
        }
    }

    pub(crate) fn try_parse(
        mut data: HashMap<String, ValueReceive>,
    ) -> Result<Self, RoutingTableParseError> {
        let rt = data.remove("rt").ok_or(RoutingTableParseError {
            reason: "top-level key \"rt\" missing",
        })?;
        let mut rt = rt.try_into_map().map_err(|_| RoutingTableParseError {
            reason: "value \"rt\" did not contain a map",
        })?;
        let ttl = rt.remove("ttl").ok_or(RoutingTableParseError {
            reason: "missing \"ttl\"",
        })?;
        let ttl: i64 = ttl.try_into().map_err(|_| RoutingTableParseError {
            reason: "\"ttl\" was not integer",
        })?;
        if ttl < 0 {
            return Err(RoutingTableParseError {
                reason: "negative \"ttl\"",
            });
        }
        let ttl = Duration::from_secs(ttl as u64);
        let db = match rt.remove("db") {
            None => Ok(None),
            Some(ValueReceive::String(db)) => Ok(Some(db)),
            Some(_) => Err(RoutingTableParseError {
                reason: "\"db\" was not string",
            }),
        }?;
        let servers = rt.remove("servers").ok_or(RoutingTableParseError {
            reason: "missing \"servers\"",
        })?;
        let mut routers = Vec::new();
        let mut readers = Vec::new();
        let mut writers = Vec::new();

        let servers = servers
            .try_into_list()
            .map_err(|_| RoutingTableParseError {
                reason: "\"servers\" was not list",
            })?;
        for server in servers.into_iter() {
            match Self::parse_server(server)? {
                (ServerRole::Router, addresses) => routers = addresses,
                (ServerRole::Reader, addresses) => readers = addresses,
                (ServerRole::Writer, addresses) => writers = addresses,
                (_, _) => {}
            }
        }

        let initialized_without_writers = writers.is_empty();
        Ok(Self {
            routers,
            readers,
            writers,
            database: db.map(Arc::new),
            initialized_without_writers,
            created: Instant::now(),
            ttl,
        })
    }

    fn parse_server(
        server: ValueReceive,
    ) -> Result<(ServerRole, Vec<Arc<Address>>), RoutingTableParseError> {
        let mut server = server.try_into_map().map_err(|_| RoutingTableParseError {
            reason: "\"servers\" entry was not map",
        })?;
        let role = server.remove("role").ok_or(RoutingTableParseError {
            reason: "\"servers\" entry missing \"role\"",
        })?;
        let role: String = role.try_into().map_err(|_| RoutingTableParseError {
            reason: "\"servers\" entry missing \"role\" was not string",
        })?;
        let role = match role.as_str().into() {
            ServerRole::Unknown => {
                warn!("ignoring unknown server role {}", role);
                return Ok((ServerRole::Unknown, vec![]));
            }
            role => role,
        };
        let addresses = server.remove("addresses").ok_or(RoutingTableParseError {
            reason: "\"servers\" entry missing \"addresses\"",
        })?;
        let addresses: Vec<ValueReceive> =
            addresses.try_into().map_err(|_| RoutingTableParseError {
                reason: "\"servers\" entry missing \"addresses\" was not list",
            })?;
        let addresses = addresses
            .into_iter()
            .map(|address| {
                let address: String = address.try_into().map_err(|_| RoutingTableParseError {
                    reason: "\"servers\" entry missing \"addresses\" contained non-string",
                })?;
                let address = Address::from(&*address);
                Ok(Arc::new(address))
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok((role, addresses))
    }

    pub(crate) fn is_fresh(&self, mode: RoutingControl) -> bool {
        if self.routers.is_empty() {
            debug!("routing table expired: no routers left {:?}", self);
            return false;
        }
        if self.servers_for_mode(mode).is_empty() {
            debug!(
                "routing table expired: no servers for {:?} mode left {:?}",
                mode, self
            );
            return false;
        }
        if self.created.elapsed() > self.ttl {
            debug!(
                "routing table expired: ttl ({:?}) < age ({:?}) {:?}",
                self.ttl,
                self.created.elapsed(),
                self
            );
            return false;
        }
        debug!("routing table is fresh {:?}", self);
        true
    }

    pub(crate) fn deactivate(&mut self, addr: &Address) {
        self.routers = mem::take(&mut self.routers)
            .into_iter()
            .filter(|a| **a != *addr)
            .collect();
        self.readers = mem::take(&mut self.readers)
            .into_iter()
            .filter(|a| **a != *addr)
            .collect();
        self.deactivate_writer(addr);
    }

    pub(crate) fn deactivate_writer(&mut self, addr: &Address) {
        self.writers = mem::take(&mut self.writers)
            .into_iter()
            .filter(|a| **a != *addr)
            .collect();
    }
}

#[derive(Error, Debug)]
#[error("failed to parse routing table: {reason}")]
pub(crate) struct RoutingTableParseError {
    reason: &'static str,
}

enum ServerRole {
    Router,
    Reader,
    Writer,
    Unknown,
}

impl From<&str> for ServerRole {
    fn from(s: &str) -> Self {
        match s {
            "ROUTE" => ServerRole::Router,
            "READ" => ServerRole::Reader,
            "WRITE" => ServerRole::Writer,
            _ => ServerRole::Unknown,
        }
    }
}
