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
use std::sync::atomic::AtomicUsize;

use super::super::bolt::Bolt;

#[derive(Debug, Default)]
pub(crate) struct SsrTracker {
    with_ssr: AtomicUsize,
    without_ssr: AtomicUsize,
}

impl SsrTracker {
    pub(super) fn new() -> Self {
        Default::default()
    }

    fn with_ssr(&self) -> usize {
        self.with_ssr.load(std::sync::atomic::Ordering::Relaxed)
    }

    fn increment_with_ssr(&self) {
        self.with_ssr
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn decrement_with_ssr(&self) {
        self.with_ssr
            .fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn increment_without_ssr(&self) {
        self.without_ssr
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn decrement_without_ssr(&self) {
        self.without_ssr
            .fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn without_ssr(&self) -> usize {
        self.without_ssr.load(std::sync::atomic::Ordering::Relaxed)
    }

    pub(super) fn add_connection(&self, connection: &impl SsrTrackable) {
        match connection.ssr_enabled() {
            true => self.increment_with_ssr(),
            false => self.increment_without_ssr(),
        }
    }

    pub(super) fn remove_connection(&self, connection: &impl SsrTrackable) {
        match connection.ssr_enabled() {
            true => self.decrement_with_ssr(),
            false => self.decrement_without_ssr(),
        }
    }

    pub(super) fn ssr_enabled(&self) -> bool {
        self.with_ssr() > 0 && self.without_ssr() == 0
    }
}

pub(super) trait SsrTrackable {
    fn ssr_enabled(&self) -> bool;
}

impl<RW: Read + Write> SsrTrackable for Bolt<RW> {
    fn ssr_enabled(&self) -> bool {
        self.ssr_enabled()
    }
}
