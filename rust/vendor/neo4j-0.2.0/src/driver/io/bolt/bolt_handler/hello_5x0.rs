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

use std::borrow::Cow;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::mem;
use std::net::TcpStream;
use std::ops::Deref;
use std::sync::Arc;
use std::time::Duration;

use atomic_refcell::AtomicRefCell;
use log::warn;
use usize_cast::FromUsize;

use super::super::message_parameters::HelloParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{
    debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltMeta, BoltStructTranslator,
};
use super::common::{check_no_notification_filter, write_auth_entries, write_str_entry};
use crate::error_::Result;
use crate::value::{ValueReceive, ValueSend};

pub(super) const SERVER_AGENT_KEY: &str = "server";
pub(super) const HINTS_KEY: &str = "hints";
pub(super) const RECV_TIMEOUT_KEY: &str = "connection.recv_timeout_seconds";

pub(in super::super) struct HelloHandler5x0();

impl HelloHandler5x0 {
    pub(in super::super) fn hello<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: HelloParameters,
    ) -> Result<()> {
        let HelloParameters {
            user_agent,
            auth,
            routing_context,
            notification_filter,
        } = parameters;
        check_no_notification_filter(data, Some(notification_filter))?;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: HELLO");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x01, 1)?;

        let extra_size = 1
            + <bool as Into<u64>>::into(routing_context.is_some())
            + u64::from_usize(auth.data.len());
        serializer.write_dict_header(extra_size)?;
        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_dict_header(extra_size).unwrap();
            dbg_serializer.flush()
        });

        Self::write_user_agent_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            user_agent,
        )?;

        Self::write_routing_context_entry(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            routing_context,
        )?;

        write_auth_entries(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            auth,
        )?;
        data.auth = Some(Arc::clone(auth));

        data.message_buff.push_back(vec![message_buff]);
        debug_buf_end!(data, log_buf);

        Self::enqueue_hello_response(data);
        Ok(())
    }

    pub(super) fn write_user_agent_entry(
        log_buf: Option<&mut String>,
        serializer: &mut PackStreamSerializerImpl<impl Write>,
        dbg_serializer: &mut PackStreamSerializerDebugImpl,
        user_agent: &str,
    ) -> Result<()> {
        write_str_entry(
            log_buf,
            serializer,
            dbg_serializer,
            "user_agent",
            user_agent,
        )
    }

    pub(super) fn write_routing_context_entry(
        translator: &impl BoltStructTranslator,
        mut log_buf: Option<&mut String>,
        serializer: &mut PackStreamSerializerImpl<impl Write>,
        dbg_serializer: &mut PackStreamSerializerDebugImpl,
        data: &BoltData<impl Read + Write>,
        routing_context: Option<&HashMap<String, ValueSend>>,
    ) -> Result<()> {
        if let Some(routing_context) = routing_context {
            serializer.write_string("routing")?;
            data.serialize_dict(serializer, translator, routing_context)?;
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("routing").unwrap();
                data.serialize_dict(dbg_serializer, translator, routing_context)
                    .unwrap();
                dbg_serializer.flush()
            });
        }
        Ok(())
    }

    pub(super) fn enqueue_hello_response(data: &mut BoltData<impl Read + Write>) {
        let bolt_meta = Arc::clone(&data.meta);
        let bolt_server_agent = Arc::clone(&data.server_agent);
        let socket = Arc::clone(&data.socket);

        data.responses.push_back(BoltResponse::new(
            ResponseMessage::Hello,
            ResponseCallbacks::new().with_on_success(move |mut meta| {
                Self::hello_response_handle_agent(&mut meta, &bolt_server_agent);
                Self::hello_response_handle_connection_hints(&meta, socket.deref().as_ref());
                mem::swap(&mut *bolt_meta.borrow_mut(), &mut meta);
                Ok(())
            }),
        ));
    }

    pub(super) fn hello_response_handle_agent(
        meta: &mut BoltMeta,
        bolt_server_agent: &AtomicRefCell<Arc<String>>,
    ) {
        if let Some((key, value)) = meta.remove_entry(SERVER_AGENT_KEY) {
            match value {
                ValueReceive::String(value) => {
                    mem::swap(&mut *bolt_server_agent.borrow_mut(), &mut Arc::new(value));
                }
                _ => {
                    warn!("Server sent unexpected server_agent type {:?}", &value);
                    meta.insert(key, value);
                }
            }
        }
    }

    pub(super) fn hello_response_handle_connection_hints(
        meta: &BoltMeta,
        socket: Option<&TcpStream>,
    ) {
        let hints = Self::extract_connection_hints(meta);
        Self::hello_response_handle_timeout_hint(&hints, socket);
    }

    pub(super) fn extract_connection_hints(
        meta: &BoltMeta,
    ) -> Cow<'_, HashMap<String, ValueReceive>> {
        match meta.get(HINTS_KEY) {
            Some(ValueReceive::Map(hints)) => Cow::Borrowed(hints),
            Some(value) => {
                warn!("Server sent unexpected {HINTS_KEY} type {:?}", value);
                Cow::Owned(HashMap::new())
            }
            None => Cow::Owned(HashMap::new()),
        }
    }

    pub(super) fn hello_response_handle_timeout_hint(
        hints: &HashMap<String, ValueReceive>,
        socket: Option<&TcpStream>,
    ) {
        if let Some(timeout) = hints.get(RECV_TIMEOUT_KEY) {
            match timeout {
                ValueReceive::Integer(timeout) if timeout > &0 => {
                    socket
                        .map(|socket| {
                            let timeout = Some(Duration::from_secs(*timeout as u64));
                            socket.set_read_timeout(timeout)
                        })
                        .transpose()
                        .unwrap_or_else(|err| {
                            warn!("Failed to set socket timeout as hinted by the server: {err}");
                            None
                        });
                }
                ValueReceive::Integer(_) => {
                    warn!("Server sent unexpected {RECV_TIMEOUT_KEY} value {timeout:?}");
                }
                _ => {
                    warn!("Server sent unexpected {RECV_TIMEOUT_KEY} type {timeout:?}");
                }
            }
        }
    }
}
