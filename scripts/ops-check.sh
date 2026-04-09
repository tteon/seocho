#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/context-event.sh
source "${SCRIPT_DIR}/context-event.sh"

usage() {
  cat <<'EOF'
Usage:
  scripts/ops-check.sh --task-id <id> [options]

Options:
  --task-id <id>            Task/issue ID for context trace.
  --run-id <id>             Existing run ID to reuse.
  --scope <name>            Event scope (default: town).
  --rig <name>              Rig name metadata (default: seocho).
  --events-file <path>      JSONL output path (default: logs/context/events.jsonl).
  --skip-workspace-check    Skip git workspace preflight gate.
  --skip-agent-doc-lint     Skip scripts/pm/lint-agent-docs.sh gate.
  --quiet-events            Do not print event JSON to stdout.
  -h, --help                Show this help text.
EOF
}

task_id=""
run_id=""
scope="town"
rig="seocho"
events_file="${REPO_ROOT}/logs/context/events.jsonl"
skip_workspace_check=0
skip_agent_doc_lint=0
quiet_events=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id)
      task_id="$2"
      shift 2
      ;;
    --run-id)
      run_id="$2"
      shift 2
      ;;
    --scope)
      scope="$2"
      shift 2
      ;;
    --rig)
      rig="$2"
      shift 2
      ;;
    --events-file)
      events_file="$2"
      shift 2
      ;;
    --skip-workspace-check)
      skip_workspace_check=1
      shift
      ;;
    --skip-agent-doc-lint)
      skip_agent_doc_lint=1
      shift
      ;;
    --quiet-events)
      quiet_events=1
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

if [[ -z "${run_id}" ]]; then
  run_id="$(context_event_new_run_id "${task_id}")"
fi

if [[ "${quiet_events}" -eq 1 ]]; then
  export CONTEXT_EVENTS_STDOUT=0
fi

check_required_tools() {
  command -v git >/dev/null 2>&1
  command -v bd >/dev/null 2>&1
  command -v python3 >/dev/null 2>&1
}

check_workspace() {
  (
    cd "${REPO_ROOT}"
    git rev-parse --is-inside-work-tree >/dev/null 2>&1
  )
}

check_agent_doc_lint() {
  (
    cd "${REPO_ROOT}"
    scripts/pm/lint-agent-docs.sh >/dev/null
  )
}

emit_gate_status() {
  local gate status
  gate="$1"
  status="$2"
  context_event_emit \
    "gate_result" \
    "${task_id}" \
    "${run_id}" \
    "${scope}" \
    "scripts/ops-check.sh" \
    "$(printf '{"gate":"%s","status":"%s"}' "${gate}" "${status}")" \
    "${events_file}" >/dev/null
}

run_gate() {
  local gate
  gate="$1"
  shift
  if "$@"; then
    emit_gate_status "${gate}" "pass"
    echo "PASS ${gate}"
    return 0
  fi

  emit_gate_status "${gate}" "fail"
  echo "FAIL ${gate}" >&2
  return 1
}

skip_gate() {
  local gate
  gate="$1"
  emit_gate_status "${gate}" "skipped"
  echo "SKIP ${gate}"
}

start_payload="$(printf '{"script":"ops-check","rig":"%s"}' "${rig}")"
context_event_emit \
  "run_started" \
  "${task_id}" \
  "${run_id}" \
  "${scope}" \
  "scripts/ops-check.sh" \
  "${start_payload}" \
  "${events_file}" >/dev/null

failures=0

if ! run_gate "tools_available" check_required_tools; then
  failures=$((failures + 1))
fi

if [[ "${skip_workspace_check}" -eq 1 ]]; then
  skip_gate "workspace_check"
else
  if ! run_gate "workspace_check" check_workspace; then
    failures=$((failures + 1))
  fi
fi

if [[ "${skip_agent_doc_lint}" -eq 1 ]]; then
  skip_gate "lint_agent_docs"
else
  if ! run_gate "lint_agent_docs" check_agent_doc_lint; then
    failures=$((failures + 1))
  fi
fi

if [[ "${failures}" -eq 0 ]]; then
  context_event_emit \
    "run_finished" \
    "${task_id}" \
    "${run_id}" \
    "${scope}" \
    "scripts/ops-check.sh" \
    '{"status":"pass","failed_gates":0}' \
    "${events_file}" >/dev/null
  echo "ops-check completed: pass"
  exit 0
fi

context_event_emit \
  "run_finished" \
  "${task_id}" \
  "${run_id}" \
  "${scope}" \
  "scripts/ops-check.sh" \
  "$(printf '{"status":"fail","failed_gates":%d}' "${failures}")" \
  "${events_file}" >/dev/null
echo "ops-check completed: fail (${failures})" >&2
exit 1
