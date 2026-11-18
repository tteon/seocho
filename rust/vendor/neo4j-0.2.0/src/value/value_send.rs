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

use super::spatial;
use super::time;
use super::value_receive::ValueReceive;
use super::ValueConversionError;
use itertools::Itertools;
use std::collections::HashMap;

// imports for docs
#[allow(unused)]
use crate::error_::Neo4jError;

/// A value that can be sent to the server. For example as query parameters or transaction metadata.
///
/// **NOTE**:  
/// See [`Neo4jError::InvalidConfig`] for information on what constraints apply to the values that
/// can be sent to the server.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum ValueSend {
    Null,
    Boolean(bool),
    Integer(i64),
    Float(f64),
    Bytes(Vec<u8>),
    String(String),
    List(Vec<ValueSend>),
    Map(HashMap<String, ValueSend>),
    Cartesian2D(spatial::Cartesian2D),
    Cartesian3D(spatial::Cartesian3D),
    WGS84_2D(spatial::WGS84_2D),
    WGS84_3D(spatial::WGS84_3D),
    Duration(time::Duration),
    LocalTime(time::LocalTime),
    Time(time::Time),
    Date(time::Date),
    LocalDateTime(time::LocalDateTime),
    DateTime(time::DateTime),
    DateTimeFixed(time::DateTimeFixed),
}

impl ValueSend {
    pub(crate) fn eq_data(&self, other: &Self) -> bool {
        match self {
            ValueSend::Null => matches!(other, ValueSend::Null),
            ValueSend::Boolean(v1) => matches!(other, ValueSend::Boolean(v2) if v1 == v2),
            ValueSend::Integer(v1) => matches!(other, ValueSend::Integer(v2) if v1 == v2),
            ValueSend::Float(v1) => match other {
                ValueSend::Float(v2) => v1.to_bits() == v2.to_bits(),
                _ => false,
            },
            ValueSend::Bytes(v1) => matches!(other, ValueSend::Bytes(v2) if v1 == v2),
            ValueSend::String(v1) => matches!(other, ValueSend::String(v2) if v1 == v2),
            ValueSend::List(v1) => match other {
                ValueSend::List(v2) if v1.len() == v2.len() => {
                    v1.iter().zip(v2.iter()).all(|(v1, v2)| v1.eq_data(v2))
                }
                _ => false,
            },
            ValueSend::Map(v1) => match other {
                ValueSend::Map(v2) if v1.len() == v2.len() => v1
                    .iter()
                    .sorted_by(|(k1, _), (k2, _)| k1.cmp(k2))
                    .zip(v2.iter().sorted_by(|(k1, _), (k2, _)| k1.cmp(k2)))
                    .all(|((k1, v1), (k2, v2))| k1 == k2 && v1.eq_data(v2)),
                _ => false,
            },
            ValueSend::Cartesian2D(v1) => {
                matches!(other, ValueSend::Cartesian2D(v2) if v1.eq_data(v2))
            }
            ValueSend::Cartesian3D(v1) => {
                matches!(other, ValueSend::Cartesian3D(v2) if v1.eq_data(v2))
            }
            ValueSend::WGS84_2D(v1) => matches!(other, ValueSend::WGS84_2D(v2) if v1.eq_data(v2)),
            ValueSend::WGS84_3D(v1) => matches!(other, ValueSend::WGS84_3D(v2) if v1.eq_data(v2)),
            ValueSend::Duration(v1) => matches!(other, ValueSend::Duration(v2) if v1 == v2),
            ValueSend::LocalTime(v1) => matches!(other, ValueSend::LocalTime(v2) if v1 == v2),
            ValueSend::Time(v1) => matches!(other, ValueSend::Time(v2) if v1 == v2),
            ValueSend::Date(v1) => matches!(other, ValueSend::Date(v2) if v1 == v2),
            ValueSend::LocalDateTime(v1) => {
                matches!(other, ValueSend::LocalDateTime(v2) if v1 == v2)
            }
            ValueSend::DateTime(v1) => matches!(other, ValueSend::DateTime(v2) if v1 == v2),
            ValueSend::DateTimeFixed(v1) => {
                matches!(other, ValueSend::DateTimeFixed(v2) if v1 == v2)
            }
        }
    }
}

macro_rules! impl_value_from_into {
    ( $value:expr, $($ty:ty),* ) => {
        $(
            impl From<$ty> for ValueSend {
                fn from(value: $ty) -> Self {
                    $value(value.into())
                }
            }
        )*
    };
}

