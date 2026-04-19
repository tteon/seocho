#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/gt-doctor.sh [options]

Options:
  --issues-file <path>   Issues JSONL fallback path (default: .beads/issues.jsonl).
  --issues-source <mode> Source mode: auto|bd|file (default: auto).
  --beads-dir <path>     Beads directory for single-path guard (default: .beads).
  --repo-root <path>     Repository root for git tracked-file checks (default: repo root).
  --fix                  Apply safe wisp classification fixes via bd update.
  --json                 Emit machine-readable JSON.
  --no-strict            Always exit 0 (default exits 1 on failed checks).
  -h, --help             Show this help text.
EOF
}

issues_file="${REPO_ROOT}/.beads/issues.jsonl"
issues_source="auto"
beads_dir="${REPO_ROOT}/.beads"
repo_root="${REPO_ROOT}"
fix_mode=0
json_output=0
strict_mode=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issues-file)
      issues_file="$2"
      shift 2
      ;;
    --issues-source)
      issues_source="$2"
      shift 2
      ;;
    --beads-dir)
      beads_dir="$2"
      shift 2
      ;;
    --repo-root)
      repo_root="$2"
      shift 2
      ;;
    --fix)
      fix_mode=1
      shift
      ;;
    --json)
      json_output=1
      shift
      ;;
    --no-strict)
      strict_mode=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

ISSUES_FILE="${issues_file}" \
ISSUES_SOURCE="${issues_source}" \
BEADS_DIR="${beads_dir}" \
REPO_ROOT="${repo_root}" \
FIX_MODE="${fix_mode}" \
JSON_OUTPUT="${json_output}" \
STRICT_MODE="${strict_mode}" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

LOCAL_ARTIFACT_GLOBS = [
    "beads.db",
    "beads.db-*",
    "daemon.lock",
    "daemon.pid",
    "daemon.log",
    "interactions.jsonl",
    "issues.jsonl",
    "last-touched",
    ".jsonl.lock",
    "export-state/*.json",
]

REDIRECT_MARKERS = [
    "redirect",
    ".redirect",
    "redirect_path",
    "path.redirect",
]

RUNTIME_FORBIDDEN_GLOBS = [
    ".beads/beads.db",
    ".beads/beads.db-*",
    ".beads/daemon.lock",
    ".beads/daemon.log",
    ".beads/daemon.pid",
    ".beads/bd.sock",
    ".beads/.jsonl.lock",
    ".beads/last-touched",
    ".beads/export-state/*.json",
]

DOCTOR_SCAN_SKIP_TOP_LEVEL = {
    ".git",
    ".beads",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
}

WISP_ALLOWED_TYPES = {"task", "epic"}


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
    return out


def _is_wisp_candidate(issue_id: str, labels: list[str]) -> bool:
    return "-wisp-" in issue_id or any(label == "gt:wisp" for label in labels)


def _is_true(value: Any) -> bool:
    return value is True


