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

// imports for docs
#[allow(unused)]
use super::super::session::config::SessionConfig;
#[allow(unused)]
use super::super::ExecuteQueryBuilder;
#[allow(unused)]
use super::DriverConfig;
#[allow(unused)]
use crate::Neo4jError;

/// Defines which notifications the server should send to the client.
///
/// The filter contains two parts:
///  * `minimum_severity`:
///    Request the server to only send notifications of this severity or higher.
///  * `disabled_classifications`/`disabled_categories`:
///    Request the server to not send notifications of these categories/classifications.
///    "categories" is the legacy name for "classifications".
///    Otherwise, they're the same thing.
///    They're both tracked together.
///    I.e., changing one will change the other.
///
/// See [`DriverConfig::with_notification_filter()`], [`SessionConfig::with_notification_filter()`],
/// and [`ExecuteQueryBuilder::with_notification_filter()`].
#[derive(Debug, Clone, Eq, PartialEq, Default)]
pub struct NotificationFilter {
    /// Request the server to only send notifications of this severity or higher.
    ///
    /// Set to [`None`] to leave it up to the server to choose a default.
    pub(crate) minimum_severity: Option<MinimumSeverity>,
    /// Request the server to not send notifications of these categories.
    ///
    /// Set to [`None`] to leave it up to the server to choose a default.
    /// Set to an empty [`Vec`] to make sure no categories are disabled.
    pub(crate) disabled_classifications: Option<Vec<DisabledClassification>>,
}

impl NotificationFilter {
    /// Create a new [`NotificationFilter`] with default values.
    ///
    /// This is equivalent to [`Default::default()`]:
    ///  * leave it up to the server to choose a `minimum_severity`.
    ///  * leave it up to the server to choose `disabled_categories`/`disabled_classifications`.
    pub fn new() -> Self {
        Self::default()
    }

    /// Create a new [`NotificationFilter`] which requests the server to not send any notifications.
    pub fn new_disable_all() -> Self {
        Self {
            minimum_severity: Some(MinimumSeverity::Disabled),
            disabled_classifications: None,
        }
    }

    /// Request the server to only send notifications of this severity or higher.
    #[inline]
    pub fn with_minimum_severity(mut self, minimum_severity: MinimumSeverity) -> Self {
        self.minimum_severity = Some(minimum_severity);
        self
    }

    /// Leave it up to the server to choose a `minimum_severity`.
    ///
    /// See also [`with_minimum_severity()`](`Self::with_minimum_severity()`).
    #[inline]
    pub fn with_default_minimum_severity(mut self) -> Self {
        self.minimum_severity = None;
        self
    }

    /// Request the server to not send notifications of these classifications.
    #[inline]
    pub fn with_disabled_classifications(
        mut self,
        disabled_classifications: Vec<DisabledClassification>,
    ) -> Self {
        self.disabled_classifications = Some(disabled_classifications);
        self
    }

    /// Request the server to not send notifications of these classifications.
    ///
    /// This is the same as
    /// [`with_disabled_classifications()`](`Self::with_disabled_classifications()`).
    #[inline]
    pub fn with_disabled_categories(self, disabled_categories: Vec<DisabledCategory>) -> Self {
        self.with_disabled_classifications(disabled_categories)
    }

    /// Leave it up to the server to choose which classifications to disable.
    #[inline]
    pub fn with_default_disabled_classifications(mut self) -> Self {
        self.disabled_classifications = None;
        self
    }

    /// Leave it up to the server to choose which categories to disable.
    ///
    /// This is the same as
    /// [`with_default_disabled_classifications()`](`Self::with_default_disabled_classifications()`).
    #[inline]
    pub fn with_default_disabled_categories(self) -> Self {
        self.with_default_disabled_classifications()
    }

    pub(crate) fn is_default(&self) -> bool {
        self.minimum_severity.is_none() && self.disabled_classifications.is_none()
    }
}

/// See [`NotificationFilter`].
#[derive(Debug, Copy, Clone, Eq, PartialEq)]
#[non_exhaustive]
pub enum MinimumSeverity {
    /// Disable all notifications
    Disabled,
    Warning,
    Information,
}

impl MinimumSeverity {
    pub(crate) fn as_protocol_str(&self) -> &'static str {
        match self {
            Self::Disabled => "OFF",
            Self::Warning => "WARNING",
            Self::Information => "INFORMATION",
        }
    }
}

/// See [`NotificationFilter`].
#[derive(Debug, Copy, Clone, Eq, PartialEq)]
#[non_exhaustive]
pub enum DisabledCategory {
    Hint,
    Unrecognized,
    Unsupported,
    Performance,
    Deprecation,
    Generic,
    Security,
    /// This requires Neo4j 5.13 or newer.
    /// For older versions, this will result in a [`Neo4jError::ServerError`].
    Topology,
    /// This requires Neo4j 5.17 or newer.
    /// For older versions, this will result in a [`Neo4jError::ServerError`].
    Schema,
}

/// See [`NotificationFilter`].
pub type DisabledClassification = DisabledCategory;

impl DisabledCategory {
    pub(crate) fn as_protocol_str(&self) -> &'static str {
        match self {
            Self::Hint => "HINT",
            Self::Unrecognized => "UNRECOGNIZED",
            Self::Unsupported => "UNSUPPORTED",
            Self::Performance => "PERFORMANCE",
            Self::Deprecation => "DEPRECATION",
            Self::Generic => "GENERIC",
            Self::Security => "SECURITY",
            Self::Topology => "TOPOLOGY",
            Self::Schema => "SCHEMA",
        }
    }
}
