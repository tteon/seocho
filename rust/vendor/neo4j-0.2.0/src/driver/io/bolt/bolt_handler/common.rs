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

use std::borrow::Borrow;
use std::collections::HashMap;
use std::fmt::Debug;
use std::io::{Read, Write};
use std::sync::Arc;

use usize_cast::FromUsize;

use super::super::bolt_common::{unsupported_protocol_feature_error, ServerAwareBoltVersion};
use super::super::packstream::{
    PackStreamSerializer, PackStreamSerializerDebugImpl, PackStreamSerializerImpl,
};
use super::super::response::ResponseCallbacks;
use super::super::{debug_buf, BoltData, BoltMeta, BoltStructTranslator};
use crate::bookmarks::Bookmarks;
use crate::driver::auth::AuthToken;
use crate::driver::config::notification::NotificationFilter;
use crate::error_::{Neo4jError, Result};
use crate::value::{ValueReceive, ValueSend};

pub(super) fn assert_response_field_count<T>(
    name: &str,
    fields: &[T],
    expected_count: usize,
) -> Result<()> {
    if fields.len() == expected_count {
        Ok(())
    } else {
        Err(Neo4jError::protocol_error(format!(
            "{} response should have {} field(s) but found {:?}",
            name,
            expected_count,
            fields.len()
        )))
    }
}

pub(super) fn check_no_notification_filter<T: Read + Write>(
    data: &BoltData<T>,
    notification_filter: Option<&NotificationFilter>,
) -> Result<()> {
    if !notification_filter.map(|n| n.is_default()).unwrap_or(true) {
        return Err(unsupported_protocol_feature_error(
            "notification filtering",
            data.protocol_version,
            ServerAwareBoltVersion::V5x2,
        ));
    }
    Ok(())
}

pub(in super::super) fn write_str_entry(
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    key: &str,
    value: &str,
) -> Result<()> {
    serializer.write_string(key)?;
    serializer.write_string(value)?;
    debug_buf!(log_buf, "{}", {
        dbg_serializer.write_string(key).unwrap();
        dbg_serializer.write_string(value).unwrap();
        dbg_serializer.flush()
    });
    Ok(())
}

pub(in super::super) fn write_int_entry(
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    key: &str,
    value: i64,
) -> Result<()> {
    serializer.write_string(key)?;
    serializer.write_int(value)?;
    debug_buf!(log_buf, "{}", {
        dbg_serializer.write_string(key).unwrap();
        dbg_serializer.write_int(value).unwrap();
        dbg_serializer.flush()
    });
    Ok(())
}

pub(in super::super) fn write_dict_entry(
    translator: &impl BoltStructTranslator,
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    key: &str,
    value: &HashMap<impl Borrow<str> + Debug, ValueSend>,
) -> Result<()> {
    serializer.write_string(key)?;
    data.serialize_dict(serializer, translator, value)?;
    debug_buf!(log_buf, "{}", {
        dbg_serializer.write_string(key).unwrap();
        data.serialize_dict(dbg_serializer, translator, value)
            .unwrap();
        dbg_serializer.flush()
    });
    Ok(())
}

pub(in super::super) fn write_parameter_dict(
    translator: &impl BoltStructTranslator,
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    parameters: Option<&HashMap<impl Borrow<str> + Debug, ValueSend>>,
) -> Result<()> {
    match parameters {
        Some(parameters) => {
            data.serialize_dict(serializer, translator, parameters)?;
            debug_buf!(log_buf, " {}", {
                data.serialize_dict(dbg_serializer, translator, parameters)
                    .unwrap();
                dbg_serializer.flush()
            });
        }
        None => {
            serializer.write_dict_header(0)?;
            debug_buf!(log_buf, " {}", {
                dbg_serializer.write_dict_header(0).unwrap();
                dbg_serializer.flush()
            });
        }
    }
    Ok(())
}

pub(super) fn write_tx_timeout_entry(
    log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    tx_timeout: Option<i64>,
) -> Result<()> {
    if let Some(tx_timeout) = tx_timeout {
        write_int_entry(
            log_buf,
            serializer,
            dbg_serializer,
            "tx_timeout",
            tx_timeout,
        )?;
    }
    Ok(())
}

