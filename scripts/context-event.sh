#!/usr/bin/env bash
set -euo pipefail

readonly CONTEXT_EVENT_SCHEMA_VERSION="cg.v0"

context_event_now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

context_event_sanitize_token() {
  echo "$1" | tr -c 'A-Za-z0-9_.:-' '_' | sed -E 's/^_+//; s/_+$//'
}

context_event_new_id() {
  local stamp rand
  stamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  rand="$(printf '%04x' "$RANDOM")"
  printf 'evt_%s_%s\n' "${stamp}" "${rand}"
}

context_event_new_run_id() {
  local task_id safe
  task_id="${1:-task}"
  safe="$(context_event_sanitize_token "${task_id}")"
  if [[ -z "${safe}" ]]; then
    safe="task"
  fi
  printf 'run_%s_%s\n' "${safe}" "$(date -u +"%Y%m%dT%H%M%SZ")"
}

context_event_is_valid_type() {
  case "${1}" in
    task_claimed|run_started|artifact_changed|gate_result|landing_result|run_finished|task_closed)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

context_event_emit() {
  if [[ $# -lt 6 || $# -gt 7 ]]; then
    echo "context_event_emit requires 6-7 args: <event_type> <task_id> <run_id> <scope> <source_ref> <payload_json> [events_file]" >&2
    return 1
  fi

  local event_type task_id run_id scope source_ref payload_json events_file
  event_type="$1"
  task_id="$2"
  run_id="$3"
  scope="$4"
  source_ref="$5"
  payload_json="$6"
  events_file="${7:-${CONTEXT_EVENTS_FILE:-logs/context/events.jsonl}}"

  if ! context_event_is_valid_type "${event_type}"; then
    echo "Invalid context event type: ${event_type}" >&2
    return 1
  fi

  if [[ -z "${task_id}" || -z "${run_id}" || -z "${scope}" || -z "${source_ref}" ]]; then
    echo "context_event_emit requires non-empty task_id/run_id/scope/source_ref" >&2
    return 1
  fi

  local event_id timestamp event_json
  event_id="$(context_event_new_id)"
  timestamp="$(context_event_now_utc)"

  mkdir -p "$(dirname "${events_file}")"

  event_json="$(
    EVENT_TYPE="${event_type}" \
    TASK_ID="${task_id}" \
    RUN_ID="${run_id}" \
    TIMESTAMP="${timestamp}" \
    SCOPE="${scope}" \
    SOURCE_REF="${source_ref}" \
    PAYLOAD_JSON="${payload_json}" \
    SCHEMA_VERSION="${CONTEXT_EVENT_SCHEMA_VERSION}" \
    EVENT_ID="${event_id}" \
    python3 - <<'PY'
import json
import os

payload_raw = os.environ["PAYLOAD_JSON"]
try:
    payload = json.loads(payload_raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"context-event payload must be valid JSON: {exc}") from exc

if not isinstance(payload, dict):
    raise SystemExit("context-event payload must be a JSON object")

event = {
    "schema_version": os.environ["SCHEMA_VERSION"],
    "event_id": os.environ["EVENT_ID"],
    "task_id": os.environ["TASK_ID"],
    "run_id": os.environ["RUN_ID"],
    "event_type": os.environ["EVENT_TYPE"],
    "timestamp": os.environ["TIMESTAMP"],
    "scope": os.environ["SCOPE"],
    "payload": payload,
    "source_ref": os.environ["SOURCE_REF"],
}
print(json.dumps(event, separators=(",", ":"), ensure_ascii=True))
PY
  )"

  printf '%s\n' "${event_json}" >> "${events_file}"
  if [[ "${CONTEXT_EVENTS_STDOUT:-1}" != "0" ]]; then
    printf '%s\n' "${event_json}"
  fi
}
