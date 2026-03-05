#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/demo/_common.sh
source "${SCRIPT_DIR}/_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo/pipeline_raw_data.sh [options]

Options:
  --workspace <id>      Workspace ID (default: default)
  --db <name>           Target database name (default: kgdemo_raw)
  --api-port <port>     Extraction API port (default: 8001)
  --output-dir <path>   Output directory (default: /tmp/seocho_beginner_demo)
  --skip-wait           Skip API readiness wait
  -h, --help            Show this help
USAGE
}

workspace_id="default"
target_db="kgdemo_raw"
output_dir="${DEMO_OUTPUT_DIR:-/tmp/seocho_beginner_demo}"
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
    --output-dir)
      output_dir="$2"
      shift 2
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

DEMO_TAG="[raw]"

require_core_tools
if [[ "${skip_wait}" != "true" ]]; then
  ensure_extraction_ready
fi

log "running raw-data ingest demo (workspace=${workspace_id}, db=${target_db})"

payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    '{
      workspace_id: $workspace_id,
      target_database: $target_db,
      semantic_artifact_policy: "auto",
      records: [
        {
          id: "raw_demo_1",
          source_type: "text",
          content: "ACME acquired Beta in 2024."
        },
        {
          id: "raw_demo_2",
          source_type: "csv",
          content: "company,partner\\nBeta,ACME\\nGamma,Delta"
        },
        {
          id: "raw_demo_3",
          source_type: "text",
          content: "Beta provides graph analytics services to ACME."
        }
      ]
    }'
})"

response="$(http_post_json "$(api_base)/platform/ingest/raw" "${payload}")"
status="$(echo "${response}" | jq -r '.status // "unknown"')"
processed="$(echo "${response}" | jq -r '.records_processed // 0')"
failed="$(echo "${response}" | jq -r '.records_failed // 0')"

if ! is_ingest_status_ok "${status}"; then
  err "unexpected ingest status: ${status}"
  echo "${response}" | jq . >&2
  exit 1
fi

if [[ "${processed}" -lt 1 ]]; then
  err "records_processed is too low: ${processed}"
  echo "${response}" | jq . >&2
  exit 1
fi

output_path="${output_dir}/01_raw_data_ingest.json"
write_json_output "${output_path}" "${response}"

log "ingest complete: status=${status}, processed=${processed}, failed=${failed}"
log "next: run scripts/demo/pipeline_meta_artifact.sh --workspace ${workspace_id} --db ${target_db}"
