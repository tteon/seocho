#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/demo/_common.sh
source "${SCRIPT_DIR}/_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo/pipeline_neo4j_load.sh [options]

Options:
  --workspace <id>          Workspace ID (default: default)
  --db <name>               Target database name (default: kgdemo_neo4j)
  --api-port <port>         Extraction API port (default: 8001)
  --neo4j-container <name>  Neo4j/DozerDB container (default: graphrag-neo4j)
  --neo4j-user <user>       Database user (default: neo4j)
  --neo4j-password <pass>   Database password (default: password)
  --output-dir <path>       Output directory (default: /tmp/seocho_beginner_demo)
  --skip-wait               Skip API readiness wait
  -h, --help                Show this help
USAGE
}

workspace_id="default"
target_db="kgdemo_neo4j"
neo4j_container="${NEO4J_CONTAINER:-graphrag-neo4j}"
neo4j_user="${NEO4J_USER:-neo4j}"
neo4j_password="${NEO4J_PASSWORD:-password}"
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
    --neo4j-container)
      neo4j_container="$2"
      shift 2
      ;;
    --neo4j-user)
      neo4j_user="$2"
      shift 2
      ;;
    --neo4j-password)
      neo4j_password="$2"
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

DEMO_TAG="[neo4j]"

require_core_tools
require_cmd docker
if [[ "${skip_wait}" != "true" ]]; then
  ensure_extraction_ready
fi

log "running neo4j pipeline demo (workspace=${workspace_id}, db=${target_db})"

ingest_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    '{
      workspace_id: $workspace_id,
      target_database: $target_db,
      records: [
        {id: "neo4j_demo_1", source_type: "text", content: "Alice works at ACME."},
        {id: "neo4j_demo_2", source_type: "text", content: "ACME partners with Beta Corp."}
      ]
    }'
})"

ingest_response="$(http_post_json "$(api_base)/platform/ingest/raw" "${ingest_payload}")"
ingest_status="$(echo "${ingest_response}" | jq -r '.status // "unknown"')"
if ! is_ingest_status_ok "${ingest_status}"; then
  err "ingest failed before Neo4j query: ${ingest_status}"
  echo "${ingest_response}" | jq . >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -Fxq "${neo4j_container}"; then
  err "container not running: ${neo4j_container}"
  err "start core services first (make up)"
  exit 1
fi

if ! docker exec "${neo4j_container}" sh -lc 'command -v cypher-shell >/dev/null 2>&1'; then
  err "cypher-shell not found in container: ${neo4j_container}"
  exit 1
fi

cypher_query='MATCH (n) RETURN labels(n) AS labels, count(*) AS count ORDER BY count DESC LIMIT 5;'
cypher_output="$(docker exec "${neo4j_container}" cypher-shell -u "${neo4j_user}" -p "${neo4j_password}" -d "${target_db}" --format plain "${cypher_query}")"

if [[ -z "$(echo "${cypher_output}" | tr -d '[:space:]')" ]]; then
  err "empty cypher query output"
  exit 1
fi

summary_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg database "${target_db}" \
    --arg container "${neo4j_container}" \
    --arg query "${cypher_query}" \
    --arg cypher_output "${cypher_output}" \
    --argjson ingest_result "${ingest_response}" \
    '{
      workspace_id: $workspace_id,
      database: $database,
      container: $container,
      ingest_result: $ingest_result,
      cypher_query: $query,
      cypher_output: $cypher_output
    }'
})"

output_path="${output_dir}/03_neo4j_load_and_query.json"
write_json_output "${output_path}" "${summary_payload}"

log "neo4j query complete."
log "next: run scripts/demo/pipeline_graphrag_opik.sh --workspace ${workspace_id} --db ${target_db}"
