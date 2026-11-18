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

use std::io::{Read, Write};

use super::super::message_parameters::HelloParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{notification_filter_entries_count, write_notification_filter_entries};
use super::hello_5x0::HelloHandler5x0;
use crate::error_::Result;

pub(in super::super) struct HelloHandler5x2();

impl HelloHandler5x2 {
    pub(in super::super) fn hello<RW: Read + Write>(
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

        let extra_size = 1
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

        HelloHandler5x0::enqueue_hello_response(data);
        Ok(())
    }
}
