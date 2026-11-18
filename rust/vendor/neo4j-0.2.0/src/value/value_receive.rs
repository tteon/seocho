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

use std::collections::{HashMap, VecDeque};

use itertools::Itertools;

use super::graph;
use super::spatial;
use super::time;
use super::value_send::ValueSend;

/// A value received from the database.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum ValueReceive {
    Null,
    Boolean(bool),
    Integer(i64),
    Float(f64),
    Bytes(Vec<u8>),
    String(String),
    List(Vec<ValueReceive>),
    Map(HashMap<String, ValueReceive>),
    Node(graph::Node),
    Relationship(graph::Relationship),
    Path(graph::Path),
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
    /// A value that could not be received.
    /// This can have multiple reasons, for example:
    ///  * Unexpected struct data (likely a driver or server bug)
    ///  * Temporal types that cannot be represented on the client side like values out of range
    ///    and time zones not supported by the client.
    BrokenValue(BrokenValue),
}

impl ValueReceive {
    #[inline]
    pub fn is_null(&self) -> bool {
        matches!(self, ValueReceive::Null)
    }
}

impl TryFrom<ValueReceive> for bool {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Boolean(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_bool(&self) -> bool {
        matches!(self, ValueReceive::Boolean(_))
    }

    #[inline]
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            ValueReceive::Boolean(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_bool_mut(&mut self) -> Option<&mut bool> {
        match self {
            ValueReceive::Boolean(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_bool(self) -> Result<bool, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for i64 {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Integer(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_int(&self) -> bool {
        matches!(self, ValueReceive::Integer(_))
    }

    #[inline]
    pub fn as_int(&self) -> Option<i64> {
        match self {
            ValueReceive::Integer(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_int_mut(&mut self) -> Option<&mut i64> {
        match self {
            ValueReceive::Integer(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_int(self) -> Result<i64, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for f64 {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Float(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_float(&self) -> bool {
        matches!(self, ValueReceive::Float(_))
    }

    #[inline]
    pub fn as_float(&self) -> Option<f64> {
        match self {
            ValueReceive::Float(v) => Some(*v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_float_mut(&mut self) -> Option<&mut f64> {
        match self {
            ValueReceive::Float(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_float(self) -> Result<f64, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for Vec<u8> {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Bytes(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_bytes(&self) -> bool {
        matches!(self, ValueReceive::Bytes(_))
    }

    #[inline]
    pub fn as_bytes(&self) -> Option<&Vec<u8>> {
        match self {
            ValueReceive::Bytes(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_bytes_mut(&mut self) -> Option<&mut Vec<u8>> {
        match self {
            ValueReceive::Bytes(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_bytes(self) -> Result<Vec<u8>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for String {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::String(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_string(&self) -> bool {
        matches!(self, ValueReceive::String(_))
    }

    #[inline]
    pub fn as_string(&self) -> Option<&String> {
        match self {
            ValueReceive::String(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_string_mut(&mut self) -> Option<&mut String> {
        match self {
            ValueReceive::String(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_string(self) -> Result<String, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for Vec<ValueReceive> {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::List(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_list(&self) -> bool {
        matches!(self, ValueReceive::List(_))
    }

    #[inline]
    pub fn as_list(&self) -> Option<&Vec<ValueReceive>> {
        match self {
            ValueReceive::List(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_list_mut(&mut self) -> Option<&mut Vec<ValueReceive>> {
        match self {
            ValueReceive::List(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_list(self) -> Result<Vec<ValueReceive>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for HashMap<String, ValueReceive> {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Map(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_map(&self) -> bool {
        matches!(self, ValueReceive::Map(_))
    }

    #[inline]
    pub fn as_map(&self) -> Option<&HashMap<String, ValueReceive>> {
        match self {
            ValueReceive::Map(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_map_mut(&mut self) -> Option<&mut HashMap<String, ValueReceive>> {
        match self {
            ValueReceive::Map(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_map(self) -> Result<HashMap<String, ValueReceive>, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for graph::Node {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Node(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_node(&self) -> bool {
        matches!(self, ValueReceive::Node(_))
    }

    #[inline]
    pub fn as_node(&self) -> Option<&graph::Node> {
        match self {
            ValueReceive::Node(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_node_mut(&mut self) -> Option<&mut graph::Node> {
        match self {
            ValueReceive::Node(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_node(self) -> Result<graph::Node, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for graph::Relationship {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Relationship(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_relationship(&self) -> bool {
        matches!(self, ValueReceive::Relationship(_))
    }

    #[inline]
    pub fn as_relationship(&self) -> Option<&graph::Relationship> {
        match self {
            ValueReceive::Relationship(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_relationship_mut(&mut self) -> Option<&mut graph::Relationship> {
        match self {
            ValueReceive::Relationship(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_relationship(self) -> Result<graph::Relationship, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for graph::Path {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Path(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_path(&self) -> bool {
        matches!(self, ValueReceive::Path(_))
    }

    #[inline]
    pub fn as_path(&self) -> Option<&graph::Path> {
        match self {
            ValueReceive::Path(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_path_mut(&mut self) -> Option<&mut graph::Path> {
        match self {
            ValueReceive::Path(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_path(self) -> Result<graph::Path, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for spatial::Cartesian2D {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Cartesian2D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_cartesian_2d(&self) -> bool {
        matches!(self, ValueReceive::Cartesian2D(_))
    }

    #[inline]
    pub fn as_cartesian_2d(&self) -> Option<&spatial::Cartesian2D> {
        match self {
            ValueReceive::Cartesian2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_cartesian_2d_mut(&mut self) -> Option<&mut spatial::Cartesian2D> {
        match self {
            ValueReceive::Cartesian2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_cartesian_2d(self) -> Result<spatial::Cartesian2D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for spatial::Cartesian3D {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Cartesian3D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_cartesian_3d(&self) -> bool {
        matches!(self, ValueReceive::Cartesian3D(_))
    }

    #[inline]
    pub fn as_cartesian_3d(&self) -> Option<&spatial::Cartesian3D> {
        match self {
            ValueReceive::Cartesian3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_cartesian_3d_mut(&mut self) -> Option<&mut spatial::Cartesian3D> {
        match self {
            ValueReceive::Cartesian3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_cartesian_3d(self) -> Result<spatial::Cartesian3D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for spatial::WGS84_2D {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::WGS84_2D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_wgs84_2d(&self) -> bool {
        matches!(self, ValueReceive::WGS84_2D(_))
    }

    #[inline]
    pub fn as_wgs84_2d(&self) -> Option<&spatial::WGS84_2D> {
        match self {
            ValueReceive::WGS84_2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_wgs84_2d_mut(&mut self) -> Option<&mut spatial::WGS84_2D> {
        match self {
            ValueReceive::WGS84_2D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_wgs84_2d(self) -> Result<spatial::WGS84_2D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for spatial::WGS84_3D {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::WGS84_3D(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_wgs84_3d(&self) -> bool {
        matches!(self, ValueReceive::WGS84_3D(_))
    }

    #[inline]
    pub fn as_wgs84_3d(&self) -> Option<&spatial::WGS84_3D> {
        match self {
            ValueReceive::WGS84_3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_wgs84_3d_mut(&mut self) -> Option<&mut spatial::WGS84_3D> {
        match self {
            ValueReceive::WGS84_3D(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_wgs84_3d(self) -> Result<spatial::WGS84_3D, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::Duration {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Duration(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_duration(&self) -> bool {
        matches!(self, ValueReceive::Duration(_))
    }

    #[inline]
    pub fn as_duration(&self) -> Option<&time::Duration> {
        match self {
            ValueReceive::Duration(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_duration_mut(&mut self) -> Option<&mut time::Duration> {
        match self {
            ValueReceive::Duration(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_duration(self) -> Result<time::Duration, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::LocalTime {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::LocalTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_local_time(&self) -> bool {
        matches!(self, ValueReceive::LocalTime(_))
    }

    #[inline]
    pub fn as_local_time(&self) -> Option<&time::LocalTime> {
        match self {
            ValueReceive::LocalTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_local_time_mut(&mut self) -> Option<&mut time::LocalTime> {
        match self {
            ValueReceive::LocalTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_local_time(self) -> Result<time::LocalTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::Time {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Time(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_time(&self) -> bool {
        matches!(self, ValueReceive::Time(_))
    }

    #[inline]
    pub fn as_time(&self) -> Option<&time::Time> {
        match self {
            ValueReceive::Time(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_time_mut(&mut self) -> Option<&mut time::Time> {
        match self {
            ValueReceive::Time(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_time(self) -> Result<time::Time, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::Date {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::Date(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_date(&self) -> bool {
        matches!(self, ValueReceive::Date(_))
    }

    #[inline]
    pub fn as_date(&self) -> Option<&time::Date> {
        match self {
            ValueReceive::Date(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_date_mut(&mut self) -> Option<&mut time::Date> {
        match self {
            ValueReceive::Date(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date(self) -> Result<time::Date, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::LocalDateTime {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::LocalDateTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_local_date_time(&self) -> bool {
        matches!(self, ValueReceive::LocalDateTime(_))
    }

    #[inline]
    pub fn as_local_date_time(&self) -> Option<&time::LocalDateTime> {
        match self {
            ValueReceive::LocalDateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_local_date_time_mut(&mut self) -> Option<&mut time::LocalDateTime> {
        match self {
            ValueReceive::LocalDateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_local_date_time(self) -> Result<time::LocalDateTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::DateTime {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::DateTime(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_date_time(&self) -> bool {
        matches!(self, ValueReceive::DateTime(_))
    }

    #[inline]
    pub fn as_date_time(&self) -> Option<&time::DateTime> {
        match self {
            ValueReceive::DateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_date_time_mut(&mut self) -> Option<&mut time::DateTime> {
        match self {
            ValueReceive::DateTime(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date_time(self) -> Result<time::DateTime, Self> {
        self.try_into()
    }
}

impl TryFrom<ValueReceive> for time::DateTimeFixed {
    type Error = ValueReceive;

    #[inline]
    fn try_from(value: ValueReceive) -> Result<Self, Self::Error> {
        match value {
            ValueReceive::DateTimeFixed(v) => Ok(v),
            _ => Err(value),
        }
    }
}

impl ValueReceive {
    #[inline]
    pub fn is_date_time_fixed(&self) -> bool {
        matches!(self, ValueReceive::DateTimeFixed(_))
    }

    #[inline]
    pub fn as_date_time_fixed(&self) -> Option<&time::DateTimeFixed> {
        match self {
            ValueReceive::DateTimeFixed(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    pub fn as_date_time_fixed_mut(&mut self) -> Option<&mut time::DateTimeFixed> {
        match self {
            ValueReceive::DateTimeFixed(v) => Some(v),
            _ => None,
        }
    }

    #[inline]
    #[allow(clippy::result_large_err)]
    pub fn try_into_date_time_fixed(self) -> Result<time::DateTimeFixed, Self> {
        self.try_into()
    }
}

#[derive(Debug, Clone)]
pub struct BrokenValue {
    pub(crate) inner: BrokenValueInner,
}

impl PartialEq for BrokenValue {
    fn eq(&self, _other: &Self) -> bool {
        false
    }
}

#[derive(Debug, Clone)]
pub(crate) enum BrokenValueInner {
    Reason(String),
    UnknownStruct {
        tag: u8,
        fields: VecDeque<ValueReceive>,
    },
    InvalidStruct {
        reason: String,
    },
}

impl BrokenValue {
    pub fn reason(&self) -> &str {
        match &self.inner {
            BrokenValueInner::Reason(reason) => reason,
            BrokenValueInner::UnknownStruct { .. } => "received an unknown packstream struct",
            BrokenValueInner::InvalidStruct { reason, .. } => reason,
        }
    }
}

impl From<BrokenValueInner> for BrokenValue {
    fn from(inner: BrokenValueInner) -> Self {
        BrokenValue { inner }
    }
}

impl ValueReceive {
    pub(crate) fn dbg_print(&self) -> String {
        match self {
            ValueReceive::Null => "null".into(),
            ValueReceive::Boolean(v) => v.to_string(),
            ValueReceive::Integer(v) => v.to_string(),
            ValueReceive::Float(v) => v.to_string(),
            ValueReceive::Bytes(v) => format!("bytes{v:02X?}"),
            ValueReceive::String(v) => format!("{v:?}"),
            ValueReceive::List(v) => format!("[{}]", v.iter().map(|e| e.dbg_print()).format(", ")),
            ValueReceive::Map(v) => format!(
                "{{{}}}",
                v.iter()
                    .map(|(k, e)| format!("{:?}: {}", k, e.dbg_print()))
                    .format(", ")
            ),
            ValueReceive::Node(node) => node.to_string(),
            ValueReceive::Relationship(relationship) => relationship.to_string(),
            ValueReceive::Path(path) => path.to_string(),
            ValueReceive::Cartesian2D(v) => format!("{v:?}"),
            ValueReceive::Cartesian3D(v) => format!("{v:?}"),
            ValueReceive::WGS84_2D(v) => format!("{v:?}"),
            ValueReceive::WGS84_3D(v) => format!("{v:?}"),
            ValueReceive::Duration(v) => format!("{v:?}"),
            ValueReceive::LocalTime(v) => format!("{v:?}"),
            ValueReceive::Time(v) => format!("{v:?}"),
            ValueReceive::Date(v) => format!("{v:?}"),
            ValueReceive::LocalDateTime(v) => format!("{v:?}"),
            ValueReceive::DateTime(v) => format!("{v:?}"),
            ValueReceive::DateTimeFixed(v) => format!("{v:?}"),
            ValueReceive::BrokenValue(broken_value) => {
                format!("BrokenValue({})", broken_value.reason())
            }
        }
    }
}

impl From<ValueSend> for ValueReceive {
    fn from(v: ValueSend) -> Self {
        match v {
            ValueSend::Null => Self::Null,
            ValueSend::Boolean(v) => Self::Boolean(v),
            ValueSend::Integer(v) => Self::Integer(v),
            ValueSend::Float(v) => Self::Float(v),
            ValueSend::Bytes(v) => Self::Bytes(v),
            ValueSend::String(v) => Self::String(v),
            ValueSend::List(v) => Self::List(v.into_iter().map(Into::into).collect()),
            ValueSend::Map(v) => Self::Map(v.into_iter().map(|(k, e)| (k, e.into())).collect()),
            ValueSend::Cartesian2D(v) => Self::Cartesian2D(v),
            ValueSend::Cartesian3D(v) => Self::Cartesian3D(v),
            ValueSend::WGS84_2D(v) => Self::WGS84_2D(v),
            ValueSend::WGS84_3D(v) => Self::WGS84_3D(v),
            ValueSend::Duration(v) => Self::Duration(v),
            ValueSend::LocalTime(v) => Self::LocalTime(v),
            ValueSend::Time(v) => Self::Time(v),
            ValueSend::Date(v) => Self::Date(v),
            ValueSend::LocalDateTime(v) => Self::LocalDateTime(v),
            ValueSend::DateTime(v) => Self::DateTime(v),
            ValueSend::DateTimeFixed(v) => Self::DateTimeFixed(v),
        }
    }
}
