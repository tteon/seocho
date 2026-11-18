//! Groups planned writes into Bolt statements.
//!
//! `WriteMode::Unwind` sends one `UNWIND $rows` statement per node label or
//! per (relationship type, source label, target label), up to `max_rows`
//! rows each, instead of one MERGE round trip per item. Every row keeps the
//! per-item guards (`fence_guard`, `_writer_ts` freshness, property
//! conflicts) and returns its position `i`, so the caller still attributes a
//! fenced item or a missing endpoint to that item and rolls the whole request
//! back. Rows of one statement must not observe each other's writes, so two
//! items that may touch the same graph entity (same node id under any
//! label, same relationship type and endpoint ids under any endpoint
//! labels) never share a statement and always run in request order, also
//! across groups.

use neo4j::ValueSend;
use std::collections::HashMap;

use crate::{fence_guard, fresh_guard, node_conflicts, PlannedWrite, Target};

pub(crate) const DEFAULT_UNWIND_ROWS: i64 = 1_000;
pub(crate) const MAX_UNWIND_ROWS: i64 = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum WriteMode {
    /// One statement per item: the pre-batching query shapes.
    PerItem,
    Unwind { max_rows: usize },
}

impl WriteMode {
    /// `SEOCHOD_WRITE_MODE=per_item` restores one statement per item;
    /// otherwise `SEOCHOD_UNWIND_BATCH_ROWS` (default 1000) caps a batch.
    pub(crate) fn from_env() -> Result<Self, String> {
        match std::env::var("SEOCHOD_WRITE_MODE").ok().as_deref() {
            Some("per_item") => Ok(WriteMode::PerItem),
            None | Some("") | Some("unwind") => {
                let rows = crate::env_ms("SEOCHOD_UNWIND_BATCH_ROWS", DEFAULT_UNWIND_ROWS, 1)
                    .min(MAX_UNWIND_ROWS);
                Ok(WriteMode::Unwind {
                    max_rows: usize::try_from(rows).unwrap_or(1),
                })
            }
            Some(other) => Err(format!(
                "SEOCHOD_WRITE_MODE must be 'unwind' or 'per_item', not {other:?}"
            )),
        }
    }
}

pub(crate) struct Statement {
    pub(crate) query: String,
    pub(crate) params: HashMap<String, ValueSend>,
    /// Indices into the planned writes, in row order; a result row's `i` is
    /// a position in this list. Per-item statements carry one index and
    /// return no `i`.
    pub(crate) items: Vec<usize>,
    pub(crate) unwind: bool,
    pub(crate) node: bool,
}

pub(crate) fn node_unwind_query(label: &str) -> String {
    format!(
        "UNWIND $rows AS row MERGE (n:{label} {{id: row.id, _workspace_id: $ws}}) WITH row, n, {}, {}, {} SET n += CASE WHEN fence_ok AND fresh THEN row.props ELSE {{}} END RETURN row.i AS i, fence_ok, conflicts",
        fence_guard("n"),
        fresh_guard("n", "row.props"),
        node_conflicts("row.props")
    )
}

/// A row whose endpoints do not both exist produces no result row; the
/// caller reports that row's item as a missing endpoint. (Unlike the Python
/// writer's batch, which drops such rows silently.)
pub(crate) fn relationship_unwind_query(source_label: &str, target_label: &str, kind: &str) -> String {
    format!(
        "UNWIND $rows AS row MATCH (a{source_label} {{id: row.src, _workspace_id: $ws}}), (b{target_label} {{id: row.tgt, _workspace_id: $ws}}) MERGE (a)-[r:{kind}]->(b) WITH row, r, {}, {} SET r += CASE WHEN fence_ok AND fresh THEN row.props ELSE {{}} END RETURN row.i AS i, fence_ok",
        fence_guard("r"),
        fresh_guard("r", "row.props")
    )
}

/// The request-wide parameters every statement of one request shares.
const SHARED_PARAMS: [&str; 3] = ["ws", "fence", "fence_scope"];
/// Per-item parameters copied into an UNWIND row.
const NODE_ROW: [&str; 2] = ["id", "props"];
const RELATIONSHIP_ROW: [&str; 3] = ["src", "tgt", "props"];

