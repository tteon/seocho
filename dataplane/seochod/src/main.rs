//! `seochod` owns the local OS/Bolt boundary for approved SEOCHO projections.
//! It accepts only typed, ontology-validated projection payloads over a Unix
//! domain socket. It is deliberately not a general Cypher proxy.

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig};
use neo4j::{value_map, ValueSend};
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

const MAX_REQUEST_BYTES: usize = 2 * 1024 * 1024;

#[derive(Deserialize)]
struct Request {
    op: String,
    request_id: Option<String>,
    idempotency_key: Option<String>,
    database: Option<String>,
    workspace_id: Option<String>,
    source_id: Option<String>,
    writer_ts: Option<f64>,
    nodes: Option<Vec<Node>>,
    relationships: Option<Vec<Relationship>>,
    semantic_receipt: Option<SemanticReceipt>,
    admission: Option<ProjectionAdmission>,
}

#[derive(Deserialize)]
struct SemanticReceipt {
    schema_version: String,
    rdf_bundle_sha256: String,
    rdf_data_graph_sha256: String,
    agent_profile_sha256: String,
    projection_receipt_sha256: String,
}

/// Capability issued by the single-host ontology lifecycle store.  The daemon
/// never trusts it by itself: it is checked against SQLite immediately before
/// writes.  This prevents a stale/expired CLI holder from projecting merely by
/// replaying an old request.
#[derive(Deserialize)]
struct ProjectionAdmission {
    lease_id: String,
    fingerprint: String,
    generation: i64,
    epoch: i64,
    fencing_token: i64,
}

#[derive(Deserialize)]
struct Node {
    id: String,
    label: String,
    properties: HashMap<String, Value>,
}

#[derive(Deserialize)]
struct Relationship {
    source: String,
    target: String,
    #[serde(rename = "type")]
    kind: String,
    source_label: Option<String>,
    target_label: Option<String>,
    properties: HashMap<String, Value>,
}

#[derive(Serialize, Deserialize, Clone)]
struct Response {
    ok: bool,
    nodes_created: i64,
    relationships_created: i64,
    errors: Vec<String>,
    merge_conflicts: Vec<Value>,
    driver: String,
    error: Option<String>,
    request_id: Option<String>,
    duration_ms: f64,
}

fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn valid_database(value: &str) -> bool {
    valid_identifier(value) && value.len() <= 63
}

fn value_send(value: &Value) -> Result<ValueSend, String> {
    match value {
        Value::Null => Ok(ValueSend::Null),
        Value::Bool(v) => Ok(ValueSend::Boolean(*v)),
        Value::Number(v) => v
            .as_i64()
            .map(ValueSend::Integer)
            .or_else(|| v.as_f64().map(ValueSend::Float))
            .ok_or_else(|| "unsupported JSON number".into()),
        Value::String(v) => Ok(ValueSend::String(v.clone())),
        Value::Array(values) => values
            .iter()
            .map(value_send)
            .collect::<Result<Vec<_>, _>>()
            .map(ValueSend::List),
        Value::Object(_) => Err("nested object values are not graph properties".into()),
    }
}

fn props_send(
    mut props: HashMap<String, Value>,
    workspace_id: &str,
    source_id: &str,
    writer_ts: f64,
    receipt: Option<&SemanticReceipt>,
) -> Result<HashMap<String, ValueSend>, String> {
    props.insert("_source_id".into(), Value::String(source_id.into()));
    props.insert("_workspace_id".into(), Value::String(workspace_id.into()));
    props.insert(
        "_writer_agent".into(),
        Value::String(if source_id.is_empty() {
            "unknown".into()
        } else {
            source_id.into()
        }),
    );
    props.insert("_writer_ts".into(), serde_json::json!(writer_ts));
    if let Some(receipt) = receipt {
        props.insert(
            "_rdf_bundle_sha256".into(),
            Value::String(receipt.rdf_bundle_sha256.clone()),
        );
        props.insert(
            "_rdf_data_graph_sha256".into(),
            Value::String(receipt.rdf_data_graph_sha256.clone()),
        );
        props.insert(
            "_agent_profile_sha256".into(),
            Value::String(receipt.agent_profile_sha256.clone()),
        );
        props.insert(
            "_projection_receipt_sha256".into(),
            Value::String(receipt.projection_receipt_sha256.clone()),
        );
    }
    props
        .into_iter()
        .map(|(key, value)| {
            if key.is_empty() {
                return Err("empty property key".into());
            }
            Ok((key, value_send(&value)?))
        })
        .collect()
}

