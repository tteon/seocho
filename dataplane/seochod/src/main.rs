//! `seochod` owns the local OS/Bolt boundary for approved SEOCHO projections.
//! It accepts only typed, ontology-validated projection payloads over a Unix
//! domain socket. It is deliberately not a general Cypher proxy.

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig};
use neo4j::{value_map, ValueSend};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;

const MAX_REQUEST_BYTES: usize = 2 * 1024 * 1024;

#[derive(Deserialize)]
struct Request {
    op: String,
    database: Option<String>,
    workspace_id: Option<String>,
    source_id: Option<String>,
    writer_ts: Option<f64>,
    nodes: Option<Vec<Node>>,
    relationships: Option<Vec<Relationship>>,
}

#[derive(Deserialize)]
struct Node { id: String, label: String, properties: HashMap<String, Value> }

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

#[derive(Serialize)]
struct Response {
    ok: bool,
    nodes_created: i64,
    relationships_created: i64,
    errors: Vec<String>,
    merge_conflicts: Vec<Value>,
    driver: &'static str,
    error: Option<String>,
}

fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn valid_database(value: &str) -> bool { valid_identifier(value) && value.len() <= 63 }

fn value_send(value: &Value) -> Result<ValueSend, String> {
    match value {
        Value::Null => Ok(ValueSend::Null),
        Value::Bool(v) => Ok(ValueSend::Boolean(*v)),
        Value::Number(v) => v.as_i64().map(ValueSend::Integer)
            .or_else(|| v.as_f64().map(ValueSend::Float))
            .ok_or_else(|| "unsupported JSON number".into()),
        Value::String(v) => Ok(ValueSend::String(v.clone())),
        Value::Array(values) => values.iter().map(value_send).collect::<Result<Vec<_>, _>>().map(ValueSend::List),
        Value::Object(_) => Err("nested object values are not graph properties".into()),
    }
}

fn props_send(mut props: HashMap<String, Value>, workspace_id: &str, source_id: &str, writer_ts: f64) -> Result<HashMap<String, ValueSend>, String> {
    props.insert("_source_id".into(), Value::String(source_id.into()));
    props.insert("_workspace_id".into(), Value::String(workspace_id.into()));
    props.insert("_writer_agent".into(), Value::String(if source_id.is_empty() { "unknown".into() } else { source_id.into() }));
    props.insert("_writer_ts".into(), serde_json::json!(writer_ts));
    props.into_iter().map(|(key, value)| {
        if key.is_empty() { return Err("empty property key".into()); }
        Ok((key, value_send(&value)?))
    }).collect()
}

fn driver_from_env() -> Result<Driver, String> {
    let host = std::env::var("SEOCHOD_BOLT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port = std::env::var("SEOCHOD_BOLT_PORT").ok().and_then(|v| v.parse().ok()).unwrap_or(7687);
    let user = std::env::var("SEOCHOD_BOLT_USER").unwrap_or_else(|_| "neo4j".into());
    let password = std::env::var("SEOCHOD_BOLT_PASSWORD").map_err(|_| "SEOCHOD_BOLT_PASSWORD is required".to_string())?;
    let auth = AuthToken::new_basic_auth(&user, &password);
    Ok(Driver::new(ConnectionConfig::new(Address::from((host.as_str(), port))), DriverConfig::new().with_auth(Arc::new(auth))))
}

fn project(driver: &Driver, request: Request) -> Response {
    let database = request.database.unwrap_or_else(|| "neo4j".into());
    let workspace_id = request.workspace_id.unwrap_or_else(|| "default".into());
    let source_id = request.source_id.unwrap_or_default();
    let writer_ts = request.writer_ts.unwrap_or_else(|| std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs_f64());
    if !valid_database(&database) || workspace_id.len() > 256 || source_id.len() > 2048 {
        return failure("invalid projection scope".into());
    }
    let mut response = success();
    for node in request.nodes.unwrap_or_default() {
        if !valid_identifier(&node.label) || node.id.len() > 2048 { response.errors.push(format!("invalid node {}", node.id)); continue; }
        let props = match props_send(node.properties, &workspace_id, &source_id, writer_ts) { Ok(v) => v, Err(e) => { response.errors.push(format!("node {}: {e}", node.id)); continue; } };
        let query = format!("MERGE (n:{} {{id: $id, _workspace_id: $ws}}) SET n += CASE WHEN n._writer_ts IS NULL OR n._writer_ts <= $props._writer_ts THEN $props ELSE {{}} END RETURN n", node.label);
        match driver.execute_query(query).with_database(Arc::new(database.clone())).with_parameters(value_map!({"id": node.id, "ws": workspace_id.clone(), "props": ValueSend::Map(props)})).run() {
            Ok(result) => { response.nodes_created += result.summary.counters.nodes_created; }
            Err(e) => response.errors.push(format!("node write: {e}")),
        }
    }
    for rel in request.relationships.unwrap_or_default() {
        if !valid_identifier(&rel.kind) || rel.source.len() > 2048 || rel.target.len() > 2048 || rel.source_label.as_deref().is_some_and(|v| !valid_identifier(v)) || rel.target_label.as_deref().is_some_and(|v| !valid_identifier(v)) { response.errors.push("invalid relationship".into()); continue; }
        let props = match props_send(rel.properties, &workspace_id, &source_id, writer_ts) { Ok(v) => v, Err(e) => { response.errors.push(format!("relationship: {e}")); continue; } };
        let source = rel.source_label.map(|v| format!(":{v}")).unwrap_or_default();
        let target = rel.target_label.map(|v| format!(":{v}")).unwrap_or_default();
        let query = format!("MATCH (a{} {{id: $src, _workspace_id: $ws}}), (b{} {{id: $tgt, _workspace_id: $ws}}) MERGE (a)-[r:{}]->(b) SET r += CASE WHEN r._writer_ts IS NULL OR r._writer_ts <= $props._writer_ts THEN $props ELSE {{}} END RETURN r", source, target, rel.kind);
        match driver.execute_query(query).with_database(Arc::new(database.clone())).with_parameters(value_map!({"src": rel.source, "tgt": rel.target, "ws": workspace_id.clone(), "props": ValueSend::Map(props)})).run() {
            Ok(result) => { response.relationships_created += result.summary.counters.relationships_created; }
            Err(e) => response.errors.push(format!("relationship write: {e}")),
        }
    }
    response.ok = response.errors.is_empty();
    response
}

fn success() -> Response { Response { ok: true, nodes_created: 0, relationships_created: 0, errors: vec![], merge_conflicts: vec![], driver: "rust-neo4j", error: None } }
fn failure(error: String) -> Response { Response { ok: false, nodes_created: 0, relationships_created: 0, errors: vec![], merge_conflicts: vec![], driver: "rust-neo4j", error: Some(error) } }

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
    let socket = std::env::args().nth(1).ok_or("usage: seochod <unix-socket-path>")?;
    let socket_path = Path::new(&socket);
    if socket_path.exists() { fs::remove_file(socket_path).map_err(|e| e.to_string())?; }
    let driver = driver_from_env()?;
    let listener = UnixListener::bind(socket_path).map_err(|e| e.to_string())?;
    for stream in listener.incoming() { if let Ok(stream) = stream { handle(stream, &driver); } }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn identifiers_are_constrained() { assert!(valid_identifier("Person_2")); assert!(!valid_identifier("Person:Bad")); assert!(!valid_identifier("2Person")); }
    #[test] fn nested_properties_are_rejected() { assert!(value_send(&serde_json::json!({"bad": true})).is_err()); }
}
