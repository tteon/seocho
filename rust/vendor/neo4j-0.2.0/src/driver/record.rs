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

use std::iter;
use std::ops::Deref;
use std::sync::Arc;

use super::io::bolt::BoltRecordFields;
use crate::value::ValueReceive;

/// A record is a collection of key-value pairs that represent a single row of a query result.
#[derive(Debug)]
pub struct Record {
    entries: Vec<(Arc<String>, Option<ValueReceive>)>,
}

impl Record {
    pub(crate) fn new(keys: &[Arc<String>], fields: BoltRecordFields) -> Self {
        assert_eq!(keys.len(), fields.len());
        Self {
            entries: iter::zip(keys.iter().map(Arc::clone), fields.into_iter().map(Some)).collect(),
        }
    }

    /// Iterate over the keys of the record.
    /// The order of the keys corresponds to the order of the values.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("RETURN 1 AS one, 2 AS two, 3 AS three")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    ///
    /// let record = result.into_single().unwrap();
    ///
    /// assert_eq!(
    ///     record.keys().collect::<Vec<_>>(),
    ///     vec![
    ///         Arc::new(String::from("one")),
    ///         Arc::new(String::from("two")),
    ///         Arc::new(String::from("three")),
    ///     ]
    /// );
    /// ```
    pub fn keys(&self) -> impl Iterator<Item = Arc<String>> + '_ {
        self.entries
            .iter()
            .filter_map(|(key, value)| value.as_ref().map(|_| Arc::clone(key)))
    }

    /// Iterate over the values of the record.
    /// The order of the values corresponds to the order of the keys.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("RETURN 1 AS one, 2 AS two, 3 AS three")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    ///
    /// let record = result.into_single().unwrap();
    ///
    /// assert_eq!(
    ///     record.values().collect::<Vec<_>>(),
    ///     vec![
    ///         &ValueReceive::Integer(1),
    ///         &ValueReceive::Integer(2),
    ///         &ValueReceive::Integer(3),
    ///     ]
    /// );
    /// ```
    pub fn values(&self) -> impl Iterator<Item = &ValueReceive> {
        self.entries
            .iter()
            .map(|(_, value)| value)
            .filter_map(Option::as_ref)
    }

    /// Iterate over the values of the record.
    ///
    /// This is the same as [`Record::values()`], but consumes the record and returns owned values.
    pub fn into_values(self) -> impl Iterator<Item = ValueReceive> {
        self.entries.into_iter().filter_map(|(_, value)| value)
    }

    /// Iterate over the key-value pairs of the record.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("RETURN 1 AS one, 2 AS two, 3 AS three")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    ///
    /// let record = result.into_single().unwrap();
    /// assert_eq!(
    ///     record.entries().collect::<Vec<_>>(),
    ///     vec![
    ///         (Arc::new(String::from("one")), &ValueReceive::Integer(1)),
    ///         (Arc::new(String::from("two")), &ValueReceive::Integer(2)),
    ///         (Arc::new(String::from("three")), &ValueReceive::Integer(3)),
    ///     ]
    /// );
    /// ```
    pub fn entries(&self) -> impl Iterator<Item = (Arc<String>, &ValueReceive)> {
        self.entries
            .iter()
            .filter_map(|(key, value)| value.as_ref().map(|value| (Arc::clone(key), value)))
    }

    /// Iterate over the key-value pairs of the record.
    ///
    /// This is the same as [`Record::entries()`], but consumes the record and returns owned values.
    pub fn into_entries(self) -> impl Iterator<Item = (Arc<String>, ValueReceive)> {
        self.entries
            .into_iter()
            .filter_map(|(key, value)| value.map(|value| (key, value)))
    }

    /// Get the value for the given key or [`None`] if the key does not exist.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("RETURN 1 AS one, 2 AS two, 3 AS three")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    ///
    /// let record = result.into_single().unwrap();
    /// assert_eq!(record.value("four"), None);
    /// assert_eq!(record.value("one"), Some(&ValueReceive::Integer(1)));
    /// ```
    pub fn value(&self, key: &str) -> Option<&ValueReceive> {
        self.entries
            .iter()
            .filter_map(|(k, v)| v.as_ref().map(|v| (k, v)))
            .find_map(|(k, v)| if k.deref() == key { Some(v) } else { None })
    }

    /// Get the value for the given key or [`None`] if the key does not exist.
    ///
    /// This is the same as [`Record::value()`],
    /// but it removes the entry and returns an owned value.
    ///
    /// # Example
    /// ```
    /// use std::sync::Arc;
    ///
    /// use neo4j::ValueReceive;
    ///
    /// # let driver = doc_test_utils::get_driver();
    /// let result = driver
    ///     .execute_query("RETURN 1 AS one, 2 AS two, 3 AS three")
    ///     .with_database(Arc::new(String::from("neo4j")))
    ///     .run()
    ///     .unwrap();
    ///
    /// let mut record = result.into_single().unwrap();
    /// assert_eq!(record.take_value("four"), None);
    /// assert_eq!(record.take_value("one"), Some(ValueReceive::Integer(1)));
    /// // value is removed
    /// assert_eq!(record.take_value("one"), None);
    /// assert_eq!(
    ///     record.keys().collect::<Vec<_>>(),
    ///     vec![
    ///         Arc::new(String::from("two")),
    ///         Arc::new(String::from("three")),
    ///     ]
    /// );
    /// ```
    pub fn take_value(&mut self, key: &str) -> Option<ValueReceive> {
        self.entries
            .iter_mut()
            .filter(|(k, _)| k.deref() == key)
            .find_map(|(_, v)| v.take())
    }
}
