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
use std::ops::Deref;
use std::sync::Arc;

use atomic_refcell::AtomicRefCell;
use log::warn;
use usize_cast::FromUsize;

use super::super::message_parameters::HelloParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{
    debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslatorWithUtcPatch,
};
use super::common::{check_no_notification_filter, write_auth_entries};
use super::hello_5x0::HelloHandler5x0;
use crate::error_::Result;
use crate::value::ValueReceive;

pub(super) const PATCH_BOLT_KEY: &str = "patch_bolt";

pub(in super::super) struct HelloHandler4x4();

impl HelloHandler4x4 {
    pub(in super::super) fn hello<
        RW: Read + Write,
        T: BoltStructTranslatorWithUtcPatch + Sync + Send + 'static,
    >(
        translator: &Arc<AtomicRefCell<T>>,
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

        let extra_size = 2
            + <bool as Into<u64>>::into(routing_context.is_some())
            + u64::from_usize(auth.data.len());
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

        Self::write_utc_patch_entry(log_buf.as_mut(), &mut serializer, &mut dbg_serializer, data)?;

        HelloHandler5x0::write_routing_context_entry(
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

        Self::enqueue_hello_response(translator, data);
        Ok(())
    }

    pub(super) fn write_utc_patch_entry(
        mut log_buf: Option<&mut String>,
        serializer: &mut PackStreamSerializerImpl<impl Write>,
        dbg_serializer: &mut PackStreamSerializerDebugImpl,
        data: &BoltData<impl Read + Write>,
    ) -> Result<()> {
        serializer.write_string("patch_bolt")?;
        data.serialize_str_slice(serializer, &["utc"])?;
        debug_buf!(log_buf, "{}", {
            dbg_serializer.write_string("patch_bolt").unwrap();
            data.serialize_str_slice(dbg_serializer, &["utc"]).unwrap();
            dbg_serializer.flush()
        });
        Ok(())
    }

    pub(super) fn enqueue_hello_response<
        RW: Read + Write,
        T: BoltStructTranslatorWithUtcPatch + Sync + Send + 'static,
    >(
        translator: &Arc<AtomicRefCell<T>>,
        data: &mut BoltData<RW>,
    ) {
        let bolt_meta = Arc::clone(&data.meta);
        let bolt_server_agent = Arc::clone(&data.server_agent);
        let socket = Arc::clone(&data.socket);
        let translator = Arc::clone(translator);

        data.responses.push_back(BoltResponse::new(
            ResponseMessage::Hello,
            ResponseCallbacks::new().with_on_success(move |mut meta| {
                HelloHandler5x0::hello_response_handle_agent(&mut meta, &bolt_server_agent);
                Self::hello_response_handle_utc_patch(&translator, &meta);
                HelloHandler5x0::hello_response_handle_connection_hints(
                    &meta,
                    socket.deref().as_ref(),
                );
                mem::swap(&mut *bolt_meta.borrow_mut(), &mut meta);
                Ok(())
            }),
        ));
    }

    pub(super) fn hello_response_handle_utc_patch<
        T: BoltStructTranslatorWithUtcPatch + Sync + Send + 'static,
    >(
        translator: &AtomicRefCell<T>,
        hints: &HashMap<String, ValueReceive>,
    ) {
        if let Some(value) = hints.get(PATCH_BOLT_KEY) {
            match value {
                ValueReceive::List(value) => {
                    for entry in value {
                        match entry {
                            ValueReceive::String(s) if s == "utc" => {
                                translator.borrow_mut().enable_utc_patch();
                            }
                            _ => {}
                        }
                    }
                }
                _ => {
                    warn!("Server sent unexpected {PATCH_BOLT_KEY} type {:?}", value);
                }
            }
        }
    }
}