fn driver_from_env() -> Result<Driver, String> {
    let host = std::env::var("SEOCHOD_BOLT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port = std::env::var("SEOCHOD_BOLT_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(7687);
    let user = std::env::var("SEOCHOD_BOLT_USER").unwrap_or_else(|_| "neo4j".into());
    let password = std::env::var("SEOCHOD_BOLT_PASSWORD")
        .map_err(|_| "SEOCHOD_BOLT_PASSWORD is required".to_string())?;
    let auth = AuthToken::new_basic_auth(&user, &password);
    Ok(Driver::new(
        ConnectionConfig::new(Address::from((host.as_str(), port))),
        DriverConfig::new().with_auth(Arc::new(auth)),
    ))
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit())
}

fn validate_admission(
    control_db: &str,
    workspace_id: &str,
    receipt: &SemanticReceipt,
    admission: Option<&ProjectionAdmission>,
    now_ms: i64,
) -> Result<(), String> {
    let capability =
        admission.ok_or_else(|| "canonical projection requires lifecycle admission".to_string())?;
    if !valid_digest(&capability.fingerprint) || capability.fencing_token < 0 {
        return Err("invalid lifecycle admission".into());
    }
    if receipt.rdf_bundle_sha256 != capability.fingerprint {
        return Err("governance receipt bundle does not match lifecycle admission".into());
    }
    let conn = Connection::open_with_flags(control_db, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| "ontology lifecycle control database unavailable".to_string())?;
    // The active pointer is package scoped, therefore resolve the lease first
    // and use its package as the exact pointer key.
    let lease: Option<(String, String, i64, i64, i64, i64)> = conn.query_row(
        "SELECT package_id, fingerprint, generation, epoch, fencing_token, expires_at_ms FROM ontology_lease WHERE lease_id=?1 AND workspace_id=?2",
        (&capability.lease_id, workspace_id),
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?)),
    ).optional().map_err(|_| "ontology lifecycle lease unavailable".to_string())?;
    let (package_id, lease_fp, lease_gen, lease_epoch, lease_fence, expires_at_ms) =
        lease.ok_or_else(|| "lifecycle lease is absent".to_string())?;
    if expires_at_ms <= now_ms {
        return Err("lifecycle lease has expired".into());
    }
    if lease_fp != capability.fingerprint
        || lease_gen != capability.generation
        || lease_epoch != capability.epoch
        || lease_fence != capability.fencing_token
    {
        return Err("lifecycle lease capability mismatch".into());
    }
    let pointer: Option<(String, i64, i64, i64)> = conn.query_row(
        "SELECT fingerprint, generation, epoch, fencing_token FROM active_ontology WHERE workspace_id=?1 AND package_id=?2",
        (workspace_id, package_id.as_str()),
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
    ).optional().map_err(|_| "ontology lifecycle pointer unavailable".to_string())?;
    let (fp, generation, epoch, _pointer_fence) =
        pointer.ok_or_else(|| "active ontology is absent".to_string())?;
    if fp != capability.fingerprint
        || generation != capability.generation
        || epoch != capability.epoch
    {
        return Err("lifecycle admission is stale against active ontology".into());
    }
    Ok(())
}

