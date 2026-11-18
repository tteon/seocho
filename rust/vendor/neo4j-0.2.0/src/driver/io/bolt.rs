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

#[macro_use]
mod bolt_common;
mod bolt4x4;
mod bolt5x0;
mod bolt5x1;
mod bolt5x2;
mod bolt5x3;
mod bolt5x4;
mod bolt5x6;
mod bolt5x7;
mod bolt5x8;
mod bolt_handler;
mod bolt_state;
mod chunk;
mod handshake;
mod message;
pub(crate) mod message_parameters;
mod packstream;
mod response;
mod socket;

use std::borrow::Borrow;
use std::collections::{HashMap, VecDeque};
use std::fmt::{Debug, Formatter};
use std::hash::{Hash, Hasher};
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::ops::Deref;
use std::result;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use atomic_refcell::AtomicRefCell;
use enum_dispatch::enum_dispatch;
use usize_cast::FromUsize;

use super::deadline::DeadlineIO;
use crate::address_::Address;
use crate::driver::auth::AuthToken;
use crate::error_::{Neo4jError, Result, ServerError};
use crate::time::Instant;
use crate::value::{ValueReceive, ValueSend};
use bolt4x4::{Bolt4x4, Bolt4x4StructTranslator};
use bolt5x0::{Bolt5x0, Bolt5x0StructTranslator};
use bolt5x1::{Bolt5x1, Bolt5x1StructTranslator};
use bolt5x2::{Bolt5x2, Bolt5x2StructTranslator};
use bolt5x3::{Bolt5x3, Bolt5x3StructTranslator};
use bolt5x4::{Bolt5x4, Bolt5x4StructTranslator};
use bolt5x6::{Bolt5x6, Bolt5x6StructTranslator};
use bolt5x7::{Bolt5x7, Bolt5x7StructTranslator};
use bolt5x8::{Bolt5x8, Bolt5x8StructTranslator};
use bolt_common::ServerAwareBoltVersion;
use bolt_handler::{
    BeginHandler, CommitHandler, DiscardHandler, GoodbyeHandler, HandleResponseHandler,
    HelloHandler, LoadValueHandler, PullHandler, ReauthHandler, ResetHandler, RollbackHandler,
    RouteHandler, RunHandler, TelemetryHandler,
};
use bolt_state::{BoltState, BoltStateTracker};
use chunk::{Chunker, Dechunker};
pub(crate) use handshake::{open, TcpConnector};
use message::BoltMessage;
use message_parameters::{
    BeginParameters, CommitParameters, DiscardParameters, GoodbyeParameters, HelloParameters,
    PullParameters, ReauthParameters, ResetParameters, RollbackParameters, RouteParameters,
    RunParameters, TelemetryParameters,
};
use packstream::PackStreamSerializer;
pub(crate) use response::{
    BoltMeta, BoltRecordFields, BoltResponse, ResponseCallbacks, ResponseMessage,
};
pub(crate) use socket::{BufTcpStream, Socket};

macro_rules! debug_buf_start {
    ($name:ident) => {
        let mut $name = None;
        {
            #![allow(unused_imports)]
            use log::{log_enabled, Level};

            if log_enabled!(Level::Debug) {
                $name = Some(String::new());
            }
        }
    };
}
pub(crate) use debug_buf_start;

macro_rules! debug_buf {
    ($name:ident, $($args:tt)+) => {{
        #![allow(unused_imports)]
        use log::{log_enabled, Level};

        if log_enabled!(Level::Debug) {
            $name.as_mut().unwrap().push_str(&format!($($args)*))
        };
    }}
}
pub(crate) use debug_buf;

macro_rules! bolt_debug_extra {
    ($meta:expr, $local_port:expr) => {
        'a: {
            {
                #![allow(unused_imports)]
                use crate::driver::io::bolt::dbg_extra;

                use crate::value::ValueReceive;

                let meta = $meta;
                let Ok(meta) = meta else {
                    break 'a dbg_extra($local_port, Some("!!!!"));
                };
                let Some(ValueReceive::String(id)) = meta.get("connection_id") else {
                    break 'a dbg_extra($local_port, None);
                };
                dbg_extra($local_port, Some(id))
            }
        }
    };
}
pub(crate) use bolt_debug_extra;

