//! `seochod` owns the local OS/Bolt boundary for approved SEOCHO projections.
//! It accepts only typed, ontology-validated projection payloads over a Unix
//! domain socket. It is deliberately not a general Cypher proxy.

mod batch;
mod bolt;

use batch::{build_statements, Statement, WriteMode};
use bolt::{BoltFailure, FailureKind, RetryPolicy};
use neo4j::bookmarks::Bookmarks;
use neo4j::driver::Driver;
use neo4j::session::SessionConfig;
use neo4j::transaction::{Transaction, TransactionTimeout};
use neo4j::{value_map, ValueReceive, ValueSend};
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::cell::Cell;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

const MAX_REQUEST_BYTES: usize = 2 * 1024 * 1024;
/// Age after which a 'pending' idempotency claim is presumed abandoned.
const DEFAULT_PENDING_RECLAIM_MS: i64 = 600_000;
/// How far a client's writer_ts may run ahead of the daemon's clock.
const DEFAULT_MAX_CLOCK_SKEW_MS: i64 = 300_000;
/// Upper bound on the part of the reclaim window kept free after a
/// governed transaction ends, for recording its outcome.
const COMPLETION_MARGIN_MS: i64 = 5_000;
/// Server-side timeout of an ungoverned projection transaction, so a stuck
/// transaction cannot block the serial daemon indefinitely. `0` keeps the
/// server's own default (the pre-hardening behaviour).
const DEFAULT_UNGOVERNED_TX_TIMEOUT_MS: i64 = 60_000;
/// Timeout value passed to an attempt that means "server default".
const SERVER_DEFAULT_TX_TIMEOUT: i64 = 0;

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
    /// Bookmarks the write must causally follow (for example another
    /// writer's response `bookmarks`).
    bookmarks: Option<Vec<String>>,
}

const MAX_BOOKMARKS: usize = 64;
const MAX_BOOKMARK_BYTES: usize = 4096;

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
    /// Stable failure class for clients; absent on success. See README.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error_kind: Option<String>,
    /// Commit bookmarks of a successful projection, for read-your-writes in
    /// another session (`bookmarks` of a later request, or a reader).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    bookmarks: Option<Vec<String>>,
    /// Transaction attempts, when Bolt I/O happened (> 1 after a retry).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    attempts: Option<u32>,
}

fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn valid_database(value: &str) -> bool {
    valid_identifier(value) && value.len() <= 63
}

/// Converts one JSON property value, rejecting before any Bolt I/O what the
/// server would reject (aborting the whole transaction with an opaque
/// TypeError) or store lossily. A top-level `null` stays `Null`, which
/// `SET n += $props` applies as removing the property, as in the Python
/// writer.
fn value_send(value: &Value) -> Result<ValueSend, String> {
    match value {
        Value::Array(values) => {
            // Property lists must be homogeneous and hold no nulls or nested
            // collections; integers and floats may mix (stored as floats).
            let kind = |v: &Value| match v {
                Value::Bool(_) => Some(0),
                Value::Number(_) => Some(1),
                Value::String(_) => Some(2),
                _ => None,
            };
            let first = values.first().map(kind);
            if values.iter().any(|v| kind(v).is_none() || Some(kind(v)) != first) {
                return Err("list properties must hold non-null scalars of one type".into());
            }
            values
                .iter()
                .map(value_send)
                .collect::<Result<Vec<_>, _>>()
                .map(ValueSend::List)
        }
        other => scalar_send(other),
    }
}

fn scalar_send(value: &Value) -> Result<ValueSend, String> {
    match value {
        Value::Null => Ok(ValueSend::Null),
        Value::Bool(v) => Ok(ValueSend::Boolean(*v)),
        Value::Number(v) => match (v.as_i64(), v.as_u64(), v.as_f64()) {
            (Some(i), _, _) => Ok(ValueSend::Integer(i)),
            (None, Some(_), _) => Err(format!("integer {v} exceeds the 64-bit signed range")),
            (None, None, Some(f)) if f.is_finite() => Ok(ValueSend::Float(f)),
            _ => Err("unsupported JSON number".into()),
        },
        Value::String(v) => Ok(ValueSend::String(v.clone())),
        Value::Array(_) | Value::Object(_) => {
            Err("nested object values are not graph properties".into())
        }
    }
}

/// Properties only the daemon writes: provenance, the semantic receipt and
/// the storage fence. A caller-supplied value is rejected in every mode
/// (governed or `SEOCHOD_ALLOW_UNGOVERNED=1`), before admission-derived
/// metadata is added: otherwise an ungoverned write could plant an arbitrary
/// `_fencing_token`/`_fence_scope` that later fences out legitimate writers,
/// or a receipt-less write could forge receipt provenance.
const RESERVED_PROPERTIES: [&str; 10] = [
    "_source_id",
    "_workspace_id",
    "_writer_agent",
    "_writer_ts",
    "_rdf_bundle_sha256",
    "_rdf_data_graph_sha256",
    "_agent_profile_sha256",
    "_projection_receipt_sha256",
    "_fencing_token",
    "_fence_scope",
];

fn props_send(
    mut props: HashMap<String, Value>,
    workspace_id: &str,
    source_id: &str,
    writer_ts: f64,
    receipt: Option<&SemanticReceipt>,
) -> Result<HashMap<String, ValueSend>, String> {
    let mut reserved: Vec<&str> = RESERVED_PROPERTIES
        .iter()
        .copied()
        .filter(|key| props.contains_key(*key))
        .collect();
    if !reserved.is_empty() {
        reserved.sort_unstable();
        return Err(format!(
            "reserved properties are set by seochod, not the caller: {}",
            reserved.join(", ")
        ));
    }
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

fn valid_digest(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit())
}

/// A lease that passed admission. `fencing_token` is the raw lease token as
/// stored in the lifecycle store's lease row; the store issues these strictly
/// increasing and never reused per (workspace, package)
/// (`src/seocho/ontology/lifecycle.py` in this repository), so it orders writers within the
/// package directly.
/// The `ontology_lease.purpose` the lifecycle store records for projection
/// writers (`seocho ontology lease acquire --purpose projection`).
const PROJECTION_LEASE_PURPOSE: &str = "projection";

struct AdmittedLease {
    package_id: String,
    fencing_token: i64,
}

fn validate_admission(
    control_db: &str,
    workspace_id: &str,
    receipt: &SemanticReceipt,
    admission: Option<&ProjectionAdmission>,
    now_ms: i64,
) -> Result<AdmittedLease, String> {
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
    type LeaseRow = (String, String, String, i64, i64, i64, i64);
    let lease: Option<LeaseRow> = conn.query_row(
        "SELECT package_id, purpose, fingerprint, generation, epoch, fencing_token, expires_at_ms FROM ontology_lease WHERE lease_id=?1 AND workspace_id=?2",
        (&capability.lease_id, workspace_id),
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?)),
    ).optional().map_err(|_| "ontology lifecycle lease unavailable".to_string())?;
    let (package_id, purpose, lease_fp, lease_gen, lease_epoch, lease_fence, expires_at_ms) =
        lease.ok_or_else(|| "lifecycle lease is absent".to_string())?;
    // The lifecycle store enforces one live lease per (package, purpose), so
    // a query lease can coexist with the projection lease and carry a higher
    // token; only projection leases may write.
    if purpose != PROJECTION_LEASE_PURPOSE {
        return Err("lifecycle lease is not a projection lease".into());
    }
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
    let (fp, generation, epoch, pointer_fence) =
        pointer.ok_or_else(|| "active ontology is absent".to_string())?;
    if fp != capability.fingerprint
        || generation != capability.generation
        || epoch != capability.epoch
    {
        return Err("lifecycle admission is stale against active ontology".into());
    }
    // The lifecycle store issues lease tokens from a durable per-package
    // high-water, strictly above the pointer's token, and publishing never
    // lowers the pointer token, so a lease whose token is not above it was
    // superseded.
    // Every publish also bumps epoch or generation, which the check above
    // already rejects; this is defense in depth against a pointer row that
    // was rewritten without them.
    if lease_fence <= pointer_fence {
        return Err("lifecycle lease is fenced off by the active ontology pointer".into());
    }
    Ok(AdmittedLease {
        package_id,
        fencing_token: lease_fence,
    })
}

/// Claims `key` for one projection attempt. `Ok(None)` means the caller now
/// owns the 'pending' row whose `created_at_ms` is `now_ms`; `Ok(Some(_))`
/// replays a completed receipt. A 'failed' row, or a 'pending' row older than
/// `reclaim_after_ms` (its holder presumably died), is re-claimed atomically:
/// MERGE projection writes are idempotent, so re-running them is sound.
fn claim_idempotency(
    control_db: &str,
    key: &str,
    workspace_id: &str,
    fingerprint: &str,
    now_ms: i64,
    reclaim_after_ms: i64,
) -> Result<Option<Response>, String> {
    if key.is_empty() || key.len() > 256 {
        return Err("invalid projection idempotency key".into());
    }
    let mut conn = Connection::open(control_db)
        .map_err(|_| "ontology lifecycle control database unavailable".to_string())?;
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| "projection idempotency transaction unavailable".to_string())?;
    let inserted = tx.execute("INSERT OR IGNORE INTO projection_idempotency (idempotency_key, workspace_id, fingerprint, state, created_at_ms) VALUES (?1, ?2, ?3, 'pending', ?4)", (key, workspace_id, fingerprint, now_ms)).map_err(|_| "projection idempotency table unavailable".to_string())?;
    if inserted == 1 {
        tx.commit()
            .map_err(|_| "projection idempotency commit failed".to_string())?;
        return Ok(None);
    }
    let row: Option<(String, String, String, Option<String>)> = tx.query_row("SELECT workspace_id, fingerprint, state, response_json FROM projection_idempotency WHERE idempotency_key=?1", [key], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))).optional().map_err(|_| "projection idempotency lookup failed".to_string())?;
    let (stored_ws, stored_fp, state, response) =
        row.ok_or_else(|| "projection idempotency state disappeared".to_string())?;
    if stored_ws != workspace_id || stored_fp != fingerprint {
        return Err("projection idempotency key is bound to another scope".into());
    }
    let reclaimed = match state.as_str() {
        "completed" => {
            return response
                .and_then(|raw| serde_json::from_str(&raw).ok())
                .map(Some)
                .ok_or_else(|| "completed projection receipt is unreadable".into());
        }
        "failed" => tx.execute("UPDATE projection_idempotency SET state='pending', response_json=NULL, created_at_ms=?1, completed_at_ms=NULL WHERE idempotency_key=?2 AND state='failed'", (now_ms, key)),
        "pending" => tx.execute("UPDATE projection_idempotency SET created_at_ms=?1 WHERE idempotency_key=?2 AND state='pending' AND created_at_ms < ?3", (now_ms, key, now_ms.saturating_sub(reclaim_after_ms))),
        _ => return Err("projection idempotency state is unknown".into()),
    }
    .map_err(|_| "projection idempotency reclaim failed".to_string())?;
    if reclaimed != 1 {
        return Err("projection with this idempotency key is pending; reconcile before retry".into());
    }
    tx.commit()
        .map_err(|_| "projection idempotency commit failed".to_string())?;
    Ok(None)
}

