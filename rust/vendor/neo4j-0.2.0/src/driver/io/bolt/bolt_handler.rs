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

pub(super) mod begin_5x0;
pub(super) mod begin_5x2;
pub(super) mod commit_5x0;
mod common;
pub(super) mod goodbye_5x0;
pub(super) mod hello_4x4;
pub(super) mod hello_5x0;
pub(super) mod hello_5x1;
pub(super) mod hello_5x2;
pub(super) mod hello_5x3;
pub(super) mod hello_5x4;
pub(super) mod hello_5x8;
pub(super) mod pull_discard_5x0;
pub(super) mod pull_discard_5x6;
pub(super) mod reauth_5x1;
pub(super) mod reauth_unsupported;
pub(super) mod res_failure_5x0;
pub(super) mod res_failure_5x7;
pub(super) mod res_ignored_5x0;
pub(super) mod res_record_5x0;
pub(super) mod res_success_5x0;
pub(super) mod reset_5x0;
pub(super) mod rollback_5x0;
pub(super) mod route_5x0;
pub(super) mod run_5x0;
pub(super) mod run_5x2;
pub(super) mod run_5x6;
pub(super) mod telemetry5x4;
pub(super) mod telemetry_no_op;

use enum_dispatch::enum_dispatch;

use super::message_parameters::{
    BeginParameters, CommitParameters, DiscardParameters, GoodbyeParameters, HelloParameters,
    PullParameters, ReauthParameters, ResetParameters, RollbackParameters, RouteParameters,
    RunParameters, TelemetryParameters,
};
use super::{BoltData, OnServerErrorCb, ResponseCallbacks};
use crate::driver::io::bolt::message::BoltMessage;
use crate::error_::Result;
use crate::ValueReceive;
use std::borrow::Borrow;
use std::fmt::Debug;
use std::io::{Read, Write};

#[enum_dispatch]
pub(super) trait HelloHandler {
    fn hello<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: HelloParameters,
    ) -> Result<()>;
}
macro_rules! impl_hello {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::HelloHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn hello<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::HelloParameters,
            ) -> crate::error_::Result<()> {
                <$Handler>::hello(&self.translator, data, parameters)
            }
        }
    };
}
pub(super) use impl_hello;

#[enum_dispatch]
pub(super) trait ReauthHandler {
    fn reauth<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: ReauthParameters,
    ) -> Result<()>;

    fn supports_reauth(&self) -> bool;
}
macro_rules! impl_reauth {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::ReauthHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn reauth<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::ReauthParameters,
            ) -> crate::error_::Result<()> {
                <$Handler>::reauth(&self.translator, data, parameters)
            }

            #[inline]
            fn supports_reauth(&self) -> bool {
                <$Handler>::supports_reauth()
            }
        }
    };
}
pub(super) use impl_reauth;

#[enum_dispatch]
pub(super) trait GoodbyeHandler {
    fn goodbye<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: GoodbyeParameters,
    ) -> Result<()>;
}
macro_rules! impl_goodbye {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::GoodbyeHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn goodbye<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::GoodbyeParameters,
            ) -> crate::error_::Result<()> {
                <$Handler>::goodbye(&self.translator, data, parameters)
            }
        }
    };
}
pub(super) use impl_goodbye;

#[enum_dispatch]
pub(super) trait ResetHandler {
    fn reset<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: ResetParameters,
    ) -> Result<()>;
}
macro_rules! impl_reset {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::ResetHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn reset<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::ResetParameters,
            ) -> crate::error_::Result<()> {
                <$Handler>::reset(&self.translator, data, parameters)
            }
        }
    };
}
pub(super) use impl_reset;

