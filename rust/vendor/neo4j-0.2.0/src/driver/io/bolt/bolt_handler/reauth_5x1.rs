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
use std::sync::Arc;

use super::super::message_parameters::ReauthParameters;
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::{BoltResponse, ResponseMessage};
use super::super::{
    bolt_debug, debug_buf, debug_buf_end, debug_buf_start, BoltData, BoltStructTranslator,
};
use super::common::write_auth_dict;
use crate::error_::Result;

pub(in super::super) struct ReauthHandler5x1();

impl ReauthHandler5x1 {
    #[inline]
    pub(in super::super) fn reauth<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: ReauthParameters,
    ) -> Result<()> {
        if data.auth.is_some() {
            Self::logoff(data)?;
        }
        data.auth = Some(Arc::clone(parameters.auth));
        Self::logon(translator, data, parameters)
    }

    #[inline]
    pub(in super::super) fn supports_reauth() -> bool {
        true
    }

    pub(super) fn logon<RW: Read + Write>(
        translator: &impl BoltStructTranslator,
        data: &mut BoltData<RW>,
        parameters: ReauthParameters,
    ) -> Result<()> {
        let ReauthParameters {
            auth,
            session_auth: _,
        } = parameters;

        data.auth = Some(Arc::clone(auth));
        let auth = data
            .auth
            .as_ref()
            .expect("is some because of previous line");

        debug_buf_start!(log_buf);
        debug_buf!(log_buf, "C: LOGON");
        let mut dbg_serializer = PackStreamSerializerDebugImpl::new();
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x6A, 1)?;

        write_auth_dict(
            translator,
            log_buf.as_mut(),
            &mut serializer,
            &mut dbg_serializer,
            data,
            auth,
        )?;

        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::from_message(ResponseMessage::Logon));
        debug_buf_end!(data, log_buf);
        Ok(())
    }

    pub(super) fn logoff<RW: Read + Write>(data: &mut BoltData<RW>) -> Result<()> {
        let mut message_buff = Vec::new();
        let mut serializer = PackStreamSerializerImpl::new(&mut message_buff);
        serializer.write_struct_header(0x6B, 0)?;
        data.message_buff.push_back(vec![message_buff]);
        data.responses
            .push_back(BoltResponse::from_message(ResponseMessage::Logoff));
        bolt_debug!(data, "C: LOGOFF");
        Ok(())
    }
}
