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

use log::debug;
use std::collections::HashMap;

use super::bolt_debug_extra;
use super::response::ResponseMessage;
use crate::value::ValueReceive;

#[derive(Debug, Copy, Clone, Ord, PartialOrd, Eq, PartialEq)]
pub(crate) enum BoltState {
    Connected,
    Authenticating,
    Ready,
    Streaming,
    TxReady,
    // TxMaybeStreaming is a simplification.
    // The server transitions from TxStreaming to TxReady when *all* current
    // result streams have been consumed. This distinction is not necessary in
    // the driver and allows for much simpler code as the connection does not
    // have to track how many results are still active.
    TxMaybeStreaming,
    Failed,
}

#[derive(Debug)]
pub(crate) struct BoltStateTracker {
    version: (u8, u8),
    state: BoltState,
}

impl BoltStateTracker {
    pub(crate) fn new(version: (u8, u8)) -> Self {
        Self {
            version,
            state: BoltState::Connected,
        }
    }

    pub(crate) fn state(&self) -> BoltState {
        self.state
    }

    pub(crate) fn success<E>(
        &mut self,
        message: ResponseMessage,
        meta: &ValueReceive,
        bolt_local_port: Option<u16>,
        bolt_meta: Result<&HashMap<String, ValueReceive>, E>,
    ) {
        if let ValueReceive::Map(meta) = meta {
            if let Some(ValueReceive::Boolean(true)) = meta.get("has_more") {
                // nothing to do
                return;
            }
        }

        let pre_state = self.state;

        match message {
            ResponseMessage::Hello => self.update_hello(),
            ResponseMessage::Logon => self.update_logon(),
            ResponseMessage::Logoff => self.update_logoff(),
            ResponseMessage::Reset => self.update_reset(),
            ResponseMessage::Run => self.update_run(),
            ResponseMessage::Discard => self.update_discard(),
            ResponseMessage::Pull => self.update_pull(),
            ResponseMessage::Begin => self.update_begin(),
            ResponseMessage::Commit => self.update_commit(),
            ResponseMessage::Rollback => self.update_rollback(),
            ResponseMessage::Route => self.update_route(),
            ResponseMessage::Telemetry => {} // no state transition
        }

        if self.state != pre_state {
            debug!(
                "{}{:?}: {:?} > {:?}",
                bolt_debug_extra!(bolt_meta, bolt_local_port),
                message,
                pre_state,
                self.state
            );
        }
    }

    pub(crate) fn failure(&mut self) {
        self.state = BoltState::Failed;
    }

    fn update_hello(&mut self) {
        match self.state {
            BoltState::Connected => {
                self.state = if self.version >= (5, 1) {
                    BoltState::Authenticating
                } else {
                    BoltState::Ready
                }
            }
            BoltState::Failed => {}
            _ => panic!("unexpected hello for {:?}", self),
        }
    }

    fn update_logon(&mut self) {
        match self.state {
            BoltState::Authenticating => self.state = BoltState::Ready,
            _ => panic!("unexpected logon for {:?}", self),
        }
    }

    fn update_logoff(&mut self) {
        match self.state {
            BoltState::Ready => self.state = BoltState::Authenticating,
            _ => panic!("unexpected logoff for {:?}", self),
        }
    }

    fn update_reset(&mut self) {
        match self.state {
            BoltState::Connected => panic!("unexpected reset for {:?}", self),
            _ => self.state = BoltState::Ready,
        }
    }
    fn update_run(&mut self) {
        match self.state {
            BoltState::Ready => self.state = BoltState::Streaming,
            BoltState::TxReady => self.state = BoltState::TxMaybeStreaming,
            BoltState::TxMaybeStreaming => {} // stay in same state
            _ => panic!("unexpected run for {:?}", self),
        }
    }

    fn update_discard(&mut self) {
        match self.state {
            BoltState::Streaming => self.state = BoltState::Ready,
            BoltState::TxMaybeStreaming => {} // stay in same state
            _ => panic!("unexpected discard for {:?}", self),
        }
    }

    fn update_pull(&mut self) {
        match self.state {
            BoltState::Streaming => self.state = BoltState::Ready,
            BoltState::TxMaybeStreaming => {} // stay in same state
            _ => panic!("unexpected discard for {:?}", self),
        }
    }

    fn update_begin(&mut self) {
        match self.state {
            BoltState::Ready => self.state = BoltState::TxReady,
            _ => panic!("unexpected begin for {:?}", self),
        }
    }

    fn update_commit(&mut self) {
        match self.state {
            BoltState::TxReady | BoltState::TxMaybeStreaming => self.state = BoltState::Ready,
            _ => panic!("unexpected commit for {:?}", self),
        }
    }

    fn update_rollback(&mut self) {
        match self.state {
            BoltState::TxReady | BoltState::TxMaybeStreaming => self.state = BoltState::Ready,
            _ => panic!("unexpected rollback for {:?}", self),
        }
    }

    fn update_route(&mut self) {
        match self.state {
            BoltState::Ready => {} // stay in same state
            _ => panic!("unexpected route for {:?}", self),
        }
    }
}