macro_rules! debug_buf_end {
    ($bolt:expr, $name:ident) => {{
        #![allow(unused_imports)]
        use log::debug;

        use crate::driver::io::bolt::bolt_debug_extra;

        debug!(
            "{}{}",
            bolt_debug_extra!($bolt.meta.try_borrow(), $bolt.local_port),
            $name.as_ref().map(|s| s.as_str()).unwrap_or("")
        );
    }};
}
pub(crate) use debug_buf_end;

macro_rules! bolt_debug {
    ($bolt:expr, $($args:tt)+) => {{
        #![allow(unused_imports)]
        use log::debug;

        use crate::driver::io::bolt::bolt_debug_extra;

        debug!(
            "{}{}",
            bolt_debug_extra!($bolt.meta.try_borrow(), $bolt.local_port),
            format!($($args)*)
        );
    }};
}
pub(crate) use bolt_debug;

macro_rules! socket_debug {
    ($local_port:expr, $($args:tt)+) => {{
        #![allow(unused_imports)]
        use log::debug;

        use crate::driver::io::bolt::dbg_extra;

        debug!(
            "{}{}",
            dbg_extra(Some($local_port), None),
            format!($($args)*)
        );
    }};
}
pub(crate) use socket_debug;

pub(crate) fn dbg_extra(port: Option<u16>, bolt_id: Option<&str>) -> String {
    format!(
        "[#{:04X} {:<10}] ",
        port.unwrap_or(0),
        bolt_id.unwrap_or("")
    )
}

pub(crate) type TcpRW = Socket<BufTcpStream>;
pub(crate) type TcpBolt = Bolt<TcpRW>;

pub(crate) type OnServerErrorCb<'a, 'b, RW> =
    Option<&'a mut (dyn FnMut(&mut BoltData<RW>, &mut ServerError) -> Result<()> + 'b)>;

#[derive(Debug)]
pub(crate) struct Bolt<RW: Read + Write> {
    data: BoltData<RW>,
    protocol: BoltProtocol,
}

impl<RW: Read + Write> Bolt<RW> {
    fn new(
        version: (u8, u8),
        stream: RW,
        socket: Arc<Option<TcpStream>>,
        local_port: Option<u16>,
        address: Arc<Address>,
    ) -> Self {
        let (protocol_version, protocol) = match version {
            (5, 8) => (
                ServerAwareBoltVersion::V5x8,
                Bolt5x8::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 7) => (
                ServerAwareBoltVersion::V5x7,
                Bolt5x7::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 6) => (
                ServerAwareBoltVersion::V5x6,
                Bolt5x6::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 4) => (
                ServerAwareBoltVersion::V5x4,
                Bolt5x4::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 3) => (
                ServerAwareBoltVersion::V5x3,
                Bolt5x3::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 2) => (
                ServerAwareBoltVersion::V5x2,
                Bolt5x2::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 1) => (
                ServerAwareBoltVersion::V5x1,
                Bolt5x1::<Bolt5x0StructTranslator>::default().into(),
            ),
            (5, 0) => (
                ServerAwareBoltVersion::V5x0,
                Bolt5x0::<Bolt5x0StructTranslator>::default().into(),
            ),
            (4, 4) => (
                ServerAwareBoltVersion::V4x4,
                Bolt4x4::<Bolt4x4StructTranslator>::default().into(),
            ),
            _ => panic!("implement protocol for version {:?}", version),
        };
        let data = BoltData::new(
            version,
            protocol_version,
            stream,
            socket,
            local_port,
            address,
        );
        Self { data, protocol }
    }

