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

use std::result::Result as StdResult;
use std::sync::Arc;

use thiserror::Error;

use crate::driver::Record;
use crate::summary::Summary;
use crate::value::ValueReceive;

#[derive(Debug)]
pub struct EagerResult {
    pub keys: Vec<Arc<String>>,
    pub records: Vec<Record>,
    pub summary: Summary,
}

impl EagerResult {
    /// Assuming the result contains exactly one record with exactly one value, return that value.
    ///
    /// Returns an [`ScalarError`] if the result contains not exactly one record or the record
    /// contains not exactly one value.
    pub fn into_scalar(mut self) -> StdResult<ValueReceive, ScalarError> {
        match self.records.len() {
            0 => Err(ScalarError::NoRecord),
            1 => match self.keys.len() {
                0 => Err(ScalarError::NoValue),
                1 => {
                    let record = self.records.pop().unwrap();
                    Ok(record.into_values().next().unwrap())
                }
                _ => Err(ScalarError::MoreThanOneRecord),
            },
            _ => Err(ScalarError::MoreThanOneRecord),
        }
    }

    /// Assuming the result contains exactly one record, return that record.
    ///
    /// Returns an [`ScalarError`] if the result contains not exactly one record.
    pub fn into_single(self) -> StdResult<Record, ScalarError> {
        match self.records.len() {
            0 => Err(ScalarError::NoRecord),
            1 => Ok(self.records.into_iter().next().unwrap()),
            _ => Err(ScalarError::MoreThanOneRecord),
        }
    }

    /// Iterate over the the values of the records.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::driver::EagerResult;
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result: EagerResult = driver
    ///     .execute_query("UNWIND [1, 2, 3] AS x RETURN x, x + 1 AS y")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    /// assert_eq!(
    ///     result
    ///         .into_values()
    ///         .map(|values| values
    ///             .map(|value| value.try_into_int().unwrap())
    ///             .collect::<Vec<_>>())
    ///         .collect::<Vec<_>>(),
    ///     vec![vec![1, 2], vec![2, 3], vec![3, 4]]
    /// );
    /// ```
    pub fn into_values(self) -> impl Iterator<Item = impl Iterator<Item = ValueReceive>> {
        self.records.into_iter().map(Record::into_values)
    }
}

/// Error returned by [`EagerResult::into_scalar()`] and [`EagerResult::into_single()`] if the
/// number of records or values is not exactly one.
#[derive(Debug, Error)]
pub enum ScalarError {
    #[error("expected exactly one record, but found none")]
    NoRecord,
    #[error("expected exactly one record, but found more")]
    MoreThanOneRecord,
    #[error("expected single record to have exactly one value, but found none")]
    NoValue,
    #[error("expected single record to have exactly one value, but found more")]
    MoreThanOneValue,
}
