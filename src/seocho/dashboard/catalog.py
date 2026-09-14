"""Bounded, immutable-in-use catalog snapshots over explicitly selected roots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..run_comparison import validate_report
from ..run_outcomes import query_state

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RUNS = 500
MAX_DIRECTORIES = 5000
IGNORED = {".git", ".venv", "node_modules", "__pycache__", "cache"}


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def summarize(identity: str, path: str, report: dict[str, Any]) -> dict[str, Any]:
    run = report["run"]
    queries = report.get("queries", [])
    durations = run.get("durations") or {}
    states = [query_state(q) for q in queries]
    # Never infer successful completion from an absent legacy outcome.
    status = report.get("outcome", {}).get("status", "unknown")
    if status not in {"completed", "partial", "failed", "interrupted", "running"}:
        status = "unknown"
    return {
        "id": identity,
        "path": path,
        "name": str(run.get("name") or Path(path).parent.name),
        "workspace_id": str(run.get("workspace_id") or "unrecorded"),
        "started_at": str(run.get("started_at") or ""),
        "status": status,
        "models": {str(k): str(v) for k, v in (run.get("models") or {}).items()},
        "questions_recorded": len(queries),
        "questions_requested": number(run.get("question_count")),
        "answered": states.count("answered"),
        "errors": states.count("error"),
        "index_seconds": number(durations.get("index_s")),
        "query_seconds": number(durations.get("query_s")),
        "source_revision": report.get("reproducibility", {})
        .get("source", {})
        .get("git_revision"),
        "receipt_schema": report.get("reproducibility", {}).get("schema_version"),
    }


@dataclass(frozen=True)
class Catalog:
    reports: dict[str, dict[str, Any]]
    rows: list[dict[str, Any]]
    warnings: list[dict[str, str]]
    refreshed_at: str
    roots: tuple[str, ...]

    def listing(self) -> dict[str, Any]:
        return {
            "runs": self.rows,
            "warnings": self.warnings,
            "refreshed_at": self.refreshed_at,
            "roots": self.roots,
            "limits": {
                "runs": MAX_RUNS,
                "report_bytes": MAX_REPORT_BYTES,
                "total_bytes": MAX_TOTAL_BYTES,
                "directories": MAX_DIRECTORIES,
            },
        }


def scan(roots: tuple[Path, ...]) -> Catalog:
    reports: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    seen: set[Path] = set()
    total_bytes = directories = 0
    exhausted = False
    for raw_root in roots:
        root = raw_root.resolve()
        if not root.is_dir():
            warnings.append(
                {
                    "path": str(raw_root),
                    "reason": "Directory does not exist or is unavailable.",
                }
            )
            continue

        def on_error(error: OSError, selected_root: Path = root) -> None:
            warnings.append(
                {
                    "path": str(error.filename or selected_root),
                    "reason": "Directory could not be read.",
                }
            )

        for directory, children, files in os.walk(
            root, followlinks=False, onerror=on_error
        ):
            directories += 1
            if directories > MAX_DIRECTORIES:
                exhausted = True
                break
            children[:] = sorted(
                c
                for c in children
                if c not in IGNORED and not (Path(directory) / c).is_symlink()
            )
            if "report.json" not in files:
                continue
            path = Path(directory) / "report.json"
            if path in seen:
                continue
            seen.add(path)
            label = str(path.relative_to(root))
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Linked reports are not included.")
                size = path.stat().st_size
                if size > MAX_REPORT_BYTES:
                    raise ValueError("Report exceeds the 8 MiB display limit.")
                if len(reports) >= MAX_RUNS or total_bytes + size > MAX_TOTAL_BYTES:
                    exhausted = True
                    break
                # Read a bounded private copy; subsequent UI requests do not reread files.
                descriptor = os.open(
                    path,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("Reports must be regular files.")
                    content = stream.read(MAX_REPORT_BYTES + 1)
                total_bytes += len(content)
                if total_bytes > MAX_TOTAL_BYTES:
                    exhausted = True
                    break
                if len(content) > MAX_REPORT_BYTES:
                    raise ValueError("Report exceeds the 8 MiB display limit.")
                report = validate_report(json.loads(content))
                # Reject NaN/Infinity before emitting JSON for browser consumers.
                json.dumps(report, allow_nan=False)
                identity = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                row = summarize(identity, label, report)
                reports[identity] = report
                rows.append(row)
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                AttributeError,
                RecursionError,
                OverflowError,
            ):
                warnings.append(
                    {
                        "path": label,
                        "reason": "Unreadable, invalid, linked or oversized report; original file preserved.",
                    }
                )
        if exhausted:
            warnings.append(
                {
                    "path": str(root),
                    "reason": "Catalog limit reached. Choose a narrower directory to inspect omitted runs.",
                }
            )
            break
    rows.sort(key=lambda row: (row["started_at"], row["path"]), reverse=True)
    return Catalog(
        reports,
        rows,
        warnings,
        datetime.now(timezone.utc).isoformat(),  # noqa: UP017 — Python 3.10 CI
        tuple(str(r.resolve()) for r in roots),
    )