    pub(crate) fn close(&mut self) {
        if self.data.closed() {
            return;
        }
        self.data.connection_state = ConnectionState::Closed;
        self.data.message_buff.clear();
        self.data.responses.clear();
        if self.goodbye().is_err() {
            return;
        }
        let _ = self
            .data
            .write_all(Some(Instant::now() + Duration::from_millis(100)));
    }

    pub(crate) fn closed(&self) -> bool {
        self.data.closed()
    }

    pub(crate) fn unexpectedly_closed(&self) -> bool {
        self.data.unexpectedly_closed()
    }

    pub(crate) fn protocol_version(&self) -> (u8, u8) {
        self.data.version
    }

    pub(crate) fn address(&self) -> Arc<Address> {
        Arc::clone(&self.data.address)
    }

    pub(crate) fn server_agent(&self) -> Arc<String> {
        Arc::clone(self.data.server_agent.deref().borrow().deref())
    }

    pub(crate) fn hello(&mut self, parameters: HelloParameters) -> Result<()> {
        self.protocol.hello(&mut self.data, parameters)
    }

    pub(crate) fn reauth(&mut self, parameters: ReauthParameters) -> Result<()> {
        let res = self.protocol.reauth(&mut self.data, parameters);
        self.data.session_auth = parameters.session_auth;
        self.data.auth_reset.reset();
        res
    }

    pub(crate) fn supports_reauth(&self) -> bool {
        self.protocol.supports_reauth()
    }

    pub(crate) fn needs_reauth(&self, parameters: ReauthParameters) -> bool {
        self.data.needs_reauth(parameters)
    }

    #[inline]
    pub(crate) fn auth_reset_handler(&self) -> AuthResetHandle {
        AuthResetHandle::clone(&self.data.auth_reset)
    }

    pub(crate) fn goodbye(&mut self) -> Result<()> {
        self.protocol
            .goodbye(&mut self.data, GoodbyeParameters::new())
    }

    pub(crate) fn reset(&mut self) -> Result<()> {
        self.protocol.reset(&mut self.data, ResetParameters::new())
    }