/// Records the outcome of the claim taken at `claimed_at_ms`. Failures are
/// stored as 'failed' (re-claimable), never cached as 'completed'. Returns an
/// error when the outcome could not be recorded, including when the claim
/// was reclaimed by another attempt in the meantime.
fn complete_idempotency(
    control_db: &str,
    key: &str,
    claimed_at_ms: i64,
    response: &Response,
    now_ms: i64,
) -> Result<(), String> {
    let state = if response.ok { "completed" } else { "failed" };
    let payload = serde_json::to_string(response)
        .map_err(|e| format!("projection receipt serialization failed: {e}"))?;
    let conn = Connection::open(control_db)
        .map_err(|_| "ontology lifecycle control database unavailable".to_string())?;
    let updated = conn.execute("UPDATE projection_idempotency SET state=?1, response_json=?2, completed_at_ms=?3 WHERE idempotency_key=?4 AND state='pending' AND created_at_ms=?5", (state, payload, now_ms, key, claimed_at_ms)).map_err(|e| format!("projection idempotency update failed: {e}"))?;
    if updated != 1 {
        return Err("projection idempotency claim was lost before completion".into());
    }
    Ok(())
}

/// Values below `min` (or unparsable) fall back to `default`.
fn env_ms(name: &str, default: i64, min: i64) -> i64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
        .filter(|v| *v >= min)
        .unwrap_or(default)
}

/// `_writer_ts` decides last-writer-wins on the graph, so a value far in the
/// future would pin an item against every later legitimate update, and a
/// non-finite one cannot be compared at all. `writer_ts` is in seconds.
fn validate_writer_ts(writer_ts: f64, now_secs: f64, max_skew_ms: i64) -> Result<(), String> {
    if !writer_ts.is_finite() {
        return Err("writer_ts must be a finite number of seconds".into());
    }
    let ahead_ms = (writer_ts - now_secs) * 1000.0;
    if ahead_ms > max_skew_ms as f64 {
        return Err(format!(
            "writer_ts is {:.0} ms ahead of daemon time (max {max_skew_ms} ms, SEOCHOD_MAX_CLOCK_SKEW_MS)",
            ahead_ms
        ));
    }
    Ok(())
}

fn now_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

/// `fence_ok` is false when the item was stamped by a newer lease of the same
/// package (storage fences are only comparable within one package; across
/// packages only `_writer_ts` orders writes). A NULL `$fence` (ungoverned)
/// only passes on an item that was never fenced. The caller fails the whole
/// request on a fenced item instead of skipping it silently.
fn fence_guard(var: &str) -> String {
    format!("coalesce({var}._fencing_token IS NULL OR ({var}._fence_scope IS NOT NULL AND {var}._fence_scope <> $fence_scope) OR {var}._fencing_token <= $fence, false) AS fence_ok")
}

/// Last-writer-wins on `_writer_ts`, as in the Python writer: a stale write
/// is a silent no-op, not an error. `props` is the incoming property map
/// expression (`$props` per item, `row.props` in an UNWIND batch).
fn fresh_guard(var: &str, props: &str) -> String {
    format!("({var}._writer_ts IS NULL OR {var}._writer_ts <= {props}._writer_ts) AS fresh")
}

/// Property conflicts computed before the SET, in the same shape as the
/// Python writer's `merge_conflicts` (graph.py `_conflict_with`).
fn node_conflicts(props: &str) -> String {
    format!("[k IN keys({props}) WHERE NOT k STARTS WITH '_' AND k <> 'id' AND n[k] IS NOT NULL AND n[k] <> {props}[k] | {{property: k, existing: toString(n[k]), incoming: toString({props}[k])}}] AS conflicts")
}

fn node_merge_query(label: &str) -> String {
    format!(
        "MERGE (n:{label} {{id: $id, _workspace_id: $ws}}) WITH n, {}, {}, {} SET n += CASE WHEN fence_ok AND fresh THEN $props ELSE {{}} END RETURN fence_ok, conflicts",
        fence_guard("n"),
        fresh_guard("n", "$props"),
        node_conflicts("$props")
    )
}

/// Returns no row when either endpoint is missing; the caller treats that as
/// an error rather than a silently skipped edge.
fn relationship_merge_query(source_label: &str, target_label: &str, kind: &str) -> String {
    format!(
        "MATCH (a{source_label} {{id: $src, _workspace_id: $ws}}), (b{target_label} {{id: $tgt, _workspace_id: $ws}}) MERGE (a)-[r:{kind}]->(b) WITH r, {}, {} SET r += CASE WHEN fence_ok AND fresh THEN $props ELSE {{}} END RETURN fence_ok, [] AS conflicts",
        fence_guard("r"),
        fresh_guard("r", "$props")
    )
}

/// What a planned write merges. Endpoint labels are `":Label"` or `""`.
#[derive(Debug, PartialEq)]
enum Target {
    Node {
        label: String,
    },
    Relationship {
        kind: String,
        source_label: String,
        target_label: String,
    },
}

/// One validated write. All writes are planned before any Bolt I/O so a
/// malformed item fails the whole request instead of projecting a subset.
struct PlannedWrite {
    what: String,
    /// `(label, key)` for a node, reported in its merge conflicts.
    node: Option<(String, String)>,
    target: Target,
    /// The per-item statement (`WriteMode::PerItem`); its parameters also
    /// feed the UNWIND rows.
    query: String,
    params: HashMap<String, ValueSend>,
}

/// A storage fence: the admitted lease's raw token and the package it orders.
struct Fence<'a> {
    token: i64,
    scope: &'a str,
}

struct WriteScope<'a> {
    workspace_id: &'a str,
    source_id: &'a str,
    writer_ts: f64,
    fence: Option<Fence<'a>>,
    receipt: Option<&'a SemanticReceipt>,
    /// Reject relationships whose endpoint labels are neither given nor
    /// resolvable from the request's own nodes (an unlabeled endpoint plans
    /// to a scan of every node in the database).
    require_endpoint_labels: bool,
    /// Take a missing endpoint label from the request's own node with that
    /// id (`SEOCHOD_INFER_ENDPOINT_LABELS=0` turns it off and matches the id
    /// under every label, as before label inference).
    infer_endpoint_labels: bool,
}

impl WriteScope<'_> {
    fn props(&self, props: HashMap<String, Value>) -> Result<ValueSend, String> {
        let mut props = props_send(props, self.workspace_id, self.source_id, self.writer_ts, self.receipt)?;
        if let Some(fence) = &self.fence {
            props.insert("_fencing_token".into(), ValueSend::Integer(fence.token));
            props.insert("_fence_scope".into(), ValueSend::String(fence.scope.into()));
        }
        Ok(ValueSend::Map(props))
    }

    fn fence_param(&self) -> ValueSend {
        self.fence
            .as_ref()
            .map_or(ValueSend::Null, |f| ValueSend::Integer(f.token))
    }

    fn fence_scope_param(&self) -> ValueSend {
        self.fence
            .as_ref()
            .map_or(ValueSend::Null, |f| ValueSend::String(f.scope.into()))
    }
}

/// The label of each node id in the request, or `None` when the request
/// gives one id two different labels (then nothing is inferred).
fn request_labels(nodes: &[Node]) -> HashMap<String, Option<String>> {
    let mut labels: HashMap<String, Option<String>> = HashMap::new();
    for node in nodes.iter().filter(|n| valid_identifier(&n.label)) {
        labels
            .entry(node.id.clone())
            .and_modify(|known| {
                if known.as_deref() != Some(node.label.as_str()) {
                    *known = None;
                }
            })
            .or_insert_with(|| Some(node.label.clone()));
    }
    labels
}

fn plan_writes(
    nodes: Vec<Node>,
    relationships: Vec<Relationship>,
    scope: &WriteScope,
    errors: &mut Vec<String>,
) -> Vec<PlannedWrite> {
    let mut writes = Vec::new();
    let labels = if scope.infer_endpoint_labels {
        request_labels(&nodes)
    } else {
        HashMap::new()
    };
    for node in nodes {
        if !valid_identifier(&node.label) || node.id.len() > 2048 {
            errors.push(format!("invalid node {}", node.id));
            continue;
        }
        let props = match scope.props(node.properties) {
            Ok(v) => v,
            Err(e) => {
                errors.push(format!("node {}: {e}", node.id));
                continue;
            }
        };
        writes.push(PlannedWrite {
            what: format!("node {}", node.id),
            node: Some((node.label.clone(), node.id.clone())),
            query: node_merge_query(&node.label),
            target: Target::Node {
                label: node.label.clone(),
            },
            params: value_map!({"id": node.id, "ws": scope.workspace_id, "fence": scope.fence_param(), "fence_scope": scope.fence_scope_param(), "props": props}),
        });
    }
    for rel in relationships {
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
            errors.push("invalid relationship".into());
            continue;
        }
        let what = format!("relationship {}-[{}]->{}", rel.source, rel.kind, rel.target);
        // A missing endpoint label is taken from the request's own node with
        // that id, so the MATCH can use the label's index.
        let resolve = |given: Option<String>, id: &str| {
            given.or_else(|| labels.get(id).cloned().flatten())
        };
        let source_label = resolve(rel.source_label, &rel.source);
        let target_label = resolve(rel.target_label, &rel.target);
        if scope.require_endpoint_labels && (source_label.is_none() || target_label.is_none()) {
            errors.push(format!(
                "{what}: endpoint label is neither given nor a node of this request (SEOCHOD_REQUIRE_ENDPOINT_LABELS=1)"
            ));
            continue;
        }
        let props = match scope.props(rel.properties) {
            Ok(v) => v,
            Err(e) => {
                errors.push(format!("relationship: {e}"));
                continue;
            }
        };
        let source = source_label.map(|v| format!(":{v}")).unwrap_or_default();
        let target = target_label.map(|v| format!(":{v}")).unwrap_or_default();
        writes.push(PlannedWrite {
            what,
            node: None,
            query: relationship_merge_query(&source, &target, &rel.kind),
            target: Target::Relationship {
                kind: rel.kind.clone(),
                source_label: source,
                target_label: target,
            },
            params: value_map!({"src": rel.source, "tgt": rel.target, "ws": scope.workspace_id, "fence": scope.fence_param(), "fence_scope": scope.fence_scope_param(), "props": props}),
        });
    }
    writes
}

