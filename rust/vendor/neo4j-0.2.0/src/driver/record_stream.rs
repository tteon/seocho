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
use std::cell::RefCell;
use std::collections::VecDeque;
use std::fmt::Debug;
use std::iter::FusedIterator;
use std::mem;
use std::ops::Deref;
use std::rc::Rc;
use std::result;
use std::sync::{Arc, Weak};

use atomic_refcell::AtomicRefCell;
use duplicate::duplicate_item;
use thiserror::Error;

use super::io::bolt::message_parameters::{DiscardParameters, PullParameters, RunParameters};
use super::io::bolt::{BoltMeta, BoltRecordFields, ResponseCallbacks};
use super::summary::Summary;
use super::Record;
use crate::driver::eager_result::EagerResult;
use crate::driver::io::PooledBolt;
use crate::error_::{Neo4jError, Result, ServerError};
use crate::value::ValueReceive;

#[derive(Debug)]
pub struct RecordStream<'driver> {
    connection: Rc<RefCell<PooledBolt<'driver>>>,
    fetch_size: i64,
    auto_commit: bool,
    listener: Arc<AtomicRefCell<RecordListener>>,
}

type BoltMetaCb = Box<dyn FnMut(&mut BoltMeta) + Send + Sync + 'static>;

impl<'driver> RecordStream<'driver> {
    pub(crate) fn new(
        connection: Rc<RefCell<PooledBolt<'driver>>>,
        fetch_size: i64,
        auto_commit: bool,
        error_propagator: Option<SharedErrorPropagator>,
    ) -> Self {
        let listener = Arc::new(AtomicRefCell::new(RecordListener::new(
            &(*connection).borrow(),
            error_propagator.clone(),
        )));
        if let Some(error_propagator) = error_propagator {
            error_propagator
                .borrow_mut()
                .add_listener(Arc::downgrade(&listener));
        }
        Self {
            connection,
            fetch_size,
            auto_commit,
            listener,
        }
    }

    pub(crate) fn run<KP: Borrow<str> + Debug, KM: Borrow<str> + Debug>(
        &mut self,
        parameters: RunParameters<KP, KM>,
        mut db_resolution_cb: Option<BoltMetaCb>,
    ) -> Result<()> {
        if let RecordListenerState::ForeignError(e) = &(*self.listener).borrow().state {
            return Err(Neo4jError::ServerError {
                error: e.deref().clone(),
            });
        }

        let mut callbacks = self.failure_callbacks();
        let listener = Arc::downgrade(&self.listener);
        callbacks = callbacks.with_on_success(move |mut meta| {
            if let Some(db_resolution_cb) = db_resolution_cb.as_mut() {
                db_resolution_cb(&mut meta)
            }
            if let Some(listener) = listener.upgrade() {
                return listener.borrow_mut().run_success_cb(meta);
            }
            Ok(())
        });

        let mut res = self.connection.borrow_mut().run(parameters, callbacks);
        if self.auto_commit {
            res = res.and_then(|_| self.connection.borrow_mut().write_all(None));
            res = match res.and_then(|_| self.pull(true)) {
                Err(e) => {
                    let mut listener = self.listener.borrow_mut();
                    listener.state = RecordListenerState::Done;
                    return Err(e);
                }
                Ok(res) => Ok(res),
            }
        } else {
            res = self.pull(true);
        }

        if let Err(e) = res.and_then(|_| {
            // read until only response(s) to PULL is/are left
            let mut connection = self.connection.borrow_mut();
            let mut res = Ok(());
            while res.is_ok() && connection.expected_reply_len() > 1 {
                res = connection.read_one(None);
            }
            res
        }) {
            let mut listener = self.listener.borrow_mut();
            listener.state = RecordListenerState::Done;
            return Err(self.failed_commit(e));
        };

        {
            let state = &mut self.listener.borrow_mut().state;
            match state {
                RecordListenerState::Error(_) => {
                    let mut state_swap = RecordListenerState::Done;
                    mem::swap(state, &mut state_swap);
                    match state_swap {
                        RecordListenerState::Error(e) => return Err(self.failed_commit(e)),
                        _ => panic!("checked state to be error above"),
                    }
                }
                RecordListenerState::ForeignError(_) => {
                    let mut state_swap = RecordListenerState::Done;
                    mem::swap(state, &mut state_swap);
                    match state_swap {
                        RecordListenerState::ForeignError(e) => {
                            return Err(Neo4jError::ServerError {
                                error: e.deref().clone(),
                            })
                        }
                        _ => panic!("checked state to be error above"),
                    }
                }
                RecordListenerState::Ignored => {
                    let mut state_swap = RecordListenerState::Done;
                    mem::swap(state, &mut state_swap);
                    return Err(Neo4jError::protocol_error("record stream was ignored"));
                }
                _ => {}
            }
        }
        let mut connection_borrow = self.connection.borrow_mut();
        if let Err(err) = connection_borrow.read_all(None) {
            self.listener.borrow_mut().state = RecordListenerState::Error(self.failed_commit(err));
        } else {
            assert!(!connection_borrow.has_buffered_message());
            assert!(!connection_borrow.expects_reply());
        }

        Ok(())
    }

