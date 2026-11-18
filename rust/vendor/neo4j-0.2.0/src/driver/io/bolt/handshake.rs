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

use std::fmt::{Debug, Display};
use std::io::{self, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use itertools::Itertools;
use log::Level::Trace;
use log::{debug, log_enabled, trace};
use rustls::ClientConfig;
use socket2::{Socket as Socket2, TcpKeepalive};

use super::super::deadline::DeadlineIO;
use super::super::varint::{read_var_int, write_var_int, ReadVarIntError};
pub(crate) use super::socket::{BufTcpStream, Socket};
use super::{dbg_extra, socket_debug, Bolt};
use crate::address_::Address;
use crate::driver::config::KeepAliveConfig;
use crate::error_::{Neo4jError, Result};
use crate::time::Instant;

const BOLT_MAGIC_PREAMBLE: [u8; 4] = [0x60, 0x60, 0xB0, 0x17];
// [bolt-version-bump] search tag when changing bolt version support
const BOLT_VERSION_OFFER: [u8; 16] = [
    0, 0, 1, 255, // BOLT handshake manifest v1
    0, 2, 8, 5, // BOLT 5.8 - 5.6
    0, 4, 4, 5, // BOLT 5.4 - 5.0
    0, 0, 4, 4, // BOLT 4.4
];
const BOLT_SUPPORTED_CAPABILITIES: u64 = 0;

pub(crate) trait AddressProvider: Debug + Display + ToSocketAddrs + Sized + 'static {
    fn unresolved_host(&self) -> &str;
    fn into_address(self: Arc<Self>) -> Arc<Address>;
}

impl AddressProvider for Address {
    fn unresolved_host(&self) -> &str {
        self.unresolved_host()
    }

    fn into_address(self: Arc<Self>) -> Arc<Address> {
        self
    }
}

pub(crate) trait SocketProvider {
    type RW: Read + Write + Debug;
    type BuffRW: Read + Write + Debug;

    fn connect(&mut self, addr: &Arc<impl AddressProvider>) -> io::Result<Self::RW>;
    fn get_local_port(&mut self, sock: &Self::RW) -> u16;
    fn connect_timeout(&mut self, addr: &SocketAddr, timeout: Duration) -> io::Result<Self::RW>;
    fn set_tcp_keepalive(
        &mut self,
        socket: Self::RW,
        keepalive: Option<KeepAliveConfig>,
    ) -> io::Result<Self::RW>;
    fn shutdown(&mut self, sock: &Self::RW, how: Shutdown) -> io::Result<()>;
    fn new_buffered(&mut self, sock: &Self::RW) -> Result<Self::BuffRW>;
    fn new_deadline_io<'rw, S: Read + Write>(
        &'_ mut self,
        stream: S,
        sock: &'rw Self::RW,
        deadline: Option<Instant>,
    ) -> DeadlineIO<'rw, S>;
    fn new_socket(&mut self, _sock: Self::RW) -> Option<TcpStream> {
        None
    }
}

pub(crate) struct TcpConnector;

// SEOCHO patch (see vendor/neo4j-0.2.0/SEOCHO_PATCHES.md).
fn nodelay(stream: TcpStream) -> io::Result<TcpStream> {
    stream.set_nodelay(true)?;
    Ok(stream)
}

impl SocketProvider for TcpConnector {
    type RW = TcpStream;
    type BuffRW = BufTcpStream;

    #[inline]
    fn connect(&mut self, addr: &Arc<impl AddressProvider>) -> io::Result<Self::RW> {
        // SEOCHO patch: Bolt is request/response; without TCP_NODELAY any
        // message larger than the 8 KiB write buffer stalls ~40 ms (Nagle +
        // delayed ACK).
        TcpStream::connect(&**addr).and_then(nodelay)
    }

    #[inline]
    fn get_local_port(&mut self, sock: &Self::RW) -> u16 {
        sock.local_addr()
            .map(|addr| addr.port())
            .unwrap_or_default()
    }

    #[inline]
    fn connect_timeout(&mut self, addr: &SocketAddr, timeout: Duration) -> io::Result<Self::RW> {
        TcpStream::connect_timeout(addr, timeout).and_then(nodelay)
    }

