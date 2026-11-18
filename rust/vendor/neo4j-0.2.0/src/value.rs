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

pub mod graph;
pub mod spatial;
pub mod time;
mod value_receive;
mod value_send;
// mod de;
// mod ser;

use thiserror::Error;

pub(crate) use value_receive::BrokenValueInner;
pub use value_receive::{BrokenValue, ValueReceive};
pub use value_send::ValueSend;

/// Error returned when trying to convert a [`ValueReceive`] into a [`ValueSend`] that's not
/// convertable.
///
/// See
/// [`<ValueSend as TryFrom<ValueReceive>>`](`ValueSend#impl-TryFrom<ValueReceive>-for-ValueSend`)
/// for more information.
#[derive(Debug, Error)]
#[error("{reason}")]
pub struct ValueConversionError {
    reason: &'static str,
}

impl From<&'static str> for ValueConversionError {
    fn from(reason: &'static str) -> Self {
        Self { reason }
    }
}
