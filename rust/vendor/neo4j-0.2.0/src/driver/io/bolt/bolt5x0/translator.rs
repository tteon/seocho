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

use std::collections::VecDeque;
use std::str::FromStr;

use chrono::{Datelike, Offset, TimeZone, Timelike};
use usize_cast::{FromUsize, IntoIsize};

use super::super::bolt_common::*;
use super::super::BoltStructTranslator;
use crate::driver::io::bolt::PackStreamSerializer;
use crate::value::graph::{Node, Path, Relationship, UnboundRelationship};
use crate::value::spatial::{
    Cartesian2D, Cartesian3D, SRID_CARTESIAN_2D, SRID_CARTESIAN_3D, SRID_WGS84_2D, SRID_WGS84_3D,
    WGS84_2D, WGS84_3D,
};
use crate::value::time::{
    local_date_time_from_timestamp, Date, Duration, FixedOffset, LocalTime, Time, Tz,
};
use crate::value::{BrokenValue, BrokenValueInner, ValueReceive, ValueSend};

#[derive(Debug, Default)]
pub(crate) struct Bolt5x0StructTranslator {}

impl Bolt5x0StructTranslator {
    fn write_2d_point<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        srid: i64,
        coordinates: &[f64; 2],
    ) -> Result<(), S::Error> {
        serializer.write_struct_header(TAG_2D_POINT, 3)?;
        serializer.write_int(srid)?;
        serializer.write_float(coordinates[0])?;
        serializer.write_float(coordinates[1])
    }

    fn write_3d_point<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        srid: i64,
        coordinates: &[f64; 3],
    ) -> Result<(), S::Error> {
        serializer.write_struct_header(TAG_3D_POINT, 4)?;
        serializer.write_int(srid)?;
        serializer.write_float(coordinates[0])?;
        serializer.write_float(coordinates[1])?;
        serializer.write_float(coordinates[2])
    }
}

