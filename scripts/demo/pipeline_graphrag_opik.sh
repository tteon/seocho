#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/demo/_common.sh
source "${SCRIPT_DIR}/_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo/pipeline_graphrag_opik.sh [options]

Options:
  --workspace <id>      Workspace ID (default: default)
  --db <name>           Target database name (default: kgdemo_graphrag)
  --api-port <port>     Extraction API port (default: 8001)
  --chat-port <port>    Chat API port (default: 8501)
  --output-dir <path>   Output directory (default: /tmp/seocho_beginner_demo)
  --allow-no-opik       Do not fail when Opik profile is not running
  --skip-wait           Skip API readiness wait
  -h, --help            Show this help
USAGE
}

workspace_id="default"
target_db="kgdemo_graphrag"
output_dir="${DEMO_OUTPUT_DIR:-/tmp/seocho_beginner_demo}"
allow_no_opik=false
skip_wait=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace_id="$2"
      shift 2
      ;;
    --db)
      target_db="$2"
      shift 2
      ;;
    --api-port)
      API_PORT="$2"
      shift 2
      ;;
    --chat-port)
      CHAT_PORT="$2"
      shift 2
      ;;
    --output-dir)
      output_dir="$2"
      shift 2
      ;;
    --allow-no-opik)
      allow_no_opik=true
      shift
      ;;
    --skip-wait)
      skip_wait=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

DEMO_TAG="[graphrag]"

require_core_tools
require_cmd docker
if [[ "${skip_wait}" != "true" ]]; then
  ensure_extraction_ready
  ensure_chat_ready
fi

log "running GraphRAG + Opik demo (workspace=${workspace_id}, db=${target_db})"

ingest_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    '{
      workspace_id: $workspace_id,
      target_database: $target_db,
      records: [
        {id: "graphrag_demo_1", source_type: "text", content: "ACME acquired Beta in 2024."},
        {id: "graphrag_demo_2", source_type: "text", content: "Beta collaborates with Gamma on graph analytics."}
      ]
    }'
})"

ingest_response="$(http_post_json "$(api_base)/platform/ingest/raw" "${ingest_payload}")"
ingest_status="$(echo "${ingest_response}" | jq -r '.status // "unknown"')"
if ! is_ingest_status_ok "${ingest_status}"; then
  err "ingest failed for GraphRAG demo: ${ingest_status}"
  echo "${ingest_response}" | jq . >&2
  exit 1
fi

fulltext_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    '{
      workspace_id: $workspace_id,
      databases: [$target_db],
      index_name: "entity_fulltext",
      create_if_missing: true
    }'
})"

fulltext_response="$(http_post_json "$(api_base)/indexes/fulltext/ensure" "${fulltext_payload}")"
if ! echo "${fulltext_response}" | jq -e '.results | length > 0' >/dev/null; then
  err "failed to ensure fulltext index"
  echo "${fulltext_response}" | jq . >&2
  exit 1
fi

session_seed="$(date +%s)"
semantic_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    --arg session_id "demo_semantic_${session_seed}" \
    '{
      session_id: $session_id,
      message: "Show key entities and relationships in this database.",
      mode: "semantic",
      workspace_id: $workspace_id,
      databases: [$target_db]
    }'
})"

semantic_response="$(http_post_json "$(chat_base)/api/chat/send" "${semantic_payload}")"
semantic_message_len="$(echo "${semantic_response}" | jq -r '.assistant_message | length')"
if [[ "${semantic_message_len}" -le 0 ]]; then
  err "semantic chat response is empty"
  echo "${semantic_response}" | jq . >&2
  exit 1
fi

openai_key="${OPENAI_API_KEY:-}"
strict_debate=false
if [[ -n "${openai_key}" && "${openai_key}" != "dummy-key" ]]; then
  strict_debate=true
fi

debate_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg session_id "demo_debate_${session_seed}" \
    '{
      session_id: $session_id,
      message: "Compare important entities across available databases.",
      mode: "debate",
      workspace_id: $workspace_id
    }'
})"

debate_tmp="$(mktemp)"
debate_http_code=""
debate_response='{}'
debate_status="skipped"