    #[inline]
    fn set_tcp_keepalive(
        &mut self,
        socket: Self::RW,
        keepalive: Option<KeepAliveConfig>,
    ) -> io::Result<Self::RW> {
        let keep_alive = match keepalive {
            None => return Ok(socket),
            Some(KeepAliveConfig::Default) => TcpKeepalive::new(),
            Some(KeepAliveConfig::CustomTime(time)) => TcpKeepalive::new().with_time(time),
        };
        let socket = Socket2::from(socket);
        socket.set_tcp_keepalive(&keep_alive)?;
        Ok(socket.into())
    }

    #[inline]
    fn shutdown(&mut self, sock: &Self::RW, how: Shutdown) -> io::Result<()> {
        sock.shutdown(how)
    }

    #[inline]
    fn new_buffered(&mut self, sock: &Self::RW) -> Result<Self::BuffRW> {
        BufTcpStream::new(sock, true)
    }

    #[inline]
    fn new_deadline_io<'rw, S: Read + Write>(
        &'_ mut self,
        stream: S,
        sock: &'rw Self::RW,
        deadline: Option<Instant>,
    ) -> DeadlineIO<'rw, S> {
        DeadlineIO::new(stream, deadline, Some(sock))
    }

    #[inline]
    fn new_socket(&mut self, sock: Self::RW) -> Option<TcpStream> {
        Some(sock)
    }
}

pub(crate) fn open<S: SocketProvider>(
    mut socket_provider: S,
    address: Arc<impl AddressProvider>,
    deadline: Option<Instant>,
    connect_timeout: Option<Duration>,
    keep_alive: Option<KeepAliveConfig>,
    tls_config: Option<Arc<ClientConfig>>,
) -> Result<Bolt<Socket<S::BuffRW>>> {
    if log_enabled!(Trace) {
        trace!(
            "{}{}",
            dbg_extra(None, None),
            format!("C: <OPEN> {address:?}")
        );
    } else {
        debug!(
            "{}{}",
            dbg_extra(None, None),
            format!("C: <OPEN> {address}")
        );
    }

    let timeout = combine_connection_timout(connect_timeout, deadline);
    let raw_socket = Neo4jError::wrap_connect(match timeout {
        None => socket_provider.connect(&address),
        Some(timeout) => {
            let mut timeout = timeout;
            each_addr(&*address, |addr| {
                match socket_provider.connect_timeout(addr?, timeout) {
                    Ok(connection) => Ok(connection),
                    Err(e) => {
                        timeout = combine_connection_timout(connect_timeout, deadline)
                            .expect("timeout cannot disappear");
                        Err(e)
                    }
                }
            })
        }
    })?;
    let raw_socket = socket_provider
        .set_tcp_keepalive(raw_socket, keep_alive)
        .map_err(|err| Neo4jError::InvalidConfig {
            message: format!("failed to set tcp keepalive: {}", err),
        })?;
    let local_port = socket_provider.get_local_port(&raw_socket);

    let buffered_socket = socket_provider.new_buffered(&raw_socket)?;
    let mut socket = Socket::new(buffered_socket, address.unresolved_host(), tls_config)?;

    let mut deadline_io = socket_provider.new_deadline_io(&mut socket, &raw_socket, deadline);

    socket_debug!(
        local_port,
        "C: <HANDSHAKE> {:#010X?}",
        u32::from_be_bytes(BOLT_MAGIC_PREAMBLE)
    );
    wrap_socket_write(
        &mut socket_provider,
        &raw_socket,
        local_port,
        deadline_io.write_all(&BOLT_MAGIC_PREAMBLE),
    )?;
    socket_debug!(
        local_port,
        "C: <BOLT> {}",
        BOLT_VERSION_OFFER
            .into_iter()
            .chunks(4)
            .into_iter()
            .map(|chunk| format!(
                "{:#010X?}",
                u32::from_be_bytes(chunk.into_iter().collect::<Vec<_>>().try_into().unwrap())
            ))
            .join(" ")
    );
    wrap_socket_write(
        &mut socket_provider,
        &raw_socket,
        local_port,
        deadline_io.write_all(&BOLT_VERSION_OFFER),
    )?;
    wrap_socket_write(
        &mut socket_provider,
        &raw_socket,
        local_port,
        deadline_io.flush(),
    )?;

    let mut negotiated_version = [0u8; 4];
    wrap_socket_read(
        &mut socket_provider,
        &raw_socket,
        local_port,
        deadline_io.read_exact(&mut negotiated_version),
    )?;

    let version = match negotiated_version {
        [_, _, 1, 255] => {
            // BOLT handshake manifest v1
            handshake_manifest_v1(
                &mut socket_provider,
                &mut deadline_io,
                &raw_socket,
                local_port,
            )?
        }
        _ => {
            socket_debug!(
                local_port,
                "S: <BOLT> {:#010X?}",
                u32::from_be_bytes(negotiated_version)
            );
            wrap_socket_killing(
                &mut socket_provider,
                &raw_socket,
                local_port,
                decode_version_offer(&negotiated_version),
            )?
        }
    };

    Ok(Bolt::new(
        version,
        socket,
        Arc::new(socket_provider.new_socket(raw_socket)),
        Some(local_port),
        address.into_address(),
    ))
}