fn claim_idempotency(
    control_db: &str,
    key: &str,
    workspace_id: &str,
    fingerprint: &str,
    now_ms: i64,
) -> Result<Option<Response>, String> {
    if key.is_empty() || key.len() > 256 {
        return Err("invalid projection idempotency key".into());
    }
    let mut conn = Connection::open(control_db)
        .map_err(|_| "ontology lifecycle control database unavailable".to_string())?;
    let tx = conn
        .transaction()
        .map_err(|_| "projection idempotency transaction unavailable".to_string())?;
    let inserted = tx.execute("INSERT OR IGNORE INTO projection_idempotency (idempotency_key, workspace_id, fingerprint, state, created_at_ms) VALUES (?1, ?2, ?3, 'pending', ?4)", (key, workspace_id, fingerprint, now_ms)).map_err(|_| "projection idempotency table unavailable".to_string())?;
    if inserted == 1 {
        tx.commit()
            .map_err(|_| "projection idempotency commit failed".to_string())?;
        return Ok(None);
    }
    let row: Option<(String, String, String, Option<String>)> = tx.query_row("SELECT workspace_id, fingerprint, state, response_json FROM projection_idempotency WHERE idempotency_key=?1", [key], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))).optional().map_err(|_| "projection idempotency lookup failed".to_string())?;
    tx.commit()
        .map_err(|_| "projection idempotency commit failed".to_string())?;
    let (stored_ws, stored_fp, state, response) =
        row.ok_or_else(|| "projection idempotency state disappeared".to_string())?;
    if stored_ws != workspace_id || stored_fp != fingerprint {
        return Err("projection idempotency key is bound to another scope".into());
    }
    if state == "completed" {
        return response
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .map(Some)
            .ok_or_else(|| "completed projection receipt is unreadable".into());
    }
    Err("projection with this idempotency key is pending; reconcile before retry".into())
}

fn complete_idempotency(control_db: &str, key: &str, response: &Response, now_ms: i64) {
    if let Ok(conn) = Connection::open(control_db) {
        let _ = conn.execute("UPDATE projection_idempotency SET state='completed', response_json=?1, completed_at_ms=?2 WHERE idempotency_key=?3 AND state='pending'", (serde_json::to_string(response).unwrap_or_default(), now_ms, key));
    }
}

