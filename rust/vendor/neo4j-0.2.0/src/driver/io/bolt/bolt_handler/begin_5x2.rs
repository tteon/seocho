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
use super::common::{notification_filter_entries_count, write_notification_filter_entries};
use crate::error_::Result;

pub(in super::super) struct BeginHandler5x2();

impl BeginHandler5x2 {
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
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: BEGIN");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x11, 1)?;

        let extra_size = notification_filter_entries_count(Some(notification_filter))
            + [
                bookmarks.is_some() && !bookmarks.unwrap().is_empty(),
                tx_timeout.is_some(),
                tx_metadata.map(|m| !m.is_empty()).unwrap_or_default(),
                mode.is_some() && mode.unwrap() != "w",
                db.is_some(),
                imp_user.is_some(),
            ]
            .into_iter()
            .map(<bool as Into<u64>>::into)
            .sum::<u64>();

        debug_buf!(log_buf, " {}", {
            dbg_serializer.write_dict_header(extra_size).unwrap();
            dbg_serializer.flush()
        });
        serializer.write_dict_header(extra_size)?;

        if let Some(bookmarks) = bookmarks {
            if !bookmarks.is_empty() {
                debug_buf!(log_buf, "{}", {
                    dbg_serializer.write_string("bookmarks").unwrap();
                    data.serialize_str_iter(&mut dbg_serializer, bookmarks.raw())
                        .unwrap();
                    dbg_serializer.flush()
                });
                serializer.write_string("bookmarks").unwrap();
                data.serialize_str_iter(&mut serializer, bookmarks.raw())?;
            }
        }

        if let Some(tx_timeout) = tx_timeout {
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("tx_timeout").unwrap();
                dbg_serializer.write_int(tx_timeout).unwrap();
                dbg_serializer.flush()
            });
            serializer.write_string("tx_timeout")?;
            serializer.write_int(tx_timeout)?;
        }

        if let Some(tx_metadata) = tx_metadata {
            if !tx_metadata.is_empty() {
                debug_buf!(log_buf, "{}", {
                    dbg_serializer.write_string("tx_metadata").unwrap();
                    data.serialize_dict(&mut dbg_serializer, translator, tx_metadata)
                        .unwrap();
                    dbg_serializer.flush()
                });
                serializer.write_string("tx_metadata")?;
                data.serialize_dict(&mut serializer, translator, tx_metadata)?;
            }
        }

        if let Some(mode) = mode {
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("mode").unwrap();
                dbg_serializer.write_string(mode).unwrap();
                dbg_serializer.flush()
            });
            serializer.write_string("mode")?;
            serializer.write_string(mode)?;
        }

        if let Some(db) = db {
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("db").unwrap();
                dbg_serializer.write_string(&db).unwrap();
                dbg_serializer.flush()
            });
            serializer.write_string("db")?;
            serializer.write_string(&db)?;
        }

        if let Some(imp_user) = imp_user {
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("imp_user").unwrap();
                dbg_serializer.write_string(imp_user).unwrap();
                dbg_serializer.flush()
            });
            serializer.write_string("imp_user")?;
            serializer.write_string(imp_user)?;
        }

        write_notification_filter_entries(
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            Some(notification_filter),
        )?;

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Begin, callbacks));
        debug_buf_end!(data, log_buf);
        Ok(())
    }
}