macro_rules! impl_value_from_owned {
    ( $value:expr, $($ty:ty),* ) => {
        $(
            impl From<$ty> for ValueSend {
                fn from(value: $ty) -> Self {
                    $value(value)
                }
            }
        )*
    };
}

impl_value_from_into!(ValueSend::Boolean, bool);
impl_value_from_into!(ValueSend::Integer, u8, u16, u32, i8, i16, i32, i64);
impl_value_from_into!(ValueSend::Float, f32, f64);
impl_value_from_into!(ValueSend::String, &str);

impl_value_from_owned!(ValueSend::String, String);
// impl_value_from_owned!(Value::List, Vec<Value>);
// impl_value_from_owned!(Value::Map, HashMap<String, Value>);
impl_value_from_owned!(ValueSend::Cartesian2D, spatial::Cartesian2D);
impl_value_from_owned!(ValueSend::Cartesian3D, spatial::Cartesian3D);
impl_value_from_owned!(ValueSend::WGS84_2D, spatial::WGS84_2D);
impl_value_from_owned!(ValueSend::WGS84_3D, spatial::WGS84_3D);
impl_value_from_owned!(ValueSend::Duration, time::Duration);
impl_value_from_owned!(ValueSend::LocalTime, time::LocalTime);
impl_value_from_owned!(ValueSend::Time, time::Time);
impl_value_from_owned!(ValueSend::Date, time::Date);
impl_value_from_owned!(ValueSend::LocalDateTime, time::LocalDateTime);
impl_value_from_owned!(ValueSend::DateTime, time::DateTime);
impl_value_from_owned!(ValueSend::DateTimeFixed, time::DateTimeFixed);

impl<T: Into<ValueSend>> From<HashMap<String, T>> for ValueSend {
    fn from(value: HashMap<String, T>) -> Self {
        ValueSend::Map(value.into_iter().map(|(k, v)| (k, v.into())).collect())
    }
}

impl<T: Into<ValueSend>> From<Vec<T>> for ValueSend {
    fn from(value: Vec<T>) -> Self {
        ValueSend::List(value.into_iter().map(|v| v.into()).collect())
    }
}

impl<T: Into<ValueSend>> From<Option<T>> for ValueSend {
    fn from(value: Option<T>) -> Self {
        match value {
            None => ValueSend::Null,
            Some(v) => v.into(),
        }
    }
}

impl TryFrom<ValueReceive> for ValueSend {
    type Error = ValueConversionError;

