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

use std::io;

use crate::error_::Neo4jError;

#[derive(thiserror::Error, Debug)]
#[error("failed serialization: {reason}")]
pub struct PackStreamSerializeError {
    reason: String,
    // #[source]
    cause: Option<io::Error>,
}

impl From<String> for PackStreamSerializeError {
    fn from(reason: String) -> Self {
        Self {
            reason,
            cause: None,
        }
    }
}

impl From<&str> for PackStreamSerializeError {
    fn from(reason: &str) -> Self {
        String::from(reason).into()
    }
}

impl From<io::Error> for PackStreamSerializeError {
    fn from(err: io::Error) -> Self {
        let mut e: Self = format!("IO error while serializing: {}", err).into();
        e.cause = Some(err);
        e
    }
}

impl From<PackStreamSerializeError> for Neo4jError {
    fn from(err: PackStreamSerializeError) -> Self {
        match err.cause {
            None => Self::InvalidConfig {
                message: err.reason,
            },
            Some(cause) => Neo4jError::write_error(cause),
        }
    }
}

#[derive(thiserror::Error, Debug)]
#[error("failed deserialization: {reason}")]
pub struct PackStreamDeserializeError {
    reason: String,
    protocol_violation: bool,
    #[source]
    cause: Option<io::Error>,
}

impl PackStreamDeserializeError {
    pub(crate) fn protocol_violation(e: impl Into<Self>) -> Self {
        let mut e: Self = e.into();
        e.protocol_violation = true;
        e
    }
}

impl From<String> for PackStreamDeserializeError {
    fn from(reason: String) -> Self {
        Self {
            reason,
            cause: None,
            protocol_violation: false,
        }
    }
}

impl From<&str> for PackStreamDeserializeError {
    fn from(reason: &str) -> Self {
        String::from(reason).into()
    }
}

impl From<io::Error> for PackStreamDeserializeError {
    fn from(err: io::Error) -> Self {
        let mut e: Self = format!("IO error while deserializing: {}", err).into();
        e.cause = Some(err);
        e
    }
}

impl From<PackStreamDeserializeError> for Neo4jError {
    fn from(err: PackStreamDeserializeError) -> Self {
        match err.cause {
            None => {
                if err.protocol_violation {
                    Self::protocol_error(err.reason)
                } else {
                    Self::InvalidConfig {
                        message: err.reason,
                    }
                }
            }
            Some(cause) => Neo4jError::read_err(cause),
        }
    }
}
