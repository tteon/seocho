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

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use super::io::bolt::BoltMeta;
use super::io::PooledBolt;
use crate::address_::Address;
use crate::error_::{Neo4jError, Result};
use crate::value::ValueReceive;

// Imports for docs
#[allow(unused)]
use super::eager_result::EagerResult;
#[allow(unused)]
use super::notification::NotificationFilter;
#[allow(unused)]
use super::record_stream::RecordStream;
#[allow(unused)]
use super::transaction::TransactionRecordStream;

/// Root struct containing query metadata.
///
/// Can be obtained from [`EagerResult::summary`], [`RecordStream::consume()`], or
/// [`TransactionRecordStream::consume()`].
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct Summary {
    pub result_available_after: Option<Duration>,
    pub result_consumed_after: Option<Duration>,
    pub counters: Counters,
    pub notifications: Vec<Notification>,
    pub gql_status_objects: Vec<GqlStatusObject>,
    pub profile: Option<Profile>,
    pub plan: Option<Plan>,
    pub query_type: Option<SummaryQueryType>,
    pub database: Option<String>,
    pub server_info: ServerInfo,
    had_record: bool,
    had_key: bool,
}

impl Summary {
    pub(crate) fn new(connection: &PooledBolt) -> Self {
        Self {
            result_available_after: Default::default(),
            result_consumed_after: Default::default(),
            counters: Default::default(),
            notifications: Default::default(),
            profile: Default::default(),
            plan: Default::default(),
            query_type: Default::default(),
            database: Default::default(),
            server_info: ServerInfo::new(connection),
            gql_status_objects: Default::default(),
            had_record: false,
            had_key: false,
        }
    }

