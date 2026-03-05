#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/demo/_common.sh
source "${SCRIPT_DIR}/_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo/run_beginner_pipelines.sh [options]

Options:
  --workspace <id>      Workspace ID (default: default)
  --db-prefix <prefix>  Database prefix (default: kgdemo_)
  --api-port <port>     Extraction API port (default: 8001)
  --chat-port <port>    Chat API port (default: 8501)
  --output-dir <path>   Output directory (default: /tmp/seocho_beginner_demo)
  --allow-no-opik       Do not fail when Opik profile is not running
  -h, --help            Show this help
USAGE
}

workspace_id="default"
db_prefix="kgdemo_"
output_dir="${DEMO_OUTPUT_DIR:-/tmp/seocho_beginner_demo}"
allow_no_opik=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace_id="$2"
      shift 2
      ;;
    --db-prefix)
      db_prefix="$2"
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

DEMO_TAG="[demo-all]"

raw_db="${db_prefix}raw"
meta_db="${db_prefix}meta"
neo4j_db="${db_prefix}neo4j"
graphrag_db="${db_prefix}graphrag"

log "starting beginner 4-pipeline demo pack"
log "workspace=${workspace_id}, output_dir=${output_dir}"

"${SCRIPT_DIR}/pipeline_raw_data.sh" \
  --workspace "${workspace_id}" \
  --db "${raw_db}" \
  --api-port "${API_PORT}" \
  --output-dir "${output_dir}"

"${SCRIPT_DIR}/pipeline_meta_artifact.sh" \
  --workspace "${workspace_id}" \
  --db "${meta_db}" \
  --api-port "${API_PORT}" \
  --output-dir "${output_dir}"

"${SCRIPT_DIR}/pipeline_neo4j_load.sh" \
  --workspace "${workspace_id}" \
  --db "${neo4j_db}" \
  --api-port "${API_PORT}" \
  --output-dir "${output_dir}"

graphrag_args=(
  --workspace "${workspace_id}"
  --db "${graphrag_db}"
  --api-port "${API_PORT}"
  --chat-port "${CHAT_PORT}"
  --output-dir "${output_dir}"
)
if [[ "${allow_no_opik}" == "true" ]]; then
  graphrag_args+=(--allow-no-opik)
fi
"${SCRIPT_DIR}/pipeline_graphrag_opik.sh" "${graphrag_args[@]}"

log "all four demo pipelines completed"
log "result files are under: ${output_dir}"
