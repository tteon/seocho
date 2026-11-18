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

use std::error::Error as StdError;
use std::fmt::Debug;
use std::io::Result as IoResult;
use std::net::{SocketAddr, ToSocketAddrs};
use std::result::Result as StdResult;
use std::sync::Arc;
use std::vec::IntoIter;

use log::debug;

use super::Address;
use crate::error_::{Neo4jError, Result, UserCallbackError};

// imports for docs
#[allow(unused)]
use crate::driver::DriverConfig;

type BoxError = Box<dyn StdError + Send + Sync>;
/// See [`AddressResolver::resolve()`].
pub type AddressResolverReturn = StdResult<Vec<Address>, BoxError>;

/// A trait for custom address resolution.
///
/// See [`DriverConfig::with_resolver()`].
pub trait AddressResolver: Debug + Send + Sync {
    /// must not return an empty vector
    fn resolve(&self, address: &Address) -> AddressResolverReturn;
    #[cfg(feature = "_internal_testkit_backend")]
    fn dns_resolve(&self, address: &Address) -> IoResult<Vec<SocketAddr>>;
}

#[derive(Debug)]
pub(super) enum CustomResolution {
    NoResolver(Option<Arc<Address>>),
    Resolver(Vec<Arc<Address>>),
}

impl CustomResolution {
    pub(super) fn new(
        address: Arc<Address>,
        resolver: Option<&dyn AddressResolver>,
    ) -> Result<Self> {
        match resolver {
            None => Ok(Self::NoResolver(Some(address))),
            Some(_) if address.is_custom_resolved => Ok(Self::NoResolver(Some(address))),
            Some(resolver) => {
                debug!("custom resolver in: {address}");
                let res = resolver.resolve(&address);
                match res {
                    Ok(mut addrs) => {
                        addrs.iter_mut().for_each(|a| a.is_custom_resolved = true);
                        let addrs = addrs.into_iter().rev().map(Arc::new).collect::<Vec<_>>();
                        debug!(
                            "custom resolver out: {:?}",
                            addrs.iter().map(|a| format!("{a}")).collect::<Vec<_>>()
                        );
                        if addrs.is_empty() {
                            return Err(Neo4jError::InvalidConfig {
                                message: String::from(
                                    "DriverConfig::resolver returned no addresses.",
                                ),
                            });
                        }
                        Ok(Self::Resolver(addrs))
                    }
                    Err(err) => {
                        debug!("custom resolver failed: {:?}", err);
                        Err(Neo4jError::UserCallback {
                            error: UserCallbackError::Resolver(err),
                        })
                    }
                }
            }
        }
    }
}

impl Iterator for CustomResolution {
    type Item = Arc<Address>;

    fn next(&mut self) -> Option<Self::Item> {
        match self {
            CustomResolution::NoResolver(address) => address.take(),
            CustomResolution::Resolver(addresses) => addresses.pop(),
        }
    }
}

#[derive(Debug)]
pub(super) enum DnsResolution {
    AlreadyResolved(Option<Arc<Address>>),
    RealResolution(Option<IoResult<Vec<Arc<Address>>>>),
}

fn dns_resolve(
    address: &Address,
    #[cfg(feature = "_internal_testkit_backend")] resolver: Option<&dyn AddressResolver>,
) -> IoResult<IntoIter<SocketAddr>> {
    #[cfg(feature = "_internal_testkit_backend")]
    {
        match resolver {
            None => address.to_socket_addrs(),
            Some(resolver) => resolver.dns_resolve(address).map(|addrs| addrs.into_iter()),
        }
    }
    #[cfg(not(feature = "_internal_testkit_backend"))]
    {
        address.to_socket_addrs()
    }
}

impl DnsResolution {
    pub(super) fn new(
        address: Arc<Address>,
        #[cfg(feature = "_internal_testkit_backend")] resolver: Option<&dyn AddressResolver>,
    ) -> Self {
        if address.is_dns_resolved {
            Self::AlreadyResolved(Some(address))
        } else {
            debug!("dns resolver in: {address}");
            let res = dns_resolve(
                &address,
                #[cfg(feature = "_internal_testkit_backend")]
                resolver,
            )
            .map(|resolved| {
                resolved
                    .map(|resolved| Address {
                        host: resolved.ip().to_string(),
                        port: resolved.port(),
                        key: address.host.clone(),
                        is_custom_resolved: address.is_custom_resolved,
                        is_dns_resolved: true,
                    })
                    .map(Arc::new)
                    .collect::<Vec<_>>()
            });
            match &res {
                Ok(addrs) => {
                    debug!(
                        "dns resolver out: {:?}",
                        addrs.iter().map(|a| format!("{a}")).collect::<Vec<_>>()
                    );
                }
                Err(err) => {
                    debug!("dns resolver out: {:?}", err);
                }
            }
            Self::RealResolution(Some(res))
        }
    }
}

impl Iterator for DnsResolution {
    type Item = IoResult<Arc<Address>>;

    fn next(&mut self) -> Option<Self::Item> {
        match self {
            DnsResolution::AlreadyResolved(address) => address.take().map(Ok),
            DnsResolution::RealResolution(res) => match res {
                None => None,
                Some(Err(_)) => Some(Err(res.take().unwrap().unwrap_err())),
                Some(Ok(resolved)) => resolved.pop().map(Ok),
            },
        }
    }
}
