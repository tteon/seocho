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
use std::ops::Deref;

use atomic_refcell::AtomicRefCell;

use super::super::message_parameters::{TelemetryAPI, TelemetryParameters};
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseCallbacks, ResponseMessage};
use super::super::{debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator};
use crate::error_::Result;

pub(in super::super) struct TelemetryHandler5x4();

impl TelemetryHandler5x4 {
    pub(in super::super) fn telemetry<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: TelemetryParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        if !AtomicRefCell::borrow(&data.telemetry_enabled).deref() {
            return Ok(());
        }

        let TelemetryParameters { api } = parameters;
        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: TELEMETRY");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x54, 1)?;

        serializer.write_int(Self::encode_telemetry(api))?;
        debug_buf!(
            log_buf,
            " {} // ({})",
            {
                dbg_serializer
                    .write_int(Self::encode_telemetry(api))
                    .unwrap();
                dbg_serializer.flush()
            },
            api.name()
        );

        data.message_buff.push_back(vec![message_buff]);
        debug_buf_end!(data, log_buf);
        data.responses
            .push_back(BoltResponse::new(ResponseMessage::Telemetry, callbacks));
        Ok(())
    }

    pub(super) fn encode_telemetry(api: TelemetryAPI) -> i64 {
        match api {
            TelemetryAPI::TxFunc => 0,
            TelemetryAPI::UnmanagedTx => 1,
            TelemetryAPI::AutoCommit => 2,
            TelemetryAPI::DriverLevel => 3,
        }
    }
}
