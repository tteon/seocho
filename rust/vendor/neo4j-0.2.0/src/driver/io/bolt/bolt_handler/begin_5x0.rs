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

use std::borrow::Borrow;
use std::fmt::Debug;
use std::io::{Read, Write};

use super::super::message_parameters::BeginParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{
    check_no_notification_filter, write_bookmarks_entry, write_db_entry, write_imp_user_entry,
    write_mode_entry, write_tx_metadata_entry, write_tx_timeout_entry,
};
use crate::error_::Result;

pub(in super::super) struct BeginHandler5x0();

impl BeginHandler5x0 {
    pub(in super::super) fn begin<RW: Read + Write, K: Borrow<str> + Debug>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: BeginParameters<K>,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let BeginParameters {
            bookmarks,
            tx_timeout,
            tx_metadata,
            mode,
            db,
            imp_user,
            notification_filter,
        } = parameters;
        check_no_notification_filter(data, Some(notification_filter))?;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: BEGIN");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x11, 1)?;

        let extra_size = [
            bookmarks.is_some() && !bookmarks.unwrap().is_empty(),
            tx_timeout.is_some(),
            tx_metadata.map(|m| !m.is_empty()).unwrap_or_default(),
            mode.is_some() && mode.unwrap() != "w",
            db.is_some(),
            imp_user.is_some(),
        ]
        .into_iter()
        .map(<bool as Into<u64>>::into)
        .sum();

        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_dict_header(extra_size).unwrap();
            dbg_serializer.flush()
        });
        serializer.write_dict_header(extra_size)?;

        write_bookmarks_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            bookmarks,
        )?;

        write_tx_timeout_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            tx_timeout,
        )?;

        write_tx_metadata_entry(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            tx_metadata,
        )?;

        write_mode_entry(log_buf.as_mut(), &mut serializer, &mut dbg_serializer, mode)?;

        write_db_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            db.as_deref().map(String::as_str),
        )?;

        write_imp_user_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            imp_user,
        )?;

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Begin, callbacks));
        debug_buf_end!(data, log_buf);
        Ok(())
    }
}
