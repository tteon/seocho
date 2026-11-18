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

//! Temporal types based on the [`chrono`] crate.

use duplicate::duplicate_item;

pub type Tz = chrono_tz::Tz;
pub type FixedOffset = chrono::FixedOffset;

pub type LocalTime = chrono::NaiveTime;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Time {
    pub time: chrono::NaiveTime,
    pub offset: FixedOffset,
}
pub type Date = chrono::NaiveDate;
pub type LocalDateTime = chrono::NaiveDateTime;
pub type DateTime = chrono::DateTime<Tz>;
pub type DateTimeFixed = chrono::DateTime<FixedOffset>;

const AVERAGE_SECONDS_IN_MONTH: i64 = 2629746;
const AVERAGE_SECONDS_IN_DAY: i64 = 86400;

// TODO: implement Ord, Neg, Add, Sub, from, into, etc.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Duration {
    pub(crate) months: i64,
    pub(crate) days: i64,
    pub(crate) seconds: i64,
    pub(crate) nanoseconds: i32,
}

impl Duration {
    pub fn new(months: i64, days: i64, seconds: i64, nanoseconds: i32) -> Option<Self> {
        let seconds = seconds.checked_add(i64::from(nanoseconds) / 1_000_000_000)?;
        let nanoseconds = nanoseconds % 1_000_000_000;
        let months_seconds = months.checked_mul(AVERAGE_SECONDS_IN_MONTH)?;
        let days_seconds = days.checked_mul(AVERAGE_SECONDS_IN_DAY)?;
        seconds
            .checked_add(months_seconds)?
            .checked_add(days_seconds)?;
        Some(Self {
            months,
            days,
            seconds,
            nanoseconds,
        })
    }

    #[duplicate_item(
        name            type_;
        [ months ]      [ i64 ];
        [ days ]        [ i64 ];
        [ seconds ]     [ i64 ];
        [ nanoseconds ] [ i32 ];
    )]
    pub fn name(&self) -> type_ {
        self.name
    }
}

pub(crate) fn local_date_time_from_timestamp(secs: i64, nsecs: u32) -> Option<LocalDateTime> {
    chrono::DateTime::from_timestamp(secs, nsecs).map(|dt| dt.naive_utc())
}