    pub(crate) fn run<KP: Borrow<str> + Debug, KM: Borrow<str> + Debug>(
        &mut self,
        parameters: RunParameters<KP, KM>,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol.run(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn discard(
        &mut self,
        parameters: DiscardParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol.discard(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn pull(
        &mut self,
        parameters: PullParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol.pull(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn begin<K: Borrow<str> + Debug>(
        &mut self,
        parameters: BeginParameters<K>,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol.begin(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn commit(&mut self, callbacks: ResponseCallbacks) -> Result<()> {
        self.protocol
            .commit(&mut self.data, CommitParameters::new(), callbacks)
    }

    pub(crate) fn rollback(&mut self) -> Result<()> {
        self.protocol
            .rollback(&mut self.data, RollbackParameters::new())
    }

    pub(crate) fn route(
        &mut self,
        parameters: RouteParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol.route(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn telemetry(
        &mut self,
        parameters: TelemetryParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        self.protocol
            .telemetry(&mut self.data, parameters, callbacks)
    }

    pub(crate) fn read_all(
        &mut self,
        deadline: Option<Instant>,
        mut on_server_error: OnServerErrorCb<RW>,
    ) -> Result<()> {
        let on_server_error_ref = &mut on_server_error;
        while self.expects_reply() {
            self.read_one(deadline, on_server_error_ref.as_deref_mut())?;
        }
        Ok(())
    }

    pub(crate) fn read_one(
        &mut self,
        deadline: Option<Instant>,
        on_server_error: OnServerErrorCb<RW>,
    ) -> Result<()> {
        let mut reader = DeadlineIO::new(
            &mut self.data.stream,
            deadline,
            self.data.socket.deref().as_ref(),
        );
        let mut dechunker = Dechunker::new(&mut reader);
        let message_result: Result<BoltMessage<ValueReceive>> =
            BoltMessage::load(&mut dechunker, |r| self.protocol.load_value(r));
        drop(dechunker);
        let message_result = reader.rewrite_error(message_result);
        let message = self.wrap_read_result(message_result)?;
        self.data.idle_since = Instant::now();
        self.protocol
            .handle_response(&mut self.data, message, on_server_error)
    }

    fn wrap_read_result<T>(&mut self, res: Result<T>) -> Result<T> {
        if let Err(err) = &res {
            bolt_debug!(self.data, "read failed: {err:?}");
            self.data.connection_state = ConnectionState::Broken;
            self.data
                .socket
                .deref()
                .as_ref()
                .map(|s| s.shutdown(Shutdown::Both));
        }
        res
    }

    pub(crate) fn write_all(&mut self, deadline: Option<Instant>) -> Result<()> {
        self.data.idle_since = Instant::now();
        self.data.write_all(deadline)?;
        self.data.flush(deadline)
    }
    pub(crate) fn has_buffered_message(&self) -> bool {
        self.data.has_buffered_message()
    }
    pub(crate) fn expects_reply(&self) -> bool {
        self.data.expects_reply()
    }
    pub(crate) fn expected_reply_len(&self) -> usize {
        self.data.expected_reply_len()
    }
    pub(crate) fn needs_reset(&self) -> bool {
        self.data.needs_reset()
    }
    pub(crate) fn is_older_than(&self, duration: Duration) -> bool {
        self.data.is_older_than(duration)
    }
    pub(crate) fn is_idle_for(&self, timeout: Duration) -> bool {
        self.data.is_idle_for(timeout)
    }
    pub(crate) fn set_telemetry_enabled(&mut self, enabled: bool) {
        self.data.set_telemetry_enabled(enabled)
    }

    pub(crate) fn ssr_enabled(&self) -> bool {
        self.data.ssr_enabled()
    }

    #[inline(always)]
    pub(crate) fn debug_log(&self, msg: impl FnOnce() -> String) {
        bolt_debug!(self.data, "{}", msg());
    }
}

impl<RW: Read + Write> Drop for Bolt<RW> {
    fn drop(&mut self) {
        self.close();
    }
}

// [bolt-version-bump] search tag when changing bolt version support
#[enum_dispatch(
    HelloHandler,
    BeginHandler,
    CommitHandler,
    DiscardHandler,
    GoodbyeHandler,
    PullHandler,
    ReauthHandler,
    ResetHandler,
    RollbackHandler,
    RouteHandler,
    RunHandler,
    TelemetryHandler,
    HandleResponseHandler,
    LoadValueHandler
)]
#[derive(Debug)]
enum BoltProtocol {
    V4x4(Bolt4x4<Bolt4x4StructTranslator>),
    V5x0(Bolt5x0<Bolt5x0StructTranslator>),
    V5x1(Bolt5x1<Bolt5x1StructTranslator>),
    V5x2(Bolt5x2<Bolt5x2StructTranslator>),
    V5x3(Bolt5x3<Bolt5x3StructTranslator>),
    V5x4(Bolt5x4<Bolt5x4StructTranslator>),
    V5x6(Bolt5x6<Bolt5x6StructTranslator>),
    V5x7(Bolt5x7<Bolt5x7StructTranslator>),
    V5x8(Bolt5x8<Bolt5x8StructTranslator>),
}

#[derive(Debug, Copy, Clone, Ord, PartialOrd, Eq, PartialEq)]
enum ConnectionState {
    Healthy,
    Broken,
    Closed,
}

pub(crate) struct BoltData<RW: Read + Write> {
    message_buff: VecDeque<Vec<Vec<u8>>>,
    responses: VecDeque<BoltResponse>,
    stream: RW,
    socket: Arc<Option<TcpStream>>,
    local_port: Option<u16>,
    version: (u8, u8),
    protocol_version: ServerAwareBoltVersion,
    connection_state: ConnectionState,
    bolt_state: BoltStateTracker,
    meta: Arc<AtomicRefCell<HashMap<String, ValueReceive>>>,
    server_agent: Arc<AtomicRefCell<Arc<String>>>,
    telemetry_enabled: Arc<AtomicRefCell<bool>>,
    ssr_enabled: Arc<AtomicRefCell<bool>>,
    address: Arc<Address>,
    last_qid: Arc<AtomicRefCell<Option<i64>>>,
    auth: Option<Arc<AuthToken>>,
    session_auth: bool,
    auth_reset: AuthResetHandle,
    created_at: Instant,
    idle_since: Instant,
}

impl<RW: Read + Write> BoltData<RW> {
    fn new(
        version: (u8, u8),
        protocol_version: ServerAwareBoltVersion,
        stream: RW,
        socket: Arc<Option<TcpStream>>,
        local_port: Option<u16>,
        address: Arc<Address>,
    ) -> Self {
        let now = Instant::now();
        Self {
            message_buff: VecDeque::with_capacity(2048),
            responses: VecDeque::with_capacity(10),
            stream,
            socket,
            local_port,
            version,
            protocol_version,
            connection_state: ConnectionState::Healthy,
            bolt_state: BoltStateTracker::new(version),
            meta: Default::default(),
            server_agent: Default::default(),
            telemetry_enabled: Default::default(),
            ssr_enabled: Default::default(),
            address,
            last_qid: Default::default(),
            auth: None,
            session_auth: false,
            auth_reset: Default::default(),
            created_at: now,
            idle_since: now,
        }
    }

    pub(crate) fn address(&self) -> &Arc<Address> {
        &self.address
    }

    pub(crate) fn auth(&self) -> Option<&Arc<AuthToken>> {
        self.auth.as_ref()
    }

    pub(crate) fn session_auth(&self) -> bool {
        self.session_auth
    }

    fn closed(&self) -> bool {
        !matches!(self.connection_state, ConnectionState::Healthy)
    }

    fn unexpectedly_closed(&self) -> bool {
        matches!(self.connection_state, ConnectionState::Broken)
            && !matches!(self.bolt_state.state(), BoltState::Failed)
    }

    fn can_omit_qid(&self, qid: i64) -> bool {
        qid == -1 || Some(qid) == *(self.last_qid.deref().borrow())
    }

    fn serialize_dict<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        translator: &impl BoltStructTranslator,
        map: &HashMap<impl Borrow<str>, ValueSend>,
    ) -> result::Result<(), S::Error> {
        serializer.write_dict_header(u64::from_usize(map.len()))?;
        for (k, v) in map {
            serializer.write_string(k.borrow())?;
            self.serialize_value(serializer, translator, v)?;
        }
        Ok(())
    }

    fn serialize_str_slice<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        slice: &[impl Borrow<str>],
    ) -> result::Result<(), S::Error> {
        serializer.write_list_header(u64::from_usize(slice.len()))?;
        for v in slice {
            serializer.write_string(v.borrow())?;
        }
        Ok(())
    }

    #[inline]
    fn serialize_str_iter<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        iter: impl Iterator<Item = impl Borrow<str>>,
    ) -> result::Result<(), S::Error> {
        self.serialize_str_slice(serializer, &iter.collect::<Vec<_>>())
    }

    #[inline]
    fn serialize_value<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        translator: &impl BoltStructTranslator,
        v: &ValueSend,
    ) -> result::Result<(), S::Error> {
        translator.serialize(serializer, v)
    }

    fn write_all(&mut self, deadline: Option<Instant>) -> Result<()> {
        while self.has_buffered_message() {
            self.write_one(deadline)?
        }
        Ok(())
    }

    fn write_one(&mut self, deadline: Option<Instant>) -> Result<()> {
        if let Some(message_buff) = self.message_buff.pop_front() {
            let chunker = Chunker::new(&message_buff);
            let mut writer =
                DeadlineIO::new(&mut self.stream, deadline, self.socket.deref().as_ref());
            for chunk in chunker {
                let res = Neo4jError::wrap_write(writer.write_all(&chunk));
                let res = writer.rewrite_error(res);
                if let Err(err) = &res {
                    self.handle_write_error(err);
                    return res;
                }
            }
        }
        Ok(())
    }

    fn flush(&mut self, deadline: Option<Instant>) -> Result<()> {
        let mut writer = DeadlineIO::new(&mut self.stream, deadline, self.socket.deref().as_ref());
        let res = Neo4jError::wrap_write(writer.flush());
        let res = writer.rewrite_error(res);
        if let Err(err) = &res {
            self.handle_write_error(err);
            return res;
        }
        Ok(())
    }

    fn handle_write_error(&mut self, err: &Neo4jError) {
        bolt_debug!(self, "write failed: {}", err);
        self.connection_state = ConnectionState::Broken;
        self.socket
            .deref()
            .as_ref()
            .map(|s| s.shutdown(Shutdown::Both));
    }

    fn has_buffered_message(&self) -> bool {
        !self.message_buff.is_empty()
    }

    fn expects_reply(&self) -> bool {
        !self.responses.is_empty()
    }
    fn expected_reply_len(&self) -> usize {
        self.responses.len()
    }

    fn needs_reset(&self) -> bool {
        if let Some(response) = self.responses.iter().last() {
            if response.message == ResponseMessage::Reset {
                return false;
            }
        }
        if self.connection_state != ConnectionState::Healthy {
            return false;
        }
        !(self.bolt_state.state() == BoltState::Ready && self.responses.is_empty())
    }

    fn needs_reauth(&self, parameters: ReauthParameters) -> bool {
        self.auth_reset.marked_for_reset()
            || self
                .auth
                .as_ref()
                .map(|auth| !auth.eq_data(parameters.auth))
                .unwrap_or(true)
    }

    fn is_older_than(&self, duration: Duration) -> bool {
        self.created_at.elapsed() >= duration
    }

    fn is_idle_for(&self, timeout: Duration) -> bool {
        self.idle_since.elapsed() >= timeout
    }

    fn set_telemetry_enabled(&mut self, enabled: bool) {
        *self.telemetry_enabled.borrow_mut() = enabled;
    }

    fn ssr_enabled(&self) -> bool {
        *(*self.ssr_enabled).borrow()
    }
}

impl<RW: Read + Write> Debug for BoltData<RW> {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BoltData")
            .field("message_buff", &self.message_buff)
            .field("responses", &self.responses)
            .finish()
    }
}

#[derive(Debug, Default)]
pub(crate) struct AuthResetHandle(Arc<AtomicBool>);

impl PartialEq for AuthResetHandle {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

impl Eq for AuthResetHandle {}

impl Hash for AuthResetHandle {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let bool_ref: &AtomicBool = &self.0;
        std::ptr::hash(bool_ref, state);
    }
}

impl AuthResetHandle {
    #[inline]
    pub(crate) fn clone(handle: &Self) -> Self {
        Self(Arc::clone(&handle.0))
    }

    #[inline]
    pub(crate) fn mark_for_reset(&self) {
        self.0.store(true, Ordering::Release);
    }

    #[inline]
    fn reset(&self) {
        self.0.store(false, Ordering::Release);
    }

    #[inline]
    fn marked_for_reset(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }
}

pub(crate) trait BoltStructTranslator: Debug + Default {
    fn serialize<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        value: &ValueSend,
    ) -> result::Result<(), S::Error>;

    fn deserialize_struct(&self, tag: u8, fields: Vec<ValueReceive>) -> ValueReceive;
}

impl<T: BoltStructTranslator> BoltStructTranslator for Arc<AtomicRefCell<T>> {
    fn serialize<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        value: &ValueSend,
    ) -> result::Result<(), S::Error> {
        AtomicRefCell::borrow(self).serialize(serializer, value)
    }

    fn deserialize_struct(&self, tag: u8, fields: Vec<ValueReceive>) -> ValueReceive {
        AtomicRefCell::borrow(self).deserialize_struct(tag, fields)
    }
}

pub(crate) trait BoltStructTranslatorWithUtcPatch: BoltStructTranslator {
    fn enable_utc_patch(&mut self);
}