pub(super) fn write_tx_metadata_entry(
    translator: &impl BoltStructTranslator,
    log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    tx_metadata: Option<&HashMap<impl Borrow<str> + Debug, ValueSend>>,
) -> Result<()> {
    if let Some(tx_metadata) = tx_metadata {
        if !tx_metadata.is_empty() {
            write_dict_entry(
                translator,
                log_buf,
                serializer,
                dbg_serializer,
                data,
                "tx_metadata",
                tx_metadata,
            )?;
        }
    }
    Ok(())
}

pub(super) fn write_mode_entry(
    log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    mode: Option<&str>,
) -> Result<()> {
    if let Some(mode) = mode {
        if mode != "w" {
            write_str_entry(log_buf, serializer, dbg_serializer, "mode", mode)?;
        }
    }
    Ok(())
}

pub(super) fn write_db_entry(
    log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    db: Option<&str>,
) -> Result<()> {
    if let Some(db) = db {
        write_str_entry(log_buf, serializer, dbg_serializer, "db", db)?;
    }
    Ok(())
}

pub(super) fn write_bookmarks_entry(
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    bookmarks: Option<&Bookmarks>,
) -> Result<()> {
    if let Some(bookmarks) = bookmarks {
        if !bookmarks.is_empty() {
            serializer.write_string("bookmarks")?;
            data.serialize_str_iter(serializer, bookmarks.raw())?;
            debug_buf!(log_buf, "{}", {
                dbg_serializer.write_string("bookmarks").unwrap();
                data.serialize_str_iter(dbg_serializer, bookmarks.raw())
                    .unwrap();
                dbg_serializer.flush()
            });
        }
    }
    Ok(())
}

pub(super) fn write_bookmarks_list(
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    bookmarks: Option<&Bookmarks>,
) -> Result<()> {
    match bookmarks {
        None => {
            debug_buf!(log_buf, " {}", {
                dbg_serializer.write_list_header(0).unwrap();
                dbg_serializer.flush()
            });
            serializer.write_list_header(0)?;
        }
        Some(bms) => {
            debug_buf!(log_buf, " {}", {
                data.serialize_str_iter(dbg_serializer, bms.raw()).unwrap();
                dbg_serializer.flush()
            });
            data.serialize_str_iter(serializer, bms.raw())?;
        }
    }
    Ok(())
}

pub(super) fn write_imp_user_entry(
    log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    imp_user: Option<&str>,
) -> Result<()> {
    if let Some(imp_user) = imp_user {
        write_str_entry(log_buf, serializer, dbg_serializer, "imp_user", imp_user)?;
    }
    Ok(())
}

pub(super) fn write_auth_dict(
    translator: &impl BoltStructTranslator,
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    auth: &Arc<AuthToken>,
) -> Result<()> {
    let auth_size = u64::from_usize(auth.data.len());
    serializer.write_dict_header(auth_size)?;
    debug_buf!(log_buf, " {}", {
        dbg_serializer.write_dict_header(auth_size).unwrap();
        dbg_serializer.flush()
    });
    write_auth_entries(translator, log_buf, serializer, dbg_serializer, data, auth)?;
    Ok(())
}

pub(super) fn write_auth_entries(
    translator: &impl BoltStructTranslator,
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    data: &BoltData<impl Read + Write>,
    auth: &Arc<AuthToken>,
) -> Result<()> {
    for (k, v) in &auth.data {
        serializer.write_string(k)?;
        data.serialize_value(serializer, translator, v)?;
        debug_buf!(log_buf, "{}", {
            dbg_serializer.write_string(k).unwrap();
            if k == "credentials" {
                dbg_serializer.write_string("**********").unwrap();
            } else {
                data.serialize_value(dbg_serializer, translator, v).unwrap();
            }
            dbg_serializer.flush()
        });
    }
    Ok(())
}

