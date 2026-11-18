//! Read-only audit of an existing control DB (v0, v1 or v2).
//!
//! The database is opened as `file:<path>?mode=ro`; the audit never writes,
//! so it can run against a production control DB while Python holds it.
//!
//! Severity policy:
//! - **violation**: the token contract (I1/I2) or schema integrity (I13) is
//!   already broken: a token present on two lease rows, a high-water below a
//!   lease token, two live leases for one `(scope, purpose)`, tampered v2 DDL;
//!   on v2 also any pointer token not covered by the high-water.
//! - **warning**: a state the legacy code tolerates but that `migrate` (P2)
//!   must handle: expired rows still held in `ontology_lease`, a v1 pointer
//!   above the high-water (normal right after a v1 `activate`, see
//!   #783 src/seocho/ontology/lifecycle.py:240-245), a v0/v1 pointer token
//!   equal to a lease token (caller-chosen publish tokens), and the v0 fact
//!   that released tokens are unrecoverable.

use crate::error::LifecycleError;
use crate::schema::{self, table_exists, SchemaVersion};
use rusqlite::{Connection, OpenFlags};
use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Violation,
    Warning,
}

impl Severity {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Violation => "violation",
            Self::Warning => "warning",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Finding {
    pub severity: Severity,
    /// Stable machine-readable kind, e.g. `duplicate_token`.
    pub kind: &'static str,
    /// `(workspace_id, package_id)`, or `None` for a database-level finding.
    pub scope: Option<(String, String)>,
    pub detail: String,
}

impl fmt::Display for Finding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.scope {
            Some((ws, pkg)) => write!(
                f,
                "{} {} [{ws}/{pkg}] {}",
                self.severity.as_str(),
                self.kind,
                self.detail
            ),
            None => write!(f, "{} {} {}", self.severity.as_str(), self.kind, self.detail),
        }
    }
}

/// Per-scope token summary.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ScopeSummary {
    pub workspace_id: String,
    pub package_id: String,
    pub high_water: Option<i64>,
    pub max_lease_token: Option<i64>,
    pub max_tombstone_token: Option<i64>,
    pub pointer_token: Option<i64>,
    pub live_leases: u64,
    pub expired_leases: u64,
}

#[derive(Clone, Debug)]
pub struct AuditReport {
    pub schema: SchemaVersion,
    pub now_ms: i64,
    pub scopes: Vec<ScopeSummary>,
    pub findings: Vec<Finding>,
}

impl AuditReport {
    pub fn violations(&self) -> usize {
        self.count(Severity::Violation)
    }

    pub fn warnings(&self) -> usize {
        self.count(Severity::Warning)
    }

    fn count(&self, s: Severity) -> usize {
        self.findings.iter().filter(|f| f.severity == s).count()
    }

    pub fn has_kind(&self, kind: &str) -> bool {
        self.findings.iter().any(|f| f.kind == kind)
    }
}

fn file_uri_ro(path: &Path) -> Result<String, LifecycleError> {
    let raw = path
        .to_str()
        .ok_or_else(|| LifecycleError::InvalidRequest("control DB path is not UTF-8".into()))?;
    let mut uri = String::from("file:");
    for b in raw.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'/' | b'-' | b'.' | b'_' | b'~') {
            uri.push(char::from(b));
        } else {
            uri.push_str(&format!("%{b:02X}"));
        }
    }
    uri.push_str("?mode=ro");
    Ok(uri)
}