fn project(driver: &Driver, request: Request) -> Response {
    let started = Instant::now();
    let request_id = request.request_id.clone();
    let idempotency_key = request.idempotency_key.clone();
    let database = request.database.unwrap_or_else(|| "neo4j".into());
    let workspace_id = request.workspace_id.unwrap_or_else(|| "default".into());
    let source_id = request.source_id.unwrap_or_default();
    let writer_ts = request.writer_ts.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64()
    });
    if !valid_database(&database) || workspace_id.len() > 256 || source_id.len() > 2048 {
        return failure("invalid projection scope".into());
    }
    let receipt = request.semantic_receipt.as_ref();
    let required = std::env::var("SEOCHOD_REQUIRE_GOVERNANCE").ok().as_deref() == Some("1");
    if required && receipt.is_none() {
        return failure("canonical projection requires a semantic governance receipt".into());
    }
    if let Some(value) = receipt {
        if value.schema_version != "seocho.canonical_projection_receipt.v1"
            || ![
                &value.rdf_bundle_sha256,
                &value.rdf_data_graph_sha256,
                &value.agent_profile_sha256,
                &value.projection_receipt_sha256,
            ]
            .iter()
            .all(|digest| valid_digest(digest))
        {
            return failure("invalid semantic governance receipt".into());
        }
    }
    let control_db = std::env::var("SEOCHOD_CONTROL_DB").ok();
    if let Some(control_db) = control_db.as_ref() {
        match receipt {
            Some(value) => {
                if let Err(error) = validate_admission(
                    &control_db,
                    &workspace_id,
                    value,
                    request.admission.as_ref(),
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as i64,
                ) {
                    return failure(error);
                }
            }
            None => {
                return failure(
                    "canonical projection requires a semantic governance receipt".into(),
                )
            }
        }
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        let key = match idempotency_key.as_deref() {
            Some(value) => value,
            None => return failure("canonical projection requires an idempotency key".into()),
        };
        match claim_idempotency(
            control_db,
            key,
            &workspace_id,
            &receipt.unwrap().rdf_bundle_sha256,
            now_ms,
        ) {
            Ok(Some(cached)) => return cached,
            Ok(None) => {}
            Err(error) => return failure(error),
        }
    }
    let mut response = success();
    for node in request.nodes.unwrap_or_default() {
        if !valid_identifier(&node.label) || node.id.len() > 2048 {
            response.errors.push(format!("invalid node {}", node.id));
            continue;
        }
        let props = match props_send(
            node.properties,
            &workspace_id,
            &source_id,
            writer_ts,
            receipt,
        ) {
            Ok(v) => v,
            Err(e) => {
                response.errors.push(format!("node {}: {e}", node.id));
                continue;
            }
        };
        let query = format!("MERGE (n:{} {{id: $id, _workspace_id: $ws}}) SET n += CASE WHEN n._writer_ts IS NULL OR n._writer_ts <= $props._writer_ts THEN $props ELSE {{}} END RETURN n", node.label);
        match driver.execute_query(query).with_database(Arc::new(database.clone())).with_parameters(value_map!({"id": node.id, "ws": workspace_id.clone(), "props": ValueSend::Map(props)})).run() {
            Ok(result) => { response.nodes_created += result.summary.counters.nodes_created; }
            Err(e) => response.errors.push(format!("node write: {e}")),
        }
    }
    for rel in request.relationships.unwrap_or_default() {
        if !valid_identifier(&rel.kind)
            || rel.source.len() > 2048
            || rel.target.len() > 2048
            || rel
                .source_label
                .as_deref()
                .is_some_and(|v| !valid_identifier(v))
            || rel
                .target_label
                .as_deref()
                .is_some_and(|v| !valid_identifier(v))
        {
            response.errors.push("invalid relationship".into());
            continue;
        }
        let props = match props_send(
            rel.properties,
            &workspace_id,
            &source_id,
            writer_ts,
            receipt,
        ) {
            Ok(v) => v,
            Err(e) => {
                response.errors.push(format!("relationship: {e}"));
                continue;
            }
        };
        let source = rel
            .source_label
            .map(|v| format!(":{v}"))
            .unwrap_or_default();
        let target = rel
            .target_label
            .map(|v| format!(":{v}"))
            .unwrap_or_default();
        let query = format!("MATCH (a{} {{id: $src, _workspace_id: $ws}}), (b{} {{id: $tgt, _workspace_id: $ws}}) MERGE (a)-[r:{}]->(b) SET r += CASE WHEN r._writer_ts IS NULL OR r._writer_ts <= $props._writer_ts THEN $props ELSE {{}} END RETURN r", source, target, rel.kind);
        match driver.execute_query(query).with_database(Arc::new(database.clone())).with_parameters(value_map!({"src": rel.source, "tgt": rel.target, "ws": workspace_id.clone(), "props": ValueSend::Map(props)})).run() {
            Ok(result) => { response.relationships_created += result.summary.counters.relationships_created; }
            Err(e) => response.errors.push(format!("relationship write: {e}")),
        }
    }
    response.ok = response.errors.is_empty();
    response.request_id = request_id;
    response.duration_ms = started.elapsed().as_secs_f64() * 1000.0;
    if let (Some(control_db), Some(key)) = (control_db.as_deref(), idempotency_key.as_deref()) {
        complete_idempotency(
            control_db,
            key,
            &response,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as i64,
        );
    }
    response
}

fn success() -> Response {
    Response {
        ok: true,
        nodes_created: 0,
        relationships_created: 0,
        errors: vec![],
        merge_conflicts: vec![],
        driver: "rust-neo4j".into(),
        error: None,
        request_id: None,
        duration_ms: 0.0,
    }
}
fn failure(error: String) -> Response {
    Response {
        ok: false,
        nodes_created: 0,
        relationships_created: 0,
        errors: vec![],
        merge_conflicts: vec![],
        driver: "rust-neo4j".into(),
        error: Some(error),
        request_id: None,
        duration_ms: 0.0,
    }
}

