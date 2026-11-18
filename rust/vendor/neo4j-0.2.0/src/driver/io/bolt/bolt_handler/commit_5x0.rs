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

use super::super::message_parameters::CommitParameters;
use super::super::packstream::{PackStreamSerializer, PackStreamSerializerImpl};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{bolt_debug, BoltData, BoltStructTranslator};
use crate::error_::Result;

pub(in super::super) struct CommitHandler5x0();

impl CommitHandler5x0 {
    pub(in super::super) fn commit<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        _: CommitParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x12, 0)?;

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Commit, callbacks));
        bolt_debug!(data, "C: COMMIT");
        Ok(())
    }
}