struct WriteOutcome {
    nodes_created: i64,
    relationships_created: i64,
    merge_conflicts: Vec<Value>,
}

/// A request-level rejection found inside the transaction (fenced item,
/// missing endpoint, failed admission re-check); the transaction is rolled
/// back and the request is not retried.
struct Rejection {
    kind: &'static str,
    message: String,
}

/// Runs the statements of one attempt. `Ok(Err(_))` asks the caller to roll
/// back; a driver error aborts the attempt.
// The error type is the driver's `neo4j::Result`, not ours.
#[allow(clippy::result_large_err)]
fn run_statements(
    tx: &Transaction,
    writes: &[PlannedWrite],
    statements: &[Statement],
    source_id: &str,
) -> neo4j::Result<Result<WriteOutcome, Rejection>> {
    let mut outcome = WriteOutcome {
        nodes_created: 0,
        relationships_created: 0,
        merge_conflicts: vec![],
    };
    // (write index, entry): sorted at the end so conflicts keep request order.
    let mut conflicts: Vec<(usize, Value)> = vec![];
    for statement in statements {
        let mut stream = tx
            .query(&statement.query)
            .with_parameters(&statement.params)
            .run()?;
        let mut found = vec![false; statement.items.len()];
        let mut fenced = vec![false; statement.items.len()];
        let mut stray_row = false;
        for record in stream.by_ref() {
            let record = record?;
            let position = if statement.unwind {
                record
                    .value("i")
                    .and_then(|v| v.as_int())
                    .and_then(|i| usize::try_from(i).ok())
                    .filter(|i| *i < statement.items.len())
            } else {
                Some(0)
            };
            let Some(position) = position else {
                stray_row = true;
                continue;
            };
            found[position] = true;
            fenced[position] |= record.value("fence_ok").and_then(|v| v.as_bool()) != Some(true);
            let index = statement.items[position];
            if let Some((label, key)) = &writes[index].node {
                conflicts.extend(
                    conflict_entries(record.value("conflicts"), label, key, source_id)
                        .into_iter()
                        .map(|entry| (index, entry)),
                );
            }
        }
        let counters = stream.consume()?.map(|summary| summary.counters);
        if stray_row {
            return Ok(Err(Rejection {
                kind: "fatal",
                message: "unexpected projection result row; projection rolled back".into(),
            }));
        }
        for (position, index) in statement.items.iter().enumerate() {
            let what = &writes[*index].what;
            if !found[position] {
                return Ok(Err(Rejection {
                    kind: "graph_rejected",
                    message: format!("{what}: endpoint node not found; projection rolled back"),
                }));
            }
            if fenced[position] {
                return Ok(Err(Rejection {
                    kind: "graph_rejected",
                    message: format!("{what}: fenced off by a newer lifecycle lease (or ungoverned write onto fenced data); projection rolled back"),
                }));
            }
        }
        if let Some(counters) = counters {
            if statement.node {
                outcome.nodes_created += counters.nodes_created;
            } else {
                outcome.relationships_created += counters.relationships_created;
            }
        }
    }
    conflicts.sort_by_key(|(index, _)| *index);
    outcome.merge_conflicts = conflicts.into_iter().map(|(_, entry)| entry).collect();
    Ok(Ok(outcome))
}

/// Session-level inputs of one projection transaction.
struct TxContext<'a> {
    database: &'a str,
    source_id: &'a str,
    /// Shows up in `SHOW TRANSACTIONS` and the server's query log.
    meta: HashMap<String, ValueSend>,
    bookmarks: Vec<String>,
}

/// Time limits of one projection: the retry policy, each attempt's
/// transaction timeout (`timeout_at(now)`, `None` once no time is left) and
/// the error reported when not even the first attempt fits.
struct Budget<'a> {
    policy: RetryPolicy,
    timeout_at: &'a mut dyn FnMut(i64) -> Option<i64>,
    no_time: String,
}

struct Committed {
    outcome: WriteOutcome,
    bookmarks: Vec<String>,
    attempts: u32,
}

struct WriteFailure {
    kind: &'static str,
    message: String,
    attempts: u32,
}

/// Runs every write of one request in a single explicit transaction. Any
/// write error, missing relationship endpoint or failed `recheck` (admission
/// re-validated right before commit) rolls the whole projection back. A
/// transient failure before COMMIT (dropped connection, deadlock) re-runs
/// the whole transaction, statements and `recheck` included, within
/// `budget`. A failure after COMMIT
/// was sent is reported as outcome unknown and never retried here.
// The transaction closure returns the driver's `neo4j::Result`.
#[allow(clippy::result_large_err)]
fn execute_writes(
    driver: &Driver,
    context: &TxContext,
    writes: &[PlannedWrite],
    statements: &[Statement],
    budget: Budget,
    recheck: &mut dyn FnMut() -> Result<(), String>,
) -> Result<Committed, WriteFailure> {
    let mut config = SessionConfig::new()
        .with_database(Arc::new(context.database.into()))
        .with_fetch_all();
    if !context.bookmarks.is_empty() {
        config = config.with_bookmarks(Arc::new(Bookmarks::from_raw(context.bookmarks.clone())));
    }
    let mut session = driver.session(config);
    let mut attempt = |timeout_ms: i64| -> Result<Result<WriteOutcome, Rejection>, BoltFailure> {
        let timeout = if timeout_ms == SERVER_DEFAULT_TX_TIMEOUT {
            TransactionTimeout::default()
        } else {
            TransactionTimeout::from_millis(timeout_ms).ok_or_else(|| BoltFailure {
                kind: FailureKind::Fatal,
                message: format!("invalid transaction timeout {timeout_ms} ms"),
            })?
        };
        // Set just before COMMIT is sent: an error after that point means the
        // server may or may not have applied the transaction.
        let committing = Cell::new(false);
        session
            .transaction()
            .with_transaction_timeout(timeout)
            .with_transaction_meta(&context.meta)
            .run(|tx: Transaction| {
                match run_statements(&tx, writes, statements, context.source_id)? {
                    Ok(outcome) => {
                        if let Err(error) = recheck() {
                            tx.rollback()?;
                            return Ok(Err(Rejection {
                                kind: "admission",
                                message: format!("{error}; projection rolled back"),
                            }));
                        }
                        committing.set(true);
                        tx.commit()?;
                        Ok(Ok(outcome))
                    }
                    Err(rejection) => {
                        tx.rollback()?;
                        Ok(Err(rejection))
                    }
                }
            })
            .map_err(|e| BoltFailure {
                kind: bolt::classify(&e, committing.get()),
                message: e.to_string(),
            })
    };
    let result = bolt::run_with_retries(
        &budget.policy,
        &mut now_ms,
        &mut std::thread::sleep,
        budget.timeout_at,
        BoltFailure {
            kind: FailureKind::Fatal,
            message: budget.no_time,
        },
        &mut attempt,
    );
    match result {
        Ok((Ok(outcome), attempts)) => Ok(Committed {
            outcome,
            bookmarks: session.last_bookmarks().raw().map(String::from).collect(),
            attempts,
        }),
        Ok((Err(rejection), attempts)) => Err(WriteFailure {
            kind: rejection.kind,
            message: rejection.message,
            attempts,
        }),
        Err((failure, 0)) => Err(WriteFailure {
            kind: "claim_window",
            message: failure.message,
            attempts: 0,
        }),
        Err((failure, attempts)) => Err(WriteFailure {
            kind: failure.kind.as_str(),
            message: match failure.kind {
                FailureKind::CommitUnknown => format!("projection commit outcome unknown (writes may have been applied; retry is safe): {}", failure.message),
                FailureKind::Transient => format!("projection transaction failed and was rolled back after {attempts} attempt(s): {}", failure.message),
                FailureKind::Fatal => format!("projection transaction failed and was rolled back: {}", failure.message),
            },
            attempts,
        }),
    }
}

/// Converts the query's `conflicts` list into the Python writer's
/// `{label, key, property, existing, incoming, source_id}` entries.
fn conflict_entries(
    conflicts: Option<&ValueReceive>,
    label: &str,
    key: &str,
    source_id: &str,
) -> Vec<Value> {
    let Some(ValueReceive::List(items)) = conflicts else {
        return vec![];
    };
    let text = |entry: &HashMap<String, ValueReceive>, field: &str| match entry.get(field) {
        Some(ValueReceive::String(v)) => Value::String(v.clone()),
        _ => Value::Null,
    };
    items
        .iter()
        .filter_map(|item| match item {
            ValueReceive::Map(entry) => Some(serde_json::json!({
                "label": label,
                "key": key,
                "property": text(entry, "property"),
                "existing": text(entry, "existing"),
                "incoming": text(entry, "incoming"),
                "source_id": source_id,
            })),
            _ => None,
        })
        .collect()
}

/// Server-side timeout for the projection transaction. A pending claim may be
/// re-claimed `reclaim_after_ms` after `claimed_at_ms`, so the transaction
/// must end (commit or be killed) before then, leaving `margin_ms` to record
/// the outcome. `None` means too little of the window is left to start.
fn transaction_timeout_ms(
    claimed_at_ms: i64,
    reclaim_after_ms: i64,
    now_ms: i64,
    margin_ms: i64,
) -> Option<i64> {
    let remaining = claimed_at_ms
        .saturating_add(reclaim_after_ms)
        .saturating_sub(margin_ms)
        .saturating_sub(now_ms);
    (remaining > 0).then_some(remaining)
}

/// Request-level checks that need no control DB. They run before the
/// idempotency key is claimed, so a rejected request never leaves a pending
/// claim behind (which would block a corrected retry with the same key until
/// the reclaim window passes).
fn validate_bookmarks(bookmarks: Option<Vec<String>>) -> Result<Vec<String>, String> {
    let bookmarks = bookmarks.unwrap_or_default();
    if bookmarks.len() > MAX_BOOKMARKS
        || bookmarks
            .iter()
            .any(|b| b.is_empty() || b.len() > MAX_BOOKMARK_BYTES)
    {
        return Err("invalid bookmarks".into());
    }
    Ok(bookmarks)
}