    pub(crate) fn load_run_meta(&mut self, meta: &mut BoltMeta, had_key: bool) -> Result<()> {
        self.result_available_after = meta
            .remove("t_first")
            .map(|t_first| {
                let ValueReceive::Integer(t_first) = t_first else {
                    return Err(Neo4jError::protocol_error(format!(
                        "t_first in summary was not integer but {t_first:?}"
                    )));
                };
                Ok(Duration::from_millis(t_first.try_into().map_err(|e| {
                    Neo4jError::protocol_error(format!(
                        "t_first ({t_first}) in summary was not i64: {e}"
                    ))
                })?))
            })
            .transpose()?;
        self.had_key = had_key;
        Ok(())
    }
    pub(crate) fn load_pull_meta(&mut self, meta: &mut BoltMeta, had_record: bool) -> Result<()> {
        self.had_record = had_record;
        self.result_consumed_after = meta
            .remove("t_last")
            .map(|t_last| {
                let ValueReceive::Integer(t_last) = t_last else {
                    return Err(Neo4jError::protocol_error(format!(
                        "t_last in summary was not integer but {t_last:?}"
                    )));
                };
                Ok(Duration::from_millis(t_last.try_into().map_err(|e| {
                    Neo4jError::protocol_error(format!(
                        "t_last ({t_last}) in summary was not i64: {e}"
                    ))
                })?))
            })
            .transpose()?;
        self.counters = Counters::load_meta(meta)?;
        (self.notifications, self.gql_status_objects) =
            statuses_load_meta(meta, self.had_key, self.had_record)?;
        self.profile = Profile::load_meta(meta)?;
        self.query_type = SummaryQueryType::load_meta(meta)?;
        self.plan = Plan::load_meta(meta)?;
        if let Some(db) = meta.remove("db") {
            let ValueReceive::String(db) = db else {
                return Err(Neo4jError::protocol_error(format!(
                    "db in summary was not string but {:?}",
                    db
                )));
            };
            self.database = Some(db);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Default)]
#[non_exhaustive]
pub struct Counters {
    pub nodes_created: i64,
    pub nodes_deleted: i64,
    pub relationships_created: i64,
    pub relationships_deleted: i64,
    pub properties_set: i64,
    pub labels_added: i64,
    pub labels_removed: i64,
    pub indexes_added: i64,
    pub indexes_removed: i64,
    pub constraints_added: i64,
    pub constraints_removed: i64,
    pub system_updates: i64,
    pub contains_updates: bool,
    pub contains_system_updates: bool,
}

impl Counters {
    fn load_meta(meta: &mut BoltMeta) -> Result<Self> {
        let Some(meta) = meta.remove("stats") else {
            return Ok(Counters {
                nodes_created: 0,
                nodes_deleted: 0,
                relationships_created: 0,
                relationships_deleted: 0,
                properties_set: 0,
                labels_added: 0,
                labels_removed: 0,
                indexes_added: 0,
                indexes_removed: 0,
                constraints_added: 0,
                constraints_removed: 0,
                system_updates: 0,
                contains_updates: false,
                contains_system_updates: false,
            });
        };
        let mut meta = try_into_map(meta, "stats")?;
        let nodes_created = meta
            .remove("nodes-created")
            .map(|c| try_into_int(c, "nodes-created in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let nodes_deleted = meta
            .remove("nodes-deleted")
            .map(|c| try_into_int(c, "nodes-deleted in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let relationships_created = meta
            .remove("relationships-created")
            .map(|c| try_into_int(c, "relationships-created in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let relationships_deleted = meta
            .remove("relationships-deleted")
            .map(|c| try_into_int(c, "relationships-deleted in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let properties_set = meta
            .remove("properties-set")
            .map(|c| try_into_int(c, "properties-set in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let labels_added = meta
            .remove("labels-added")
            .map(|c| try_into_int(c, "labels-added in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let labels_removed = meta
            .remove("labels-removed")
            .map(|c| try_into_int(c, "labels-removed in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let indexes_added = meta
            .remove("indexes-added")
            .map(|c| try_into_int(c, "indexes-added in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let indexes_removed = meta
            .remove("indexes-removed")
            .map(|c| try_into_int(c, "indexes-removed in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let constraints_added = meta
            .remove("constraints-added")
            .map(|c| try_into_int(c, "constraints-added in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let constraints_removed = meta
            .remove("constraints-removed")
            .map(|c| try_into_int(c, "constraints-removed in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let system_updates = meta
            .remove("system-updates")
            .map(|c| try_into_int(c, "system-updates in stats"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let contains_updates = meta
            .remove("contains-updates")
            .map(|c| try_into_bool(c, "contains-updates in stats"))
            .unwrap_or_else(|| {
                Ok(nodes_created > 0
                    || nodes_deleted > 0
                    || relationships_created > 0
                    || relationships_deleted > 0
                    || properties_set > 0
                    || labels_added > 0
                    || labels_removed > 0
                    || indexes_added > 0
                    || indexes_removed > 0
                    || constraints_added > 0
                    || constraints_removed > 0)
            })?;
        let contains_system_updates = meta
            .remove("contains-system-updates")
            .map(|c| try_into_bool(c, "contains-system-updates in stats"))
            .unwrap_or_else(|| Ok(system_updates > 0))?;
        Ok(Self {
            nodes_created,
            nodes_deleted,
            relationships_created,
            relationships_deleted,
            properties_set,
            labels_added,
            labels_removed,
            indexes_added,
            indexes_removed,
            constraints_added,
            constraints_removed,
            system_updates,
            contains_updates,
            contains_system_updates,
        })
    }
}

#[derive(Debug, Clone, Default)]
#[non_exhaustive]
pub enum SummaryQueryType {
    #[default]
    Read,
    Write,
    ReadWrite,
    Schema,
}

impl SummaryQueryType {
    pub(crate) fn load_meta(meta: &mut BoltMeta) -> Result<Option<Self>> {
        if let Some(query_type) = meta.remove("type") {
            let ValueReceive::String(query_type) = query_type else {
                return Err(Neo4jError::protocol_error(format!(
                    "type in summary was not string but {:?}",
                    query_type
                )));
            };
            return Ok(Some(match query_type.as_str() {
                "r" => Self::Read,
                "w" => Self::Write,
                "rw" => Self::ReadWrite,
                "s" => Self::Schema,
                _ => {
                    return Err(Neo4jError::protocol_error(format!(
                        "query type in summary was an unknown string {:?}",
                        query_type
                    )))
                }
            }));
        }
        Ok(None)
    }
}

/// See [`Summary::notifications`].
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct Notification {
    pub description: String,
    pub code: String,
    pub title: String,
    pub position: Option<Position>,
    pub severity: Severity,
    pub raw_severity: String,
    pub category: Category,
    pub raw_category: String,
}

fn statuses_load_meta(
    meta: &mut BoltMeta,
    had_key: bool,
    had_record: bool,
) -> Result<(Vec<Notification>, Vec<GqlStatusObject>)> {
    let Some(raw_statuses) = meta.remove("statuses") else {
        return statuses_load_meta_legacy(meta, had_key, had_record);
    };
    let raw_statuses = try_into_list(raw_statuses, "statuses")?;
    let mut notifications = Vec::with_capacity(raw_statuses.len());
    let mut gql_status_objects = Vec::with_capacity(raw_statuses.len());
    for raw_status in raw_statuses.into_iter() {
        let mut raw_status = try_into_map(raw_status, "status")?;
        let (notification, gql_status_object) = status_load_single(&mut raw_status)?;
        if let Some(notification) = notification {
            notifications.push(notification);
        }
        gql_status_objects.push(gql_status_object);
    }
    Ok((notifications, gql_status_objects))
}

fn statuses_load_meta_legacy(
    meta: &mut BoltMeta,
    had_key: bool,
    had_record: bool,
) -> Result<(Vec<Notification>, Vec<GqlStatusObject>)> {
    let raw_notifications = meta
        .remove("notifications")
        .unwrap_or_else(|| ValueReceive::List(Default::default()));
    let raw_entries = try_into_list(raw_notifications, "notifications")?;
    let mut notifications = Vec::with_capacity(raw_entries.len());
    let mut gql_status_objects = Vec::with_capacity(notifications.len());
    for raw_entry in raw_entries.into_iter() {
        let mut raw_notification = try_into_map(raw_entry, "notification")?;
        let (notification, gql_status_object) = status_load_single_legacy(&mut raw_notification)?;
        notifications.push(notification);
        gql_status_objects.push(gql_status_object);
    }
    if had_record {
        gql_status_objects.push(GqlStatusObject::new_success())
    } else if had_key {
        gql_status_objects.push(GqlStatusObject::new_no_data())
    } else {
        gql_status_objects.push(GqlStatusObject::new_omitted_result())
    }
    gql_status_objects.sort_by_key(|status| {
        if status.gql_status.starts_with("02") {
            // no data
            return -3;
        }
        if status.gql_status.starts_with("01") {
            // warning
            return -2;
        }
        if status.gql_status.starts_with("00") {
            // success
            return -1;
        }
        if status.gql_status.starts_with("03") {
            // informational
            return 0;
        }
        1
    });
    Ok((notifications, gql_status_objects))
}

fn status_load_single(meta: &mut BoltMeta) -> Result<(Option<Notification>, GqlStatusObject)> {
    let gql_status = meta
        .remove("gql_status")
        .map(|c| try_into_string(c, "gql_status in status"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let neo4j_code = meta
        .remove("neo4j_code")
        .map(|c| try_into_string(c, "neo4j_code in status"))
        .transpose()?;
    let description = meta
        .remove("description")
        .map(|c| try_into_string(c, "description in status"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let status_description = meta
        .remove("status_description")
        .map(|c| try_into_string(c, "status_description in status"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let title = meta
        .remove("title")
        .map(|c| try_into_string(c, "title in status"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let diagnostic_record = meta
        .remove("diagnostic_record")
        .map(|c| try_into_map(c, "diagnostic_record in status"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let position = diagnostic_record
        .get("_position")
        .and_then(ValueReceive::as_map)
        .cloned()
        .and_then(|mut c| Position::load_meta(&mut c).ok());
    let raw_severity = diagnostic_record
        .get("_severity")
        .and_then(ValueReceive::as_string);
    let has_severity = raw_severity.is_some();
    let raw_severity = raw_severity.cloned().unwrap_or_default();
    let severity = Severity::from_str(&raw_severity);
    let raw_classification = diagnostic_record
        .get("_classification")
        .and_then(ValueReceive::as_string);
    let has_classification = raw_classification.is_some();
    let raw_classification = raw_classification.cloned().unwrap_or_default();
    let classification = Classification::from_str(&raw_classification);

    let notification = neo4j_code.map(|neo4j_code| Notification {
        description: description.clone(),
        code: neo4j_code,
        title: title.clone(),
        position,
        severity,
        raw_severity: raw_severity.clone(),
        category: classification,
        raw_category: raw_classification.clone(),
    });
    let is_notification = notification.is_some();

    Ok((
        notification,
        GqlStatusObject {
            is_notification,
            gql_status,
            status_description,
            position,
            raw_classification: match has_classification {
                true => Some(raw_classification),
                false => None,
            },
            classification,
            raw_severity: match has_severity {
                true => Some(raw_severity),
                false => None,
            },
            severity,
            diagnostic_record,
        },
    ))
}

fn status_load_single_legacy(meta: &mut BoltMeta) -> Result<(Notification, GqlStatusObject)> {
    let description = meta
        .remove("description")
        .map(|c| try_into_string(c, "description in notification"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let code = meta
        .remove("code")
        .map(|c| try_into_string(c, "code in notification"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let title = meta
        .remove("title")
        .map(|c| try_into_string(c, "title in notification"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let raw_position = meta
        .remove("position")
        .map(|c| try_into_map(c, "position in notification"))
        .transpose()?;
    let position = raw_position
        .clone()
        .map(|mut meta| Position::load_meta(&mut meta).map(Some))
        .unwrap_or_else(|| Ok(None))?;
    let raw_severity = meta.remove("severity");
    let has_severity = raw_severity.is_some();
    let raw_severity = raw_severity
        .map(|c| try_into_string(c, "severity in notification"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let severity = Severity::from_str(&raw_severity);
    let raw_category = meta.remove("category");
    let has_category = raw_category.is_some();
    let raw_category = raw_category
        .map(|c| try_into_string(c, "category in notification"))
        .unwrap_or_else(|| Ok(Default::default()))?;
    let category = Category::from_str(&raw_category);
    let status = GqlStatusObject {
        is_notification: true,
        gql_status: String::from(match severity {
            Severity::Warning => "01N42",
            _ => "03N42",
        }),
        status_description: match description.is_empty() {
            true => String::from(match severity {
                Severity::Warning => "warn: unknown warning",
                _ => "info: unknown notification",
            }),
            false => description.clone(),
        },
        position,
        raw_classification: match has_category {
            true => Some(raw_category.clone()),
            false => None,
        },
        classification: category,
        raw_severity: match has_severity {
            true => Some(raw_severity.clone()),
            false => None,
        },
        severity,
        diagnostic_record: {
            let mut diagnostic_record = HashMap::with_capacity(6);
            diagnostic_record.insert(
                String::from("OPERATION"),
                ValueReceive::String(String::from("")),
            );
            diagnostic_record.insert(
                String::from("OPERATION_CODE"),
                ValueReceive::String(String::from("0")),
            );
            diagnostic_record.insert(
                String::from("CURRENT_SCHEMA"),
                ValueReceive::String(String::from("/")),
            );
            if has_category {
                diagnostic_record.insert(
                    String::from("_classification"),
                    ValueReceive::String(raw_category.clone()),
                );
            }
            if has_severity {
                diagnostic_record.insert(
                    String::from("_severity"),
                    ValueReceive::String(raw_severity.clone()),
                );
            }
            if let Some(raw_position) = raw_position {
                diagnostic_record
                    .insert(String::from("_position"), ValueReceive::Map(raw_position));
            }
            diagnostic_record
        },
    };
    Ok((
        Notification {
            description,
            code,
            title,
            position,
            severity,
            raw_severity,
            category,
            raw_category,
        },
        status,
    ))
}

/// See [`Summary::gql_status_objects`].
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct GqlStatusObject {
    /// Whether this GqlStatusObject is a notification.
    ///
    /// Only some [`GqlStatusObject`]s are notifications.
    /// The definition of notification is vendor-specific.
    /// Notifications are those [`GqlStatusObject`]s that provide additional information and can be
    /// filtered out via [`NotificationFilter`].
    ///
    /// The fields [`position`](`Self::position`),
    /// [`raw_classification`](`Self::raw_classification`),
    /// [`classification`](`Self::classification`),
    /// [`raw_severity`](`Self::raw_severity`), and
    /// [`severity`](`Self::severity`) are only meaningful for notifications.
    pub is_notification: bool,
    /// The GQLSTATUS.
    ///
    /// The following GQLSTATUS codes denote codes that the driver will use for polyfilling (when
    /// connected to an old, non-GQL-aware server).
    /// Further, they may be used by servers during the transition-phase to GQLSTATUS-awareness.
    ///
    ///  * `01N42` (warning - unknown warning)
    ///  * `02N42` (no data - unknown subcondition)
    ///  * `03N42` (informational - unknown notification)
    ///  * `05N42` (general processing exception - unknown error)
    ///
    /// <div class="warning">
    ///
    /// This means these codes are not guaranteed to be stable and may change in future versions of
    /// the driver or the server.
    ///
    /// </div>
    pub gql_status: String,
    /// A description of the status.
    pub status_description: String,
    /// The position of the input that caused the status (if applicable).
    ///
    /// This is vendor-specific information.
    ///
    /// Only notifications (see [`is_notification`](`Self::is_notification`)) have a meaningful
    /// position.
    ///
    /// The value is [`None`] if the server’s data was missing.
    pub position: Option<Position>,
    /// The raw (string) classification of the status.
    ///
    /// This is a vendor-specific classification that can be used to filter notifications.
    ///
    /// Only notifications (see [`is_notification`](`Self::is_notification`)) have a meaningful
    /// classification.
    pub raw_classification: Option<String>,
    /// Parsed version of [`raw_classification`](`Self::raw_classification`).
    ///
    /// Only notifications (see [`is_notification`](`Self::is_notification`)) have a meaningful
    /// classification.
    pub classification: Classification,
    /// The raw (string) severity of the status.
    ///
    /// This is a vendor-specific severity that can be used to filter notifications.
    ///
    /// Only notifications (see [`is_notification`](`Self::is_notification`)) have a meaningful
    /// severity.
    pub raw_severity: Option<String>,
    /// Parsed version of [`raw_severity`](`Self::raw_severity`).
    ///
    /// Only notifications (see [`is_notification`](`Self::is_notification`)) have a meaningful
    pub severity: Severity,
    /// Further information about the GQLSTATUS for diagnostic purposes.
    pub diagnostic_record: HashMap<String, ValueReceive>,
}

impl GqlStatusObject {
    fn new_success() -> Self {
        Self {
            is_notification: false,
            gql_status: String::from("00000"),
            status_description: String::from("note: successful completion"),
            position: None,
            raw_classification: None,
            classification: Category::Unknown,
            raw_severity: None,
            severity: Severity::Unknown,
            diagnostic_record: Self::default_diagnostic_record(),
        }
    }

    fn new_no_data() -> Self {
        Self {
            is_notification: false,
            gql_status: String::from("02000"),
            status_description: String::from("note: no data"),
            position: None,
            raw_classification: None,
            classification: Category::Unknown,
            raw_severity: None,
            severity: Severity::Unknown,
            diagnostic_record: Self::default_diagnostic_record(),
        }
    }

    fn new_omitted_result() -> Self {
        Self {
            is_notification: false,
            gql_status: String::from("00001"),
            status_description: String::from("note: successful completion - omitted result"),
            position: None,
            raw_classification: None,
            classification: Category::Unknown,
            raw_severity: None,
            severity: Severity::Unknown,
            diagnostic_record: Self::default_diagnostic_record(),
        }
    }

    fn default_diagnostic_record() -> HashMap<String, ValueReceive> {
        let mut map = HashMap::with_capacity(3);
        map.insert(
            String::from("OPERATION"),
            ValueReceive::String(String::from("")),
        );
        map.insert(
            String::from("OPERATION_CODE"),
            ValueReceive::String(String::from("0")),
        );
        map.insert(
            String::from("CURRENT_SCHEMA"),
            ValueReceive::String(String::from("/")),
        );
        map
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Position {
    /// The column number referred to by the position; column numbers start at 1.
    pub column: i64,
    /// The character offset referred to by this position; offset numbers start at 0.
    pub offset: i64,
    /// The line number referred to by the position; line numbers start at 1.
    pub line: i64,
}

impl Position {
    fn load_meta(meta: &mut BoltMeta) -> Result<Self> {
        let column = meta
            .remove("column")
            .map(|c| try_into_int(c, "column in notification position"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let offset = meta
            .remove("offset")
            .map(|c| try_into_int(c, "column in notification position"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let line = meta
            .remove("line")
            .map(|c| try_into_int(c, "column in notification position"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        Ok(Self {
            column,
            offset,
            line,
        })
    }
}

/// See [`Notification::severity`].
#[derive(Debug, Copy, Clone, Eq, PartialEq)]
#[non_exhaustive]
pub enum Severity {
    Warning,
    Information,
    Unknown,
}

impl Severity {
    fn from_str(s: &str) -> Self {
        match s {
            "WARNING" => Self::Warning,
            "INFORMATION" => Self::Information,
            _ => Self::Unknown,
        }
    }
}

/// See [`Notification::category`].
#[derive(Debug, Copy, Clone, Eq, PartialEq)]
#[non_exhaustive]
pub enum Category {
    Hint,
    Unrecognized,
    Unsupported,
    Performance,
    Deprecation,
    Generic,
    Security,
    Topology,
    Schema,
    Unknown,
}

impl Category {
    fn from_str(s: &str) -> Self {
        match s {
            "HINT" => Self::Hint,
            "UNRECOGNIZED" => Self::Unrecognized,
            "UNSUPPORTED" => Self::Unsupported,
            "PERFORMANCE" => Self::Performance,
            "DEPRECATION" => Self::Deprecation,
            "GENERIC" => Self::Generic,
            "SECURITY" => Self::Security,
            "TOPOLOGY" => Self::Topology,
            "SCHEMA" => Self::Schema,
            _ => Self::Unknown,
        }
    }
}

/// Alias for [`Category`] to better match [`GqlStatusObject::classification`].
pub type Classification = Category;

#[derive(Debug)]
struct PlanProfileCommon {
    pub args: HashMap<String, ValueReceive>,
    pub op_type: String,
    pub identifiers: Vec<String>,
}

impl PlanProfileCommon {
    fn load_single(meta: &mut BoltMeta) -> Result<Self> {
        let args = meta
            .remove("args")
            .map(|v| try_into_map(v, "args in plan/profile"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let op_type = meta
            .remove("operatorType")
            .map(|v| try_into_string(v, "operatorType in plan/profile"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let identifiers = meta
            .remove("identifiers")
            .map(|v| {
                try_into_list(v, "identifiers in plan/profile")?
                    .into_iter()
                    .map(|e| try_into_string(e, "element in identifiers in plan/profile"))
                    .collect()
            })
            .unwrap_or_else(|| Ok(Default::default()))?;
        Ok(Self {
            args,
            op_type,
            identifiers,
        })
    }
}

#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct Plan {
    pub args: HashMap<String, ValueReceive>,
    pub op_type: String,
    pub identifiers: Vec<String>,
    pub children: Vec<Plan>,
}

impl Plan {
    fn load_meta(meta: &mut BoltMeta) -> Result<Option<Self>> {
        meta.remove("plan")
            .map(|v| {
                let mut v = try_into_map(v, "plan in summary")?;
                Ok(Some(Self::load_single(&mut v)?))
            })
            .unwrap_or(Ok(None))
    }

    fn load_single(meta: &mut BoltMeta) -> Result<Self> {
        let PlanProfileCommon {
            args,
            op_type,
            identifiers,
        } = PlanProfileCommon::load_single(meta)?;
        let children = meta
            .remove("children")
            .map(|v| {
                try_into_list(v, "children in plan")?
                    .into_iter()
                    .map(|v| {
                        Self::load_single(&mut try_into_map(v, "element in children in plan")?)
                    })
                    .collect()
            })
            .unwrap_or_else(|| Ok(Default::default()))?;
        Ok(Self {
            args,
            op_type,
            identifiers,
            children,
        })
    }
}

#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct Profile {
    pub args: HashMap<String, ValueReceive>,
    pub op_type: String,
    pub identifiers: Vec<String>,
    pub children: Vec<Profile>,
    pub db_hits: i64,
    pub rows: i64,

    /// If `false`, the following stats were not reported by the server and are set to some default
    /// value instead.
    pub has_page_cache_stats: bool,
    pub page_cache_hit_ratio: f64,
    pub page_cache_hits: i64,
    pub page_cache_misses: i64,
    pub time: i64,
}

impl Profile {
    fn load_meta(meta: &mut BoltMeta) -> Result<Option<Self>> {
        meta.remove("profile")
            .map(|v| {
                let mut v = try_into_map(v, "profile in summary")?;
                Ok(Some(Self::load_single(&mut v)?))
            })
            .unwrap_or(Ok(None))
    }

    fn load_single(meta: &mut BoltMeta) -> Result<Self> {
        let PlanProfileCommon {
            args,
            op_type,
            identifiers,
        } = PlanProfileCommon::load_single(meta)?;
        let children = meta
            .remove("children")
            .map(|v| {
                try_into_list(v, "children in profile")?
                    .into_iter()
                    .map(|v| {
                        Self::load_single(&mut try_into_map(v, "element in children in profile")?)
                    })
                    .collect()
            })
            .unwrap_or_else(|| Ok(Default::default()))?;
        let db_hits = meta
            .remove("dbHits")
            .map(|v| try_into_int(v, "dbHits in profile"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        let page_cache_hit_ratio = meta
            .remove("pageCacheHitRatio")
            .map(|v| try_into_float(v, "pageCacheHitRatio in profile").map(Some))
            .unwrap_or(Ok(None))?;
        let page_cache_hits = meta
            .remove("pageCacheHits")
            .map(|v| try_into_int(v, "pageCacheHits in profile").map(Some))
            .unwrap_or(Ok(None))?;
        let page_cache_misses = meta
            .remove("pageCacheMisses")
            .map(|v| try_into_int(v, "pageCacheMisses in profile").map(Some))
            .unwrap_or(Ok(None))?;
        let time = meta
            .remove("time")
            .map(|v| try_into_int(v, "time in profile").map(Some))
            .unwrap_or(Ok(None))?;
        let has_page_cache_stats = page_cache_hit_ratio.is_some()
            || page_cache_hits.is_some()
            || page_cache_misses.is_some()
            || time.is_some();
        let page_cache_hit_ratio = page_cache_hit_ratio.unwrap_or_default();
        let page_cache_hits = page_cache_hits.unwrap_or_default();
        let page_cache_misses = page_cache_misses.unwrap_or_default();
        let time = time.unwrap_or_default();
        let rows = meta
            .remove("rows")
            .map(|v| try_into_int(v, "rows in profile"))
            .unwrap_or_else(|| Ok(Default::default()))?;
        Ok(Self {
            args,
            op_type,
            identifiers,
            children,
            db_hits,
            rows,
            has_page_cache_stats,
            page_cache_hit_ratio,
            page_cache_hits,
            page_cache_misses,
            time,
        })
    }
}

#[derive(Debug, Clone)]
pub struct ServerInfo {
    pub address: Arc<Address>,
    pub server_agent: Arc<String>,
    pub protocol_version: (u8, u8),
}

impl ServerInfo {
    pub(crate) fn new(connection: &PooledBolt) -> Self {
        Self {
            address: connection.address(),
            server_agent: connection.server_agent(),
            protocol_version: connection.protocol_version(),
        }
    }
}

fn try_into_bool(v: ValueReceive, context: &str) -> Result<bool> {
    v.try_into_bool()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not bool but {:?}", context, v)))
}

fn try_into_int(v: ValueReceive, context: &str) -> Result<i64> {
    v.try_into_int()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not int but {:?}", context, v)))
}

fn try_into_float(v: ValueReceive, context: &str) -> Result<f64> {
    v.try_into_float()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not float but {:?}", context, v)))
}

fn try_into_string(v: ValueReceive, context: &str) -> Result<String> {
    v.try_into_string()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not string but {:?}", context, v)))
}

fn try_into_list(v: ValueReceive, context: &str) -> Result<Vec<ValueReceive>> {
    v.try_into_list()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not list but {:?}", context, v)))
}

fn try_into_map(v: ValueReceive, context: &str) -> Result<HashMap<String, ValueReceive>> {
    v.try_into_map()
        .map_err(|v| Neo4jError::protocol_error(format!("{} was not map but {:?}", context, v)))
}
