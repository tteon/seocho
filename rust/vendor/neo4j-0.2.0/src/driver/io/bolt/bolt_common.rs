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

use crate::error_::Neo4jError;
use crate::value::{BrokenValueInner, ValueReceive};

pub(super) const BOLT_AGENT_PRODUCT: &str = env!("NEO4J_BOLT_AGENT_PRODUCT");
pub(super) const BOLT_AGENT_PLATFORM: &str = env!("NEO4J_BOLT_AGENT_PLATFORM");
pub(super) const BOLT_AGENT_LANGUAGE: &str = env!("NEO4J_BOLT_AGENT_LANGUAGE");
pub(super) const BOLT_AGENT_LANGUAGE_DETAILS: &str = env!("NEO4J_BOLT_AGENT_LANGUAGE_DETAILS");

pub(super) const TAG_2D_POINT: u8 = b'X';
pub(super) const TAG_3D_POINT: u8 = b'Y';
pub(super) const TAG_NODE: u8 = b'N';
pub(super) const TAG_RELATIONSHIP: u8 = b'R';
pub(super) const TAG_UNBOUND_RELATIONSHIP: u8 = b'r';
pub(super) const TAG_PATH: u8 = b'P';
pub(super) const TAG_DATE: u8 = b'D';
pub(super) const TAG_TIME: u8 = b'T';
pub(super) const TAG_LOCAL_TIME: u8 = b't';
pub(super) const TAG_DATE_TIME: u8 = b'I';
pub(super) const TAG_LEGACY_DATE_TIME: u8 = b'F';
pub(super) const TAG_DATE_TIME_ZONE_ID: u8 = b'i';
pub(super) const TAG_LEGACY_DATE_TIME_ZONE_ID: u8 = b'f';
pub(super) const TAG_LOCAL_DATE_TIME: u8 = b'd';
pub(super) const TAG_DURATION: u8 = b'E';

macro_rules! value_as {
    ($variant:ident, $value:expr, $name:literal, $type_name:literal $($format_arg:tt)*) => {
        match $value {
            ValueReceive::$variant(i) => i,
            v => {
                return invalid_struct(format!(
                    concat!(
                        "expected ",
                        $name,
                        " to be ",
                        $type_name,
                        ", found {0:?}"
                    ),
                    v
                    $($format_arg)*
                ));
            }
        }
    };
}

macro_rules! as_int {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(Integer, $value, $name, "integer" $($format_arg)*)
    };
}

macro_rules! as_float {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(Float, $value, $name, "float" $($format_arg)*)
    };
}

macro_rules! as_string {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(String, $value, $name, "string" $($format_arg)*)
    };
}

macro_rules! as_map {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(Map, $value, $name, "map" $($format_arg)*)
    };
}

macro_rules! as_vec {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(List, $value, $name, "list" $($format_arg)*)
    };
}

macro_rules! as_node {
    ($value:expr, $name:literal $($format_arg:tt)*) => {
        value_as!(Node, $value, $name, "Node" $($format_arg)*)
    };
}

#[inline]
pub(super) fn invalid_struct(reason: impl Into<String>) -> ValueReceive {
    let reason = reason.into();
    ValueReceive::BrokenValue(BrokenValueInner::InvalidStruct { reason }.into())
}

#[inline]
pub(super) fn failed_struct(reason: impl Into<String>) -> ValueReceive {
    ValueReceive::BrokenValue(BrokenValueInner::Reason(reason.into()).into())
}

// [bolt-version-bump] search tag when changing bolt version support
#[derive(Debug, Copy, Clone)]
pub(super) enum ServerAwareBoltVersion {
    V4x4,
    V5x0,
    V5x1,
    V5x2,
    V5x3,
    V5x4,
    V5x6,
    V5x7,
    V5x8,
}

impl ServerAwareBoltVersion {
    #[inline]
    fn protocol_version(&self) -> &'static str {
        match self {
            Self::V4x4 => "4.4",
            Self::V5x0 => "5.0",
            Self::V5x1 => "5.1",
            Self::V5x2 => "5.2",
            Self::V5x3 => "5.3",
            Self::V5x4 => "5.4",
            Self::V5x6 => "5.6",
            Self::V5x7 => "5.7",
            Self::V5x8 => "5.8",
        }
    }

    #[inline]
    fn min_server_version(&self) -> &'static str {
        match self {
            Self::V4x4 => "4.4",
            Self::V5x0 => "5.0",
            Self::V5x1 => "5.5",
            Self::V5x2 => "5.7",
            Self::V5x3 => "5.9",
            Self::V5x4 => "5.13",
            Self::V5x6 => "5.23",
            Self::V5x7 | Self::V5x8 => "5.26",
        }
    }
}

#[inline]
pub(super) fn unsupported_protocol_feature_error(
    name: &str,
    current_version: ServerAwareBoltVersion,
    needed_version: ServerAwareBoltVersion,
) -> Neo4jError {
    Neo4jError::InvalidConfig {
        message: format!(
            "{name} is not supported via bolt version {}, requires at least server version {}",
            current_version.protocol_version(),
            needed_version.min_server_version(),
        ),
    }
}
