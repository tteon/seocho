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
use std::collections::HashMap;
use std::fmt::Debug;
use std::sync::Arc;

use crate::bookmarks::Bookmarks;
use crate::driver::config::auth::AuthToken;
use crate::driver::config::notification::NotificationFilter;
use crate::value::ValueSend;

#[derive(Debug, Clone, Copy)]
pub(crate) struct HelloParameters<'a> {
    pub(super) user_agent: &'a str,
    pub(super) auth: &'a Arc<AuthToken>,
    pub(super) routing_context: Option<&'a HashMap<String, ValueSend>>,
    pub(super) notification_filter: &'a NotificationFilter,
}

impl<'a> HelloParameters<'a> {
    pub(crate) fn new(
        user_agent: &'a str,
        auth: &'a Arc<AuthToken>,
        routing_context: Option<&'a HashMap<String, ValueSend>>,
        notification_filter: &'a NotificationFilter,
    ) -> Self {
        Self {
            user_agent,
            auth,
            routing_context,
            notification_filter,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ReauthParameters<'a> {
    pub(super) auth: &'a Arc<AuthToken>,
    pub(super) session_auth: bool,
}

impl<'a> ReauthParameters<'a> {
    pub(crate) fn new(auth: &'a Arc<AuthToken>, session_auth: bool) -> Self {
        Self { auth, session_auth }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct GoodbyeParameters {}

impl GoodbyeParameters {
    pub(crate) fn new() -> Self {
        Self {}
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ResetParameters {}

impl ResetParameters {
    pub(crate) fn new() -> Self {
        Self {}
    }
}

#[derive(Debug, Clone)]
pub(crate) struct RunParameters<'a, KP: Borrow<str> + Debug, KM: Borrow<str> + Debug> {
    pub(super) query: &'a str,
    pub(super) parameters: Option<&'a HashMap<KP, ValueSend>>,
    pub(super) bookmarks: Option<&'a Bookmarks>,
    pub(super) tx_timeout: Option<i64>,
    pub(super) tx_metadata: Option<&'a HashMap<KM, ValueSend>>,
    pub(super) mode: Option<&'a str>,
    pub(super) db: Option<&'a str>,
    pub(super) imp_user: Option<&'a str>,
    pub(super) notification_filter: Option<&'a NotificationFilter>,
}

impl<'a, KP: Borrow<str> + Debug, KM: Borrow<str> + Debug> RunParameters<'a, KP, KM> {
    #[allow(clippy::too_many_arguments)] // builder pattern for internal API seems overkill
    pub(crate) fn new_auto_commit_run(
        query: &'a str,
        parameters: Option<&'a HashMap<KP, ValueSend>>,
        bookmarks: Option<&'a Bookmarks>,
        tx_timeout: Option<i64>,
        tx_metadata: Option<&'a HashMap<KM, ValueSend>>,
        mode: Option<&'a str>,
        db: Option<&'a str>,
        imp_user: Option<&'a str>,
        notification_filter: &'a NotificationFilter,
    ) -> Self {
        Self {
            query,
            parameters,
            bookmarks,
            tx_timeout,
            tx_metadata,
            mode,
            db,
            imp_user,
            notification_filter: Some(notification_filter),
        }
    }
}

impl<'a, KP: Borrow<str> + Debug> RunParameters<'a, KP, String> {
    pub(crate) fn new_transaction_run(
        query: &'a str,
        parameters: Option<&'a HashMap<KP, ValueSend>>,
    ) -> Self {
        Self {
            query,
            parameters,
            bookmarks: None,
            tx_timeout: None,
            tx_metadata: None,
            mode: None,
            db: None,
            imp_user: None,
            notification_filter: None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct DiscardParameters {
    pub(super) n: i64,
    pub(super) qid: i64,
}

impl DiscardParameters {
    pub(crate) fn new(n: i64, qid: i64) -> Self {
        Self { n, qid }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct PullParameters {
    pub(super) n: i64,
    pub(super) qid: i64,
}

impl PullParameters {
    pub(crate) fn new(n: i64, qid: i64) -> Self {
        Self { n, qid }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct BeginParameters<'a, K: Borrow<str> + Debug> {
    pub(super) bookmarks: Option<&'a Bookmarks>,
    pub(super) tx_timeout: Option<i64>,
    pub(super) tx_metadata: Option<&'a HashMap<K, ValueSend>>,
    pub(super) mode: Option<&'a str>,
    pub(super) db: Option<Arc<String>>,
    pub(super) imp_user: Option<&'a str>,
    pub(super) notification_filter: &'a NotificationFilter,
}

impl<'a, K: Borrow<str> + Debug> BeginParameters<'a, K> {
    pub(crate) fn new(
        bookmarks: Option<&'a Bookmarks>,
        tx_timeout: Option<i64>,
        tx_metadata: Option<&'a HashMap<K, ValueSend>>,
        mode: Option<&'a str>,
        db: Option<Arc<String>>,
        imp_user: Option<&'a str>,
        notification_filter: &'a NotificationFilter,
    ) -> Self {
        Self {
            bookmarks,
            tx_timeout,
            tx_metadata,
            mode,
            db,
            imp_user,
            notification_filter,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct CommitParameters {}

impl CommitParameters {
    pub(crate) fn new() -> Self {
        Self {}
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct RollbackParameters {}

impl RollbackParameters {
    pub(crate) fn new() -> Self {
        Self {}
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct RouteParameters<'a> {
    pub(super) routing_context: &'a HashMap<String, ValueSend>,
    pub(super) bookmarks: Option<&'a Bookmarks>,
    pub(super) db: Option<&'a str>,
    pub(super) imp_user: Option<&'a str>,
}

impl<'a> RouteParameters<'a> {
    pub(crate) fn new(
        routing_context: &'a HashMap<String, ValueSend>,
        bookmarks: Option<&'a Bookmarks>,
        db: Option<&'a str>,
        imp_user: Option<&'a str>,
    ) -> Self {
        Self {
            routing_context,
            bookmarks,
            db,
            imp_user,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum TelemetryAPI {
    TxFunc,
    UnmanagedTx,
    AutoCommit,
    DriverLevel,
}

impl TelemetryAPI {
    pub(crate) fn name(&self) -> &'static str {
        match self {
            Self::TxFunc => "Session::transaction with retry",
            Self::UnmanagedTx => "Session::transaction without retry",
            Self::AutoCommit => "Session::auto_commit",
            Self::DriverLevel => "Driver::execute_query",
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct TelemetryParameters {
    pub(super) api: TelemetryAPI,
}

impl TelemetryParameters {
    pub(crate) fn new(api: TelemetryAPI) -> Self {
        Self { api }
    }
}