#[enum_dispatch]
pub(super) trait RunHandler {
    fn run<RW: Read + Write, KP: Borrow<str> + Debug, KM: Borrow<str> + Debug>(
        &self,
        data: &mut BoltData<RW>,
        parameters: RunParameters<KP, KM>,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_run {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::RunHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn run<
                RW: std::io::Read + std::io::Write,
                KP: std::borrow::Borrow<str> + std::fmt::Debug,
                KM: std::borrow::Borrow<str> + std::fmt::Debug,
            >(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::RunParameters<KP, KM>,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::run(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_run;

#[enum_dispatch]
pub(super) trait DiscardHandler {
    fn discard<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: DiscardParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_discard {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::DiscardHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn discard<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::DiscardParameters,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::discard(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_discard;

#[enum_dispatch]
pub(super) trait PullHandler {
    fn pull<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: PullParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_pull {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::PullHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn pull<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::PullParameters,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::pull(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_pull;

#[enum_dispatch]
pub(super) trait BeginHandler {
    fn begin<RW: Read + Write, K: Borrow<str> + Debug>(
        &self,
        data: &mut BoltData<RW>,
        parameters: BeginParameters<K>,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_begin {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::BeginHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn begin<
                RW: std::io::Read + std::io::Write,
                K: std::borrow::Borrow<str> + std::fmt::Debug,
            >(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::BeginParameters<K>,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::begin(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_begin;

#[enum_dispatch]
pub(super) trait CommitHandler {
    fn commit<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: CommitParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_commit {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::CommitHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn commit<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::CommitParameters,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::commit(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_commit;

#[enum_dispatch]
pub(super) trait RollbackHandler {
    fn rollback<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: RollbackParameters,
    ) -> Result<()>;
}
macro_rules! impl_rollback {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::RollbackHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn rollback<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::RollbackParameters,
            ) -> crate::error_::Result<()> {
                <$Handler>::rollback(&self.translator, data, parameters)
            }
        }
    };
}
pub(super) use impl_rollback;

#[enum_dispatch]
pub(super) trait RouteHandler {
    fn route<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: RouteParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_route {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::RouteHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn route<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::RouteParameters,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::route(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_route;

#[enum_dispatch]
pub(super) trait TelemetryHandler {
    fn telemetry<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        parameters: TelemetryParameters,
        callbacks: ResponseCallbacks,
    ) -> Result<()>;
}
macro_rules! impl_telemetry {
    (($($HandlerType:tt),+), $ProtocolType:ty, $Handler:ty) => {
        impl<T> crate::driver::io::bolt::bolt_handler::TelemetryHandler
            for $ProtocolType
            where $(T: $HandlerType),+
        {
            #[inline]
            fn telemetry<RW: std::io::Read + std::io::Write>(
                &self,
                data: &mut crate::driver::io::bolt::BoltData<RW>,
                parameters: crate::driver::io::bolt::message_parameters::TelemetryParameters,
                callbacks: crate::driver::io::bolt::response::ResponseCallbacks,
            ) -> crate::error_::Result<()> {
                <$Handler>::telemetry(&self.translator, data, parameters, callbacks)
            }
        }
    };
}
pub(super) use impl_telemetry;

#[enum_dispatch]
pub(super) trait HandleResponseHandler {
    fn handle_response<RW: Read + Write>(
        &self,
        data: &mut BoltData<RW>,
        message: BoltMessage<ValueReceive>,
        on_server_error: OnServerErrorCb<RW>,
    ) -> Result<()>;
}

macro_rules! impl_response {
    (($($HandlerType:tt),+), $ProtocolType:ty, $(($tag:expr, $Handler:ty)),+) => {
        mod impl_response {
            use super::*;
            #[allow(unused_imports)]
            use crate::driver::io::bolt::message::BoltMessage;
            #[allow(unused_imports)]
            use crate::value::ValueReceive;
            #[allow(unused_imports)]
            use crate::error_::Neo4jError;

            impl<T> crate::driver::io::bolt::bolt_handler::HandleResponseHandler
                for $ProtocolType
                where $(T: $HandlerType),+
            {
                fn handle_response<RW: std::io::Read + std::io::Write>(
                    &self,
                    data: &mut crate::driver::io::bolt::BoltData<RW>,
                    message: BoltMessage<ValueReceive>,
                    on_server_error: crate::driver::io::bolt::OnServerErrorCb<RW>,
                ) -> crate::error_::Result<()> {
                    let response = data
                        .responses
                        .pop_front()
                        .expect("called Bolt::read_one with empty response queue");
                    match message {
                        $(BoltMessage {
                            tag: $tag,
                            fields,
                        } => {
                            <$Handler>::handle_response(&self.translator, data, response, fields, on_server_error)
                        })+
                        BoltMessage { tag, .. } => Err(Neo4jError::protocol_error(format!(
                            "unknown response message tag {:02X?}",
                            tag
                        ))),
                    }
                }
            }
        }
    };
}
pub(super) use impl_response;

#[enum_dispatch]
pub(super) trait LoadValueHandler {
    fn load_value(&self, reader: &mut impl Read) -> Result<ValueReceive>;
}
macro_rules! impl_load_value {
    (($($HandlerType:tt),+), $ProtocolType:ty) => {
        mod impl_load_value {
            use super::*;
            #[allow(unused_imports)]
            use crate::driver::io::bolt::packstream::{PackStreamDeserializerImpl, PackStreamDeserializer};

            impl<T> crate::driver::io::bolt::bolt_handler::LoadValueHandler
                for $ProtocolType
                where $(T: $HandlerType),+
            {
                #[inline]
                fn load_value(
                    &self,
                    reader: &mut impl std::io::Read,
                ) -> crate::error_::Result<crate::value::ValueReceive> {
                    let mut deserializer = PackStreamDeserializerImpl::new(reader);
                    deserializer.load(&self.translator).map_err(Into::into)
                }
            }
        }
    };
}
pub(super) use impl_load_value;