impl BoltStructTranslator for Bolt5x0StructTranslator {
    fn serialize<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        value: &ValueSend,
    ) -> Result<(), S::Error> {
        match value {
            ValueSend::Null => serializer.write_null(),
            ValueSend::Boolean(b) => serializer.write_bool(*b),
            ValueSend::Integer(i) => serializer.write_int(*i),
            ValueSend::Float(f) => serializer.write_float(*f),
            ValueSend::Bytes(b) => serializer.write_bytes(b),
            ValueSend::String(s) => serializer.write_string(s),
            ValueSend::List(l) => {
                serializer.write_list_header(u64::from_usize(l.len()))?;
                for v in l {
                    self.serialize(serializer, v)?;
                }
                Ok(())
            }
            ValueSend::Map(d) => {
                serializer.write_dict_header(u64::from_usize(d.len()))?;
                for (k, v) in d {
                    serializer.write_string(k)?;
                    self.serialize(serializer, v)?;
                }
                Ok(())
            }
            ValueSend::Cartesian2D(Cartesian2D { srid, coordinates }) => {
                self.write_2d_point(serializer, *srid, coordinates)
            }
            ValueSend::Cartesian3D(Cartesian3D { srid, coordinates }) => {
                self.write_3d_point(serializer, *srid, coordinates)
            }
            ValueSend::WGS84_2D(WGS84_2D { srid, coordinates }) => {
                self.write_2d_point(serializer, *srid, coordinates)
            }
            ValueSend::WGS84_3D(WGS84_3D { srid, coordinates }) => {
                self.write_3d_point(serializer, *srid, coordinates)
            }
            ValueSend::Duration(Duration {
                months,
                days,
                seconds,
                nanoseconds,
            }) => {
                serializer.write_struct_header(TAG_DURATION, 4)?;
                serializer.write_int(*months)?;
                serializer.write_int(*days)?;
                serializer.write_int(*seconds)?;
                serializer.write_int((*nanoseconds).into())?;
                Ok(())
            }
            ValueSend::LocalTime(t) => {
                let mut nanoseconds = t.nanosecond().into();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("LocalTime with leap second is not supported".into())
                    );
                }
                nanoseconds += i64::from(t.num_seconds_from_midnight()) * 1_000_000_000;
                serializer.write_struct_header(TAG_LOCAL_TIME, 1)?;
                serializer.write_int(nanoseconds)?;
                Ok(())
            }
            ValueSend::Time(Time { time, offset }) => {
                let mut nanoseconds = time.nanosecond().into();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("LocalTime with leap second is not supported".into())
                    );
                }
                nanoseconds += i64::from(time.num_seconds_from_midnight()) * 1_000_000_000;
                let tz_offset = offset.local_minus_utc().into();
                serializer.write_struct_header(TAG_TIME, 2)?;
                serializer.write_int(nanoseconds)?;
                serializer.write_int(tz_offset)?;
                Ok(())
            }
            ValueSend::Date(d) => {
                // let unix_epoc_days = Date::from_ymd_opt(1970, 1, 1).unwrap().num_days_from_ce();
                const UNIX_EPOC_DAYS: i64 = 719163;
                let mut days = d.num_days_from_ce().into();
                days -= UNIX_EPOC_DAYS;
                serializer.write_struct_header(TAG_DATE, 1)?;
                serializer.write_int(days)?;
                Ok(())
            }
            ValueSend::LocalDateTime(dt) => {
                let seconds = dt.and_utc().timestamp();
                let nanoseconds = dt.nanosecond();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("LocalDateTime with leap second is not supported".into())
                    );
                }
                let nanoseconds = nanoseconds.into();
                serializer.write_struct_header(TAG_LOCAL_DATE_TIME, 2)?;
                serializer.write_int(seconds)?;
                serializer.write_int(nanoseconds)?;
                Ok(())
            }
            ValueSend::DateTime(dt) => {
                let seconds = dt.timestamp();
                let nanoseconds = dt.nanosecond();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("LocalDateTime with leap second is not supported".into())
                    );
                }
                let nanoseconds = nanoseconds.into();
                let tz_id = dt.timezone().name();
                serializer.write_struct_header(TAG_DATE_TIME_ZONE_ID, 3)?;
                serializer.write_int(seconds)?;
                serializer.write_int(nanoseconds)?;
                serializer.write_string(tz_id)?;
                Ok(())
            }
            ValueSend::DateTimeFixed(dt) => {
                let seconds = dt.timestamp();
                let nanoseconds = dt.nanosecond();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("LocalDateTime with leap second is not supported".into())
                    );
                }
                let nanoseconds = nanoseconds.into();
                let tz_offset = dt.offset().fix().local_minus_utc().into();
                serializer.write_struct_header(TAG_DATE_TIME, 3)?;
                serializer.write_int(seconds)?;
                serializer.write_int(nanoseconds)?;
                serializer.write_int(tz_offset)?;
                Ok(())
            }
        }
    }

    fn deserialize_struct(&self, tag: u8, fields: Vec<ValueReceive>) -> ValueReceive {
        let size = fields.len();
        let mut fields = VecDeque::from(fields);
        match tag {
            TAG_2D_POINT => {
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for point 2D struct b'X', found {size}"
                    ));
                }
                let srid = as_int!(fields.pop_front().unwrap(), "2D SRID");
                let mut coordinates = [0.0; 2];
                for (i, coordinate) in coordinates.iter_mut().enumerate() {
                    *coordinate =
                        as_float!(fields.pop_front().unwrap(), "{i}th 2D coordinate", i = i);
                }
                let x = coordinates[0];
                let y = coordinates[1];
                match srid {
                    SRID_CARTESIAN_2D => ValueReceive::Cartesian2D(Cartesian2D::new(x, y)),
                    SRID_WGS84_2D => ValueReceive::WGS84_2D(WGS84_2D::new(x, y)),
                    srid => invalid_struct(format!("unknown 2D SRID {srid}")),
                }
            }
            TAG_3D_POINT => {
                if size != 4 {
                    return invalid_struct(format!(
                        "expected 4 fields for point 3D struct b'X', found {size}"
                    ));
                }
                let srid = as_int!(fields.pop_front().unwrap(), "3D SRID");
                let mut coordinates = [0.0; 3];
                for (i, coordinate) in coordinates.iter_mut().enumerate() {
                    *coordinate =
                        as_float!(fields.pop_front().unwrap(), "{i}th 3D coordinate", i = i);
                }
                let x = coordinates[0];
                let y = coordinates[1];
                let z = coordinates[2];
                match srid {
                    SRID_CARTESIAN_3D => ValueReceive::Cartesian3D(Cartesian3D::new(x, y, z)),
                    SRID_WGS84_3D => ValueReceive::WGS84_3D(WGS84_3D::new(x, y, z)),
                    srid => invalid_struct(format!("unknown 3D SRID {srid}")),
                }
            }
            TAG_NODE => {
                if size != 4 {
                    return invalid_struct(format!(
                        "expected 4 fields for node struct b'N', found {size}"
                    ));
                }
                let id = as_int!(fields.pop_front().unwrap(), "node id");
                let raw_labels = as_vec!(fields.pop_front().unwrap(), "node labels");
                let mut labels = Vec::with_capacity(raw_labels.len());
                for label in raw_labels {
                    labels.push(as_string!(label, "node label"));
                }
                let properties = as_map!(fields.pop_front().unwrap(), "node properties");
                let element_id = as_string!(fields.pop_front().unwrap(), "node element_it");
                ValueReceive::Node(Node {
                    id,
                    labels,
                    properties,
                    element_id,
                })
            }
            TAG_RELATIONSHIP => {
                if size != 8 {
                    return invalid_struct(format!(
                        "expected 8 fields for relationship struct b'R', found {size}"
                    ));
                }
                let id = as_int!(fields.pop_front().unwrap(), "relationship id");
                let start_node_id =
                    as_int!(fields.pop_front().unwrap(), "relationship start_node_id");
                let end_node_id = as_int!(fields.pop_front().unwrap(), "relationship end_node_id");
                let type_ = as_string!(fields.pop_front().unwrap(), "relationship type");
                let properties = as_map!(fields.pop_front().unwrap(), "relationship properties");
                let element_id = as_string!(fields.pop_front().unwrap(), "relationship element_id");
                let start_node_element_id = as_string!(
                    fields.pop_front().unwrap(),
                    "relationship start_node_element_id"
                );
                let end_node_element_id = as_string!(
                    fields.pop_front().unwrap(),
                    "relationship end_node_element_id"
                );
                ValueReceive::Relationship(Relationship {
                    id,
                    start_node_id,
                    end_node_id,
                    type_,
                    properties,
                    element_id,
                    start_node_element_id,
                    end_node_element_id,
                })
            }
            TAG_PATH => {
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'P', found {size}"
                    ));
                }
                let raw_nodes = as_vec!(fields.pop_front().unwrap(), "path nodes");
                let mut nodes = Vec::with_capacity(raw_nodes.len());
                for node in raw_nodes {
                    nodes.push(as_node!(node, "path node"));
                }
                let relationships = match fields.pop_front().unwrap() {
                    ValueReceive::List(v) => {
                        let mut relationships = Vec::with_capacity(v.len());
                        for relationship in v {
                            relationships.push(match relationship {
                                ValueReceive::BrokenValue(BrokenValue {
                                    inner:
                                        BrokenValueInner::UnknownStruct {
                                            tag: rel_tag,
                                            fields: mut rel_fields,
                                        },
                                }) if rel_tag == TAG_UNBOUND_RELATIONSHIP => {
                                    let rel_size = rel_fields.len();
                                    if rel_size != 4 {
                                        return invalid_struct(
                                            format!(
                                                "expected 4 fields for unbound relationship struct b'r', found {rel_size}",
                                            ),
                                        );
                                    }
                                    let id = as_int!(rel_fields.pop_front().unwrap(), "unbound relationship id");
                                    let type_ = as_string!(rel_fields.pop_front().unwrap(), "unbound relationship type");
                                    let properties = as_map!(rel_fields.pop_front().unwrap(),"unbound relationship properties");
                                    let element_id = as_string!(rel_fields.pop_front().unwrap(), "unbound relationship element_id");
                                    UnboundRelationship {
                                        id,
                                        type_,
                                        properties,
                                        element_id,
                                    }
                                }
                                v => {
                                    return invalid_struct(
                                        format!("expected path relationship to be an unbound relationship, found {v:?}"),
                                    )
                                }
                            });
                        }
                        relationships
                    }
                    v => {
                        return invalid_struct(format!(
                            "expected path nodes to be a list, found {v:?}"
                        ))
                    }
                };
                let raw_indices = as_vec!(fields.pop_front().unwrap(), "path indices");
                let mut indices = Vec::with_capacity(raw_indices.len());
                for index in raw_indices {
                    indices.push(as_int!(index, "path index").into_isize());
                }
                match Path::new(nodes, relationships, indices) {
                    Ok(path) => ValueReceive::Path(path),
                    Err(e) => invalid_struct(format!("Path invariant violated: {e}")),
                }
            }
            TAG_DATE => {
                if size != 1 {
                    return invalid_struct(format!(
                        "expected 1 field for Date struct b'D', found {size}"
                    ));
                }
                let days = as_int!(fields.pop_front().unwrap(), "Date days");
                let date = match Date::from_yo_opt(1970, 1)
                    .unwrap()
                    .checked_add_signed(chrono::Duration::days(days))
                {
                    Some(date) => date,
                    None => return failed_struct("Date out of bounds"),
                };
                ValueReceive::Date(date)
            }
            TAG_TIME => {
                if size != 2 {
                    return invalid_struct(format!(
                        "expected 2 field for Time struct b'T', found {size}"
                    ));
                }
                let mut nanoseconds = as_int!(fields.pop_front().unwrap(), "nanoseconds be");
                let tz_offset = as_int!(fields.pop_front().unwrap(), "Time tz_offset");
                let seconds = nanoseconds.div_euclid(1_000_000_000);
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let seconds = match seconds.try_into() {
                    Ok(seconds) => seconds,
                    Err(_) => return failed_struct("Time seconds out of bounds"),
                };
                let nanoseconds = match nanoseconds.try_into() {
                    Ok(nanoseconds) => nanoseconds,
                    Err(_) => return failed_struct("Time nanoseconds out of bounds"),
                };
                let tz_offset = match tz_offset.try_into() {
                    Ok(tz_offset) => tz_offset,
                    Err(_) => return failed_struct("Time tz_offset out of bounds"),
                };
                let offset = match FixedOffset::east_opt(tz_offset) {
                    Some(tz) => tz,
                    None => return failed_struct("Time tz_offset out of bounds"),
                };
                let time = match LocalTime::from_num_seconds_from_midnight_opt(seconds, nanoseconds)
                {
                    Some(time) => time,
                    None => return failed_struct("Time tz_offset out of bounds"),
                };
                ValueReceive::Time(Time { time, offset })
            }
            TAG_LOCAL_TIME => {
                if size != 1 {
                    return invalid_struct(format!(
                        "expected 1 field for Time struct b't', found {size}"
                    ));
                }
                let mut nanoseconds = as_int!(fields.pop_front().unwrap(), "LocalTime nanoseconds");
                let seconds = nanoseconds.div_euclid(1_000_000_000);
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let seconds = match seconds.try_into() {
                    Ok(seconds) => seconds,
                    Err(_) => return failed_struct("LocalTime nanoseconds out of bounds"),
                };
                let nanoseconds = match nanoseconds.try_into() {
                    Ok(nanoseconds) => nanoseconds,
                    Err(_) => return failed_struct("LocalTime nanoseconds out of bounds"),
                };
                let time = match LocalTime::from_num_seconds_from_midnight_opt(seconds, nanoseconds)
                {
                    Some(time) => time,
                    None => return failed_struct("LocalTime tz_offset out of bounds"),
                };
                ValueReceive::LocalTime(time)
            }
            TAG_DATE_TIME => {
                let size = fields.len();
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'I', found {size}"
                    ));
                }
                let mut seconds = as_int!(fields.pop_front().unwrap(), "DateTime seconds");
                let mut nanoseconds = as_int!(fields.pop_front().unwrap(), "DateTime nanoseconds");
                let tz_offset = as_int!(fields.pop_front().unwrap(), "DateTime tz_offset");
                if nanoseconds < 0 {
                    return invalid_struct("DateTime nanoseconds out of bounds");
                }
                seconds = match seconds.checked_add(nanoseconds.div_euclid(1_000_000_000)) {
                    Some(s) => s,
                    None => return failed_struct("DateTime seconds out of bounds"),
                };
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let nanoseconds = nanoseconds as u32;
                let tz_offset = match tz_offset.try_into() {
                    Ok(tz_offset) => tz_offset,
                    Err(_) => return failed_struct("DateTime tz_offset out of bounds"),
                };
                let tz = match FixedOffset::east_opt(tz_offset) {
                    Some(tz) => tz,
                    None => return failed_struct("DateTime tz_offset out of bounds"),
                };
                let utc_dt = match local_date_time_from_timestamp(seconds, nanoseconds) {
                    Some(dt) => dt,
                    None => return failed_struct("DateTime out of bounds"),
                };
                ValueReceive::DateTimeFixed(tz.from_utc_datetime(&utc_dt))
            }
            TAG_DATE_TIME_ZONE_ID => {
                let size = fields.len();
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'I', found {size}"
                    ));
                }
                let mut seconds = as_int!(fields.pop_front().unwrap(), "DateTimeZoneId seconds");
                let mut nanoseconds =
                    as_int!(fields.pop_front().unwrap(), "DateTimeZoneId nanoseconds");
                let tz_id = as_string!(fields.pop_front().unwrap(), "DateTimeZoneId tz_id");
                if nanoseconds < 0 {
                    return invalid_struct("DateTimeZoneId nanoseconds out of bounds");
                }
                seconds = match seconds.checked_add(nanoseconds.div_euclid(1_000_000_000)) {
                    Some(s) => s,
                    None => return failed_struct("DateTimeZoneId seconds out of bounds"),
                };
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let nanoseconds = nanoseconds as u32;
                let tz = match Tz::from_str(&tz_id) {
                    Ok(tz) => tz,
                    Err(e) => {
                        return failed_struct(format!(
                            "failed to load DateTimeZoneId time zone \"{tz_id}\": {e}"
                        ));
                    }
                };
                let utc_dt = match local_date_time_from_timestamp(seconds, nanoseconds) {
                    Some(dt) => dt,
                    None => return failed_struct("DateTimeZoneId out of bounds"),
                };
                ValueReceive::DateTime(tz.from_utc_datetime(&utc_dt))
            }
            TAG_LOCAL_DATE_TIME => {
                let size = fields.len();
                if size != 2 {
                    return invalid_struct(format!(
                        "expected 2 fields for path struct b'd', found {size}"
                    ));
                }
                let mut seconds = as_int!(fields.pop_front().unwrap(), "LocalDateTime seconds");
                let mut nanoseconds =
                    as_int!(fields.pop_front().unwrap(), "LocalDateTime nanoseconds");
                seconds = match seconds.checked_add(nanoseconds.div_euclid(1_000_000_000)) {
                    Some(s) => s,
                    None => return failed_struct("LocalDateTime seconds out of bounds"),
                };
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let nanoseconds = nanoseconds as u32;
                let dt = match local_date_time_from_timestamp(seconds, nanoseconds) {
                    Some(dt) => dt,
                    None => return failed_struct("LocalDateTime out of bounds"),
                };
                ValueReceive::LocalDateTime(dt)
            }
            TAG_DURATION => {
                let size = fields.len();
                if size != 4 {
                    return invalid_struct(format!(
                        "expected 4 fields for path struct b'E', found {size}"
                    ));
                }
                let months = as_int!(fields.pop_front().unwrap(), "Duration months");
                let days = as_int!(fields.pop_front().unwrap(), "Duration days");
                let seconds = as_int!(fields.pop_front().unwrap(), "Duration seconds");
                let nanoseconds = as_int!(fields.pop_front().unwrap(), "Duration nanoseconds");
                let nanoseconds = match nanoseconds.try_into() {
                    Ok(ns) => ns,
                    Err(_) => return failed_struct("Duration nanoseconds out of bounds"),
                };
                let duration = match Duration::new(months, days, seconds, nanoseconds) {
                    Some(d) => d,
                    None => return failed_struct("Duration out of bounds"),
                };
                ValueReceive::Duration(duration)
            }
            _ => ValueReceive::BrokenValue(BrokenValue {
                inner: BrokenValueInner::UnknownStruct { tag, fields },
            }),
        }
    }
}