if [[ "${strict_debate}" == "true" ]]; then
  debate_http_code="$(curl -sS --max-time 120 -o "${debate_tmp}" -w "%{http_code}" -X POST "$(chat_base)/api/chat/send" \
    -H "Content-Type: application/json" \
    -d "${debate_payload}")"
  debate_response="$(cat "${debate_tmp}")"
  if [[ "${debate_http_code}" != "200" ]]; then
    rm -f "${debate_tmp}"
    err "debate chat failed in strict mode: HTTP ${debate_http_code}"
    echo "${debate_response}" | jq . >&2 || true
    exit 1
  fi
  if [[ "$(echo "${debate_response}" | jq -r '.assistant_message | length')" -le 0 ]]; then
    rm -f "${debate_tmp}"
    err "debate chat returned empty assistant_message"
    exit 1
  fi
  debate_status="ok_strict"
else
  set +e
  debate_http_code="$(curl -sS --max-time 20 -o "${debate_tmp}" -w "%{http_code}" -X POST "$(chat_base)/api/chat/send" \
    -H "Content-Type: application/json" \
    -d "${debate_payload}")"
  curl_status=$?
  set -e
  if [[ ${curl_status} -ne 0 ]]; then
    debate_status="skipped_no_real_key"
    debate_response='{"note":"debate skipped in non-strict mode (timeout/no key)"}'
  else
    debate_response="$(cat "${debate_tmp}")"
    if [[ "${debate_http_code}" != "200" && "${debate_http_code}" != "500" ]]; then
      rm -f "${debate_tmp}"
      err "unexpected debate HTTP code in non-strict mode: ${debate_http_code}"
      echo "${debate_response}" | jq . >&2 || true
      exit 1
    fi
    debate_status="ok_non_strict"
  fi
fi
rm -f "${debate_tmp}"

opik_backend_running=false
opik_ui_reachable=false
extraction_opik_configured=false
extraction_opik_url=""

if docker ps --format '{{.Names}}' | grep -Fxq "opik-backend"; then
  opik_backend_running=true
fi
if curl -fsS "http://localhost:5173" >/dev/null 2>&1; then
  opik_ui_reachable=true
fi
if docker ps --format '{{.Names}}' | grep -Fxq "extraction-service"; then
  extraction_opik_url="$(docker exec extraction-service sh -lc 'printf "%s" "${OPIK_URL_OVERRIDE:-}"' 2>/dev/null || true)"
  if [[ -n "${extraction_opik_url}" ]]; then
    extraction_opik_configured=true
  fi
fi

if [[ "${allow_no_opik}" != "true" ]]; then
  if [[ "${opik_backend_running}" != "true" || "${opik_ui_reachable}" != "true" || "${extraction_opik_configured}" != "true" ]]; then
    err "opik checks failed (backend=${opik_backend_running}, ui=${opik_ui_reachable}, extraction_configured=${extraction_opik_configured})"
    err "start with 'make opik-up' and ensure OPIK_URL is configured in .env"
    exit 1
  fi
fi

summary_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg database "${target_db}" \
    --argjson ingest_result "${ingest_response}" \
    --argjson fulltext_result "${fulltext_response}" \
    --argjson semantic_chat_result "${semantic_response}" \
    --argjson debate_chat_result "${debate_response}" \
    --arg debate_http_code "${debate_http_code}" \
    --arg debate_status "${debate_status}" \
    --arg extraction_opik_url "${extraction_opik_url}" \
    --argjson strict_debate "${strict_debate}" \
    --argjson opik_backend_running "${opik_backend_running}" \
    --argjson opik_ui_reachable "${opik_ui_reachable}" \
    --argjson extraction_opik_configured "${extraction_opik_configured}" \
    '{
      workspace_id: $workspace_id,
      database: $database,
      ingest_result: $ingest_result,
      fulltext_result: $fulltext_result,
      semantic_chat_result: $semantic_chat_result,
      debate: {
        status: $debate_status,
        strict_mode: $strict_debate,
        http_code: $debate_http_code,
        response: $debate_chat_result
      },
      opik_checks: {
        backend_running: $opik_backend_running,
        ui_reachable: $opik_ui_reachable,
        extraction_configured: $extraction_opik_configured,
        extraction_opik_url: $extraction_opik_url
      }
    }'
})"

output_path="${output_dir}/04_graphrag_with_opik.json"
write_json_output "${output_path}" "${summary_payload}"

log "GraphRAG demo complete."
log "if Opik is enabled, inspect traces at http://localhost:5173 (project: seocho)."
