#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/task-context-trail.sh --task-id <id> [options]

Options:
  --task-id <id>         Task/issue ID to inspect.
  --events-file <path>   Context event JSONL path (default: logs/context/events.jsonl).
  --limit <n>            Max recent events in output (default: 20, 0 means all).
  --json                 Emit machine-readable JSON output.
  -h, --help             Show this help text.
EOF
}

task_id=""
events_file="${REPO_ROOT}/logs/context/events.jsonl"
limit=20
json_output=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id)
      task_id="$2"
      shift 2
      ;;
    --events-file)
      events_file="$2"
      shift 2
      ;;
    --limit)
      limit="$2"
      shift 2
      ;;
    --json)
      json_output=1
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

if [[ -z "${task_id}" ]]; then
  echo "--task-id is required" >&2
  usage
  exit 1
fi

if ! [[ "${limit}" =~ ^[0-9]+$ ]]; then
  echo "--limit must be a non-negative integer" >&2
  exit 1
fi

issue_json="$(bd --sandbox show "${task_id}" --json 2>/dev/null || true)"
if [[ -z "${issue_json}" ]]; then
  echo "Failed to load task '${task_id}' from bd." >&2
  exit 1
fi

TASK_ID="${task_id}" \
ISSUE_JSON="${issue_json}" \
EVENTS_FILE="${events_file}" \
LIMIT="${limit}" \
JSON_OUTPUT="${json_output}" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_issue(raw: str, task_id: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid issue JSON from bd: {exc}") from exc

    if isinstance(data, dict):
        if data.get("id") == task_id:
            return data
        raise SystemExit(f"Task '{task_id}' not found in bd response.")

    if not isinstance(data, list):
        raise SystemExit("Unexpected bd show JSON shape.")

    for item in data:
        if isinstance(item, dict) and item.get("id") == task_id:
            return item

    if len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    raise SystemExit(f"Task '{task_id}' not found.")


def _load_events(path: Path, task_id: str) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    events: list[dict[str, Any]] = []
    invalid_lines = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(event, dict) and event.get("task_id") == task_id:
            events.append(event)
    events.sort(key=lambda e: (str(e.get("timestamp", "")), str(e.get("event_id", ""))))
    return events, invalid_lines


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


task_id = os.environ["TASK_ID"]
issue = _load_issue(os.environ["ISSUE_JSON"], task_id)
events_file = Path(os.environ["EVENTS_FILE"])
limit = int(os.environ["LIMIT"])
json_output = os.environ["JSON_OUTPUT"] == "1"

events, invalid_lines = _load_events(events_file, task_id)
recent_events = events if limit == 0 else events[-limit:]

checks: list[dict[str, Any]] = []
landing_results: list[dict[str, Any]] = []
for event in events:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        continue
    if event.get("event_type") == "gate_result":
        checks.append(
            {
                "timestamp": event.get("timestamp"),
                "run_id": event.get("run_id"),
                "scope": event.get("scope"),
                "gate": payload.get("gate"),
                "status": payload.get("status"),
                "source_ref": event.get("source_ref"),
            }
        )
    if event.get("event_type") == "landing_result":
        landing_results.append(
            {
                "timestamp": event.get("timestamp"),
                "run_id": event.get("run_id"),
                "scope": event.get("scope"),
                "status": payload.get("status"),
                "failures": payload.get("failures"),
                "warnings": payload.get("warnings"),
                "source_ref": event.get("source_ref"),
            }
        )

task_summary = {
    "id": issue.get("id"),
    "title": issue.get("title"),
    "status": issue.get("status"),
    "priority": issue.get("priority"),
    "issue_type": issue.get("issue_type"),
    "created_at": issue.get("created_at"),
    "updated_at": issue.get("updated_at"),
    "closed_at": issue.get("closed_at"),
}

output = {
    "task": task_summary,
    "events_file": str(events_file),
    "event_count": len(events),
    "recent_events": recent_events,
    "checks": checks,
    "landing_results": landing_results,
    "invalid_event_lines": invalid_lines,
}

if json_output:
    print(json.dumps(output, ensure_ascii=True, indent=2))
    sys.exit(0)

print(f"Task Context Trail: {task_summary['id']}")
if task_summary.get("title"):
    print(f"Title: {task_summary['title']}")
print(
    f"Status: {task_summary.get('status')}  Priority: {task_summary.get('priority')}  Type: {task_summary.get('issue_type')}"
)
print(
    f"Lifecycle: created={task_summary.get('created_at')} updated={task_summary.get('updated_at')} closed={task_summary.get('closed_at')}"
)
print(f"Events file: {events_file} (task_events={len(events)}, invalid_lines={invalid_lines})")
print()

print(f"Recent Events (last {len(recent_events)}):")
if not recent_events:
    print("- (none)")
else:
    for event in recent_events:
        print(
            "- "
            + f"{event.get('timestamp')} "
            + f"{event.get('event_type')} "
            + f"run={event.get('run_id')} "
            + f"scope={event.get('scope')} "
            + f"payload={_compact_json(event.get('payload', {}))}"
        )
print()

print(f"Gate Checks ({len(checks)}):")
if not checks:
    print("- (none)")
else:
    for check in checks[-limit:] if limit != 0 else checks:
        print(
            "- "
            + f"{check.get('timestamp')} "
            + f"gate={check.get('gate')} "
            + f"status={check.get('status')} "
            + f"run={check.get('run_id')}"
        )
print()

print(f"Landing Results ({len(landing_results)}):")
if not landing_results:
    print("- (none)")
else:
    for landing in landing_results[-limit:] if limit != 0 else landing_results:
        print(
            "- "
            + f"{landing.get('timestamp')} "
            + f"status={landing.get('status')} "
            + f"failures={landing.get('failures')} "
            + f"warnings={landing.get('warnings')} "
            + f"run={landing.get('run_id')}"
        )
PY