fn project(driver: &Driver, request: Request) -> Response {
    let control_db = std::env::var("SEOCHOD_CONTROL_DB")
        .ok()
        .filter(|v| !v.is_empty());
    project_with(driver, request, control_db)
}

fn project_with(driver: &Driver, request: Request, control_db: Option<String>) -> Response {
    let started = Instant::now();
    let request_id = request.request_id.clone();
    let idempotency_key = request.idempotency_key.clone();
    let database = request.database.unwrap_or_else(|| "neo4j".into());
    let workspace_id = request.workspace_id.unwrap_or_else(|| "default".into());
    let source_id = request.source_id.unwrap_or_default();
    let server_now = now_secs();
    let writer_ts = request.writer_ts.unwrap_or(server_now);
    if let Err(error) = validate_writer_ts(
        writer_ts,
        server_now,
        env_ms("SEOCHOD_MAX_CLOCK_SKEW_MS", DEFAULT_MAX_CLOCK_SKEW_MS, 0),
    ) {
        return failure(error);
    }
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
    // Everything that can reject the request without the control DB is
    // checked before the idempotency key is claimed below.
    let bookmarks = match validate_bookmarks(request.bookmarks) {
        Ok(bookmarks) => bookmarks,
        Err(error) => return failure(error),
    };
    let mode = match WriteMode::from_env() {
        Ok(mode) => mode,
        Err(error) => return failure(error),
    };
    let reclaim_after_ms = env_ms("SEOCHOD_PENDING_RECLAIM_MS", DEFAULT_PENDING_RECLAIM_MS, 1);
    let mut claim: Option<(&str, &str, i64)> = None;
    // Only an admitted lease gets a storage fence; an ungoverned write
    // carries none and so cannot overwrite fenced data.
    let mut fence: Option<(i64, String)> = None;
    if let Some(control_db) = control_db.as_ref() {
        let valid_receipt = match receipt {
            Some(value) => match validate_admission(
                control_db,
                &workspace_id,
                value,
                request.admission.as_ref(),
                now_ms(),
            ) {
                Ok(lease) => {
                    // The admission-verified raw lease token is the storage
                    // fence, scoped to the lease's package.
                    fence = Some((lease.fencing_token, lease.package_id));
                    value
                }
                Err(error) => return failure_kind("admission", error),
            },
            None => {
                return failure(
                    "canonical projection requires a semantic governance receipt".into(),
                )
            }
        };
        let key = match idempotency_key.as_deref() {
            Some(value) => value,
            None => return failure("canonical projection requires an idempotency key".into()),
        };
        let claimed_at_ms = now_ms();
        match claim_idempotency(
            control_db,
            key,
            &workspace_id,
            &valid_receipt.rdf_bundle_sha256,
            claimed_at_ms,
            reclaim_after_ms,
        ) {
            Ok(Some(cached)) => return cached,
            Ok(None) => claim = Some((control_db.as_str(), key, claimed_at_ms)),
            Err(error) => return failure_kind("idempotency", error),
        }
    }
    let mut response = success();
    let writes = plan_writes(
        request.nodes.unwrap_or_default(),
        request.relationships.unwrap_or_default(),
        &WriteScope {
            workspace_id: &workspace_id,
            source_id: &source_id,
            writer_ts,
            fence: fence.as_ref().map(|(token, scope)| Fence {
                token: *token,
                scope: scope.as_str(),
            }),
            receipt,
            require_endpoint_labels: std::env::var("SEOCHOD_REQUIRE_ENDPOINT_LABELS").ok().as_deref()
                == Some("1"),
            infer_endpoint_labels: std::env::var("SEOCHOD_INFER_ENDPOINT_LABELS").ok().as_deref()
                != Some("0"),
        },
        &mut response.errors,
    );
    if response.errors.is_empty() {
        let statements = build_statements(&writes, mode);
        // A governed transaction must end before its pending claim becomes
        // re-claimable, or a second holder could overlap it; every attempt
        // gets the part of that window still left.
        let ungoverned_timeout_ms = env_ms(
            "SEOCHOD_UNGOVERNED_TX_TIMEOUT_MS",
            DEFAULT_UNGOVERNED_TX_TIMEOUT_MS,
            SERVER_DEFAULT_TX_TIMEOUT,
        );
        let mut timeout_at = |now: i64| match claim {
            Some((_, _, claimed_at_ms)) => transaction_timeout_ms(
                claimed_at_ms,
                reclaim_after_ms,
                now,
                (reclaim_after_ms / 10).min(COMPLETION_MARGIN_MS),
            ),
            None => Some(ungoverned_timeout_ms),
        };
        let mut recheck = || match (control_db.as_deref(), receipt) {
            (Some(db), Some(value)) => validate_admission(
                db,
                &workspace_id,
                value,
                request.admission.as_ref(),
                now_ms(),
            )
            .map(|_| ()),
            _ => Ok(()),
        };
        let mut meta: HashMap<String, ValueSend> = HashMap::from([
            ("app".to_string(), ValueSend::String("seochod".into())),
            ("workspace_id".to_string(), ValueSend::String(workspace_id.clone())),
            ("source_id".to_string(), ValueSend::String(source_id.clone())),
        ]);
        for (key, value) in [("request_id", &request_id), ("idempotency_key", &idempotency_key)] {
            if let Some(value) = value {
                meta.insert(key.into(), ValueSend::String(value.clone()));
            }
        }
        let context = TxContext {
            database: &database,
            source_id: &source_id,
            meta,
            bookmarks,
        };
        let budget = Budget {
            policy: RetryPolicy::from_env(now_ms()),
            timeout_at: &mut timeout_at,
            no_time: "too little of SEOCHOD_PENDING_RECLAIM_MS is left to run the projection".into(),
        };
        match execute_writes(driver, &context, &writes, &statements, budget, &mut recheck) {
            Ok(committed) => {
                response.nodes_created = committed.outcome.nodes_created;
                response.relationships_created = committed.outcome.relationships_created;
                response.merge_conflicts = committed.outcome.merge_conflicts;
                response.bookmarks = Some(committed.bookmarks);
                response.attempts = Some(committed.attempts);
            }
            Err(failure) => {
                response.error = Some(failure.message);
                response.error_kind = Some(failure.kind.into());
                response.attempts = (failure.attempts > 0).then_some(failure.attempts);
            }
        }
    } else {
        response.error_kind = Some("invalid_request".into());
    }
    response.ok = response.errors.is_empty() && response.error.is_none();
    response.request_id = request_id;
    response.duration_ms = started.elapsed().as_secs_f64() * 1000.0;
    if let Some((control_db, key, claimed_at_ms)) = claim {
        if let Err(error) = complete_idempotency(control_db, key, claimed_at_ms, &response, now_ms()) {
            eprintln!("seochod: request {:?}: {error}", response.request_id);
            // The writes may well be committed; ok=false only makes the
            // client retry, which re-runs idempotent MERGEs.
            let committed = if response.ok { "projection committed but " } else { "" };
            response.ok = false;
            response.error = Some(format!("{committed}{error}"));
            response.error_kind = Some("receipt_unrecorded".into());
        }
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
        error_kind: None,
        bookmarks: None,
        attempts: None,
    }
}

/// A request rejected before any Bolt I/O.
fn failure(error: String) -> Response {
    failure_kind("invalid_request", error)
}

fn failure_kind(kind: &str, error: String) -> Response {
    Response {
        ok: false,
        error: Some(error),
        error_kind: Some(kind.into()),
        ..success()
    }
}

/// Total budget for receiving one request frame. Processing is serial, so a
/// client that connects and stalls must not hold the daemon past this.
const REQUEST_READ_DEADLINE: Duration = Duration::from_secs(5);
const RESPONSE_WRITE_TIMEOUT: Duration = Duration::from_secs(5);

/// Caps every read at the remaining time to an absolute deadline, so a client
/// trickling bytes cannot extend the per-read timeout indefinitely.
struct DeadlineReader<'a> {
    stream: &'a UnixStream,
    deadline: Instant,
}

impl Read for DeadlineReader<'_> {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        let remaining = self
            .deadline
            .checked_duration_since(Instant::now())
            .filter(|d| !d.is_zero())
            .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::TimedOut))?;
        self.stream.set_read_timeout(Some(remaining))?;
        self.stream.read(bytes)
    }
}

/// Reads exactly one newline-terminated frame of at most `MAX_REQUEST_BYTES`
/// (newline included) without ever buffering more than that.
fn decode_request(reader: impl Read) -> Result<Request, &'static str> {
    let mut bytes = Vec::new();
    BufReader::new(reader.take((MAX_REQUEST_BYTES + 1) as u64))
        .read_until(b'\n', &mut bytes)
        .map_err(|_| "invalid request")?;
    if bytes.len() > MAX_REQUEST_BYTES || bytes.last() != Some(&b'\n') {
        return Err("invalid request frame");
    }
    serde_json::from_slice(&bytes).map_err(|_| "invalid json")
}

fn handle(mut stream: UnixStream, driver: &Driver) {
    if stream
        .set_write_timeout(Some(RESPONSE_WRITE_TIMEOUT))
        .is_err()
    {
        return;
    }
    let response = match decode_request(DeadlineReader {
        stream: &stream,
        deadline: Instant::now() + REQUEST_READ_DEADLINE,
    }) {
        Ok(request) if request.op == "health" => success(),
        Ok(request) if request.op == "project" => project(driver, request),
        Ok(_) => failure("unsupported operation".into()),
        Err(error) => failure(error.into()),
    };
    if let Ok(payload) = serde_json::to_string(&response) {
        let _ = writeln!(stream, "{payload}");
    }
}

/// Without a lifecycle control DB the daemon cannot check admission, so it
/// would project any well-formed payload. That must be an explicit opt-in.
fn check_governance_mode(
    control_db: Option<&str>,
    allow_ungoverned: Option<&str>,
) -> Result<(), String> {
    match (control_db, allow_ungoverned) {
        (Some(db), _) if !db.is_empty() => Ok(()),
        (_, Some("1")) => {
            eprintln!("seochod: SEOCHOD_CONTROL_DB unset; running UNGOVERNED (SEOCHOD_ALLOW_UNGOVERNED=1)");
            Ok(())
        }
        _ => Err("SEOCHOD_CONTROL_DB is required; set SEOCHOD_ALLOW_UNGOVERNED=1 to run without lifecycle admission".into()),
    }
}