fn handshake_manifest_v1<S: SocketProvider>(
    socket_provider: &mut S,
    mut read_write: impl Read + Write,
    raw_socket: &S::RW,
    local_port: u16,
) -> Result<(u8, u8)> {
    let offering_count: usize = match read_var_int(&mut read_write) {
        Ok(offering_count) => wrap_socket_killing(
            socket_provider,
            raw_socket,
            local_port,
            offering_count.try_into().map_err(|_| {
                Neo4jError::protocol_error(String::from(
                    "manifest v1 offered versions count too big (didn't fit usize)",
                ))
            }),
        ),
        Err(ReadVarIntError::Io(err)) => {
            wrap_socket_read(socket_provider, raw_socket, local_port, Err(err))
        }
        Err(ReadVarIntError::TooBig) => wrap_socket_killing(
            socket_provider,
            raw_socket,
            local_port,
            Err(Neo4jError::protocol_error(String::from(
                "manifest v1 offered versions count too big (didn't fit u64)",
            ))),
        ),
    }?;
    let mut raw_version_offerings = Vec::with_capacity(offering_count);
    let mut highest_offering = (0, 0);
    let mut offering_buff = [0u8; 4];
    for _ in 0..offering_count {
        wrap_socket_read(
            socket_provider,
            raw_socket,
            local_port,
            read_write.read_exact(&mut offering_buff),
        )?;
        raw_version_offerings.push(u32::from_be_bytes(offering_buff));
        let Some(version) = map_version_offer_range(&offering_buff) else {
            continue;
        };
        if version > highest_offering {
            highest_offering = version;
        }
    }

    let capabilities = match read_var_int(&mut read_write) {
        Ok(capabilities) => Ok(capabilities),
        Err(ReadVarIntError::Io(err)) => {
            wrap_socket_read(socket_provider, raw_socket, local_port, Err(err))
        }
        Err(ReadVarIntError::TooBig) => wrap_socket_killing(
            socket_provider,
            raw_socket,
            local_port,
            Err(Neo4jError::protocol_error(String::from(
                "manifest v1 capabilities bit map too big",
            ))),
        ),
    }?;
    socket_debug!(
        local_port,
        "S: <BOLT> 0x000001FF [{}] {} {:#X?}",
        offering_count,
        raw_version_offerings
            .iter()
            .map(|offering| format!("{:#010X}", offering))
            .join(" "),
        capabilities,
    );

    if highest_offering == (0, 0) {
        return wrap_socket_killing(
            socket_provider,
            raw_socket,
            local_port,
            Err(Neo4jError::InvalidConfig {
                message: String::from("server version not supported"),
            }),
        );
    }

    let selected_capabilities = capabilities & BOLT_SUPPORTED_CAPABILITIES;

    wrap_socket_write(
        socket_provider,
        raw_socket,
        local_port,
        read_write
            .write_all(&[0, 0, highest_offering.1, highest_offering.0])
            .and_then(|_| write_var_int(&mut read_write, selected_capabilities))
            .and_then(|_| read_write.flush()),
    )?;
    socket_debug!(
        local_port,
        "C: <BOLT> {:#010X?} {:#X?}",
        u32::from_be_bytes([0, 0, highest_offering.1, highest_offering.0]),
        selected_capabilities
    );

    Ok(highest_offering)
}

