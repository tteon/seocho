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
use std::io::{Read, Write};
use std::mem;
use std::net::TcpStream;
use std::ops::Deref;
use std::sync::Arc;

use log::warn;

use super::super::message_parameters::HelloParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltMeta, BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{notification_filter_entries_count, write_notification_filter_entries};
use super::hello_5x0::HelloHandler5x0;
use super::hello_5x3::HelloHandler5x3;
use crate::error_::Result;
use crate::value::ValueReceive;

pub(super) const TELEMETRY_ENABLED_KEY: &str = "telemetry.enabled";

pub(in super::super) struct HelloHandler5x4();

impl HelloHandler5x4 {
    pub(in super::super) fn hello<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: HelloParameters,
    ) -> Result<()> {
        Self::write_hello(translator, data, parameters)?;
        Self::enqueue_hello_response(data);
        Ok(())
    }

    pub(super) fn write_hello<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: HelloParameters,
    ) -> Result<()> {
        let HelloParameters {
            user_agent,
            auth: _,
            routing_context,
            notification_filter,
        } = parameters;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: HELLO");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x01, 1)?;

        let extra_size = 2
            + notification_filter_entries_count(Some(notification_filter))
            + <bool as Into<u64>>::into(routing_context.is_some());

        serializer.write_dict_header(extra_size)?;
        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_dict_header(extra_size).unwrap();
            dbg_serializer.flush()
        });

        HelloHandler5x0::write_user_agent_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            user_agent,
        )?;

        HelloHandler5x3::write_bolt_agent_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
        )?;

        HelloHandler5x0::write_routing_context_entry(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            routing_context,
        )?;

        write_notification_filter_entries(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            Some(notification_filter),
        )?;

        data.message_buff.push_back(vec![message_buff]);
        debug_buf_end!(data, log_buf);
        Ok(())
    }

    pub(in super::super) fn enqueue_hello_response(data: &mut BoltData<impl Read + Write>) {
        let bolt_meta = Arc::clone(&data.meta);
        let telemetry_enabled = Arc::clone(&data.telemetry_enabled);
        let bolt_server_agent = Arc::clone(&data.server_agent);
        let socket = Arc::clone(&data.socket);

        data.responses.push_back(BoltResponse::new(
            ResponseMessage::Hello,
            ResponseCallbacks::new().with_on_success(move |mut meta| {
                HelloHandler5x0::hello_response_handle_agent(&mut meta, &bolt_server_agent);
                Self::hello_response_handle_connection_hints(
                    &meta,
                    socket.deref().as_ref(),
                    &mut telemetry_enabled.borrow_mut(),
                );
                mem::swap(&mut *bolt_meta.borrow_mut(), &mut meta);
                Ok(())
            }),
        ));
    }

    pub(super) fn hello_response_telemetry_hint(
        hints: &HashMap<String, ValueReceive>,
        telemetry_enabled: &mut bool,
    ) {
        if !*telemetry_enabled {
            // driver config opted out of telemetry
            return;
        }
        let Some(enabled) = hints.get(TELEMETRY_ENABLED_KEY) else {
            // server implicitly opted out of telemetry
            *telemetry_enabled = false;
            return;
        };
        let ValueReceive::Boolean(enabled) = enabled else {
            warn!(
                "Server sent unexpected {TELEMETRY_ENABLED_KEY} type {:?}",
                enabled
            );
            return;
        };
        // since client didn't opt out, leave it up to the server
        *telemetry_enabled = *enabled;
    }

    pub(super) fn hello_response_handle_connection_hints(
        meta: &BoltMeta,
        socket: Option<&TcpStream>,
        telemetry_enabled: &mut bool,
    ) {
        let hints = HelloHandler5x0::extract_connection_hints(meta);
        HelloHandler5x0::hello_response_handle_timeout_hint(&hints, socket);
        Self::hello_response_telemetry_hint(&hints, telemetry_enabled);
    }
}
