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
use super::super::response::{BoltMeta, BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{BoltData, BoltStructTranslator};
use super::hello_5x0::HelloHandler5x0;
use super::hello_5x4::HelloHandler5x4;
use crate::error_::Result;
use crate::value::ValueReceive;

pub(super) const SSR_ENABLED_KEY: &str = "ssr.enabled";

pub(in super::super) struct HelloHandler5x8();

impl HelloHandler5x8 {
    pub(in super::super) fn hello<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: HelloParameters,
    ) -> Result<()> {
        HelloHandler5x4::write_hello(translator, data, parameters)?;
        Self::enqueue_hello_response(data);
        Ok(())
    }

    pub(super) fn enqueue_hello_response(data: &mut BoltData<impl Read + Write>) {
        let bolt_meta = Arc::clone(&data.meta);
        let telemetry_enabled = Arc::clone(&data.telemetry_enabled);
        let ssr_enabled = Arc::clone(&data.ssr_enabled);
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
                    &mut ssr_enabled.borrow_mut(),
                );
                mem::swap(&mut *bolt_meta.borrow_mut(), &mut meta);
                Ok(())
            }),
        ));
    }

    pub(super) fn hello_response_handle_connection_hints(
        meta: &BoltMeta,
        socket: Option<&TcpStream>,
        telemetry_enabled: &mut bool,
        ssr_enabled: &mut bool,
    ) {
        let hints = HelloHandler5x0::extract_connection_hints(meta);
        HelloHandler5x0::hello_response_handle_timeout_hint(&hints, socket);
        HelloHandler5x4::hello_response_telemetry_hint(&hints, telemetry_enabled);
        Self::hello_response_handle_ssr_enabled_hint(&hints, ssr_enabled);
    }

    pub(super) fn hello_response_handle_ssr_enabled_hint(
        hints: &HashMap<String, ValueReceive>,
        ssr_enabled: &mut bool,
    ) {
        match hints.get(SSR_ENABLED_KEY) {
            None => {
                *ssr_enabled = false;
            }
            Some(ValueReceive::Boolean(value)) => *ssr_enabled = *value,
            Some(value) => {
                *ssr_enabled = false;
                warn!("Server sent unexpected {SSR_ENABLED_KEY} type {:?}", value);
            }
        }
    }
}
