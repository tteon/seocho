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

use chrono::{LocalResult, Offset, TimeZone};
use usize_cast::IntoIsize;

use super::super::bolt5x0::Bolt5x0StructTranslator;
use super::super::bolt_common::*;
use super::super::{BoltStructTranslator, BoltStructTranslatorWithUtcPatch};
use crate::driver::io::bolt::PackStreamSerializer;
use crate::value::graph::{Node, Path, Relationship, UnboundRelationship};
use crate::value::time::{local_date_time_from_timestamp, FixedOffset, Tz};
use crate::value::{BrokenValue, BrokenValueInner, ValueReceive, ValueSend};

#[derive(Debug, Default)]
pub(crate) struct Bolt4x4StructTranslator {
    utc_patch: bool,
    bolt5x0_translator: Bolt5x0StructTranslator,
}

impl BoltStructTranslator for Bolt4x4StructTranslator {
    fn serialize<S: PackStreamSerializer>(
        &self,
        serializer: &mut S,
        value: &ValueSend,
    ) -> Result<(), S::Error> {
        if self.utc_patch {
            return self.bolt5x0_translator.serialize(serializer, value);
        }
        match value {
            ValueSend::DateTime(dt) => {
                let offset = dt.offset().fix().local_minus_utc().into();
                let mut seconds = dt.timestamp();
                let nanoseconds = dt.timestamp_subsec_nanos();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("DateTime with leap second is not supported".into())
                    );
                }
                seconds = seconds
                    .checked_add(offset)
                    .ok_or_else(|| serializer.error("DateTime out of bounds".into()))?;
                serializer.write_struct_header(TAG_LEGACY_DATE_TIME_ZONE_ID, 3)?;
                serializer.write_int(seconds)?;
                serializer.write_int(nanoseconds.into())?;
                serializer.write_string(dt.timezone().name())?;
                Ok(())
            }
            ValueSend::DateTimeFixed(dt) => {
                let mut seconds = dt.timestamp();
                let nanoseconds = dt.timestamp_subsec_nanos();
                if nanoseconds >= 1_000_000_000 {
                    return Err(
                        serializer.error("DateTimeFixed with leap second is not supported".into())
                    );
                }
                let offset = dt.offset().fix().local_minus_utc().into();
                seconds += offset;
                serializer.write_struct_header(TAG_LEGACY_DATE_TIME, 3)?;
                serializer.write_int(seconds)?;
                serializer.write_int(nanoseconds.into())?;
                serializer.write_int(offset)?;
                Ok(())
            }
            _ => self.bolt5x0_translator.serialize(serializer, value),
        }
    }

    fn deserialize_struct(&self, tag: u8, fields: Vec<ValueReceive>) -> ValueReceive {
        match tag {
            TAG_NODE => {
                let size = fields.len();
                let mut fields = VecDeque::from(fields);
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for node struct b'N', found {size}"
                    ));
                }
                let id = as_int!(fields.pop_front().unwrap(), "node id");
                let raw_labels = as_vec!(fields.pop_front().unwrap(), "node labels");
                let mut labels = Vec::with_capacity(raw_labels.len());
                for label in raw_labels {
                    labels.push(as_string!(label, "node label"));
                }
                let properties = as_map!(fields.pop_front().unwrap(), "node properties");
                ValueReceive::Node(Node {
                    id,
                    labels,
                    properties,
                    element_id: format!("{id}"),
                })
            }
            TAG_RELATIONSHIP => {
                let size = fields.len();
                let mut fields = VecDeque::from(fields);
                if size != 5 {
                    return invalid_struct(format!(
                        "expected 5 fields for relationship struct b'R', found {size}"
                    ));
                }
                let id = as_int!(fields.pop_front().unwrap(), "relationship id");
                let start_node_id =
                    as_int!(fields.pop_front().unwrap(), "relationship start_node_id");
                let end_node_id = as_int!(fields.pop_front().unwrap(), "relationship end_node_id");
                let type_ = as_string!(fields.pop_front().unwrap(), "relationship type");
                let properties = as_map!(fields.pop_front().unwrap(), "relationship properties");
                ValueReceive::Relationship(Relationship {
                    id,
                    start_node_id,
                    end_node_id,
                    type_,
                    properties,
                    element_id: format!("{id}"),
                    start_node_element_id: format!("{start_node_id}"),
                    end_node_element_id: format!("{end_node_id}"),
                })
            }
            TAG_PATH => {
                let size = fields.len();
                let mut fields = VecDeque::from(fields);
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'P', found {size}"
                    ));
                }
                let raw_nodes = as_vec!(fields.pop_front().unwrap(), "Path nodes");
                let mut nodes = Vec::with_capacity(raw_nodes.len());
                for node in raw_nodes {
                    nodes.push(as_node!(node, "Path node"));
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
                                    if rel_size != 3 {
                                        return invalid_struct(
                                            format!(
                                                "expected 3 fields for unbound relationship struct b'r', found {rel_size}",
                                            ),
                                        );
                                    }
                                    let id = as_int!(rel_fields.pop_front().unwrap(), "unbound relationship id");
                                    let type_ = as_string!(rel_fields.pop_front().unwrap(), "unbound relationship type");
                                    let properties = as_map!(rel_fields.pop_front().unwrap(),"unbound relationship properties");
                                    UnboundRelationship {
                                        id,
                                        type_,
                                        properties,
                                        element_id: id.to_string(),
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
            TAG_DATE_TIME | TAG_DATE_TIME_ZONE_ID if !self.utc_patch => {
                ValueReceive::BrokenValue(BrokenValue {
                    inner: BrokenValueInner::UnknownStruct {
                        tag,
                        fields: VecDeque::from(fields),
                    },
                })
            }
            TAG_LEGACY_DATE_TIME | TAG_LEGACY_DATE_TIME_ZONE_ID if self.utc_patch => {
                ValueReceive::BrokenValue(BrokenValue {
                    inner: BrokenValueInner::UnknownStruct {
                        tag,
                        fields: VecDeque::from(fields),
                    },
                })
            }
            TAG_LEGACY_DATE_TIME => {
                let size = fields.len();
                let mut fields = VecDeque::from(fields);
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'F', found {size}"
                    ));
                }
                let mut seconds = as_int!(fields.pop_front().unwrap(), "DateTime seconds");
                let mut nanoseconds = as_int!(fields.pop_front().unwrap(), "DateTime nanoseconds");
                let tz_offset = as_int!(fields.pop_front().unwrap(), "DateTime tz_offset");
                seconds = match seconds.checked_sub(tz_offset) {
                    Some(s) => s,
                    None => return failed_struct("DateTime seconds out of bounds"),
                };
                if nanoseconds < 0 {
                    return invalid_struct("DateTime nanoseconds out of bounds");
                }
                seconds = match seconds.checked_add(nanoseconds.div_euclid(1_000_000_000)) {
                    Some(s) => s,
                    None => return failed_struct("DateTime seconds out of bounds"),
                };
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let nanoseconds = nanoseconds as u32;
                let dt = match local_date_time_from_timestamp(seconds, nanoseconds) {
                    Some(dt) => dt,
                    None => return failed_struct("DateTime out of bounds"),
                };
                let tz_offset = match tz_offset.try_into() {
                    Ok(tz_offset) => tz_offset,
                    Err(_) => return failed_struct("DateTime tz_offset out of bounds"),
                };
                let tz = match FixedOffset::east_opt(tz_offset) {
                    Some(tz) => tz,
                    None => return failed_struct("DateTime tz_offset out of bounds"),
                };
                ValueReceive::DateTimeFixed(tz.from_utc_datetime(&dt))
            }
            TAG_LEGACY_DATE_TIME_ZONE_ID => {
                let size = fields.len();
                let mut fields = VecDeque::from(fields);
                if size != 3 {
                    return invalid_struct(format!(
                        "expected 3 fields for path struct b'f', found {size}"
                    ));
                }
                let mut seconds = as_int!(fields.pop_front().unwrap(), "DateTimeZoneId seconds");
                let mut nanoseconds =
                    as_int!(fields.pop_front().unwrap(), "DateTimeZoneId nanoseconds");
                let tz_id = as_string!(fields.pop_front().unwrap(), "DateTimeZoneId tz_offset");
                let tz = match Tz::from_str(&tz_id) {
                    Ok(tz) => tz,
                    Err(e) => {
                        return failed_struct(format!(
                            "failed to load DateTimeZoneId time zone \"{tz_id}\": {e}"
                        ));
                    }
                };
                if nanoseconds < 0 {
                    return invalid_struct("DateTimeZoneId nanoseconds out of bounds");
                }
                seconds = match seconds.checked_add(nanoseconds.div_euclid(1_000_000_000)) {
                    Some(s) => s,
                    None => return failed_struct("DateTimeZoneId seconds out of bounds"),
                };
                nanoseconds = nanoseconds.rem_euclid(1_000_000_000);
                let nanoseconds = nanoseconds as u32;
                let dt = match local_date_time_from_timestamp(seconds, nanoseconds) {
                    Some(dt) => dt,
                    None => return failed_struct("DateTimeZoneId out of bounds"),
                };
                let dt = match dt.and_local_timezone(tz) {
                    LocalResult::None => {
                        return invalid_struct("DateTimeZoneId contains invalid local date time")
                    }
                    LocalResult::Single(dt) => dt,
                    // This sure looks funny ;)
                    // But pre bolt4.4 these temporal values were ambiguously encoded.
                    // So we just pick one of the two possible values.
                    LocalResult::Ambiguous(dt, _) => dt,
                };
                ValueReceive::DateTime(dt)
            }
            _ => self.bolt5x0_translator.deserialize_struct(tag, fields),
        }
    }
}

impl BoltStructTranslatorWithUtcPatch for Bolt4x4StructTranslator {
    fn enable_utc_patch(&mut self) {
        self.utc_patch = true;
    }
}
