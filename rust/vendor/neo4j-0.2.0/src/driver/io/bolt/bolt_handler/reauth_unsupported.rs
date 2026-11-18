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

use super::super::bolt_common::{unsupported_protocol_feature_error, ServerAwareBoltVersion};
use super::super::message_parameters::ReauthParameters;
use super::super::{BoltData, BoltStructTranslator};
use crate::error_::Result;

pub(in super::super) struct ReauthUnsupportedHandler();

impl ReauthUnsupportedHandler {
    #[inline]
    pub(in super::super) fn reauth<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        _: ReauthParameters,
    ) -> Result<()> {
        Err(unsupported_protocol_feature_error(
            "session authentication",
            data.protocol_version,
            ServerAwareBoltVersion::V5x1,
        ))
    }

    #[inline]
    pub(in super::super) fn supports_reauth() -> bool {
        false
    }
}
