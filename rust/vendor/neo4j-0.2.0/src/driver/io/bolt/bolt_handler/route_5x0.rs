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

use usize_cast::FromUsize;

use super::super::message_parameters::RouteParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{write_bookmarks_list, write_db_entry, write_imp_user_entry};
use crate::error_::Result;

pub(in super::super) struct RouteHandler5x0();

impl RouteHandler5x0 {
    pub(in super::super) fn route<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: RouteParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let RouteParameters {
            routing_context,
            bookmarks,
            db,
            imp_user,
        } = parameters;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: ROUTE");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x66, 3)?;

        data.serialize_dict(&mut serializer, translator, routing_context)?;
        debug_buf!(log_buf, " {}", {
            data.serialize_dict(&mut dbg_serializer, translator, routing_context)
                .unwrap();
            dbg_serializer.flush()
        });

        write_bookmarks_list(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            bookmarks,
        )?;

        let extra_size = <bool as Into<usize>>::into(db.is_some())
            + <bool as Into<usize>>::into(imp_user.is_some());
        serializer.write_dict_header(u64::from_usize(extra_size))?;
        debug_buf!(log_buf, " {}", {
            dbg_serializer
                .write_dict_header(u64::from_usize(extra_size))
                .unwrap();
            dbg_serializer.flush()
        });

        write_db_entry(log_buf.as_mut(), &mut serializer, &mut dbg_serializer, db)?;

        write_imp_user_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            imp_user,
        )?;

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Route, callbacks));
        debug_buf_end!(data, log_buf);
        Ok(())
    }
}
