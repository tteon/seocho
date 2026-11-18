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

use super::super::message_parameters::RunParameters;
use super::super::response::ResponseCallbacks;
use super::super::{BoltData, BoltStructTranslator};
use super::common::make_enrich_diag_record_callback;
use super::run_5x2::RunHandler5x2;
use crate::error_::Result;

pub(in super::super) struct RunHandler5x6();

impl RunHandler5x6 {
    pub(in super::super) fn run<
        RW: Read + Write,
        KP: Borrow<str> + Debug,
        KM: Borrow<str> + Debug,
    >(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: RunParameters<KP, KM>,
        callbacks: ResponseCallbacks,
    ) -> Result<()> {
        let callbacks = make_enrich_diag_record_callback(callbacks);
        RunHandler5x2::run(translator, data, parameters, callbacks)
    }
}