    /// Fully consumes the result and returns the [`Summary`].
    ///
    /// Returns [`None`] if
    ///  * [`RecordStream::consume()`] has been called before or
    ///  * there was an error (earlier) other than [`Neo4jError`]
    ///    while processing the [`RecordStream`].
    pub fn consume(&mut self) -> Result<Option<Summary>> {
        self.exhaust()?;

        Ok(self.listener.borrow_mut().summary.take())
    }

    pub fn keys(&self) -> Vec<Arc<String>> {
        (*self.listener)
            .borrow()
            .keys
            .as_ref()
            .expect(
                "keys were not present but should be after RUN's SUCCESS. \
                Even if they are missing, the SUCCESS handler should've caused a protocol \
                violation error before the user is handed out the stream object",
            )
            .iter()
            .map(Arc::clone)
            .collect()
    }

    /// Exhausts the stream and returns a single record.
    /// If any error occurs while consuming the stream, the error is returned as `Ok(Err(error))`.
    /// If consumption is successful return `Ok(Ok(record))` if exactly one record was consumed.
    /// If more or less records were found, `Err(GetSingleRecordError)` is returned.
    ///
    /// TODO: examples
    ///  * successful single
    ///  * failing, wrong number
    ///  * failing, wrong number and stream error (show precedence)
    pub fn single(&mut self) -> result::Result<Result<Record>, GetSingleRecordError> {
        let next = self.next();
        match next {
            Some(Ok(record)) => match self.next() {
                None => Ok(Ok(record)),
                Some(Err(e)) => Ok(Err(e)),
                Some(Ok(_)) => match self.exhaust() {
                    Ok(()) => Err(GetSingleRecordError::TooManyRecords),
                    Err(e) => Ok(Err(e)),
                },
            },
            Some(Err(e)) => Ok(Err(e)),
            None => Err(GetSingleRecordError::NoRecords),
        }
    }

    /// Collects the result into an [`EagerResult`].
    ///
    /// Returns [`None`] if the stream has already been consumed (i.e., [`RecordStream::consume()`]
    /// has been called before).
    ///
    /// ```
    /// use neo4j::driver::record_stream::RecordStream;
    /// # use neo4j::retry::ExponentialBackoff;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// # for example in [already_consumed, not_yet_consumed] {
    /// #     let _ = driver
    /// #         .execute_query("RETURN 1 AS n")
    /// #         .with_receiver(|stream| {
    /// #             example(stream);
    /// #             Ok(())
    /// #         })
    /// #         .run_with_retry(ExponentialBackoff::new());
    /// # }
    /// #
    /// fn already_consumed(stream: &mut RecordStream) {
    ///     stream.consume().unwrap();
    ///     assert!(stream.try_as_eager_result().unwrap().is_none());
    /// }
    ///
    /// fn not_yet_consumed(stream: &mut RecordStream) {
    ///     assert!(stream.try_as_eager_result().unwrap().is_some());
    /// }
    /// ```
    pub fn try_as_eager_result(&mut self) -> Result<Option<EagerResult>> {
        let keys = self.keys();
        let records = self.collect::<Result<_>>()?;
        let summary = self.consume()?;
        let Some(summary) = summary else {
            return Ok(None);
        };
        Ok(Some(EagerResult {
            keys,
            records,
            summary,
        }))
    }

    pub(crate) fn into_bookmark(self) -> Option<String> {
        Arc::try_unwrap(self.listener)
            .unwrap()
            .into_inner()
            .bookmark
    }

