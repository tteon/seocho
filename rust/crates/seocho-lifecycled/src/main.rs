//! `seocho-lifecycled`: SEOCHO lifecycle authority daemon.
//!
//! PR-1 ships only the read-only `audit` subcommand
//! (docs/design/rust-lifecycle-authority.md §9). `serve`, `migrate` and
//! `downgrade` arrive in P2.
//!
//! ```text
//! seocho-lifecycled audit <db> [--json] [--strict] [--now-ms <ms>]
//! ```
//!
//! Exit status: 0 clean, 1 violations found (or warnings with `--strict`),
//! 2 usage error or the database could not be read.

use seocho_lifecycle::audit::{self, AuditReport};
use seocho_lifecycle::{Clock, SystemClock};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: seocho-lifecycled audit <db> [--json] [--strict] [--now-ms <ms>]";

struct AuditArgs {
    db: PathBuf,
    json: bool,
    strict: bool,
    now_ms: Option<i64>,
}

fn parse_audit(args: &[String]) -> Result<AuditArgs, String> {
    let mut db = None;
    let mut json = false;
    let mut strict = false;
    let mut now_ms = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--json" => json = true,
            "--strict" => strict = true,
            "--now-ms" => {
                let v = it.next().ok_or("--now-ms needs a value")?;
                now_ms = Some(v.parse::<i64>().map_err(|e| format!("--now-ms: {e}"))?);
            }
            s if s.starts_with("--") => return Err(format!("unknown flag {s}")),
            s => {
                if db.replace(PathBuf::from(s)).is_some() {
                    return Err("audit takes exactly one <db>".into());
                }
            }
        }
    }
    Ok(AuditArgs {
        db: db.ok_or("audit needs <db>")?,
        json,
        strict,
        now_ms,
    })
}

fn report_json(db: &std::path::Path, r: &AuditReport) -> serde_json::Value {
    serde_json::json!({
        "db": db.display().to_string(),
        "schema": r.schema.as_str(),
        "now_ms": r.now_ms,
        "violations": r.violations(),
        "warnings": r.warnings(),
        "scopes": r.scopes.iter().map(|s| serde_json::json!({
            "workspace_id": s.workspace_id,
            "package_id": s.package_id,
            "high_water": s.high_water,
            "max_lease_token": s.max_lease_token,
            "max_tombstone_token": s.max_tombstone_token,
            "pointer_token": s.pointer_token,
            "live_leases": s.live_leases,
            "expired_leases": s.expired_leases,
        })).collect::<Vec<_>>(),
        "findings": r.findings.iter().map(|f| serde_json::json!({
            "severity": f.severity.as_str(),
            "kind": f.kind,
            "workspace_id": f.scope.as_ref().map(|s| s.0.clone()),
            "package_id": f.scope.as_ref().map(|s| s.1.clone()),
            "detail": f.detail,
        })).collect::<Vec<_>>(),
    })
}

fn run_audit(args: &[String]) -> ExitCode {
    let parsed = match parse_audit(args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let now_ms = parsed
        .now_ms
        .unwrap_or_else(|| i64::try_from(SystemClock.now_ms().get()).unwrap_or(i64::MAX));
    let report = match audit::audit_path(&parsed.db, now_ms) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("audit failed ({}): {e}", e.code());
            return ExitCode::from(2);
        }
    };
    if parsed.json {
        println!("{}", report_json(&parsed.db, &report));
    } else {
        println!(
            "audit {} schema={} scopes={} violations={} warnings={}",
            parsed.db.display(),
            report.schema.as_str(),
            report.scopes.len(),
            report.violations(),
            report.warnings()
        );
        for s in &report.scopes {
            println!(
                "scope {}/{} high_water={:?} max_lease={:?} max_tombstone={:?} pointer={:?} live={} expired={}",
                s.workspace_id,
                s.package_id,
                s.high_water,
                s.max_lease_token,
                s.max_tombstone_token,
                s.pointer_token,
                s.live_leases,
                s.expired_leases
            );
        }
        for f in &report.findings {
            println!("{f}");
        }
    }
    if report.violations() > 0 || (parsed.strict && report.warnings() > 0) {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("audit") => run_audit(&args[1..]),
        Some("-h" | "--help") => {
            println!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some(other) => {
            eprintln!("unknown subcommand {other:?} (PR-1 supports only `audit`)\n{USAGE}");
            ExitCode::from(2)
        }
        None => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}