fn decode_version_offer(offer: &[u8; 4]) -> Result<(u8, u8)> {
    if let Some(version) = map_version_offer(offer) {
        return Ok(version);
    }
    match offer {
        [0, 0, 0, 0] => Err(Neo4jError::InvalidConfig {
            message: String::from("server version not supported"),
        }),
        [72, 84, 84, 80] => {
            // "HTTP"
            Err(Neo4jError::InvalidConfig {
                message: format!(
                    "unexpected server handshake response {:?} (looks like HTTP)",
                    offer
                ),
            })
        }
        _ => Err(Neo4jError::InvalidConfig {
            message: format!("unexpected server handshake response {:?}", offer),
        }),
    }
}

// [bolt-version-bump] search tag when changing bolt version support
const BOLT_VERSIONS: [(u8, u8); 9] = [
    // important: ordered by descending preference
    (5, 8),
    (5, 7),
    (5, 6),
    (5, 4),
    (5, 3),
    (5, 2),
    (5, 1),
    (5, 0),
    (4, 4),
];

fn map_version_offer(offer: &[u8; 4]) -> Option<(u8, u8)> {
    let [_, _, minor, major] = *offer;
    let version = (major, minor);
    BOLT_VERSIONS.iter().contains(&version).then_some(version)
}

fn map_version_offer_range(offer: &[u8; 4]) -> Option<(u8, u8)> {
    let [_, range, minor, major] = *offer;
    let offer_range = (major, minor.saturating_sub(range))..=(major, minor);
    BOLT_VERSIONS
        .iter()
        .find(|version| offer_range.contains(version))
        .copied()
}

fn combine_connection_timout(
    connect_timeout: Option<Duration>,
    deadline: Option<Instant>,
) -> Option<Duration> {
    deadline.map(|deadline| {
        let mut time_left = deadline.saturating_duration_since(Instant::now());
        if time_left == Duration::from_secs(0) {
            time_left = Duration::from_nanos(1);
        }
        match connect_timeout {
            None => time_left,
            Some(timeout) => timeout.min(time_left),
        }
    })
}

// copied from std::net
fn each_addr<A: ToSocketAddrs, F, T>(addr: A, mut f: F) -> io::Result<T>
where
    F: FnMut(io::Result<&SocketAddr>) -> io::Result<T>,
{
    let addrs = match addr.to_socket_addrs() {
        Ok(addrs) => addrs,
        Err(e) => return f(Err(e)),
    };
    let mut last_err = None;
    for addr in addrs {
        match f(Ok(&addr)) {
            Ok(l) => return Ok(l),
            Err(e) => last_err = Some(e),
        }
    }
    Err(last_err.unwrap_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "could not resolve to any addresses",
        )
    }))
}

fn wrap_socket_killing<S: SocketProvider, T>(
    socket_provider: &mut S,
    stream: &S::RW,
    local_port: u16,
    res: Result<T>,
) -> Result<T> {
    match res {
        Ok(res) => Ok(res),
        Err(err) => {
            socket_debug!(local_port, "  closing socket because {}", &err);
            let _ = socket_provider.shutdown(stream, Shutdown::Both);
            Err(err)
        }
    }
}

fn wrap_socket_write<S: SocketProvider, T>(
    socket_provider: &mut S,
    stream: &S::RW,
    local_port: u16,
    res: io::Result<T>,
) -> Result<T> {
    match res {
        Ok(res) => Ok(res),
        Err(err) => {
            socket_debug!(local_port, "   write error: {}", err);
            let _ = socket_provider.shutdown(stream, Shutdown::Both);
            Neo4jError::wrap_write(Err(err))
        }
    }
}

fn wrap_socket_read<S: SocketProvider, T>(
    socket_provider: &mut S,
    stream: &S::RW,
    local_port: u16,
    res: io::Result<T>,
) -> Result<T> {
    match res {
        Ok(res) => Ok(res),
        Err(err) => {
            socket_debug!(local_port, "   read error: {}", err);
            let _ = socket_provider.shutdown(stream, Shutdown::Both);
            Neo4jError::wrap_read(Err(err))
        }
    }
}

#[cfg(test)]
mod tests {
    use std::any::Any;
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::fmt::Formatter;
    use std::rc::Rc;
    use std::str::FromStr;
    use std::vec;

    use rstest::*;

    use super::*;