    fn exhaust(&mut self) -> Result<()> {
        if (*self.listener).borrow().state.is_streaming() {
            let mut listener = self.listener.borrow_mut();
            listener.buffer.clear();
            listener.state = RecordListenerState::Discarding;
        }

        let res = self.try_for_each(|e| e.map(drop));
        self.wrap_commit(res)?;

        Ok(())
    }

    fn pull(&mut self, flush: bool) -> Result<()> {
        let callbacks = self.pull_callbacks();
        self.connection
            .borrow_mut()
            .pull(PullParameters::new(self.fetch_size, self.qid()), callbacks)?;
        if flush {
            self.connection.borrow_mut().write_all(None)?;
        }
        Ok(())
    }

    fn discard(&mut self, flush: bool) -> Result<()> {
        let callbacks = self.discard_callbacks();
        self.connection
            .borrow_mut()
            .discard(DiscardParameters::new(-1, self.qid()), callbacks)?;
        if flush {
            self.connection.borrow_mut().write_all(None)?;
        }
        Ok(())
    }

    fn pull_callbacks(&self) -> ResponseCallbacks {
        let callbacks = self.discard_callbacks();
        let listener = Arc::downgrade(&self.listener);
        callbacks.with_on_record(move |data| {
            if let Some(listener) = listener.upgrade() {
                return listener.borrow_mut().record_cb(data);
            }
            Ok(())
        })
    }

    fn discard_callbacks(&self) -> ResponseCallbacks {
        let callbacks = self.failure_callbacks();
        let listener = Arc::downgrade(&self.listener);
        callbacks.with_on_success(move |meta| {
            if let Some(listener) = listener.upgrade() {
                return listener.borrow_mut().pull_success_cb(meta);
            }
            Ok(())
        })
    }

    fn failure_callbacks(&self) -> ResponseCallbacks {
        let mut callbacks = ResponseCallbacks::new();
        let listener = Arc::downgrade(&self.listener);
        callbacks = callbacks.with_on_failure(move |error| {
            if let Some(listener) = listener.upgrade() {
                return listener
                    .borrow_mut()
                    .failure_cb(Arc::downgrade(&listener), error);
            }
            Ok(())
        });
        let listener = Arc::downgrade(&self.listener);
        callbacks.with_on_ignored(move || {
            if let Some(listener) = listener.upgrade() {
                return listener.borrow_mut().ignored_cb();
            }
            Ok(())
        })
    }

    fn qid(&self) -> i64 {
        (*self.listener).borrow().qid.unwrap_or(-1)
    }

    fn failed_commit(&self, err: Neo4jError) -> Neo4jError {
        match self.auto_commit {
            true => Neo4jError::failed_commit(err),
            false => err,
        }
    }

    fn wrap_commit<T>(&self, res: Result<T>) -> Result<T> {
        match self.auto_commit {
            true => Neo4jError::wrap_commit(res),
            false => res,
        }
    }
}

