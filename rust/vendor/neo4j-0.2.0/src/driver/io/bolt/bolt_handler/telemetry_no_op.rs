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

use super::super::message_parameters::TelemetryParameters;
use super::super::response::ResponseCallbacks;
use super::super::{BoltData, BoltStructTranslator};
use crate::error_::Result;

pub(in super::super) struct TelemetryNoOpHandler();

impl TelemetryNoOpHandler {
    #[inline]
    pub(in super::super) fn telemetry<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        _data: &mut BoltData<RW>,
        _parameters: TelemetryParameters,
        _callbacks: ResponseCallbacks,
    ) -> Result<()> {
        // TELEMETRY not support by this protocol version, so we ignore it.
        Ok(())
    }
}