    // [bolt-version-bump] search tag when changing bolt version support
    #[rstest]
    #[case([0, 0, 4, 4], (4, 4))]
    #[case([0, 0, 0, 5], (5, 0))]
    #[case([0, 0, 1, 5], (5, 1))]
    #[case([0, 0, 2, 5], (5, 2))]
    #[case([0, 0, 3, 5], (5, 3))]
    #[case([0, 0, 4, 5], (5, 4))]
    #[case([0, 0, 6, 5], (5, 6))]
    #[case([0, 0, 7, 5], (5, 7))]
    #[case([0, 0, 8, 5], (5, 8))]
    fn test_decode_version_offer(
        #[case] mut offer: [u8; 4],
        #[case] expected: (u8, u8),
        #[values([0, 0], [1, 2], [255, 254])] garbage: [u8; 2],
    ) {
        offer[0..2].copy_from_slice(&garbage);
        assert_eq!(decode_version_offer(dbg!(&offer)).unwrap(), expected);
    }

    #[test]
    fn test_unsupported_server_version() {
        let res = decode_version_offer(&[0, 0, 0, 0]);
        let Err(Neo4jError::InvalidConfig { message }) = res else {
            panic!("Expected InvalidConfig error, got {:?}", res);
        };
        let message = message.to_lowercase();
        assert!(message.contains("server version not supported"));
    }

    #[test]
    fn test_server_version_looks_like_http() {
        let res = decode_version_offer(&[72, 84, 84, 80]);
        let Err(Neo4jError::InvalidConfig { message }) = res else {
            panic!("Expected InvalidConfig error, got {:?}", res);
        };
        let message = message.to_lowercase();
        assert!(message.contains("unexpected server handshake response"));
        assert!(message.contains("looks like http"));
    }

    // [bolt-version-bump] search tag when changing bolt version support
    #[rstest]
    #[case([0, 0, 0, 1])] // driver didn't offer version 1
    #[case([0, 0, 0, 2])] // driver didn't offer version 2
    #[case([0, 0, 0, 3])] // driver didn't offer version 3
    #[case([0, 0, 0, 4])] // driver didn't offer version 4.0
    #[case([0, 0, 1, 4])] // driver didn't offer version 4.1
    #[case([0, 0, 2, 4])] // driver didn't offer version 4.2
    #[case([0, 0, 3, 4])] // driver didn't offer version 4.3
    #[case([0, 0, 5, 5])] // driver didn't offer version 5.5
    #[case([0, 0, 9, 5])] // driver didn't offer version 5.8
    #[case([0, 0, 0, 6])] // driver didn't offer version 6.0
    fn test_garbage_server_version(
        #[case] mut offer: [u8; 4],
        #[values([0, 0], [1, 2], [255, 254])] garbage: [u8; 2],
    ) {
        offer[0..2].copy_from_slice(&garbage);
        let res = decode_version_offer(&offer);
        let Err(Neo4jError::InvalidConfig { message }) = res else {
            panic!("Expected InvalidConfig error, got {:?}", res);
        };
        let message = message.to_lowercase();
        assert!(message.contains("unexpected server handshake response"));
    }

    #[derive(Debug)]
    struct MockSocket {
        received_data: VecDeque<u8>,
        response: VecDeque<u8>,
    }

    #[derive(Debug, Clone)]
    struct RRMockSocket(Rc<RefCell<MockSocket>>);

    impl RRMockSocket {
        fn new(sock: MockSocket) -> Self {
            Self(Rc::new(RefCell::new(sock)))
        }
    }

