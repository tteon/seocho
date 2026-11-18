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

use super::super::response::BoltResponse;
use super::super::{bolt_debug, BoltData, BoltStructTranslator, OnServerErrorCb};
use super::common::assert_response_field_count;
use crate::error_::Result;
use crate::value::ValueReceive;

pub(in super::super) struct ResultRecordHandler5x0();

impl ResultRecordHandler5x0 {
    pub(in super::super) fn handle_response<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        mut response: BoltResponse,
        mut fields: Vec<ValueReceive>,
        _: OnServerErrorCb<RW>,
    ) -> Result<()> {
        assert_response_field_count("RECORD", &fields, 1)?;
        let record_data = fields.pop().unwrap();
        bolt_debug!(data, "S: RECORD [...]");
        let res = response.callbacks.on_record(record_data);
        data.responses.push_front(response);
        res
    }
}