/// Open `path` strictly read-only (`file:...?mode=ro`).
pub fn open_read_only(path: &Path) -> Result<Connection, LifecycleError> {
    if !path.exists() {
        return Err(LifecycleError::InvalidRequest(format!(
            "control DB {} does not exist",
            path.display()
        )));
    }
    let uri = file_uri_ro(path)?;
    let conn = Connection::open_with_flags(
        uri,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.busy_timeout(std::time::Duration::from_millis(10_000))?;
    Ok(conn)
}

/// Audit the control DB at `path` read-only.
pub fn audit_path(path: &Path, now_ms: i64) -> Result<AuditReport, LifecycleError> {
    let conn = open_read_only(path)?;
    audit_connection(&conn, now_ms)
}

type Key = (String, String);

struct TokenRow {
    key: Key,
    token: i64,
    lease_id: String,
    source: &'static str,
}

/// Audit an already-open connection (it is only read).
pub fn audit_connection(conn: &Connection, now_ms: i64) -> Result<AuditReport, LifecycleError> {
    // One consistent snapshot across all queries.
    conn.execute_batch("BEGIN DEFERRED;")?;
    let result = audit_inner(conn, now_ms);
    let end = conn.execute_batch("COMMIT;");
    let report = result?;
    end?;
    Ok(report)
}

fn scope_entry<'a>(scopes: &'a mut BTreeMap<Key, ScopeSummary>, key: &Key) -> &'a mut ScopeSummary {
    scopes.entry(key.clone()).or_insert_with(|| ScopeSummary {
        workspace_id: key.0.clone(),
        package_id: key.1.clone(),
        ..ScopeSummary::default()
    })
}