impl Iterator for RecordStream<'_> {
    type Item = Result<Record>;

    fn next(&mut self) -> Option<Self::Item> {
        fn need_to_pull(listener: &Arc<AtomicRefCell<RecordListener>>) -> bool {
            let listener = (**listener).borrow();
            listener.buffer.is_empty() && listener.state.is_streaming()
        }

        fn need_to_discard(listener: &Arc<AtomicRefCell<RecordListener>>) -> bool {
            let listener = (**listener).borrow();
            listener.buffer.is_empty() && listener.state.is_discarding()
        }

        if AtomicRefCell::borrow(&*self.listener).state.is_done() {
            return None;
        }

        loop {
            if matches!(
                AtomicRefCell::borrow(&*self.listener).state,
                RecordListenerState::Streaming | RecordListenerState::Discarding
            ) && RefCell::borrow(&self.connection).expects_reply()
            {
                if let Err(err) = self.connection.borrow_mut().read_one(None) {
                    self.listener
                        .borrow_mut()
                        .set_error(self.failed_commit(err));
                }
            }
            if let Some(record) = self.listener.borrow_mut().buffer.pop_front() {
                return Some(Ok(record));
            }
            if need_to_pull(&self.listener) {
                if let Err(err) = self.pull(true) {
                    self.listener
                        .borrow_mut()
                        .set_error(self.failed_commit(err));
                } else {
                    continue;
                }
            } else if need_to_discard(&self.listener) {
                if let Err(err) = self.discard(true) {
                    self.listener
                        .borrow_mut()
                        .set_error(self.failed_commit(err));
                } else {
                    continue;
                }
            }
            let mut listener = self.listener.borrow_mut();
            match listener.state {
                RecordListenerState::Error(_) => {
                    let mut state = RecordListenerState::Done;
                    mem::swap(&mut listener.state, &mut state);
                    match state {
                        RecordListenerState::Error(e) => return Some(Err(e)),
                        _ => panic!("checked state to be error above"),
                    }
                }
                RecordListenerState::ForeignError(_) => {
                    let mut state = RecordListenerState::Done;
                    mem::swap(&mut listener.state, &mut state);
                    match state {
                        RecordListenerState::ForeignError(e) => {
                            return Some(Err(Neo4jError::ServerError {
                                error: e.deref().clone(),
                            }))
                        }
                        _ => panic!("checked state to be foreign error above"),
                    }
                }
                RecordListenerState::Ignored => {
                    let mut state = RecordListenerState::Done;
                    mem::swap(&mut listener.state, &mut state);
                    return Some(Err(Neo4jError::protocol_error("record stream was ignored")));
                }
                RecordListenerState::Success => {
                    let mut state = RecordListenerState::Done;
                    mem::swap(&mut listener.state, &mut state);
                    return None;
                }
                RecordListenerState::Done => return None,
                _ => {}
            }
        }
    }
}

impl FusedIterator for RecordStream<'_> {}

#[derive(Debug)]
enum RecordListenerState {
    Streaming,
    Discarding,
    Error(Neo4jError),
    /// another result stream of the same transaction has failed
    ForeignError(Arc<ServerError>),
    Ignored,
    Success,
    Done,
}

impl RecordListenerState {
    #[allow(dead_code)] // cover all states
    #[duplicate_item(
        fn_name               variant;
        [ is_streaming ]      [ Streaming ];
        [ is_discarding ]     [ Discarding ];
        [ is_error ]          [ Error(_) ];
        [ is_foreign_error ]  [ ForeignError(_) ];
        [ is_ignored ]        [ Ignored ];
        [ is_success ]        [ Success ];
        [ is_done ]           [ Done ];
    )]
    pub fn fn_name(&self) -> bool {
        matches!(self, RecordListenerState::variant)
    }
}

#[derive(Debug)]
struct RecordListener {
    buffer: VecDeque<Record>,
    keys: Option<Vec<Arc<String>>>,
    qid: Option<i64>,
    state: RecordListenerState,
    summary: Option<Summary>,
    bookmark: Option<String>,
    error_propagator: Option<SharedErrorPropagator>,
    had_record: bool,
}

impl RecordListener {
    fn new(connection: &PooledBolt, error_propagator: Option<SharedErrorPropagator>) -> Self {
        let summary = Summary::new(connection);
        Self {
            buffer: VecDeque::new(),
            keys: None,
            qid: None,
            state: RecordListenerState::Streaming,
            summary: Some(summary),
            bookmark: None,
            error_propagator,
            had_record: false,
        }
    }

    fn run_success_cb(&mut self, mut meta: BoltMeta) -> Result<()> {
        if self.keys.is_some() {
            return Ok(());
        }
        if let Some(qid) = meta.remove("qid") {
            let ValueReceive::Integer(qid) = qid else {
                return Err(Neo4jError::protocol_error(
                    "SUCCESS after RUN 'qid' was not an integer",
                ));
            };
            self.qid = Some(qid);
        }
        let Some(fields) = meta.remove("fields") else {
            return Err(Neo4jError::protocol_error(
                "SUCCESS after RUN did not contain 'fields'",
            ));
        };
        let ValueReceive::List(fields) = fields else {
            return Err(Neo4jError::protocol_error(
                "SUCCESS after RUN 'fields' was not a list",
            ));
        };
        let fields = fields
            .into_iter()
            .map(|field| match field {
                ValueReceive::String(field) => Ok(Arc::new(field)),
                _ => Err(Neo4jError::protocol_error(
                    "SUCCESS after RUN 'fields' was not a list of strings",
                )),
            })
            .collect::<Result<Vec<_>>>()?;
        let has_keys = !fields.is_empty();
        self.keys = Some(fields);
        if let Some(summary) = self.summary.as_mut() {
            summary.load_run_meta(&mut meta, has_keys)?
        }

        Ok(())
    }