    /// Attempt to convert a [`ValueReceive`] into a [`ValueSend`].
    ///
    /// This will fail if the [`ValueReceive`] is (or contains) one of the following:
    ///  * [`ValueReceive::BrokenValue`]
    ///  * [`ValueReceive::Node`]
    ///  * [`ValueReceive::Relationship`]
    ///  * [`ValueReceive::Path`]
    ///
    /// These types are not convertable into [`ValueSend`]s because the server does not support
    /// receiving them.
    ///
    /// # Example
    /// ```
    /// use std::convert::TryInto;
    /// # use std::sync::Arc;
    ///
    /// # use neo4j::retry::ExponentialBackoff;
    /// # use neo4j::driver::EagerResult;
    /// use neo4j::{ValueReceive, ValueSend};
    ///
    /// let value = ValueSend::try_from(ValueReceive::Null).unwrap();
    /// assert_eq!(value, ValueSend::Null);
    ///
    /// let value = ValueReceive::List(vec![ValueReceive::Integer(1), ValueReceive::Boolean(false)]);
    /// let value = ValueSend::try_from(value).unwrap();
    /// assert_eq!(
    ///     value,
    ///     ValueSend::List(vec![ValueSend::Integer(1), ValueSend::Boolean(false)])
    /// );
    ///
    /// # fn get_broken_value() -> ValueReceive {
    /// #     doc_test_utils::get_driver()
    /// #         .execute_query("RETURN localdatetime('-999999999-01-01')")
    /// #         .with_database(Arc::new(String::from("neo4j")))
    /// #         .run_with_retry(ExponentialBackoff::new())
    /// #         .unwrap()
    /// #         .into_scalar()
    /// #         .unwrap()
    /// # }
    /// #
    /// let broken_value: ValueReceive = get_broken_value();
    /// let value = ValueSend::try_from(broken_value);
    /// assert!(value.is_err());
    ///
    /// # fn get_node_value() -> ValueReceive {
    /// #     use std::sync::Arc;
    /// #     let driver = doc_test_utils::get_driver();
    /// #     let mut session = doc_test_utils::get_session(&driver);
    /// #     let result = session
    /// #         .transaction()
    /// #         .run_with_retry(ExponentialBackoff::new(), |tx| {
    /// #             tx.query("CREATE (n:Node {name: 'test'}) RETURN n")
    /// #                 .run()?
    /// #                 .try_as_eager_result()
    /// #         });
    /// #     result.unwrap().unwrap().into_scalar().unwrap()
    /// # }
    /// #
    /// let node_value: ValueReceive = get_node_value();
    /// let value = ValueSend::try_from(node_value);
    /// assert!(value.is_err());
    /// ```
    fn try_from(v: ValueReceive) -> Result<Self, Self::Error> {
        Ok(match v {
            ValueReceive::Null => Self::Null,
            ValueReceive::Boolean(v) => Self::Boolean(v),
            ValueReceive::Integer(v) => Self::Integer(v),
            ValueReceive::Float(v) => Self::Float(v),
            ValueReceive::Bytes(v) => Self::Bytes(v),
            ValueReceive::String(v) => Self::String(v),
            ValueReceive::List(v) => Self::List(
                v.into_iter()
                    .map(TryInto::try_into)
                    .collect::<Result<_, _>>()?,
            ),
            ValueReceive::Map(v) => Self::Map(
                v.into_iter()
                    .map(|(k, e)| Ok::<_, Self::Error>((k, e.try_into()?)))
                    .collect::<Result<_, _>>()?,
            ),
            ValueReceive::Cartesian2D(v) => Self::Cartesian2D(v),
            ValueReceive::Cartesian3D(v) => Self::Cartesian3D(v),
            ValueReceive::WGS84_2D(v) => Self::WGS84_2D(v),
            ValueReceive::WGS84_3D(v) => Self::WGS84_3D(v),
            ValueReceive::Duration(v) => Self::Duration(v),
            ValueReceive::LocalTime(v) => Self::LocalTime(v),
            ValueReceive::Time(v) => Self::Time(v),
            ValueReceive::Date(v) => Self::Date(v),
            ValueReceive::LocalDateTime(v) => Self::LocalDateTime(v),
            ValueReceive::DateTime(v) => Self::DateTime(v),
            ValueReceive::DateTimeFixed(v) => Self::DateTimeFixed(v),
            ValueReceive::BrokenValue { .. } => return Err("cannot convert BrokenValue".into()),
            ValueReceive::Node(_) => return Err("cannot convert Node".into()),
            ValueReceive::Relationship(_) => return Err("cannot convert Relationship".into()),
            ValueReceive::Path(_) => return Err("cannot convert Path".into()),
        })
    }
}

impl ValueSend {
    #[inline]
    pub fn is_null(&self) -> bool {
        matches!(self, ValueSend::Null)
    }
}

