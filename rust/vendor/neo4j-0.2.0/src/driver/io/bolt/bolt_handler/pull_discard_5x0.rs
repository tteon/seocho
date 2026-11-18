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

use std::fmt::Debug;
use std::io::{Read, Write};

use super::super::message_parameters::{DiscardParameters, PullParameters};
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use crate::error_::Result;

pub(in super::super) struct PullHandler5x0();

impl PullHandler5x0 {
    pub(in super::super) fn pull<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: PullParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let PullParameters { n, qid } = parameters;
        pull_or_discard(
            data,
            n,
            qid,
            callbacks,
            PullOrDiscardMessageSpec {
                name: "PULL",
                tag: 0x3F,
                response: ResponseMessage::Pull,
            },
        )
    }
}

pub(in super::super) struct DiscardHandler5x0();

impl DiscardHandler5x0 {
    pub(in super::super) fn discard<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: DiscardParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let DiscardParameters { n, qid } = parameters;
        pull_or_discard(
            data,
            n,
            qid,
            callbacks,
            PullOrDiscardMessageSpec {
                name: "DISCARD",
                tag: 0x2F,
                response: ResponseMessage::Discard,
            },
        )
    }
}

#[derive(Debug)]
pub(super) struct PullOrDiscardMessageSpec {
    name: &'static str,
    tag: u8,
    response: ResponseMessage,
}

pub(super) fn pull_or_discard<RW: Read + Write>(
    data: &mut BoltData<RW>,
    n: i64,
    qid: i64,
    callbacks: ResponseCallbacks,
    message: PullOrDiscardMessageSpec,
) -> Result<()> {
    let PullOrDiscardMessageSpec {
        name,
        tag,
        response,
    } = message;
    debug_buf_start!(log_buf);
    debug_buf!(log_buf, "C: {}", name);
    let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
    let mut message_buff = Vec::new();
    let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
    serializer.write_struct_header(tag, 1)?;

    let can_omit_qid = data.can_omit_qid(qid);
    let extra_size = 1 + <bool as Into<u64>>::into(!can_omit_qid);
    debug_buf!(log_buf, " {}", {
        dbg_serializer.write_dict_header(extra_size).unwrap();
        dbg_serializer.write_string("n").unwrap();
        dbg_serializer.write_int(n).unwrap();
        dbg_serializer.flush()
    });
    serializer.write_dict_header(extra_size)?;
    serializer.write_string("n")?;
    serializer.write_int(n)?;
    if !can_omit_qid {
        serializer.write_string("qid")?;
        serializer.write_int(qid)?;
        debug_buf!(log_buf, "{}", {
            dbg_serializer.write_string("qid").unwrap();
            dbg_serializer.write_int(qid).unwrap();
            dbg_serializer.flush()
        });
    }

    data.message_buff.push_back(vec![message_buff]);
    data.responses
        .push_back(BoltResponse::new(response, callbacks));
    debug_buf_end!(data, log_buf);
    Ok(())
}