    fn failure_cb(&mut self, me: Weak<AtomicRefCell<Self>>, error: ServerError) -> Result<()> {
        if let Some(error_propagator) = &self.error_propagator {
            error_propagator.borrow_mut().propagate_error(
                Some(me),
                &error,
                "failure in a query of this transaction caused transaction to be closed",
            );
        }
        self.state = RecordListenerState::Error(Neo4jError::ServerError { error });
        self.summary = None;
        Ok(())
    }

    fn ignored_cb(&mut self) -> Result<()> {
        if !self.state.is_foreign_error() {
            self.state = RecordListenerState::Ignored;
        }
        self.summary = None;
        Ok(())
    }

    fn record_cb(&mut self, fields: BoltRecordFields) -> Result<()> {
        let keys = self
            .keys
            .as_ref()
            .ok_or_else(|| Neo4jError::protocol_error("RECORD received before RUN SUCCESS"))?;
        if keys.len() != fields.len() {
            return Err(Neo4jError::protocol_error(format!(
                "RECORD contained {} entries but {} keys were announced",
                fields.len(),
                keys.len()
            )));
        }
        self.buffer.push_back(Record::new(keys, fields));
        self.had_record = true;
        Ok(())
    }

    fn pull_success_cb(&mut self, mut meta: BoltMeta) -> Result<()> {
        let Some(ValueReceive::Boolean(true)) = meta.remove("has_more") else {
            self.state = RecordListenerState::Success;
            if let Some(ValueReceive::String(bms)) = meta.remove("bookmark") {
                self.bookmark = Some(bms);
            };
            if let Some(summary) = self.summary.as_mut() {
                summary.load_pull_meta(&mut meta, self.had_record)?
            }
            return Ok(());
        };
        Ok(())
    }

    fn set_error(&mut self, error: Neo4jError) {
        self.state = RecordListenerState::Error(error);
        self.summary = None
    }

    fn set_foreign_error(&mut self, error: Arc<ServerError>) {
        self.state = RecordListenerState::ForeignError(error);
        self.summary = None
    }
}

#[derive(Debug, Default)]
pub(crate) struct ErrorPropagator {
    listeners: Vec<Weak<AtomicRefCell<RecordListener>>>,
    error: Option<Arc<ServerError>>,
}

pub(crate) type SharedErrorPropagator = Arc<AtomicRefCell<ErrorPropagator>>;

impl ErrorPropagator {
    fn add_listener(&mut self, listener: Weak<AtomicRefCell<RecordListener>>) {
        if let Some(error) = &self.error {
            if let Some(listener) = listener.upgrade() {
                listener.borrow_mut().set_foreign_error(Arc::clone(error));
            } else {
                // no need to add a dead listener anyway
                return;
            }
        }
        self.listeners.push(listener);
    }

    fn propagate_error(
        &mut self,
        source: Option<Weak<AtomicRefCell<RecordListener>>>,
        error: &ServerError,
        reason: &str,
    ) {
        let error = Arc::new(error.clone_with_reason(reason));
        for listener in self.listeners.iter() {
            if let Some(source) = source.as_ref() {
                if source.ptr_eq(listener) {
                    continue;
                }
            }
            if let Some(listener) = listener.upgrade() {
                listener.borrow_mut().set_foreign_error(Arc::clone(&error));
            }
        }
        self.error = Some(error);
    }

    pub(crate) fn error(&self) -> &Option<Arc<ServerError>> {
        &self.error
    }

    pub(crate) fn make_on_error_cb(
        this: SharedErrorPropagator,
    ) -> impl FnMut(ServerError) -> Result<()> + Send + Sync + 'static {
        move |err| {
            this.borrow_mut()
                .propagate_error(None, &err, "the transaction could not be started");
            Ok(())
        }
    }
}

#[derive(Debug, Error)]
pub enum GetSingleRecordError {
    #[error("no records were found")]
    NoRecords,
    #[error("more than one record was found")]
    TooManyRecords,
}

impl From<GetSingleRecordError> for Neo4jError {
    fn from(err: GetSingleRecordError) -> Self {
        Self::InvalidConfig {
            message: format!("GetSingleRecordError: {}", err),
        }
    }
}
