#!/usr/bin/env bash
set -euo pipefail

API_PORT="${EXTRACTION_API_PORT:-8001}"
CHAT_PORT="${CHAT_INTERFACE_PORT:-8501}"
API_BASE="http://localhost:${API_PORT}"
CHAT_BASE="http://localhost:${CHAT_PORT}"

log() {
  echo "[e2e] $*"
}

wait_http() {
  local url="$1"
  local max_retry="${2:-60}"
  local sleep_sec="${3:-2}"
  for ((i = 1; i <= max_retry; i++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_sec}"
  done
  return 1
}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for e2e smoke checks" >&2
    exit 1
  fi
}

check_services_ready() {
  log "Waiting for extraction API (${API_BASE})..."
  wait_http "${API_BASE}/databases" 90 2
  log "Waiting for chat interface API (${CHAT_BASE})..."
  wait_http "${CHAT_BASE}/api/config" 90 2
}

run_ingest_check() {
  log "Running runtime raw ingest smoke..."
  local payload
  payload='{
    "workspace_id":"default",
    "target_database":"kgruntimeci",
    "records":[
      {"id":"ci_raw_1","content":"ACME acquired Beta in 2024."},
      {"id":"ci_raw_2","content":"Beta provides graph analytics to ACME."}
    ]
  }'
  local response
  response="$(curl -fsS -X POST "${API_BASE}/platform/ingest/raw" \
    -H "Content-Type: application/json" \
    -d "${payload}")"

  local status
  status="$(echo "${response}" | jq -r '.status')"
  local processed
  processed="$(echo "${response}" | jq -r '.records_processed')"

  if [[ "${status}" != "success" && "${status}" != "success_with_fallback" && "${status}" != "partial_success" ]]; then
    echo "Unexpected ingest status: ${status}" >&2
    echo "${response}" >&2
    exit 1
  fi
  if [[ "${processed}" -lt 1 ]]; then
    echo "Ingest processed count is too low: ${processed}" >&2
    echo "${response}" >&2
    exit 1
  fi
}

run_fulltext_check() {
  log "Ensuring fulltext index..."
  local response
  response="$(curl -fsS -X POST "${API_BASE}/indexes/fulltext/ensure" \
    -H "Content-Type: application/json" \
    -d '{
      "workspace_id":"default",
      "databases":["kgruntimeci"],
      "index_name":"entity_fulltext",
      "create_if_missing":true
    }')"
  echo "${response}" | jq -e '.results | length > 0' >/dev/null
}

run_semantic_chat_check() {
  log "Running semantic chat smoke..."
  local response
  response="$(curl -fsS -X POST "${CHAT_BASE}/api/chat/send" \
    -H "Content-Type: application/json" \
    -d '{
      "session_id":"ci_semantic_session",
      "message":"Show graph labels in kgruntimeci",
      "mode":"semantic",
      "workspace_id":"default",
      "databases":["kgruntimeci"]
    }')"
  echo "${response}" | jq -e '.assistant_message | length > 0' >/dev/null
  echo "${response}" | jq -e '(.runtime_payload.route == "lpg") or (.runtime_payload.route == "rdf") or (.runtime_payload.route == "hybrid")' >/dev/null
}

run_debate_chat_check() {
  log "Running debate chat smoke..."

  local debate_payload
  debate_payload='{
    "session_id":"ci_debate_session",
    "message":"Compare known entities across databases",
    "mode":"debate",
    "workspace_id":"default"
  }'

  local tmp_file
  tmp_file="$(mktemp)"
  local code
  local key="${OPENAI_API_KEY:-}"
  local strict=false
  if [[ -n "${key}" && "${key}" != "dummy-key" ]]; then
    strict=true
  fi

  if [[ "${strict}" == "true" ]]; then
    code="$(curl -sS --max-time 120 -o "${tmp_file}" -w "%{http_code}" -X POST "${CHAT_BASE}/api/chat/send" \
      -H "Content-Type: application/json" \
      -d "${debate_payload}")"
    if [[ "${code}" != "200" ]]; then
      echo "Debate mode failed under strict mode: HTTP ${code}" >&2
      cat "${tmp_file}" >&2
      rm -f "${tmp_file}"
      exit 1
    fi
    cat "${tmp_file}" | jq -e '.assistant_message | length > 0' >/dev/null
  else
    # No real key: short non-blocking smoke check only.
    set +e
    code="$(curl -sS --max-time 20 -o "${tmp_file}" -w "%{http_code}" -X POST "${CHAT_BASE}/api/chat/send" \
      -H "Content-Type: application/json" \
      -d "${debate_payload}")"
    local curl_status=$?
    set -e

    if [[ ${curl_status} -ne 0 ]]; then
      log "Debate non-strict check skipped (request timed out or failed without real key)."
      rm -f "${tmp_file}"
      return 0
    fi

    if [[ "${code}" != "200" && "${code}" != "500" ]]; then
      echo "Unexpected debate response code in non-strict mode: ${code}" >&2
      cat "${tmp_file}" >&2
      rm -f "${tmp_file}"
      exit 1
    fi
    cat "${tmp_file}" | jq -e '.' >/dev/null
  fi

  rm -f "${tmp_file}"
}

main() {
  require_jq
  check_services_ready
  run_ingest_check
  run_fulltext_check
  run_semantic_chat_check
  run_debate_chat_check
  log "E2E smoke checks passed."
}

main "$@"