fn audit_inner(conn: &Connection, now_ms: i64) -> Result<AuditReport, LifecycleError> {
    let version = schema::detect(conn)?;
    let mut findings = Vec::new();
    let mut scopes: BTreeMap<Key, ScopeSummary> = BTreeMap::new();
    let mut tokens: Vec<TokenRow> = Vec::new();

    if version == SchemaVersion::Empty {
        return Err(LifecycleError::SchemaMismatch(
            "not a lifecycle control DB (no lifecycle tables)".into(),
        ));
    }
    if version == SchemaVersion::V2 {
        if let Err(e) = schema::verify_v2(conn) {
            findings.push(Finding {
                severity: Severity::Violation,
                kind: "schema_tampered",
                scope: None,
                detail: e.to_string(),
            });
        }
    }
    if version == SchemaVersion::V0 {
        findings.push(Finding {
            severity: Severity::Warning,
            kind: "no_high_water_table",
            scope: None,
            detail: "v0 schema: tokens of released leases were deleted and cannot be audited; \
                     migrate needs a DozerDB floor"
                .into(),
        });
    }

    // Lease rows (all versions).
    if table_exists(conn, "ontology_lease")? {
        let mut stmt = conn.prepare(
            "SELECT lease_id, workspace_id, package_id, purpose, fencing_token, expires_at_ms FROM ontology_lease",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                r.get::<_, Option<i64>>(4)?.unwrap_or(0),
                r.get::<_, Option<i64>>(5)?.unwrap_or(0),
            ))
        })?;
        let mut live_per_purpose: BTreeMap<(Key, String), Vec<String>> = BTreeMap::new();
        for row in rows {
            let (lease_id, ws, pkg, purpose, token, expires) = row?;
            let key = (ws, pkg);
            let s = scope_entry(&mut scopes, &key);
            s.max_lease_token = Some(s.max_lease_token.map_or(token, |m| m.max(token)));
            if expires <= now_ms {
                s.expired_leases += 1;
                findings.push(Finding {
                    severity: Severity::Warning,
                    kind: "expired_but_held",
                    scope: Some(key.clone()),
                    detail: format!(
                        "lease {lease_id} (purpose {purpose}, token {token}) expired at {expires} <= now {now_ms} but is still in ontology_lease"
                    ),
                });
            } else {
                s.live_leases += 1;
                live_per_purpose
                    .entry((key.clone(), purpose))
                    .or_default()
                    .push(lease_id.clone());
            }
            tokens.push(TokenRow {
                key,
                token,
                lease_id,
                source: "ontology_lease",
            });
        }
        for ((key, purpose), ids) in live_per_purpose {
            if ids.len() > 1 {
                findings.push(Finding {
                    severity: Severity::Violation,
                    kind: "multiple_live_leases",
                    scope: Some(key),
                    detail: format!("purpose {purpose} has {} live leases: {}", ids.len(), ids.join(", ")),
                });
            }
        }
    }

    if table_exists(conn, "ontology_lease_tombstone")? {
        let mut stmt = conn.prepare(
            "SELECT lease_id, workspace_id, package_id, fencing_token FROM ontology_lease_tombstone",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, i64>(3)?,
            ))
        })?;
        for row in rows {
            let (lease_id, ws, pkg, token) = row?;
            let key = (ws, pkg);
            let s = scope_entry(&mut scopes, &key);
            s.max_tombstone_token = Some(s.max_tombstone_token.map_or(token, |m| m.max(token)));
            tokens.push(TokenRow {
                key,
                token,
                lease_id,
                source: "ontology_lease_tombstone",
            });
        }
    }

    if table_exists(conn, "active_ontology")? {
        let mut stmt =
            conn.prepare("SELECT workspace_id, package_id, fencing_token FROM active_ontology")?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                r.get::<_, Option<i64>>(2)?.unwrap_or(0),
            ))
        })?;
        for row in rows {
            let (ws, pkg, token) = row?;
            scope_entry(&mut scopes, &(ws, pkg)).pointer_token = Some(token);
        }
    }

    let has_hw_table = table_exists(conn, "ontology_fence_high_water")?;
    if has_hw_table {
        let mut stmt = conn.prepare(
            "SELECT workspace_id, package_id, high_water FROM ontology_fence_high_water",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
        })?;
        for row in rows {
            let (ws, pkg, hw) = row?;
            scope_entry(&mut scopes, &(ws, pkg)).high_water = Some(hw);
        }
    }

    // Duplicate tokens among lease rows (+ tombstones) per scope.
    let mut by_token: BTreeMap<(Key, i64), Vec<&TokenRow>> = BTreeMap::new();
    for t in &tokens {
        by_token.entry((t.key.clone(), t.token)).or_default().push(t);
    }
    for ((key, token), rows) in &by_token {
        if rows.len() > 1 {
            let who: Vec<String> = rows
                .iter()
                .map(|r| format!("{}:{}", r.source, r.lease_id))
                .collect();
            findings.push(Finding {
                severity: Severity::Violation,
                kind: "duplicate_token",
                scope: Some(key.clone()),
                detail: format!("token {token} issued to {} leases: {}", rows.len(), who.join(", ")),
            });
        }
    }

    for (key, s) in &scopes {
        if let Some(ptr) = s.pointer_token {
            if let Some(rows) = by_token.get(&(key.clone(), ptr)) {
                let ids: Vec<&str> = rows.iter().map(|r| r.lease_id.as_str()).collect();
                findings.push(Finding {
                    severity: if version == SchemaVersion::V2 {
                        Severity::Violation
                    } else {
                        Severity::Warning
                    },
                    kind: "pointer_token_reused",
                    scope: Some(key.clone()),
                    detail: format!("active pointer token {ptr} equals lease token of {}", ids.join(", ")),
                });
            }
        }
        if !has_hw_table {
            continue;
        }
        let lease_max = s.max_lease_token.max(s.max_tombstone_token);
        let hw = s.high_water;
        if let Some(lm) = lease_max {
            if hw.is_none_or(|h| h < lm) {
                findings.push(Finding {
                    severity: Severity::Violation,
                    kind: "high_water_below_lease",
                    scope: Some(key.clone()),
                    detail: format!("high_water {hw:?} < max lease token {lm}"),
                });
            }
        }
        if let Some(ptr) = s.pointer_token {
            if hw.is_none_or(|h| h < ptr) {
                findings.push(Finding {
                    severity: if version == SchemaVersion::V2 {
                        Severity::Violation
                    } else {
                        Severity::Warning
                    },
                    kind: "high_water_below_pointer",
                    scope: Some(key.clone()),
                    detail: format!("high_water {hw:?} < active pointer token {ptr}"),
                });
            }
        }
    }

    findings.sort_by(|a, b| {
        (a.severity, a.kind, &a.scope, &a.detail).cmp(&(b.severity, b.kind, &b.scope, &b.detail))
    });
    Ok(AuditReport {
        schema: version,
        now_ms,
        scopes: scopes.into_values().collect(),
        findings,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uri_escapes_query_characters() -> Result<(), LifecycleError> {
        let uri = file_uri_ro(Path::new("/tmp/a b?c#d%e.sqlite"))?;
        assert_eq!(uri, "file:/tmp/a%20b%3Fc%23d%25e.sqlite?mode=ro");
        Ok(())
    }
}