fn group_key(target: &Target) -> String {
    match target {
        Target::Node { label } => format!("n\u{0}{label}"),
        Target::Relationship {
            kind,
            source_label,
            target_label,
        } => format!("r\u{0}{kind}\u{0}{source_label}\u{0}{target_label}"),
    }
}

/// The graph entity an item may touch, independent of its labels: a node
/// written under two labels can be one multi-label node, and a relationship
/// given once with endpoint labels and once without can be one
/// relationship. Two items with the same entity key must run in request
/// order, in different statements.
fn entity_key(write: &PlannedWrite) -> String {
    let text = |k: &str| match write.params.get(k) {
        Some(ValueSend::String(v)) => v.as_str(),
        _ => "",
    };
    match &write.target {
        Target::Node { .. } => format!("n\u{0}{}", text("id")),
        Target::Relationship { kind, .. } => {
            format!("r\u{0}{kind}\u{0}{}\u{0}{}", text("src"), text("tgt"))
        }
    }
}

pub(crate) fn build_statements(writes: &[PlannedWrite], mode: WriteMode) -> Vec<Statement> {
    let max_rows = match mode {
        WriteMode::PerItem => {
            return writes
                .iter()
                .enumerate()
                .map(|(index, write)| Statement {
                    query: write.query.clone(),
                    params: write.params.clone(),
                    items: vec![index],
                    unwind: false,
                    node: matches!(write.target, Target::Node { .. }),
                })
                .collect();
        }
        WriteMode::Unwind { max_rows } => max_rows.max(1),
    };
    // Items are placed in request order. An item joins its group's newest
    // statement only if that statement runs after the statement holding the
    // previous item with the same entity key (whatever its group) and has
    // room; otherwise it opens a new statement at the end. Every pair of
    // items touching one entity therefore runs in request order, exactly as
    // in per-item mode, while unrelated items of one group still share a
    // statement. Nodes are planned before relationships, so every node
    // statement precedes every relationship statement.
    let mut chunks: Vec<Vec<usize>> = vec![];
    let mut newest: HashMap<String, usize> = HashMap::new();
    let mut last_of_entity: HashMap<String, usize> = HashMap::new();
    for (index, write) in writes.iter().enumerate() {
        let group = group_key(&write.target);
        let entity = entity_key(write);
        let earliest = last_of_entity.get(&entity).map_or(0, |s| s + 1);
        let slot = match newest.get(&group) {
            Some(&s) if s >= earliest && chunks[s].len() < max_rows => s,
            _ => {
                chunks.push(vec![]);
                let s = chunks.len() - 1;
                newest.insert(group, s);
                s
            }
        };
        chunks[slot].push(index);
        last_of_entity.insert(entity, slot);
    }
    chunks
        .into_iter()
        .filter(|items| !items.is_empty())
        .map(|items| unwind_statement(writes, items))
        .collect()
}

fn unwind_statement(writes: &[PlannedWrite], items: Vec<usize>) -> Statement {
    let first = &writes[items[0]];
    let (query, row_keys, node): (String, &[&str], bool) = match &first.target {
        Target::Node { label } => (node_unwind_query(label), &NODE_ROW, true),
        Target::Relationship {
            kind,
            source_label,
            target_label,
        } => (
            relationship_unwind_query(source_label, target_label, kind),
            &RELATIONSHIP_ROW,
            false,
        ),
    };
    let rows = items
        .iter()
        .enumerate()
        .map(|(position, index)| {
            let mut row: HashMap<String, ValueSend> = row_keys
                .iter()
                .filter_map(|k| writes[*index].params.get(*k).map(|v| (k.to_string(), v.clone())))
                .collect();
            row.insert("i".into(), ValueSend::Integer(position as i64));
            ValueSend::Map(row)
        })
        .collect();
    let mut params: HashMap<String, ValueSend> = SHARED_PARAMS
        .iter()
        .filter_map(|k| first.params.get(*k).map(|v| (k.to_string(), v.clone())))
        .collect();
    params.insert("rows".into(), ValueSend::List(rows));
    Statement {
        query,
        params,
        items,
        unwind: true,
        node,
    }
}
