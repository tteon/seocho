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

use super::super::message_parameters::{DiscardParameters, PullParameters};
use super::super::response::ResponseCallbacks;
use super::super::{BoltData, BoltStructTranslator};
use super::common::make_enrich_diag_record_callback;
use super::pull_discard_5x0::{DiscardHandler5x0, PullHandler5x0};
use crate::error_::Result;

pub(in super::super) struct PullHandler5x6();

impl PullHandler5x6 {
    pub(in super::super) fn pull<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: PullParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let callbacks = make_enrich_diag_record_callback(callbacks);
        PullHandler5x0::pull(translator, data, parameters, callbacks)
    }
}

pub(in super::super) struct DiscardHandler5x6();

impl DiscardHandler5x6 {
    pub(in super::super) fn discard<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: DiscardParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let callbacks = make_enrich_diag_record_callback(callbacks);
        DiscardHandler5x0::discard(translator, data, parameters, callbacks)
    }
}