fn handle(stream: UnixStream, driver: &Driver) {
    let mut line = String::new();
    let response = match BufReader::new(&stream).read_line(&mut line) {
        Ok(_) if line.len() <= MAX_REQUEST_BYTES => match serde_json::from_str::<Request>(&line) {
            Ok(request) if request.op == "health" => success(),
            Ok(request) if request.op == "project" => project(driver, request),
            Ok(_) => failure("unsupported operation".into()),
            Err(_) => failure("invalid json".into()),
        },
        _ => failure("invalid request".into()),
    };
    let mut stream = stream;
    let _ = writeln!(stream, "{}", serde_json::to_string(&response).unwrap());
}

fn main() -> Result<(), String> {
    let socket = std::env::args()
        .nth(1)
        .ok_or("usage: seochod <unix-socket-path>")?;
    let socket_path = Path::new(&socket);
    if socket_path.exists() {
        fs::remove_file(socket_path).map_err(|e| e.to_string())?;
    }
    let driver = driver_from_env()?;
    let listener = UnixListener::bind(socket_path).map_err(|e| e.to_string())?;
    for stream in listener.incoming() {
        if let Ok(stream) = stream {
            handle(stream, &driver);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};
    #[test]
    fn identifiers_are_constrained() {
        assert!(valid_identifier("Person_2"));
        assert!(!valid_identifier("Person:Bad"));
        assert!(!valid_identifier("2Person"));
    }
    #[test]
    fn nested_properties_are_rejected() {
        assert!(value_send(&serde_json::json!({"bad": true})).is_err());
    }

    #[test]
    fn admission_rejects_stale_active_pointer() {
        let path = std::env::temp_dir().join(format!(
            "seochod-admission-{}.sqlite",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY(workspace_id, package_id));
             CREATE TABLE ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT, package_id TEXT, purpose TEXT, owner TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, acquired_at_ms INTEGER, expires_at_ms INTEGER);",
        ).unwrap();
        let digest = "a".repeat(64);
        conn.execute(
            "INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,1)",
            [&digest],
        )
        .unwrap();
        conn.execute("INSERT INTO ontology_lease VALUES ('lease','ws','pkg','projection','owner',?1,0,0,2,0,9999999999999)", [&digest]).unwrap();
        drop(conn);
        let receipt = SemanticReceipt {
            schema_version: "seocho.canonical_projection_receipt.v1".into(),
            rdf_bundle_sha256: digest.clone(),
            rdf_data_graph_sha256: "b".repeat(64),
            agent_profile_sha256: "c".repeat(64),
            projection_receipt_sha256: "d".repeat(64),
        };
        let admission = ProjectionAdmission {
            lease_id: "lease".into(),
            fingerprint: digest,
            generation: 0,
            epoch: 0,
            fencing_token: 2,
        };
        assert!(
            validate_admission(path.to_str().unwrap(), "ws", &receipt, Some(&admission), 1).is_ok()
        );
        let conn = Connection::open(&path).unwrap();
        conn.execute(
            "UPDATE active_ontology SET epoch=1 WHERE workspace_id='ws'",
            [],
        )
        .unwrap();
        drop(conn);
        assert!(
            validate_admission(path.to_str().unwrap(), "ws", &receipt, Some(&admission), 1)
                .unwrap_err()
                .contains("stale")
        );
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn completed_idempotency_key_replays_receipt_not_write_permission() {
        let path = std::env::temp_dir().join(format!(
            "seochod-idempotency-{}.sqlite",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch("CREATE TABLE projection_idempotency (idempotency_key TEXT PRIMARY KEY, workspace_id TEXT, fingerprint TEXT, state TEXT, response_json TEXT, created_at_ms INTEGER, completed_at_ms INTEGER);").unwrap();
        drop(conn);
        assert!(
            claim_idempotency(path.to_str().unwrap(), "key", "ws", "a", 1)
                .unwrap()
                .is_none()
        );
        let response = success();
        complete_idempotency(path.to_str().unwrap(), "key", &response, 2);
        assert!(
            claim_idempotency(path.to_str().unwrap(), "key", "ws", "a", 3)
                .unwrap()
                .is_some()
        );
        std::fs::remove_file(path).unwrap();
    }
}
