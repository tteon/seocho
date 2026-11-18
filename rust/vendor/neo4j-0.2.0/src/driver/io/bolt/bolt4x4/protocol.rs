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

use std::fmt::Debug;
use std::sync::Arc;

use atomic_refcell::AtomicRefCell;

use super::super::bolt_handler::{
    begin_5x0::BeginHandler5x0, commit_5x0::CommitHandler5x0, goodbye_5x0::GoodbyeHandler5x0,
    hello_4x4::HelloHandler4x4, impl_begin, impl_commit, impl_discard, impl_goodbye, impl_hello,
    impl_load_value, impl_pull, impl_reauth, impl_reset, impl_response, impl_rollback, impl_route,
    impl_run, impl_telemetry, pull_discard_5x0::DiscardHandler5x0,
    pull_discard_5x0::PullHandler5x0, reauth_unsupported::ReauthUnsupportedHandler,
    res_failure_5x0::ResultFailureHandler5x0, res_ignored_5x0::ResultIgnoredHandler5x0,
    res_record_5x0::ResultRecordHandler5x0, res_success_5x0::ResultSuccessHandler5x0,
    reset_5x0::ResetHandler5x0, rollback_5x0::RollbackHandler5x0, route_5x0::RouteHandler5x0,
    run_5x0::RunHandler5x0, telemetry_no_op::TelemetryNoOpHandler,
};
use super::super::BoltStructTranslatorWithUtcPatch;

#[derive(Debug, Default)]
pub(crate) struct Bolt4x4<T: BoltStructTranslatorWithUtcPatch + Sync + Send + 'static> {
    translator: Arc<AtomicRefCell<T>>,
}

impl_hello!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, HelloHandler4x4);
impl_reauth!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, ReauthUnsupportedHandler);
impl_goodbye!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, GoodbyeHandler5x0);
impl_reset!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, ResetHandler5x0);
impl_run!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, RunHandler5x0);
impl_discard!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, DiscardHandler5x0);
impl_pull!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, PullHandler5x0);
impl_begin!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, BeginHandler5x0);
impl_commit!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, CommitHandler5x0);
impl_rollback!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, RollbackHandler5x0);
impl_route!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, RouteHandler5x0);
impl_telemetry!((BoltStructTranslatorWithUtcPatch, Sync, Send, 'static), Bolt4x4<T>, TelemetryNoOpHandler);
impl_response!(
    (BoltStructTranslatorWithUtcPatch, Sync, Send, 'static),
    Bolt4x4<T>,
    (0x70, ResultSuccessHandler5x0),
    (0x7E, ResultIgnoredHandler5x0),
    (0x7F, ResultFailureHandler5x0),
    (0x71, ResultRecordHandler5x0)
);
impl_load_value!(
    (BoltStructTranslatorWithUtcPatch, Sync, Send, 'static),
    Bolt4x4<T>
);
