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
use std::sync::Arc;

use super::super::message_parameters::RunParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use super::common::{
    check_no_notification_filter, write_bookmarks_entry, write_db_entry, write_imp_user_entry,
    write_mode_entry, write_parameter_dict, write_tx_metadata_entry, write_tx_timeout_entry,
};
use crate::error_::{Neo4jError, Result};
use crate::value::ValueReceive;

pub(in super::super) struct RunHandler5x0();

impl RunHandler5x0 {
    pub(in super::super) fn run<
        RW: Read + Write,
        KP: Borrow<str> + Debug,
        KM: Borrow<str> + Debug,
    >(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: RunParameters<KP, KM>,
        mut callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let RunParameters {
            query,
            parameters,
            bookmarks,
            tx_timeout,
            tx_metadata,
            mode,
            db,
            imp_user,
            notification_filter,
        } = parameters;
        check_no_notification_filter(data, notification_filter)?;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: RUN");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x10, 3)?;

        serializer.write_string(query)?;
        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_string(query).unwrap();
            dbg_serializer.flush()
        });

        write_parameter_dict(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            parameters,
        )?;

        let extra_size = [
            bookmarks.is_some() && !bookmarks.unwrap().is_empty(),
            tx_timeout.is_some(),
            tx_metadata.is_some() && !tx_metadata.unwrap().is_empty(),
            mode.is_some() && mode.unwrap() != "w",
            db.is_some(),
            imp_user.is_some(),
        ]
        .into_iter()
        .map(<bool as Into<u64>>::into)
        .sum();

        serializer.write_dict_header(extra_size)?;
        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_dict_header(extra_size).unwrap();
            dbg_serializer.flush()
        });

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

        write_db_entry(log_buf.as_mut(), &mut serializer, &mut dbg_serializer, db)?;

        write_imp_user_entry(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            imp_user,
        )?;

        callbacks = Self::install_qid_hook(data, callbacks);

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Run, callbacks));
        debug_buf_end!(data, log_buf);
        Ok(())
    }

    pub(super) fn install_qid_hook(
        data: &BoltData<impl Read + Write>,
        callbacks: ResponseCallbacks,
    ) -> ResponseCallbacks {
        callbacks.with_on_success_pre_hook({
            let last_qid = Arc::clone(&data.last_qid);
            move |meta| match meta.get("qid") {
                Some(ValueReceive::Integer(qid)) => {
                    *last_qid.borrow_mut() = Some(*qid);
                    Ok(())
                }
                None => {
                    *last_qid.borrow_mut() = None;
                    Ok(())
                }
                Some(v) => Err(Neo4jError::protocol_error(format!(
                    "server send non-int qid: {:?}",
                    v
                ))),
            }
        })
    }
}
