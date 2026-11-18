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

use log::warn;

use super::super::response::BoltResponse;
use super::super::{bolt_debug, BoltData, BoltStructTranslator, OnServerErrorCb};
use super::common::assert_response_field_count;
use crate::error::ServerError;
use crate::error_::{Neo4jError, Result};
use crate::value::ValueReceive;

pub(in super::super) struct ResultFailureHandler5x0();

impl ResultFailureHandler5x0 {
    pub(in super::super) fn handle_response<RW: Read + Write>(
        _: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        mut response: BoltResponse,
        mut fields: Vec<ValueReceive>,
        on_server_error: OnServerErrorCb<RW>,
    ) -> Result<()> {
        assert_response_field_count("FAILURE", &fields, 1)?;
        let meta = fields.pop().unwrap();
        bolt_debug!(data, "S: FAILURE {}", meta.dbg_print());
        let error = Self::try_parse_error(meta)?;
        data.bolt_state.failure();
        Self::apply_on_server_error(data, &mut response, on_server_error, error)
    }

    pub(super) fn apply_on_server_error<RW: Read + Write>(
        data: &mut BoltData<RW>,
        response: &mut BoltResponse,
        on_server_error: OnServerErrorCb<RW>,
        mut error: ServerError,
    ) -> Result<()> {
        match on_server_error {
            None => response.callbacks.on_failure(error),
            Some(cb) => {
                let res1 = cb(data, &mut error);
                let res2 = response.callbacks.on_failure(error);
                match res1 {
                    Ok(()) => res2,
                    Err(e1) => {
                        if let Err(e2) = res2 {
                            warn!("server error swallowed because of user callback error: {e2}");
                        }
                        Err(e1)
                    }
                }
            }
        }
    }

    pub(super) fn try_parse_error(meta: ValueReceive) -> Result<ServerError> {
        let meta = meta
            .try_into_map()
            .map_err(|_| Neo4jError::protocol_error("FAILURE meta was not a Dictionary"))?;
        Ok(ServerError::from_meta(meta))
    }
}