pub(super) fn notification_filter_entries_count(
    notification_filter: Option<&NotificationFilter>,
) -> u64 {
    match notification_filter {
        None => 0,
        Some(NotificationFilter {
            minimum_severity,
            disabled_classifications: disabled_categories,
        }) => [minimum_severity.is_some(), !disabled_categories.is_none()]
            .into_iter()
            .map(<bool as Into<u64>>::into)
            .sum(),
    }
}

pub(super) fn write_notification_filter_entries(
    mut log_buf: Option<&mut String>,
    serializer: &mut PackStreamSerializerImpl<impl Write>,
    dbg_serializer: &mut PackStreamSerializerDebugImpl,
    notification_filter: Option<&NotificationFilter>,
) -> Result<()> {
    let Some(notification_filter) = notification_filter else {
        return Ok(());
    };
    let NotificationFilter {
        minimum_severity,
        disabled_classifications: disabled_categories,
    } = notification_filter;
    if let Some(minimum_severity) = minimum_severity {
        serializer.write_string("notifications_minimum_severity")?;
        serializer.write_string(minimum_severity.as_protocol_str())?;
        debug_buf!(log_buf, "{}", {
            dbg_serializer
                .write_string("notifications_minimum_severity")
                .unwrap();
            dbg_serializer
                .write_string(minimum_severity.as_protocol_str())
                .unwrap();
            dbg_serializer.flush()
        });
    }
    if let Some(disabled_categories) = disabled_categories {
        serializer.write_string("notifications_disabled_categories")?;
        serializer.write_list_header(u64::from_usize(disabled_categories.len()))?;
        for category in disabled_categories {
            serializer.write_string(category.as_protocol_str())?;
        }
        debug_buf!(log_buf, "{}", {
            dbg_serializer
                .write_string("notifications_disabled_categories")
                .unwrap();
            dbg_serializer
                .write_list_header(u64::from_usize(disabled_categories.len()))
                .unwrap();
            for category in disabled_categories {
                dbg_serializer
                    .write_string(category.as_protocol_str())
                    .unwrap();
            }
            dbg_serializer.flush()
        });
    }
    Ok(())
}

pub(super) fn make_enrich_diag_record_callback(callbacks: ResponseCallbacks) -> ResponseCallbacks {
    callbacks.with_on_success_pre_hook(|meta| {
        if meta
            .get("has_more")
            .and_then(ValueReceive::as_bool)
            .unwrap_or_default()
        {
            return Ok(());
        }
        let Some(statuses) = meta.get_mut("statuses").and_then(ValueReceive::as_list_mut) else {
            return Ok(());
        };
        for status in statuses.iter_mut() {
            let Some(status) = status.as_map_mut() else {
                continue;
            };
            let Some(diag_record) = status
                .entry(String::from("diagnostic_record"))
                .or_insert_with(|| ValueReceive::Map(HashMap::with_capacity(3)))
                .as_map_mut()
            else {
                continue;
            };
            for (key, value) in &[
                ("OPERATION", ""),
                ("OPERATION_CODE", "0"),
                ("CURRENT_SCHEMA", "/"),
            ] {
                diag_record
                    .entry(String::from(*key))
                    .or_insert_with(|| ValueReceive::String(String::from(*value)));
            }
        }
        Ok(())
    })
}

pub(super) fn enrich_failure_diag_record(mut meta: &mut BoltMeta) {
    loop {
        if let Some(diag_record) = meta
            .entry(String::from("diagnostic_record"))
            .or_insert_with(|| ValueReceive::Map(HashMap::with_capacity(3)))
            .as_map_mut()
        {
            for (key, value) in &[
                ("OPERATION", ""),
                ("OPERATION_CODE", "0"),
                ("CURRENT_SCHEMA", "/"),
            ] {
                diag_record
                    .entry(String::from(*key))
                    .or_insert_with(|| ValueReceive::String(String::from(*value)));
            }
        }
        match meta.get_mut("cause").and_then(ValueReceive::as_map_mut) {
            None => break,
            Some(cause) => meta = cause,
        };
    }
}
