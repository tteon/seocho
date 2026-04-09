#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/context-event.sh
source "${SCRIPT_DIR}/context-event.sh"

usage() {
  cat <<'EOF'
Usage:
  scripts/gt-land.sh --task-id <id> [options]

Options:
  --task-id <id>         Task/issue ID for context trace.
  --run-id <id>          Existing run ID to reuse.
  --scope <name>         Event scope (default: town).
  --rig <name>           Rig name metadata (default: seocho).
  --events-file <path>   JSONL output path (default: logs/context/events.jsonl).
  --pull                 Run git pull --rebase as a required step.
  --push                 Run git push as a required step.
  --skip-bd-sync         Skip best-effort bd sync step.
  --skip-branch-check    Skip main-branch preflight check.
  --dry-run              Emit trace events without executing side-effecting commands.
  --quiet-events         Do not print event JSON to stdout.
  -h, --help             Show this help text.
EOF
}

task_id=""
run_id=""
scope="town"
rig="seocho"
events_file="${REPO_ROOT}/logs/context/events.jsonl"
with_pull=0
with_push=0
skip_bd_sync=0
skip_branch_check=0
dry_run=0
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
    --pull)
      with_pull=1
      shift
      ;;
    --push)
      with_push=1
      shift
      ;;
    --skip-bd-sync)
      skip_bd_sync=1
      shift
      ;;
    --skip-branch-check)
      skip_branch_check=1
      shift
      ;;
    --dry-run)
      dry_run=1
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

json_bool() {
  if [[ "$1" -eq 1 ]]; then
    echo "true"
  else
    echo "false"
  fi
}

check_required_tools() {
  command -v git >/dev/null 2>&1
  command -v bd >/dev/null 2>&1
  command -v python3 >/dev/null 2>&1
}

check_branch_main() {
  (
    cd "${REPO_ROOT}"
    [[ "$(git branch --show-current)" == "main" ]]
  )
}

do_pull_rebase() {
  (
    cd "${REPO_ROOT}"
    git pull --rebase
  )
}

do_push() {
  (
    cd "${REPO_ROOT}"
    git push
  )
}

do_bd_sync() {
  (
    cd "${REPO_ROOT}"
    bd sync
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
    "scripts/gt-land.sh" \
    "$(printf '{"gate":"%s","status":"%s"}' "${gate}" "${status}")" \
    "${events_file}" >/dev/null
}

run_required_gate() {
  local gate
  gate="$1"
  shift

  if [[ "${dry_run}" -eq 1 ]]; then
    emit_gate_status "${gate}" "dry_run"
    echo "DRY-RUN ${gate}"
    return 0
  fi

  if "$@"; then
    emit_gate_status "${gate}" "pass"
    echo "PASS ${gate}"
    return 0
  fi

  emit_gate_status "${gate}" "fail"
  echo "FAIL ${gate}" >&2
  return 1
}

run_best_effort_gate() {
  local gate
  gate="$1"
  shift

  if [[ "${dry_run}" -eq 1 ]]; then
    emit_gate_status "${gate}" "dry_run"
    echo "DRY-RUN ${gate}"
    return 0
  fi

  if "$@"; then
    emit_gate_status "${gate}" "pass"
    echo "PASS ${gate}"
    return 0
  fi

  emit_gate_status "${gate}" "warn"
  echo "WARN ${gate}" >&2
  warnings=$((warnings + 1))
  return 0
}

start_payload="$(printf '{"script":"gt-land","rig":"%s","dry_run":%s,"with_pull":%s,"with_push":%s}' \
  "${rig}" \
  "$(json_bool "${dry_run}")" \
  "$(json_bool "${with_pull}")" \
  "$(json_bool "${with_push}")")"

context_event_emit \
  "run_started" \
  "${task_id}" \
  "${run_id}" \
  "${scope}" \
  "scripts/gt-land.sh" \
  "${start_payload}" \
  "${events_file}" >/dev/null

failures=0
warnings=0

if ! run_required_gate "tools_available" check_required_tools; then
  failures=$((failures + 1))
fi

if [[ "${skip_branch_check}" -eq 1 ]]; then
  emit_gate_status "branch_check" "skipped"
  echo "SKIP branch_check"
else
  if ! run_required_gate "branch_check" check_branch_main; then
    failures=$((failures + 1))
  fi
fi

if [[ "${with_pull}" -eq 1 ]]; then
  if ! run_required_gate "git_pull_rebase" do_pull_rebase; then
    failures=$((failures + 1))
  fi
else
  emit_gate_status "git_pull_rebase" "skipped"
  echo "SKIP git_pull_rebase"
fi

if [[ "${skip_bd_sync}" -eq 1 ]]; then
  emit_gate_status "bd_sync" "skipped"
  echo "SKIP bd_sync"
else
  run_best_effort_gate "bd_sync" do_bd_sync
fi

if [[ "${with_push}" -eq 1 ]]; then
  if ! run_required_gate "git_push" do_push; then
    failures=$((failures + 1))
  fi
else
  emit_gate_status "git_push" "skipped"
  echo "SKIP git_push"
fi

if [[ "${failures}" -eq 0 ]]; then
  landing_status="pass"
else
  landing_status="fail"
fi

landing_payload="$(printf '{"status":"%s","failures":%d,"warnings":%d}' \
  "${landing_status}" \
  "${failures}" \
  "${warnings}")"

context_event_emit \
  "landing_result" \
  "${task_id}" \
  "${run_id}" \
  "${scope}" \
  "scripts/gt-land.sh" \
  "${landing_payload}" \
  "${events_file}" >/dev/null

context_event_emit \
  "run_finished" \
  "${task_id}" \
  "${run_id}" \
  "${scope}" \
  "scripts/gt-land.sh" \
  "${landing_payload}" \
  "${events_file}" >/dev/null

if [[ "${landing_status}" == "pass" ]]; then
  echo "gt-land completed: pass (warnings=${warnings})"
  exit 0
fi

echo "gt-land completed: fail (failures=${failures}, warnings=${warnings})" >&2
exit 1