impl TryFrom<ValueSend> for bool {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Boolean(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_bool(&self) -> bool {
        matches!(self, ValueSend::Boolean(_))
    }

    #[inline]
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            ValueSend::Boolean(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_bool(self) -> Result<bool, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for i64 {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Integer(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_int(&self) -> bool {
        matches!(self, ValueSend::Integer(_))
    }

    #[inline]
    pub fn as_int(&self) -> Option<i64> {
        match self {
            ValueSend::Integer(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_int(self) -> Result<i64, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for f64 {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Float(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_float(&self) -> bool {
        matches!(self, ValueSend::Float(_))
    }

    #[inline]
    pub fn as_float(&self) -> Option<f64> {
        match self {
            ValueSend::Float(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_float(self) -> Result<f64, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for Vec<u8> {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Bytes(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_bytes(&self) -> bool {
        matches!(self, ValueSend::Bytes(_))
    }

    #[inline]
    pub fn as_bytes(&self) -> Option<&Vec<u8>> {
        match self {
            ValueSend::Bytes(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_bytes(self) -> Result<Vec<u8>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for String {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::String(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_string(&self) -> bool {
        matches!(self, ValueSend::String(_))
    }

    #[inline]
    pub fn as_string(&self) -> Option<&String> {
        match self {
            ValueSend::String(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_string(self) -> Result<String, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for Vec<ValueSend> {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::List(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_list(&self) -> bool {
        matches!(self, ValueSend::List(_))
    }

    #[inline]
    pub fn as_list(&self) -> Option<&[ValueSend]> {
        match self {
            ValueSend::List(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_list(self) -> Result<Vec<ValueSend>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for HashMap<String, ValueSend> {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Map(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_map(&self) -> bool {
        matches!(self, ValueSend::Map(_))
    }

    #[inline]
    pub fn as_map(&self) -> Option<&HashMap<String, ValueSend>> {
        match self {
            ValueSend::Map(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_map(self) -> Result<HashMap<String, ValueSend>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for spatial::Cartesian2D {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Cartesian2D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_cartesian_2d(&self) -> bool {
        matches!(self, ValueSend::Cartesian2D(_))
    }

    #[inline]
    pub fn as_cartesian_2d(&self) -> Option<&spatial::Cartesian2D> {
        match self {
            ValueSend::Cartesian2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_cartesian_2d(self) -> Result<spatial::Cartesian2D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for spatial::Cartesian3D {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Cartesian3D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_cartesian_3d(&self) -> bool {
        matches!(self, ValueSend::Cartesian3D(_))
    }

    #[inline]
    pub fn as_cartesian_3d(&self) -> Option<&spatial::Cartesian3D> {
        match self {
            ValueSend::Cartesian3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_cartesian_3d(self) -> Result<spatial::Cartesian3D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for spatial::WGS84_2D {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::WGS84_2D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_wgs84_2d(&self) -> bool {
        matches!(self, ValueSend::WGS84_2D(_))
    }

    #[inline]
    pub fn as_wgs84_2d(&self) -> Option<&spatial::WGS84_2D> {
        match self {
            ValueSend::WGS84_2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_wgs84_2d(self) -> Result<spatial::WGS84_2D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for spatial::WGS84_3D {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::WGS84_3D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_wgs84_3d(&self) -> bool {
        matches!(self, ValueSend::WGS84_3D(_))
    }

    #[inline]
    pub fn as_wgs84_3d(&self) -> Option<&spatial::WGS84_3D> {
        match self {
            ValueSend::WGS84_3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_wgs84_3d(self) -> Result<spatial::WGS84_3D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::Duration {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Duration(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_duration(&self) -> bool {
        matches!(self, ValueSend::Duration(_))
    }

    #[inline]
    pub fn as_duration(&self) -> Option<&time::Duration> {
        match self {
            ValueSend::Duration(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_duration(self) -> Result<time::Duration, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::LocalTime {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::LocalTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_local_time(&self) -> bool {
        matches!(self, ValueSend::LocalTime(_))
    }

    #[inline]
    pub fn as_local_time(&self) -> Option<&time::LocalTime> {
        match self {
            ValueSend::LocalTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_local_time(self) -> Result<time::LocalTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::Time {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Time(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_time(&self) -> bool {
        matches!(self, ValueSend::Time(_))
    }

    #[inline]
    pub fn as_time(&self) -> Option<&time::Time> {
        match self {
            ValueSend::Time(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_time(self) -> Result<time::Time, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::Date {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::Date(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_date(&self) -> bool {
        matches!(self, ValueSend::Date(_))
    }

    #[inline]
    pub fn as_date(&self) -> Option<&time::Date> {
        match self {
            ValueSend::Date(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date(self) -> Result<time::Date, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::LocalDateTime {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::LocalDateTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_local_date_time(&self) -> bool {
        matches!(self, ValueSend::LocalDateTime(_))
    }

    #[inline]
    pub fn as_local_date_time(&self) -> Option<&time::LocalDateTime> {
        match self {
            ValueSend::LocalDateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_local_date_time(self) -> Result<time::LocalDateTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::DateTime {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::DateTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_date_time(&self) -> bool {
        matches!(self, ValueSend::DateTime(_))
    }

    #[inline]
    pub fn as_date_time(&self) -> Option<&time::DateTime> {
        match self {
            ValueSend::DateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date_time(self) -> Result<time::DateTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueSend> for time::DateTimeFixed {
    type Error = ValueSend;

    #[inline]
    fn try_from(value: ValueSend) -> Result<Self, Self::Error> {
        match value {
            ValueSend::DateTimeFixed(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueSend {
    #[inline]
    pub fn is_date_time_fixed(&self) -> bool {
        matches!(self, ValueSend::DateTimeFixed(_))
    }

    #[inline]
    pub fn as_date_time_fixed(&self) -> Option<&time::DateTimeFixed> {
        match self {
            ValueSend::DateTimeFixed(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date_time_fixed(self) -> Result<time::DateTimeFixed, Self> {
        self.try_into()
    }
}