    impl Read for RRMockSocket {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            self.0.borrow_mut().response.read(buf)
        }
    }

    impl Write for RRMockSocket {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.0.borrow_mut().received_data.write(buf)
        }

        fn flush(&mut self) -> io::Result<()> {
            self.0.borrow_mut().received_data.flush()
        }
    }

    #[derive(Debug)]
    struct MockAddress {
        host: String,
        resolves_to: Vec<SocketAddr>,
    }

    impl MockAddress {
        fn new_localhost() -> Self {
            Self {
                host: "localhost".to_string(),
                resolves_to: vec![SocketAddr::from(([127, 0, 0, 1], 7687))],
            }
        }

        fn get_single_socket_addr(&self) -> SocketAddr {
            assert_eq!(self.resolves_to.len(), 1);
            self.resolves_to[0]
        }
    }

    impl Display for MockAddress {
        fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
            Debug::fmt(self, f)
        }
    }

    impl ToSocketAddrs for MockAddress {
        type Iter = vec::IntoIter<SocketAddr>;

        fn to_socket_addrs(&self) -> io::Result<Self::Iter> {
            Ok(self.resolves_to.clone().into_iter())
        }
    }

    impl AddressProvider for MockAddress {
        fn unresolved_host(&self) -> &str {
            &self.host
        }

        fn into_address(self: Arc<Self>) -> Arc<Address> {
            Arc::new(Address::from((self.host.as_str(), 1234)))
        }
    }

    struct MockSocketProvider {
        spawn: Box<dyn FnMut() -> io::Result<MockSocket>>,
        spawned: VecDeque<RRMockSocket>,
        calls: Vec<MockSocketProviderCall>,
    }

    impl MockSocketProvider {
        fn new<F: FnMut() -> io::Result<MockSocket> + 'static>(spawn: F) -> Self {
            Self {
                spawn: Box::new(spawn),
                spawned: VecDeque::new(),
                calls: Vec::new(),
            }
        }

        fn new_handshake_5_0() -> Self {
            Self::new(|| {
                Ok(MockSocket {
                    received_data: VecDeque::new(),
                    response: [0, 0, 0, 5].into_iter().collect(),
                })
            })
        }

        fn new_handshake_5_0_failing_first_n(mut n: usize) -> Self {
            Self::new(move || {
                if n == 0 {
                    Ok(MockSocket {
                        received_data: VecDeque::new(),
                        response: [0, 0, 0, 5].into_iter().collect(),
                    })
                } else {
                    n -= 1;
                    Err(io::Error::new(io::ErrorKind::Other, "scripted failure"))
                }
            })
        }
    }

    #[derive(Debug, Clone)]
    #[allow(dead_code)]
    enum MockSocketProviderCall {
        Connect(Arc<MockAddress>),
        ConnectTimeout {
            timeout: Duration,
            address: SocketAddr,
        },
        SetTcpKeepalive {
            keepalive: Option<KeepAliveConfig>,
        },
        Shutdown {
            how: Shutdown,
        },
        NewBuffered,
        NewDeadlineIO {
            deadline: Option<Instant>,
        },
    }

    impl SocketProvider for &mut MockSocketProvider {
        type RW = RRMockSocket;
        type BuffRW = RRMockSocket;

        fn connect(&mut self, addr: &Arc<impl AddressProvider>) -> io::Result<Self::RW> {
            let address_any: Box<dyn Any + 'static> = Box::new(Arc::clone(addr));
            let mock_address = address_any
                .downcast::<Arc<MockAddress>>()
                .expect("MockSocketProvider can only be used with MockAddress");

            self.calls
                .push(MockSocketProviderCall::Connect(*mock_address));
            let sock = RRMockSocket::new((self.spawn)()?);
            self.spawned.push_back(sock.clone());
            Ok(sock)
        }

        fn get_local_port(&mut self, _sock: &Self::RW) -> u16 {
            1234
        }

        fn connect_timeout(
            &mut self,
            addr: &SocketAddr,
            timeout: Duration,
        ) -> io::Result<Self::RW> {
            self.calls.push(MockSocketProviderCall::ConnectTimeout {
                timeout,
                address: *addr,
            });
            let sock = RRMockSocket::new((self.spawn)()?);
            self.spawned.push_back(sock.clone());
            Ok(sock)
        }

        fn set_tcp_keepalive(
            &mut self,
            socket: Self::RW,
            keepalive: Option<KeepAliveConfig>,
        ) -> io::Result<Self::RW> {
            self.calls
                .push(MockSocketProviderCall::SetTcpKeepalive { keepalive });
            Ok(socket)
        }

        fn shutdown(&mut self, _sock: &Self::RW, how: Shutdown) -> io::Result<()> {
            self.calls.push(MockSocketProviderCall::Shutdown { how });
            Ok(())
        }

        fn new_buffered(&mut self, sock: &Self::RW) -> Result<Self::BuffRW> {
            self.calls.push(MockSocketProviderCall::NewBuffered);
            Ok(sock.clone())
        }

        fn new_deadline_io<'rw, S: Read + Write>(
            &'_ mut self,
            stream: S,
            _sock: &'rw Self::RW,
            deadline: Option<Instant>,
        ) -> DeadlineIO<'rw, S> {
            self.calls
                .push(MockSocketProviderCall::NewDeadlineIO { deadline });
            DeadlineIO::new(stream, deadline, None)
        }
    }

    #[test]
    fn test_open() {
        let mut provider = MockSocketProvider::new_handshake_5_0();
        let bolt = open(
            &mut provider,
            Arc::new(MockAddress::new_localhost()),
            None,
            None,
            None,
            None,
        )
        .unwrap();

        assert_eq!(bolt.protocol_version(), (5, 0));
        assert_eq!(provider.spawned.len(), 1);
        assert_eq!(provider.spawned[0].0.borrow().received_data, {
            let mut data = VecDeque::new();
            data.extend(&BOLT_MAGIC_PREAMBLE);
            data.extend(&BOLT_VERSION_OFFER);
            data
        });
    }

    fn get_connect_calls(provider: &MockSocketProvider) -> Vec<MockSocketProviderCall> {
        provider
            .calls
            .iter()
            .filter(|call| {
                matches!(
                    call,
                    MockSocketProviderCall::Connect(_)
                        | MockSocketProviderCall::ConnectTimeout { .. }
                )
            })
            .map(Clone::clone)
            .collect::<Vec<_>>()
    }

    fn get_connect_call(provider: &MockSocketProvider) -> MockSocketProviderCall {
        let mut connects = get_connect_calls(provider);
        assert_eq!(dbg!(&connects).len(), 1);
        connects.pop().unwrap()
    }

    #[test]
    fn test_open_address() {
        let mut provider = MockSocketProvider::new_handshake_5_0();
        let address = Arc::new(MockAddress::new_localhost());
        open(&mut provider, Arc::clone(&address), None, None, None, None).unwrap();

        let connect = get_connect_call(&provider);
        let MockSocketProviderCall::Connect(connect_address) = connect else {
            panic!("expected Connect call")
        };
        assert!(Arc::ptr_eq(&connect_address, &address));
    }

    #[test]
    fn test_open_timeout_address() {
        let mut provider = MockSocketProvider::new_handshake_5_0();
        let address = Arc::new(MockAddress::new_localhost());
        let resolved_address = address.get_single_socket_addr();
        const DEADLINE_IN: u64 = 1000;
        let deadline = Instant::now()
            .checked_add(Duration::from_secs(DEADLINE_IN))
            .unwrap();
        let timeout = Duration::from_secs(500);

        open(
            &mut provider,
            Arc::clone(&address),
            Some(deadline),
            Some(timeout),
            None,
            None,
        )
        .unwrap();

        let connect = get_connect_call(&provider);
        let MockSocketProviderCall::ConnectTimeout {
            address: connect_address,
            timeout: connect_timeout,
        } = connect
        else {
            panic!("expected Connect call")
        };
        assert_eq!(connect_address, resolved_address);
        assert!(dbg!(connect_timeout) < Duration::from_secs(DEADLINE_IN))
    }

    #[test]
    fn test_open_timeout_retry() {
        let sock_addr1 = SocketAddr::from_str("127.0.0.1:7687").unwrap();
        let sock_addr2 = SocketAddr::from_str("[::1]:7687").unwrap();
        let address = Arc::new(MockAddress {
            host: "localhost".to_string(),
            resolves_to: vec![sock_addr1, sock_addr2],
        });
        let mut provider = MockSocketProvider::new_handshake_5_0_failing_first_n(1);
        const DEADLINE_IN: u64 = 200;
        let deadline = Instant::now()
            .checked_add(Duration::from_secs(DEADLINE_IN))
            .unwrap();
        let timeout = Duration::from_secs(400);

        open(
            &mut provider,
            Arc::clone(&address),
            Some(deadline),
            Some(timeout),
            None,
            None,
        )
        .unwrap();

        let mut connect_calls: VecDeque<_> = get_connect_calls(&provider).into();
        assert_eq!(dbg!(&connect_calls).len(), 2);
        let connect = connect_calls.pop_front().unwrap();
        let MockSocketProviderCall::ConnectTimeout {
            address: connect_address,
            timeout: connect_timeout1,
        } = connect
        else {
            panic!("expected Connect call")
        };
        assert_eq!(dbg!(connect_address), dbg!(sock_addr1));
        assert!(dbg!(connect_timeout1) < Duration::from_secs(DEADLINE_IN));

        let connect = connect_calls.pop_front().unwrap();
        let MockSocketProviderCall::ConnectTimeout {
            address: connect_address,
            timeout: connect_timeout2,
        } = connect
        else {
            panic!("expected Connect call")
        };
        assert_eq!(connect_address, sock_addr2);
        assert!(dbg!(connect_timeout2) < connect_timeout1);
    }
}
