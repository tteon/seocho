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

use super::super::bolt_common::{
    BOLT_AGENT_LANGUAGE, BOLT_AGENT_LANGUAGE_DETAILS, BOLT_AGENT_PLATFORM, BOLT_AGENT_PRODUCT,
};
use super::super::message_parameters::HelloParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{notification_filter_entries_count, write_notification_filter_entries};
use super::hello_5x0::HelloHandler5x0;
use crate::error_::Result;

pub(in super::super) struct HelloHandler5x3();

impl HelloHandler5x3 {
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

        Self::write_bolt_agent_entry(log_buf.as_mut(), &mut serializer, &mut dbg_serializer)?;

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

    pub(super) fn write_bolt_agent_entry(
        mut log_buf: Option<&mut String>,
        serializer: &mut PackStreamSerializerImpl<impl Write>,
        dbg_serializer: &mut PackStreamSerializerDebugImpl,
    ) -> Result<()> {
        serializer.write_string("bolt_agent")?;
        serializer.write_dict_header(4)?;
        serializer.write_string("product")?;
        serializer.write_string(BOLT_AGENT_PRODUCT)?;
        serializer.write_string("platform")?;
        serializer.write_string(BOLT_AGENT_PLATFORM)?;
        serializer.write_string("language")?;
        serializer.write_string(BOLT_AGENT_LANGUAGE)?;
        serializer.write_string("language_details")?;
        serializer.write_string(BOLT_AGENT_LANGUAGE_DETAILS)?;
        debug_buf!(log_buf, "{}", {
            dbg_serializer.write_string("bolt_agent").unwrap();
            dbg_serializer.write_dict_header(4).unwrap();
            dbg_serializer.write_string("product").unwrap();
            dbg_serializer.write_string(BOLT_AGENT_PRODUCT).unwrap();
            dbg_serializer.write_string("platform").unwrap();
            dbg_serializer.write_string(BOLT_AGENT_PLATFORM).unwrap();
            dbg_serializer.write_string("language").unwrap();
            dbg_serializer.write_string(BOLT_AGENT_LANGUAGE).unwrap();
            dbg_serializer.write_string("language_details").unwrap();
            dbg_serializer
                .write_string(BOLT_AGENT_LANGUAGE_DETAILS)
                .unwrap();
            dbg_serializer.flush()
        });
        Ok(())
    }
}