def _read_redirect_from_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    for raw in config_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("redirect:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _discover_redirect_target(beads_dir: Path) -> tuple[str, str]:
    for marker in REDIRECT_MARKERS:
        marker_path = beads_dir / marker
        if marker_path.exists():
            value = marker_path.read_text().strip()
            if value:
                return value, str(marker_path)

    config_path = beads_dir / "config.yaml"
    config_value = _read_redirect_from_config(config_path)
    if config_value:
        return config_value, str(config_path)

    if beads_dir.is_symlink():
        try:
            return os.readlink(beads_dir), f"{beads_dir} (symlink)"
        except OSError:
            return "", ""
    return "", ""


def _collect_local_artifacts(beads_dir: Path) -> list[str]:
    artifacts: set[str] = set()
    for pattern in LOCAL_ARTIFACT_GLOBS:
        for path in beads_dir.glob(pattern):
            if path.exists():
                artifacts.add(str(path))
    return sorted(artifacts)


def _load_issues_from_bd(repo_root: Path) -> tuple[list[dict[str, Any]], str]:
    command = ["bd", "--sandbox", "list", "--json", "--all", "-n", "0"]
    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if not err:
            err = f"bd list failed with code {proc.returncode}"
        return [], err

    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"bd list returned invalid JSON: {exc}"

    if not isinstance(payload, list):
        return [], "bd list payload is not a JSON array"

    out: list[dict[str, Any]] = []
    for row in payload:
        if isinstance(row, dict):
            out.append(row)
    return out, ""


def _load_issues_from_jsonl(issues_file: Path) -> tuple[list[dict[str, Any]], int, str]:
    if not issues_file.exists():
        return [], 0, f"Issues file not found: {issues_file}"

    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line_no, raw in enumerate(issues_file.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(parsed, dict):
            invalid_lines += 1
            continue
        row = dict(parsed)
        row["_doctor_line_no"] = line_no
        rows.append(row)
    return rows, invalid_lines, ""


def _select_issues(
    mode: str, repo_root: Path, issues_file: Path
) -> tuple[list[dict[str, Any]], str, int, list[str]]:
    warnings: list[str] = []

    if mode == "bd":
        rows, error = _load_issues_from_bd(repo_root)
        if error:
            raise RuntimeError(error)
        return rows, "bd", 0, warnings

    if mode == "file":
        rows, invalid_lines, error = _load_issues_from_jsonl(issues_file)
        if error:
            raise RuntimeError(error)
        return rows, "issues_file", invalid_lines, warnings

    rows, error = _load_issues_from_bd(repo_root)
    if not error:
        return rows, "bd", 0, warnings

    warnings.append(f"bd source unavailable, falling back to issues file: {error}")
    rows, invalid_lines, file_error = _load_issues_from_jsonl(issues_file)
    if file_error:
        raise RuntimeError(file_error)
    return rows, "issues_file", invalid_lines, warnings


def _scan_wisp_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        issue_id = str(row.get("id", ""))
        title = str(row.get("title", ""))
        issue_type = str(row.get("issue_type", ""))
        labels = _as_list(row.get("labels"))
        ephemeral = row.get("ephemeral")

        if not _is_wisp_candidate(issue_id, labels):
            continue

        reasons: list[str] = []
        fix_args: list[str] = []

        if "-wisp-" in issue_id:
            if issue_type not in WISP_ALLOWED_TYPES:
                observed = issue_type if issue_type else "missing"
                reasons.append(f"issue_type={observed} (expected task|epic)")
                fix_args.extend(["--type", "task"])
            if not _is_true(ephemeral):
                observed = "missing" if ephemeral is None else str(ephemeral).lower()
                reasons.append(f"ephemeral={observed} (expected true)")
                fix_args.append("--ephemeral")
        else:
            reasons.append("gt:wisp label without -wisp- issue ID pattern")

        if not reasons:
            continue

        finding = {
            "id": issue_id,
            "title": title,
            "status": row.get("status"),
            "line": row.get("_doctor_line_no", index),
            "reasons": reasons,
            "fix_args": fix_args,
        }
        findings.append(finding)

    return findings


def _build_fix_command(issue_id: str, fix_args: list[str]) -> str:
    return f"bd --sandbox update {issue_id} {' '.join(fix_args)}"


def _apply_wisp_fixes(
    repo_root: Path, findings: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for finding in findings:
        issue_id = str(finding.get("id", ""))
        fix_args = _as_list(finding.get("fix_args"))
        if not issue_id or not fix_args:
            continue

        proc = subprocess.run(
            ["bd", "--sandbox", "update", issue_id, *fix_args],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        if proc.returncode == 0:
            applied.append({"id": issue_id, "args": fix_args})
            continue

        err = proc.stderr.strip() or proc.stdout.strip()
        if not err:
            err = f"bd update failed with code {proc.returncode}"
        failed.append({"id": issue_id, "args": fix_args, "error": err})

    return applied, failed


def _public_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for finding in findings:
        issue_id = str(finding.get("id", ""))
        fix_args = _as_list(finding.get("fix_args"))
        row = {
            "id": issue_id,
            "title": finding.get("title", ""),
            "status": finding.get("status"),
            "line": finding.get("line"),
            "reasons": _as_list(finding.get("reasons")),
            "fixable": bool(fix_args),
        }
        if fix_args and issue_id:
            row["suggested_fix"] = _build_fix_command(issue_id, fix_args)
        out.append(row)
    return out


def _collect_tracked_runtime_violations(repo_root: Path) -> tuple[list[str], str]:
    command = ["git", "-C", str(repo_root), "ls-files", "-z"]
    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return [], stderr or f"git ls-files failed with code {proc.returncode}"

    tracked_paths = [
        p.decode("utf-8", errors="replace")
        for p in proc.stdout.split(b"\x00")
        if p
    ]
    violations: set[str] = set()
    for tracked in tracked_paths:
        for pattern in RUNTIME_FORBIDDEN_GLOBS:
            if Path(tracked).match(pattern):
                violations.add(tracked)
                break
    return sorted(violations), ""


def _collect_submodule_paths(repo_root: Path) -> tuple[set[str], str]:
    command = ["git", "-C", str(repo_root), "ls-files", "--stage", "-z"]
    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return set(), stderr or f"git ls-files --stage failed with code {proc.returncode}"

    out: set[str] = set()
    for raw in proc.stdout.split(b"\x00"):
        if not raw:
            continue
        decoded = raw.decode("utf-8", errors="replace")
        if "\t" not in decoded:
            continue
        left, path = decoded.split("\t", 1)
        parts = left.split()
        if parts and parts[0] == "160000":
            out.add(path)
    return out, ""


def _is_ignored_path(repo_root: Path, relative_path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-q", f"{relative_path}/"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _find_embedded_git_roots(repo_root: Path) -> list[str]:
    roots: set[str] = set()
    for current_root, dirnames, filenames in os.walk(repo_root):
        current = Path(current_root)
        rel = current.relative_to(repo_root)

        if rel == Path("."):
            filtered: list[str] = []
            for dirname in dirnames:
                if dirname in DOCTOR_SCAN_SKIP_TOP_LEVEL:
                    continue
                if _is_ignored_path(repo_root, dirname):
                    continue
                filtered.append(dirname)
            dirnames[:] = filtered
            continue

        if rel.parts and rel.parts[0] in DOCTOR_SCAN_SKIP_TOP_LEVEL:
            dirnames[:] = []
            continue

        rel_str = str(rel)
        if _is_ignored_path(repo_root, rel_str):
            dirnames[:] = []
            continue

        if ".git" in dirnames or ".git" in filenames:
            roots.add(rel_str)
            if ".git" in dirnames:
                dirnames.remove(".git")
            # Do not recurse further into embedded repo internals.
            dirnames[:] = []

    return sorted(roots)


issues_file = Path(os.environ["ISSUES_FILE"])
issues_source_mode = os.environ["ISSUES_SOURCE"]
beads_dir = Path(os.environ["BEADS_DIR"])
repo_root = Path(os.environ["REPO_ROOT"])
fix_mode = os.environ["FIX_MODE"] == "1"
json_output = os.environ["JSON_OUTPUT"] == "1"
strict_mode = os.environ["STRICT_MODE"] == "1"

if issues_source_mode not in {"auto", "bd", "file"}:
    raise SystemExit(
        f"Invalid issues source: {issues_source_mode} (expected auto|bd|file)"
    )

try:
    issue_rows, issues_source, invalid_lines, source_warnings = _select_issues(
        issues_source_mode, repo_root, issues_file
    )
except RuntimeError as exc:
    raise SystemExit(str(exc))

findings = _scan_wisp_findings(issue_rows)
applied_fixes: list[dict[str, Any]] = []
failed_fixes: list[dict[str, Any]] = []

if fix_mode and findings:
    fixable = [f for f in findings if _as_list(f.get("fix_args"))]
    if fixable and issues_source == "bd":
        applied_fixes, failed_fixes = _apply_wisp_fixes(repo_root, fixable)
        if applied_fixes:
            reloaded_rows, reload_error = _load_issues_from_bd(repo_root)
            if reload_error:
                source_warnings.append(
                    f"unable to reload bd issues after --fix: {reload_error}"
                )
            else:
                issue_rows = reloaded_rows
                findings = _scan_wisp_findings(issue_rows)
    elif fixable:
        source_warnings.append("--fix skipped: auto-migration requires bd issue source.")

status = "pass" if not findings and not failed_fixes else "fail"
redirect_target = ""
redirect_source = ""
local_artifacts: list[str] = []
beads_status = "pass"

if beads_dir.exists():
    redirect_target, redirect_source = _discover_redirect_target(beads_dir)
    local_artifacts = _collect_local_artifacts(beads_dir)
    if redirect_target and local_artifacts:
        beads_status = "fail"

runtime_status = "pass"
tracked_runtime_violations: list[str] = []
runtime_check_error = ""
if repo_root.exists():
    tracked_runtime_violations, runtime_check_error = _collect_tracked_runtime_violations(
        repo_root
    )
    if tracked_runtime_violations:
        runtime_status = "fail"
    elif runtime_check_error:
        runtime_status = "warn"

embedded_status = "pass"
embedded_violations: list[dict[str, str]] = []
embedded_check_error = ""
if repo_root.exists():
    submodule_paths, submodule_error = _collect_submodule_paths(repo_root)
    if submodule_error:
        embedded_status = "warn"
        embedded_check_error = submodule_error
    else:
        for rel_path in _find_embedded_git_roots(repo_root):
            if rel_path in submodule_paths:
                embedded_violations.append(
                    {
                        "path": rel_path,
                        "reason": "tracked as submodule (policy requires ignored path)",
                    }
                )
                continue
            if not _is_ignored_path(repo_root, rel_path):
                embedded_violations.append(
                    {
                        "path": rel_path,
                        "reason": "embedded git clone path is not ignored",
                    }
                )
        if embedded_violations:
            embedded_status = "fail"

public_findings = _public_findings(findings)
total_issues = len(issue_rows)

result = {
    "issues_source_mode": issues_source_mode,
    "issues_source": issues_source,
    "issues_file": str(issues_file),
    "beads_dir": str(beads_dir),
    "repo_root": str(repo_root),
    "total_issues": total_issues,
    "invalid_lines": invalid_lines,
    "source_warnings": source_warnings,
    "fixes": {
        "enabled": fix_mode,
        "applied": applied_fixes,
        "failed": failed_fixes,
        "count_applied": len(applied_fixes),
        "count_failed": len(failed_fixes),
    },
    "checks": [
        {
            "name": "misclassified-wisps",
            "status": status,
            "count": len(public_findings),
            "issues": public_findings,
            "fix_failures": failed_fixes,
        },
        {
            "name": "single-beads-path",
            "status": beads_status,
            "redirect_target": redirect_target,
            "redirect_source": redirect_source,
            "local_artifacts": local_artifacts,
            "count": len(local_artifacts) if beads_status == "fail" else 0,
        },
        {
            "name": "runtime-file-isolation",
            "status": runtime_status,
            "count": len(tracked_runtime_violations),
            "tracked_runtime_files": tracked_runtime_violations,
            "error": runtime_check_error,
        },
        {
            "name": "embedded-git-clones",
            "status": embedded_status,
            "count": len(embedded_violations),
            "violations": embedded_violations,
            "error": embedded_check_error,
        },
    ],
}

if json_output:
    print(json.dumps(result, ensure_ascii=True, indent=2))
else:
    print("GT Doctor")
    print(f"Issue source: {issues_source}")
    print(f"Issues file: {issues_file}")
    if source_warnings:
        print("Source warnings:")
        for warning in source_warnings:
            print(f"- {warning}")
    print(
        f"CLEANUP->misclassified-wisps: {status} ({len(public_findings)})"
    )
    if public_findings:
        for finding in public_findings:
            reason_text = "; ".join(str(reason) for reason in finding["reasons"])
            print(f"- {finding['id']}: {reason_text}")
            if finding.get("fixable"):
                print(f"  suggested fix: {finding.get('suggested_fix', '')}")
    if fix_mode and applied_fixes:
        print(f"CLEANUP->misclassified-wisps-fix: applied ({len(applied_fixes)})")
        for fix in applied_fixes:
            args_text = " ".join(fix.get("args", []))
            print(f"- {fix['id']}: {args_text}")
    if fix_mode and failed_fixes:
        print(f"CLEANUP->misclassified-wisps-fix: failed ({len(failed_fixes)})")
        for fix in failed_fixes:
            args_text = " ".join(fix.get("args", []))
            print(f"- {fix['id']}: {args_text}")
            print(f"  error: {fix.get('error', '')}")
    if beads_status == "fail":
        print(f"CLEANUP->single-beads-path: fail ({len(local_artifacts)})")
        print(f"- redirect: {redirect_target} ({redirect_source})")
        for artifact in local_artifacts:
            print(f"- local artifact: {artifact}")
        print("  auto-clean: scripts/beads-path-guard.sh --beads-dir .beads --auto-clean")
    else:
        print("CLEANUP->single-beads-path: pass (0)")
    if runtime_status == "fail":
        print(
            f"CLEANUP->runtime-file-isolation: fail ({len(tracked_runtime_violations)})"
        )
        for tracked in tracked_runtime_violations:
            print(f"- tracked runtime file: {tracked}")
    elif runtime_status == "warn":
        print("CLEANUP->runtime-file-isolation: warn (git inspection unavailable)")
        print(f"- {runtime_check_error}")
    else:
        print("CLEANUP->runtime-file-isolation: pass (0)")
    if embedded_status == "fail":
        print(f"CLEANUP->embedded-git-clones: fail ({len(embedded_violations)})")
        for violation in embedded_violations:
            print(f"- {violation['path']}: {violation['reason']}")
    elif embedded_status == "warn":
        print("CLEANUP->embedded-git-clones: warn (git inspection unavailable)")
        print(f"- {embedded_check_error}")
    else:
        print("CLEANUP->embedded-git-clones: pass (0)")
    if invalid_lines:
        print(f"Warning: skipped invalid JSONL lines: {invalid_lines}")

has_failures = (
    status == "fail"
    or beads_status == "fail"
    or runtime_status == "fail"
    or embedded_status == "fail"
)
if has_failures and strict_mode:
    sys.exit(1)
sys.exit(0)
PY
