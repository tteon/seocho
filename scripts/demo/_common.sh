#!/usr/bin/env bash
set -euo pipefail

DEMO_TAG="${DEMO_TAG:-[demo]}"
API_PORT="${API_PORT:-${EXTRACTION_API_PORT:-8001}}"
CHAT_PORT="${CHAT_PORT:-${CHAT_INTERFACE_PORT:-8501}}"

log() {
  printf '%s %s\n' "${DEMO_TAG}" "$*"
}

err() {
  printf '%s ERROR: %s\n' "${DEMO_TAG}" "$*" >&2
}

api_base() {
  printf 'http://localhost:%s' "${API_PORT}"
}

chat_base() {
  printf 'http://localhost:%s' "${CHAT_PORT}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    err "required command not found: ${cmd}"
    exit 1
  fi
}

require_core_tools() {
  require_cmd curl
  require_cmd jq
}

wait_http() {
  local url="$1"
  local max_retry="${2:-60}"
  local sleep_sec="${3:-2}"
  local i
  for ((i = 1; i <= max_retry; i++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_sec}"
  done
  return 1
}

ensure_extraction_ready() {
  local url
  url="$(api_base)/databases"
  log "waiting for extraction API: ${url}"
  if ! wait_http "${url}" 90 2; then
    err "extraction API is not ready: ${url}"
    exit 1
  fi
}

ensure_chat_ready() {
  local url
  url="$(chat_base)/api/config"
  log "waiting for chat API: ${url}"
  if ! wait_http "${url}" 90 2; then
    err "chat API is not ready: ${url}"
    exit 1
  fi
}

http_post_json() {
  local url="$1"
  local payload="$2"
  curl -fsS -X POST "${url}" \
    -H "Content-Type: application/json" \
    -d "${payload}"
}

http_get_json() {
  local url="$1"
  curl -fsS "${url}"
}

write_json_output() {
  local output_path="$1"
  local payload="$2"
  mkdir -p "$(dirname "${output_path}")"
  printf '%s\n' "${payload}" > "${output_path}"
  log "saved output: ${output_path}"
}

is_ingest_status_ok() {
  local status="$1"
  case "${status}" in
    success|success_with_fallback|partial_success)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
