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

use std::collections::HashMap;
use std::fmt::{Debug, Formatter};

use crate::error_::{Neo4jError, Result, ServerError};
use crate::value::ValueReceive;

#[derive(Debug, Copy, Clone, Ord, PartialOrd, Eq, PartialEq)]
pub(crate) enum ResponseMessage {
    Hello,
    Logon,
    Logoff,
    Reset,
    Run,
    Discard,
    Pull,
    Begin,
    Commit,
    Rollback,
    Route,
    Telemetry,
}

#[derive(Debug)]
pub(crate) struct BoltResponse {
    pub(crate) message: ResponseMessage,
    pub(crate) callbacks: ResponseCallbacks,
}

impl BoltResponse {
    pub(crate) fn new(message: ResponseMessage, callbacks: ResponseCallbacks) -> Self {
        Self { message, callbacks }
    }

    pub(crate) fn from_message(message: ResponseMessage) -> Self {
        Self::new(message, ResponseCallbacks::new())
    }
}

type OptBox<T> = Option<Box<T>>;
pub(crate) type BoltMeta = HashMap<String, ValueReceive>;
pub(crate) type BoltRecordFields = Vec<ValueReceive>;

pub(crate) struct ResponseCallbacks {
    on_success_cb: OptBox<dyn FnMut(BoltMeta) -> Result<()> + Send + Sync>,
    on_failure_cb: OptBox<dyn FnMut(ServerError) -> Result<()> + Send + Sync>,
    on_ignored_cb: OptBox<dyn FnMut() -> Result<()> + Send + Sync>,
    on_record_cb: OptBox<dyn FnMut(BoltRecordFields) -> Result<()> + Send + Sync>,
    on_summary_cb: OptBox<dyn FnMut() + Send + Sync>,
}

impl ResponseCallbacks {
    pub(crate) fn new() -> Self {
        Self {
            on_success_cb: None,
            on_failure_cb: None,
            on_ignored_cb: None,
            on_record_cb: None,
            on_summary_cb: None,
        }
        .with_on_failure(|error| Err(Neo4jError::ServerError { error }))
    }

    pub(crate) fn with_on_success<F: FnMut(BoltMeta) -> Result<()> + Send + Sync + 'static>(
        mut self,
        cb: F,
    ) -> Self {
        self.on_success_cb = Some(Box::new(cb));
        self
    }

    pub(crate) fn with_on_success_pre_hook<
        F: FnMut(&mut BoltMeta) -> Result<()> + Send + Sync + 'static,
    >(
        mut self,
        mut pre_hook: F,
    ) -> Self {
        match self.on_success_cb {
            None => self.on_success_cb = Some(Box::new(move |mut meta| pre_hook(&mut meta))),
            Some(mut cb) => {
                self.on_success_cb = Some(Box::new(move |mut meta| {
                    pre_hook(&mut meta)?;
                    cb(meta)
                }))
            }
        };
        self
    }

    pub(crate) fn with_on_failure<F: FnMut(ServerError) -> Result<()> + Send + Sync + 'static>(
        mut self,
        cb: F,
    ) -> Self {
        self.on_failure_cb = Some(Box::new(cb));
        self
    }

    pub(crate) fn with_on_ignored<F: FnMut() -> Result<()> + Send + Sync + 'static>(
        mut self,
        cb: F,
    ) -> Self {
        self.on_ignored_cb = Some(Box::new(cb));
        self
    }

    pub(crate) fn with_on_record<
        F: FnMut(BoltRecordFields) -> Result<()> + Send + Sync + 'static,
    >(
        mut self,
        cb: F,
    ) -> Self {
        self.on_record_cb = Some(Box::new(cb));
        self
    }

    pub(crate) fn on_success(&mut self, meta: ValueReceive) -> Result<()> {
        let res = match meta {
            ValueReceive::Map(meta) => match self.on_success_cb.as_mut() {
                None => Ok(()),
                Some(cb) => cb(meta),
            },
            _ => Err(Neo4jError::protocol_error(
                "onSuccess meta was not a Dictionary",
            )),
        };
        self.on_summary();
        res
    }

    pub(crate) fn on_failure(&mut self, error: ServerError) -> Result<()> {
        let res = match self.on_failure_cb.as_mut() {
            None => Ok(()),
            Some(cb) => cb(error),
        };
        self.on_summary();
        res
    }

    pub(crate) fn on_ignored(&mut self) -> Result<()> {
        let res = self.on_ignored_cb.as_mut().map(|cb| cb()).unwrap_or(Ok(()));
        self.on_summary();
        res
    }

    pub(crate) fn on_record(&mut self, data: ValueReceive) -> Result<()> {
        match data {
            ValueReceive::List(values) => match self.on_record_cb.as_mut() {
                None => Ok(()),
                Some(cb) => cb(values),
            },
            _ => Err(Neo4jError::protocol_error("onRecord data was not a List")),
        }
    }

    fn on_summary(&mut self) {
        if let Some(cb) = self.on_summary_cb.as_mut() {
            cb()
        }
    }
}

impl Debug for ResponseCallbacks {
    fn fmt(&self, f: &mut Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("ResponseCallbacks")
            .field("on_success", &self.on_success_cb.as_ref().map(|_| "..."))
            .field("on_failure", &self.on_failure_cb.as_ref().map(|_| "..."))
            .field("on_ignored", &self.on_ignored_cb.as_ref().map(|_| "..."))
            .field("on_record", &self.on_record_cb.as_ref().map(|_| "..."))
            .field("on_summary", &self.on_summary_cb.as_ref().map(|_| "..."))
            .finish()
    }
}
