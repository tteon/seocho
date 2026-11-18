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
use super::common::{assert_response_field_count, enrich_failure_diag_record};
use super::res_failure_5x0::ResultFailureHandler5x0;
use crate::error_::{Neo4jError, Result, ServerError};
use crate::value::ValueReceive;

pub(in super::super) struct ResultFailureHandler5x7();

impl ResultFailureHandler5x7 {
    pub(in super::super) fn handle_response<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        mut response: BoltResponse,
        mut fields: Vec<ValueReceive>,
        on_server_error: OnServerErrorCb<RW>,
    ) -> Result<()> {
        assert_response_field_count("FAILURE", &fields, 1)?;
        let mut meta = fields.pop().unwrap();
        bolt_debug!(data, "S: FAILURE {}", meta.dbg_print());
        meta.as_map_mut().map(enrich_failure_diag_record);
        let error = Self::try_parse_error(meta)?;
        data.bolt_state.failure();
        ResultFailureHandler5x0::apply_on_server_error(data, &mut response, on_server_error, error)
    }

    pub(super) fn try_parse_error(meta: ValueReceive) -> Result<ServerError> {
        let meta = meta
            .try_into_map()
            .map_err(|_| Neo4jError::protocol_error("FAILURE meta was not a Dictionary"))?;
        Ok(ServerError::from_meta_gql(meta))
    }
}