/// Only a stale socket left by a dead daemon may be removed. A regular file or
/// a socket that still accepts connections belongs to someone else.
fn clear_stale_socket(socket_path: &Path) -> Result<(), String> {
    let metadata = match fs::symlink_metadata(socket_path) {
        Ok(metadata) => metadata,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(format!("cannot inspect socket path: {e}")),
    };
    if !metadata.file_type().is_socket() {
        return Err(format!("{} exists and is not a socket; refusing to remove it", socket_path.display()));
    }
    match UnixStream::connect(socket_path) {
        Ok(_) => Err(format!("{} is in use by another process (socket in use)", socket_path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::ConnectionRefused => {
            fs::remove_file(socket_path).map_err(|e| format!("cannot remove stale socket: {e}"))
        }
        Err(e) => Err(format!("cannot probe existing socket {}: {e}", socket_path.display())),
    }
}

/// Binds and restricts the socket to the owner. A short bind->chmod window
/// remains; the README requires a 0700 parent directory to close it.
fn bind_socket(socket_path: &Path) -> Result<UnixListener, String> {
    clear_stale_socket(socket_path)?;
    let listener = UnixListener::bind(socket_path).map_err(|e| e.to_string())?;
    fs::set_permissions(socket_path, fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("cannot restrict socket permissions: {e}"))?;
    Ok(listener)
}

fn main() -> Result<(), String> {
    let socket = std::env::args()
        .nth(1)
        .ok_or("usage: seochod <unix-socket-path>")?;
    let socket_path = Path::new(&socket);
    check_governance_mode(
        std::env::var("SEOCHOD_CONTROL_DB").ok().as_deref(),
        std::env::var("SEOCHOD_ALLOW_UNGOVERNED").ok().as_deref(),
    )?;
    let driver = bolt::driver_from_env()?;
    let listener = bind_socket(socket_path)?;
    for stream in listener.incoming().flatten() {
        handle(stream, &driver);
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
    fn request_frames_are_bounded_and_newline_terminated() {
        assert!(decode_request(&b"{\"op\":\"health\"}\n"[..]).is_ok());
        assert_eq!(
            decode_request(&b"{\"op\":\"health\"}"[..]).err(),
            Some("invalid request frame")
        );
        // An oversized frame is rejected after reading at most the cap + 1.
        let oversized = vec![b'x'; MAX_REQUEST_BYTES * 2];
        assert_eq!(
            decode_request(oversized.as_slice()).err(),
            Some("invalid request frame")
        );
        let mut at_cap = vec![b' '; MAX_REQUEST_BYTES - 1];
        at_cap[..15].copy_from_slice(b"{\"op\":\"health\"}");
        at_cap.push(b'\n');
        assert!(decode_request(at_cap.as_slice()).is_ok());
    }

    #[test]
    fn stalled_client_hits_read_deadline() -> Result<(), Box<dyn std::error::Error>> {
        let (server, mut client) = UnixStream::pair()?;
        client.write_all(b"{\"op\":")?;
        let started = Instant::now();
        let result = decode_request(DeadlineReader {
            stream: &server,
            deadline: Instant::now() + Duration::from_millis(50),
        });
        assert_eq!(result.err(), Some("invalid request"));
        assert!(started.elapsed() < Duration::from_secs(2));
        Ok(())
    }

    fn temp_path(tag: &str) -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
        // Parallel tests can read the same clock value; the counter keeps
        // every path unique within this process.
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        Ok(std::env::temp_dir().join(format!(
            "seochod-{tag}-{}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
            SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
        )))
    }

    #[test]
    fn ungoverned_mode_requires_explicit_opt_in() {
        assert!(check_governance_mode(Some("/tmp/control.sqlite"), None).is_ok());
        assert!(check_governance_mode(None, None).is_err());
        assert!(check_governance_mode(Some(""), None).is_err());
        assert!(check_governance_mode(None, Some("0")).is_err());
        assert!(check_governance_mode(None, Some("1")).is_ok());
    }

    #[test]
    fn socket_policy_refuses_foreign_paths_and_clears_stale() -> Result<(), Box<dyn std::error::Error>> {
        let regular = temp_path("regular")?;
        fs::write(&regular, b"not a socket")?;
        assert!(bind_socket(&regular).is_err());
        assert!(regular.exists());
        fs::remove_file(&regular)?;

        let live = temp_path("live")?;
        let listener = bind_socket(&live)?;
        assert_eq!(fs::metadata(&live)?.permissions().mode() & 0o777, 0o600);
        let err = bind_socket(&live).err().ok_or("expected socket in use")?;
        assert!(err.contains("in use"));
        drop(listener);

        // The listener is gone but its socket file remains: that is stale.
        assert!(live.exists());
        let rebound = bind_socket(&live)?;
        drop(rebound);
        fs::remove_file(&live)?;
        Ok(())
    }

    #[test]
    fn writer_ts_must_be_finite_and_within_skew() {
        let now = 1_700_000_000.0;
        assert!(validate_writer_ts(now, now, 300_000).is_ok());
        assert!(validate_writer_ts(now - 86_400.0, now, 300_000).is_ok());
        assert!(validate_writer_ts(now + 299.0, now, 300_000).is_ok());
        let err = validate_writer_ts(now + 301.0, now, 300_000)
            .err()
            .unwrap_or_default();
        assert!(err.contains("ahead of daemon time"));
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let err = validate_writer_ts(bad, now, 300_000).err().unwrap_or_default();
            assert!(err.contains("finite"));
        }
    }

    #[test]
    fn nested_properties_are_rejected() {
        assert!(value_send(&serde_json::json!({"bad": true})).is_err());
    }

    #[test]
    fn admission_rejects_stale_active_pointer() -> Result<(), Box<dyn std::error::Error>> {
        let path = std::env::temp_dir().join(format!(
            "seochod-admission-{}.sqlite",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)?
                .as_nanos()
        ));
        let path_str = path.to_str().ok_or("non-utf8 temp path")?;
        let conn = Connection::open(&path)?;
        conn.execute_batch(
            "CREATE TABLE active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY(workspace_id, package_id));
             CREATE TABLE ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT, package_id TEXT, purpose TEXT, owner TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, acquired_at_ms INTEGER, expires_at_ms INTEGER);",
        )?;
        let digest = "a".repeat(64);
        conn.execute(
            "INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,1)",
            [&digest],
        )?;
        conn.execute("INSERT INTO ontology_lease VALUES ('lease','ws','pkg','projection','owner',?1,0,0,2,0,9999999999999)", [&digest])?;
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
            validate_admission(path_str, "ws", &receipt, Some(&admission), 1).is_ok()
        );
        let conn = Connection::open(&path)?;
        conn.execute(
            "UPDATE active_ontology SET epoch=1 WHERE workspace_id='ws'",
            [],
        )?;
        drop(conn);
        let err = validate_admission(path_str, "ws", &receipt, Some(&admission), 1)
            .err()
            .ok_or("expected stale admission error")?;
        assert!(err.contains("stale"));
        // Defense in depth: the lifecycle store never moves the pointer token
        // without bumping epoch or generation, but a pointer token caught up
        // with the lease token must still supersede the lease.
        let conn = Connection::open(&path)?;
        conn.execute(
            "UPDATE active_ontology SET epoch=0, fencing_token=2 WHERE workspace_id='ws'",
            [],
        )?;
        drop(conn);
        let err = validate_admission(path_str, "ws", &receipt, Some(&admission), 1)
            .err()
            .ok_or("expected fenced-off admission error")?;
        assert!(err.contains("fenced off"));
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn write_queries_guard_fencing_token_and_writer_ts() {
        let node = node_merge_query("Person");
        let rel = relationship_merge_query(":Person", "", "KNOWS");
        for (query, var) in [(&node, "n"), (&rel, "r")] {
            // The exact expression `cypher_fence_ok` evaluates; changing the
            // guard must fail here until the mirror is updated too.
            assert!(query.contains(&format!(
                "coalesce({var}._fencing_token IS NULL OR ({var}._fence_scope IS NOT NULL AND {var}._fence_scope <> $fence_scope) OR {var}._fencing_token <= $fence, false) AS fence_ok"
            )));
            assert!(query.contains("._writer_ts <= $props._writer_ts) AS fresh"));
            assert!(query.contains("CASE WHEN fence_ok AND fresh THEN $props"));
            assert!(query.contains("RETURN fence_ok, "));
        }
        assert!(node.starts_with("MERGE (n:Person {id: $id, _workspace_id: $ws})"));
        assert!(node.contains("{property: k, existing: toString(n[k]), incoming: toString($props[k])}"));
        assert!(rel.starts_with("MATCH (a:Person {id: $src, _workspace_id: $ws}), (b {id: $tgt"));
        assert!(rel.contains("MERGE (a)-[r:KNOWS]->(b)"));
    }

    #[test]
    fn conflicts_match_python_writer_shape() {
        let entry = ValueReceive::Map(HashMap::from([
            ("property".to_string(), ValueReceive::String("name".into())),
            ("existing".to_string(), ValueReceive::String("A".into())),
            ("incoming".to_string(), ValueReceive::String("B".into())),
        ]));
        let list = ValueReceive::List(vec![entry]);
        assert_eq!(
            conflict_entries(Some(&list), "Person", "a", "doc"),
            vec![serde_json::json!({"label": "Person", "key": "a", "property": "name", "existing": "A", "incoming": "B", "source_id": "doc"})]
        );
        assert!(conflict_entries(None, "Person", "a", "doc").is_empty());
    }

    #[test]
    fn transaction_ends_before_claim_is_reclaimable() {
        assert_eq!(transaction_timeout_ms(1_000, 600_000, 1_000, 5_000), Some(595_000));
        assert_eq!(transaction_timeout_ms(1_000, 600_000, 300_000, 5_000), Some(296_000));
        assert_eq!(transaction_timeout_ms(1_000, 600_000, 596_000, 5_000), None);
        assert_eq!(transaction_timeout_ms(1_000, 600_000, 900_000, 5_000), None);
    }

    /// Evaluates `fence_guard` with Cypher's three-valued logic: `existing`
    /// is the item's `(_fencing_token, _fence_scope)` (`None` = never fenced)
    /// and `incoming` the request's `($fence, $fence_scope)` (`None` =
    /// ungoverned). Mirrors the query text asserted in
    /// `write_queries_guard_fencing_token_and_writer_ts`.
    fn cypher_fence_ok(existing: Option<(i64, Option<&str>)>, incoming: Option<(i64, &str)>) -> bool {
        let Some((token, scope)) = existing else {
            return true; // `_fencing_token IS NULL`
        };
        let other_scope = scope.and_then(|s| incoming.map(|(_, cur)| s != cur));
        let not_newer = incoming.map(|(fence, _)| token <= fence);
        // OR is true when any operand is true and NULL or false otherwise;
        // coalesce(NULL, false) is false.
        other_scope == Some(true) || not_newer == Some(true)
    }

    type LifecycleDb = (std::path::PathBuf, String, String);

    const LIVE: i64 = 9_999_999_999_999;

    /// Lease rows are `(lease_id, package_id, purpose, fencing_token,
    /// expires_at_ms)`. Fixtures must be states Python `acquire` can reach:
    /// at most one live lease per (package, purpose).
    type LeaseFixture<'a> = (&'a str, &'a str, &'a str, i64, i64);

    fn lifecycle_db(leases: &[LeaseFixture]) -> Result<LifecycleDb, Box<dyn std::error::Error>> {
        let path = temp_path("lifecycle")?.with_extension("sqlite");
        let path_str = path.to_str().ok_or("non-utf8 temp path")?.to_string();
        let conn = Connection::open(&path)?;
        conn.execute_batch(
            "CREATE TABLE active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY(workspace_id, package_id));
             CREATE TABLE ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT, package_id TEXT, purpose TEXT, owner TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, acquired_at_ms INTEGER, expires_at_ms INTEGER);
             CREATE TABLE projection_idempotency (idempotency_key TEXT PRIMARY KEY, workspace_id TEXT, fingerprint TEXT, state TEXT, response_json TEXT, created_at_ms INTEGER, completed_at_ms INTEGER);",
        )?;
        let digest = "a".repeat(64);
        for pkg in ["pkg", "other"] {
            conn.execute(
                "INSERT INTO active_ontology VALUES ('ws',?1,'1.0',?2,0,0,1)",
                (pkg, &digest),
            )?;
        }
        for (lease, pkg, purpose, token, expires) in leases {
            conn.execute(
                "INSERT INTO ontology_lease VALUES (?1,'ws',?2,?3,'owner',?4,0,0,?5,0,?6)",
                (lease, pkg, purpose, &digest, token, expires),
            )?;
        }
        Ok((path, path_str, digest))
    }

    fn admit(db: &str, digest: &str, lease_id: &str, token: i64) -> Result<AdmittedLease, String> {
        let receipt = SemanticReceipt {
            schema_version: "seocho.canonical_projection_receipt.v1".into(),
            rdf_bundle_sha256: digest.into(),
            rdf_data_graph_sha256: "b".repeat(64),
            agent_profile_sha256: "c".repeat(64),
            projection_receipt_sha256: "d".repeat(64),
        };
        let admission = ProjectionAdmission {
            lease_id: lease_id.into(),
            fingerprint: digest.into(),
            generation: 0,
            epoch: 0,
            fencing_token: token,
        };
        validate_admission(db, "ws", &receipt, Some(&admission), 1)
    }

    fn control_tables(path: &std::path::Path) -> Result<Vec<String>, Box<dyn std::error::Error>> {
        let conn = Connection::open(path)?;
        let mut stmt = conn.prepare("SELECT name FROM sqlite_master ORDER BY name")?;
        let names = stmt
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(names)
    }

    #[test]
    fn admitted_raw_lease_token_is_the_storage_fence() -> Result<(), Box<dyn std::error::Error>> {
        // Pointer token 1. `pkg`: projection lease A (token 3) expired and
        // was succeeded by live projection lease B (token 5). `other`: live
        // projection lease C whose token is lower than both.
        let (path, db, digest) = lifecycle_db(&[
            ("A", "pkg", "projection", 3, 0),
            ("B", "pkg", "projection", 5, LIVE),
            ("C", "other", "projection", 2, LIVE),
        ])?;
        let before = control_tables(&path)?;
        let err = admit(&db, &digest, "A", 3)
            .err()
            .ok_or("expired lease must be rejected")?;
        assert!(err.contains("expired"));
        let b = admit(&db, &digest, "B", 5)?;
        let c = admit(&db, &digest, "C", 2)?;
        assert_eq!((b.package_id.as_str(), b.fencing_token), ("pkg", 5));
        assert_eq!((c.package_id.as_str(), c.fencing_token), ("other", 2));
        // The capability must carry the lease row's token exactly.
        let err = admit(&db, &digest, "B", 4)
            .err()
            .ok_or("token mismatch must be rejected")?;
        assert!(err.contains("capability mismatch"));
        // Admission and idempotency change no schema: seochod creates no
        // tables of its own in the control DB.
        assert!(claim_idempotency(&db, "key", "ws", &digest, 1, RECLAIM)?.is_none());
        assert_eq!(control_tables(&path)?, before);
        // The raw lease token is what gets stamped.
        let nodes: Vec<Node> = serde_json::from_value(
            serde_json::json!([{"id": "x", "label": "Person", "properties": {}}]),
        )?;
        let mut errors = vec![];
        let scope = WriteScope {
            workspace_id: "ws",
            source_id: "src",
            writer_ts: 1.0,
            fence: Some(Fence {
                token: b.fencing_token,
                scope: &b.package_id,
            }),
            receipt: None,
            require_endpoint_labels: false,
            infer_endpoint_labels: true,
        };
        let writes = plan_writes(nodes, vec![], &scope, &mut errors);
        assert!(errors.is_empty());
        let props = writes.first().and_then(props_of).ok_or("props must be a map")?;
        assert_eq!(props.get("_fencing_token"), Some(&ValueSend::Integer(5)));
        assert_eq!(props.get("_fence_scope"), Some(&ValueSend::String("pkg".into())));
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn query_purpose_lease_is_not_admitted() -> Result<(), Box<dyn std::error::Error>> {
        // One live lease per purpose of `pkg`, as Python `acquire` allows:
        // the query lease was acquired later and holds the higher token.
        let (path, db, digest) = lifecycle_db(&[
            ("P", "pkg", "projection", 3, LIVE),
            ("Q", "pkg", "query", 4, LIVE),
        ])?;
        let err = admit(&db, &digest, "Q", 4)
            .err()
            .ok_or("query lease must be rejected")?;
        assert_eq!(err, "lifecycle lease is not a projection lease");
        assert_eq!(admit(&db, &digest, "P", 3)?.fencing_token, 3);
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn lower_token_in_same_scope_is_fenced_and_other_scopes_are_not_compared() {
        // An item projected by the newer lease B (token 5) of `pkg`.
        let item = Some((5, Some("pkg")));
        // The older live lease A (token 3) of the same package projects
        // later: fenced, so the caller rolls the whole request back.
        assert!(!cypher_fence_ok(item, Some((3, "pkg"))));
        // The same or a newer lease of the package may write.
        assert!(cypher_fence_ok(item, Some((5, "pkg"))));
        assert!(cypher_fence_ok(item, Some((6, "pkg"))));
        // Another package's tokens are not comparable; a lower one passes.
        assert!(cypher_fence_ok(item, Some((2, "other"))));
        // Ungoverned writes never overwrite fenced items, only unfenced ones.
        assert!(!cypher_fence_ok(item, None));
        assert!(cypher_fence_ok(None, None));
        assert!(cypher_fence_ok(None, Some((3, "pkg"))));
        // A fenced item without a scope is compared by token alone.
        assert!(!cypher_fence_ok(Some((5, None)), Some((3, "other"))));
    }

    type Planned = (Vec<PlannedWrite>, Vec<String>);

    fn planned(fence: Option<i64>, nodes: Value, rels: Value) -> Result<Planned, serde_json::Error> {
        let nodes: Vec<Node> = serde_json::from_value(nodes)?;
        let rels: Vec<Relationship> = serde_json::from_value(rels)?;
        let mut errors = vec![];
        let scope = WriteScope {
            workspace_id: "ws",
            source_id: "src",
            writer_ts: 1.0,
            fence: fence.map(|token| Fence { token, scope: "pkg" }),
            receipt: None,
            require_endpoint_labels: false,
            infer_endpoint_labels: true,
        };
        Ok((plan_writes(nodes, rels, &scope, &mut errors), errors))
    }

    fn props_of(write: &PlannedWrite) -> Option<&HashMap<String, ValueSend>> {
        match write.params.get("props") {
            Some(ValueSend::Map(props)) => Some(props),
            _ => None,
        }
    }

    #[test]
    fn planned_writes_stamp_fence_and_reject_whole_request() -> Result<(), Box<dyn std::error::Error>> {
        let node = serde_json::json!([{"id": "a", "label": "Person", "properties": {"name": "A"}}]);
        let rel = serde_json::json!([{"source": "a", "target": "b", "type": "KNOWS", "properties": {}}]);
        let (writes, errors) = planned(Some(7), node.clone(), rel.clone())?;
        assert!(errors.is_empty());
        assert_eq!(writes.len(), 2);
        assert_eq!(writes[0].node, Some(("Person".into(), "a".into())));
        assert!(writes[1].node.is_none());
        for write in &writes {
            assert_eq!(write.params.get("fence"), Some(&ValueSend::Integer(7)));
            assert_eq!(write.params.get("fence_scope"), Some(&ValueSend::String("pkg".into())));
            let props = props_of(write).ok_or("props must be a map")?;
            assert_eq!(props.get("_fencing_token"), Some(&ValueSend::Integer(7)));
            assert_eq!(props.get("_fence_scope"), Some(&ValueSend::String("pkg".into())));
        }
        // Ungoverned: no token is stamped and $fence is NULL.
        let (writes, _) = planned(None, node, rel)?;
        assert_eq!(writes[0].params.get("fence"), Some(&ValueSend::Null));
        assert_eq!(writes[0].params.get("fence_scope"), Some(&ValueSend::Null));
        let props = props_of(&writes[0]).ok_or("props must be a map")?;
        assert!(!props.contains_key("_fencing_token"));
        assert!(!props.contains_key("_fence_scope"));
        // One malformed item is reported so the caller writes nothing.
        let (_, errors) = planned(
            Some(7),
            serde_json::json!([{"id": "a", "label": "Bad:Label", "properties": {}}]),
            serde_json::json!([]),
        )?;
        assert_eq!(errors.len(), 1);
        Ok(())
    }

    fn idempotency_db() -> Result<(std::path::PathBuf, String), Box<dyn std::error::Error>> {
        let path = temp_path("idempotency")?.with_extension("sqlite");
        let path_str = path.to_str().ok_or("non-utf8 temp path")?.to_string();
        let conn = Connection::open(&path)?;
        conn.execute_batch("CREATE TABLE projection_idempotency (idempotency_key TEXT PRIMARY KEY, workspace_id TEXT, fingerprint TEXT, state TEXT, response_json TEXT, created_at_ms INTEGER, completed_at_ms INTEGER);")?;
        Ok((path, path_str))
    }

    const RECLAIM: i64 = 1_000;

    #[test]
    fn completed_idempotency_key_replays_receipt_not_write_permission() -> Result<(), Box<dyn std::error::Error>> {
        let (path, db) = idempotency_db()?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 1, RECLAIM)?.is_none());
        complete_idempotency(&db, "key", 1, &success(), 2)?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 3, RECLAIM)?.is_some());
        // Even long after, a completed key replays rather than re-writing.
        assert!(claim_idempotency(&db, "key", "ws", "a", 10 * RECLAIM, RECLAIM)?.is_some());
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn failed_projection_is_not_cached_and_is_reclaimable() -> Result<(), Box<dyn std::error::Error>> {
        let (path, db) = idempotency_db()?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 1, RECLAIM)?.is_none());
        complete_idempotency(&db, "key", 1, &failure("boom".into()), 2)?;
        let state: String = Connection::open(&path)?.query_row(
            "SELECT state FROM projection_idempotency WHERE idempotency_key='key'",
            [],
            |r| r.get(0),
        )?;
        assert_eq!(state, "failed");
        // A retry re-claims immediately instead of replaying the failure.
        assert!(claim_idempotency(&db, "key", "ws", "a", 3, RECLAIM)?.is_none());
        // The re-claim is pending again, so a concurrent retry is blocked...
        assert!(claim_idempotency(&db, "key", "ws", "a", 4, RECLAIM).is_err());
        // ...and the new holder can record success.
        complete_idempotency(&db, "key", 3, &success(), 5)?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 6, RECLAIM)?.is_some_and(|r| r.ok));
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn fresh_pending_blocks_and_stale_pending_is_reclaimed() -> Result<(), Box<dyn std::error::Error>> {
        let (path, db) = idempotency_db()?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 1, RECLAIM)?.is_none());
        let err = claim_idempotency(&db, "key", "ws", "a", RECLAIM, RECLAIM)
            .err()
            .ok_or("fresh pending must block")?;
        assert!(err.contains("pending"));
        // Past the reclaim age the abandoned claim is taken over.
        assert!(claim_idempotency(&db, "key", "ws", "a", RECLAIM + 2, RECLAIM)?.is_none());
        // The original holder lost its claim and must not overwrite the new one.
        assert!(complete_idempotency(&db, "key", 1, &success(), RECLAIM + 3).is_err());
        complete_idempotency(&db, "key", RECLAIM + 2, &success(), RECLAIM + 4)?;
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn idempotency_scope_mismatch_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
        let (path, db) = idempotency_db()?;
        assert!(claim_idempotency(&db, "key", "ws", "a", 1, RECLAIM)?.is_none());
        complete_idempotency(&db, "key", 1, &failure("boom".into()), 2)?;
        for (ws, fp) in [("other", "a"), ("ws", "b")] {
            let err = claim_idempotency(&db, "key", ws, fp, 10 * RECLAIM, RECLAIM)
                .err()
                .ok_or("scope mismatch must be rejected")?;
            assert!(err.contains("another scope"));
        }
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn unwind_queries_keep_every_guard() {
        let node = batch::node_unwind_query("Person");
        let rel = batch::relationship_unwind_query(":Person", ":Org", "WORKS_AT");
        for (query, var) in [(&node, "n"), (&rel, "r")] {
            assert!(query.starts_with("UNWIND $rows AS row "));
            assert!(query.contains(&fence_guard(var)));
            assert!(query.contains(&format!(
                "({var}._writer_ts IS NULL OR {var}._writer_ts <= row.props._writer_ts) AS fresh"
            )));
            assert!(query.contains("CASE WHEN fence_ok AND fresh THEN row.props ELSE {} END"));
            assert!(query.contains("RETURN row.i AS i, fence_ok"));
        }
        assert!(node.contains("MERGE (n:Person {id: row.id, _workspace_id: $ws})"));
        assert!(node.contains(&node_conflicts("row.props")));
        assert!(node.contains("{property: k, existing: toString(n[k]), incoming: toString(row.props[k])}"));
        assert!(rel.contains("MATCH (a:Person {id: row.src, _workspace_id: $ws}), (b:Org {id: row.tgt, _workspace_id: $ws})"));
        assert!(rel.contains("MERGE (a)-[r:WORKS_AT]->(b)"));
        // The per-item shapes are unchanged by the refactor.
        assert_eq!(
            node_conflicts("$props"),
            "[k IN keys($props) WHERE NOT k STARTS WITH '_' AND k <> 'id' AND n[k] IS NOT NULL AND n[k] <> $props[k] | {property: k, existing: toString(n[k]), incoming: toString($props[k])}] AS conflicts"
        );
    }

    fn rows_of(statement: &Statement) -> Vec<HashMap<String, ValueSend>> {
        match statement.params.get("rows") {
            Some(ValueSend::List(rows)) => rows
                .iter()
                .filter_map(|row| match row {
                    ValueSend::Map(row) => Some(row.clone()),
                    _ => None,
                })
                .collect(),
            _ => vec![],
        }
    }

    #[test]
    fn batches_group_by_label_and_split_on_repeated_keys() -> Result<(), Box<dyn std::error::Error>> {
        let nodes = serde_json::json!([
            {"id": "a", "label": "Person", "properties": {"name": "A"}},
            {"id": "x", "label": "Org", "properties": {}},
            {"id": "b", "label": "Person", "properties": {}},
            {"id": "a", "label": "Person", "properties": {"name": "A2"}},
            {"id": "c", "label": "Person", "properties": {}},
        ]);
        let rels = serde_json::json!([
            {"source": "a", "target": "x", "type": "WORKS_AT", "properties": {}},
            {"source": "b", "target": "x", "type": "WORKS_AT", "properties": {}},
        ]);
        let (writes, errors) = planned(Some(7), nodes, rels)?;
        assert!(errors.is_empty());
        let statements = build_statements(&writes, WriteMode::Unwind { max_rows: 1000 });
        // Person [a, b] and Org [x]; the repeated `a` must run after the
        // statement holding the first `a`, so it opens Person [a, c]; one
        // relationship group.
        let items: Vec<Vec<usize>> = statements.iter().map(|s| s.items.clone()).collect();
        assert_eq!(items, vec![vec![0, 2], vec![1], vec![3, 4], vec![5, 6]]);
        assert!(statements.iter().all(|s| s.unwind));
        assert_eq!(
            statements.iter().map(|s| s.node).collect::<Vec<_>>(),
            vec![true, true, true, false]
        );
        let rows = rows_of(&statements[2]);
        assert_eq!(rows[0].get("i"), Some(&ValueSend::Integer(0)));
        assert_eq!(rows[0].get("id"), Some(&ValueSend::String("a".into())));
        assert_eq!(rows[1].get("i"), Some(&ValueSend::Integer(1)));
        let props = match rows[0].get("props") {
            Some(ValueSend::Map(props)) => props.clone(),
            _ => HashMap::new(),
        };
        assert_eq!(props.get("name"), Some(&ValueSend::String("A2".into())));
        assert_eq!(props.get("_fencing_token"), Some(&ValueSend::Integer(7)));
        for statement in &statements {
            assert_eq!(statement.params.get("fence"), Some(&ValueSend::Integer(7)));
            assert_eq!(statement.params.get("fence_scope"), Some(&ValueSend::String("pkg".into())));
            assert_eq!(statement.params.get("ws"), Some(&ValueSend::String("ws".into())));
        }
        let rel_rows = rows_of(&statements[3]);
        assert_eq!(rel_rows[1].get("src"), Some(&ValueSend::String("b".into())));
        assert_eq!(rel_rows[1].get("tgt"), Some(&ValueSend::String("x".into())));
        assert!(statements[3].query.contains("(a:Person {id: row.src"));
        assert!(statements[3].query.contains("(b:Org {id: row.tgt"));
        // Row cap.
        let capped = build_statements(&writes, WriteMode::Unwind { max_rows: 1 });
        assert_eq!(capped.len(), writes.len());
        // Per-item mode keeps the old statements one to one.
        let legacy = build_statements(&writes, WriteMode::PerItem);
        assert_eq!(legacy.len(), writes.len());
        assert!(legacy.iter().all(|s| !s.unwind && s.items.len() == 1));
        assert_eq!(legacy[0].query, node_merge_query("Person"));
        Ok(())
    }

    /// Position of each write's statement, in statement order.
    fn statement_of(statements: &[Statement], writes: usize) -> Vec<usize> {
        let mut of = vec![usize::MAX; writes];
        for (position, statement) in statements.iter().enumerate() {
            for index in &statement.items {
                of[*index] = position;
            }
        }
        of
    }

    #[test]
    fn batches_keep_request_order_for_one_entity_across_groups() -> Result<(), Box<dyn std::error::Error>> {
        // A multi-label node written as Person, Org, Person and a
        // relationship given with labels, without, and with again: each
        // write of one entity must run strictly after the previous one.
        let nodes = serde_json::json!([
            {"id": "m", "label": "Person", "properties": {"v": 1}},
            {"id": "m", "label": "Org", "properties": {"v": 2}},
            {"id": "m", "label": "Person", "properties": {"v": 3}},
            {"id": "z", "label": "Org", "properties": {}},
        ]);
        let rels = serde_json::json!([
            {"source": "a", "target": "o", "type": "R", "source_label": "Person", "target_label": "Org", "properties": {"w": 1}},
            {"source": "a", "target": "o", "type": "R", "properties": {"w": 2}},
            {"source": "a", "target": "o", "type": "R", "source_label": "Person", "target_label": "Org", "properties": {"w": 3}},
            {"source": "b", "target": "o", "type": "R", "source_label": "Person", "target_label": "Org", "properties": {}},
        ]);
        let (writes, errors) = planned(None, nodes, rels)?;
        assert!(errors.is_empty());
        for max_rows in [1, 2, 1000] {
            let statements = build_statements(&writes, WriteMode::Unwind { max_rows });
            let of = statement_of(&statements, writes.len());
            assert!(of.iter().all(|s| *s != usize::MAX));
            assert!(of[0] < of[1] && of[1] < of[2], "node m reordered: {of:?}");
            assert!(of[4] < of[5] && of[5] < of[6], "relationship a-R->o reordered: {of:?}");
            // Nodes still precede relationships.
            assert!(of[..4].iter().all(|n| of[4..].iter().all(|r| n < r)));
        }
        let statements = build_statements(&writes, WriteMode::Unwind { max_rows: 1000 });
        let items: Vec<Vec<usize>> = statements.iter().map(|s| s.items.clone()).collect();
        // Unrelated items still share their group's statement: `z` joins
        // the Org statement, and b-R->o the newest labelled R statement.
        assert_eq!(items, vec![vec![0], vec![1, 3], vec![2], vec![4], vec![5], vec![6, 7]]);
        Ok(())
    }

    #[test]
    fn endpoint_label_inference_can_be_turned_off() -> Result<(), Box<dyn std::error::Error>> {
        let nodes: Vec<Node> =
            serde_json::from_value(serde_json::json!([{"id": "a", "label": "Person", "properties": {}}]))?;
        let rels: Vec<Relationship> = serde_json::from_value(serde_json::json!([
            {"source": "a", "target": "a", "type": "KNOWS", "properties": {}}
        ]))?;
        let mut errors = vec![];
        let scope = WriteScope {
            workspace_id: "ws",
            source_id: "src",
            writer_ts: 1.0,
            fence: None,
            receipt: None,
            require_endpoint_labels: false,
            infer_endpoint_labels: false,
        };
        let writes = plan_writes(nodes, rels, &scope, &mut errors);
        assert!(errors.is_empty());
        assert_eq!(
            writes[1].target,
            Target::Relationship {
                kind: "KNOWS".into(),
                source_label: "".into(),
                target_label: "".into(),
            }
        );
        Ok(())
    }

    #[test]
    fn endpoint_labels_resolve_from_request_nodes() -> Result<(), Box<dyn std::error::Error>> {
        let nodes = serde_json::json!([
            {"id": "a", "label": "Person", "properties": {}},
            {"id": "dup", "label": "Person", "properties": {}},
            {"id": "dup", "label": "Org", "properties": {}},
        ]);
        let rels = serde_json::json!([
            {"source": "a", "target": "elsewhere", "type": "KNOWS", "properties": {}},
            {"source": "dup", "target": "a", "type": "KNOWS", "target_label": "Robot", "properties": {}},
        ]);
        let (writes, errors) = planned(None, nodes.clone(), rels.clone())?;
        assert!(errors.is_empty());
        assert_eq!(
            writes[3].target,
            Target::Relationship {
                kind: "KNOWS".into(),
                source_label: ":Person".into(),
                target_label: "".into(),
            }
        );
        // An ambiguous id is not resolved and a given label always wins.
        assert_eq!(
            writes[4].target,
            Target::Relationship {
                kind: "KNOWS".into(),
                source_label: "".into(),
                target_label: ":Robot".into(),
            }
        );
        let nodes: Vec<Node> = serde_json::from_value(nodes)?;
        let rels: Vec<Relationship> = serde_json::from_value(rels)?;
        let mut errors = vec![];
        let scope = WriteScope {
            workspace_id: "ws",
            source_id: "src",
            writer_ts: 1.0,
            fence: None,
            receipt: None,
            require_endpoint_labels: true,
            infer_endpoint_labels: true,
        };
        let writes = plan_writes(nodes, rels, &scope, &mut errors);
        assert_eq!(writes.len(), 3);
        assert_eq!(errors.len(), 2);
        assert!(errors[0].contains("endpoint label"));
        Ok(())
    }

    #[test]
    fn property_values_are_validated_before_bolt() -> Result<(), Box<dyn std::error::Error>> {
        let parse = |raw: &str| -> Result<Result<ValueSend, String>, serde_json::Error> {
            Ok(value_send(&serde_json::from_str::<Value>(raw)?))
        };
        assert_eq!(parse("9223372036854775807")?, Ok(ValueSend::Integer(i64::MAX)));
        assert_eq!(parse("-9223372036854775808")?, Ok(ValueSend::Integer(i64::MIN)));
        assert!(parse("18446744073709551615")?.err().unwrap_or_default().contains("64-bit"));
        assert_eq!(parse("1.5")?, Ok(ValueSend::Float(1.5)));
        assert_eq!(parse("null")?, Ok(ValueSend::Null));
        assert_eq!(parse("[]")?, Ok(ValueSend::List(vec![])));
        assert_eq!(
            parse("[1, 2.5]")?,
            Ok(ValueSend::List(vec![ValueSend::Integer(1), ValueSend::Float(2.5)]))
        );
        assert!(parse("[\"a\", \"b\"]")?.is_ok());
        for bad in ["[1, \"a\"]", "[1, null]", "[[1]]", "[true, 1]", "[{\"a\": 1}]", "{\"a\": 1}"] {
            assert!(parse(bad)?.is_err(), "{bad} must be rejected");
        }
        Ok(())
    }

    #[test]
    fn response_extensions_are_optional_on_the_wire() -> Result<(), Box<dyn std::error::Error>> {
        // A receipt stored before bookmarks/error_kind existed still replays.
        let old = r#"{"ok":true,"nodes_created":1,"relationships_created":0,"errors":[],"merge_conflicts":[],"driver":"rust-neo4j","error":null,"request_id":"r","duration_ms":1.0}"#;
        let replay: Response = serde_json::from_str(old)?;
        assert!(replay.ok && replay.bookmarks.is_none() && replay.error_kind.is_none());
        let encoded = serde_json::to_value(success())?;
        for absent in ["bookmarks", "error_kind", "attempts"] {
            assert!(encoded.get(absent).is_none());
        }
        let mut done = success();
        done.bookmarks = Some(vec!["FB:x".into()]);
        assert_eq!(serde_json::to_value(&done)?["bookmarks"], serde_json::json!(["FB:x"]));
        assert_eq!(
            serde_json::to_value(failure("bad".into()))?["error_kind"],
            serde_json::json!("invalid_request")
        );
        Ok(())
    }

    #[test]
    fn reserved_properties_are_rejected_on_nodes_and_relationships_in_all_modes(
    ) -> Result<(), Box<dyn std::error::Error>> {
        for key in RESERVED_PROPERTIES {
            // Governed (fence 7) and ungoverned (no fence) alike.
            for fence in [Some(7), None] {
                let (writes, errors) = planned(
                    fence,
                    serde_json::json!([{"id": "a", "label": "Person", "properties": {key: 1}}]),
                    serde_json::json!([]),
                )?;
                assert!(writes.is_empty(), "{key} on a node must not plan a write");
                assert_eq!(errors.len(), 1);
                assert!(errors[0].starts_with("node a: reserved properties"), "{}", errors[0]);
                assert!(errors[0].contains(key));

                let (writes, errors) = planned(
                    fence,
                    serde_json::json!([]),
                    serde_json::json!([{"source": "a", "target": "b", "type": "KNOWS", "properties": {key: "x"}}]),
                )?;
                assert!(writes.is_empty(), "{key} on a relationship must not plan a write");
                assert_eq!(errors.len(), 1);
                assert!(errors[0].contains("reserved properties"), "{}", errors[0]);
                assert!(errors[0].contains(key));
            }
        }
        // The ungoverned fence-planting case from review: a maximal token
        // scoped to a real package is refused, not stored.
        let (writes, errors) = planned(
            None,
            serde_json::json!([{"id": "a", "label": "Person",
                "properties": {"_fencing_token": i64::MAX, "_fence_scope": "pkg"}}]),
            serde_json::json!([]),
        )?;
        assert!(writes.is_empty());
        assert!(errors[0].ends_with("_fence_scope, _fencing_token"), "{}", errors[0]);
        // Other underscore properties are the caller's own.
        let (writes, errors) = planned(
            None,
            serde_json::json!([{"id": "a", "label": "Person", "properties": {"_note": "ok"}}]),
            serde_json::json!([]),
        )?;
        assert!(errors.is_empty());
        let props = writes.first().and_then(props_of).ok_or("props must be a map")?;
        assert_eq!(props.get("_note"), Some(&ValueSend::String("ok".into())));
        Ok(())
    }

    #[test]
    fn invalid_bookmarks_are_rejected_before_the_idempotency_key_is_claimed(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use neo4j::address::Address;
        use neo4j::driver::{ConnectionConfig, DriverConfig};
        let (path, db, digest) = lifecycle_db(&[("B", "pkg", "projection", 5, LIVE)])?;
        // Never contacted: every request below is rejected before Bolt I/O.
        let driver = Driver::new(
            ConnectionConfig::new(Address::from(("127.0.0.1", 1))).with_routing(false),
            DriverConfig::new(),
        );
        let request = |bookmarks: Value| -> Result<Request, serde_json::Error> {
            serde_json::from_value(serde_json::json!({
                "op": "project",
                "idempotency_key": "key",
                "workspace_id": "ws",
                "semantic_receipt": {
                    "schema_version": "seocho.canonical_projection_receipt.v1",
                    "rdf_bundle_sha256": digest,
                    "rdf_data_graph_sha256": "b".repeat(64),
                    "agent_profile_sha256": "c".repeat(64),
                    "projection_receipt_sha256": "d".repeat(64),
                },
                "admission": {"lease_id": "B", "fingerprint": digest, "generation": 0, "epoch": 0, "fencing_token": 5},
                "bookmarks": bookmarks,
            }))
        };
        let claims = || -> Result<i64, rusqlite::Error> {
            Connection::open(&path)?.query_row(
                "SELECT count(*) FROM projection_idempotency",
                [],
                |r| r.get(0),
            )
        };
        let invalid = [
            serde_json::json!([""]),
            serde_json::json!(["x".repeat(MAX_BOOKMARK_BYTES + 1)]),
            serde_json::json!(vec!["FB:x"; MAX_BOOKMARKS + 1]),
        ];
        for bookmarks in invalid {
            let response = project_with(&driver, request(bookmarks)?, Some(db.clone()));
            assert!(!response.ok);
            assert_eq!(response.error.as_deref(), Some("invalid bookmarks"));
            assert_eq!(response.error_kind.as_deref(), Some("invalid_request"));
            // No pending claim is left behind.
            assert_eq!(claims()?, 0);
        }
        // A corrected retry with the same key claims it at once, without
        // waiting out the reclaim window.
        assert!(validate_bookmarks(request(serde_json::json!(["FB:x"]))?.bookmarks).is_ok());
        assert!(claim_idempotency(&db, "key", "ws", &digest, now_ms(), RECLAIM)?.is_none());
        assert_eq!(claims()?, 1);
        std::fs::remove_file(path)?;
        Ok(())
    }
}
