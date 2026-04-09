#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/demo/_common.sh
source "${SCRIPT_DIR}/_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo/pipeline_meta_artifact.sh [options]

Options:
  --workspace <id>      Workspace ID (default: default)
  --db <name>           Target database name (default: kgdemo_meta)
  --api-port <port>     Extraction API port (default: 8001)
  --reviewer <name>     Artifact approver identity (default: demo-reviewer)
  --output-dir <path>   Output directory (default: /tmp/seocho_beginner_demo)
  --skip-wait           Skip API readiness wait
  -h, --help            Show this help
USAGE
}

workspace_id="default"
target_db="kgdemo_meta"
reviewer="demo-reviewer"
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
    --reviewer)
      reviewer="$2"
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

DEMO_TAG="[meta]"

require_core_tools
if [[ "${skip_wait}" != "true" ]]; then
  ensure_extraction_ready
fi

log "running meta pipeline demo (workspace=${workspace_id}, db=${target_db})"

ingest_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg target_db "${target_db}" \
    '{
      workspace_id: $workspace_id,
      target_database: $target_db,
      semantic_artifact_policy: "draft_only",
      records: [
        {id: "meta_demo_1", source_type: "text", content: "Company Alpha acquired Company Beta in 2024."},
        {id: "meta_demo_2", source_type: "text", content: "Company Beta provides analytics tooling to Alpha."}
      ]
    }'
})"

ingest_response="$(http_post_json "$(api_base)/platform/ingest/raw" "${ingest_payload}")"
ingest_status="$(echo "${ingest_response}" | jq -r '.status // "unknown"')"
if ! is_ingest_status_ok "${ingest_status}"; then
  err "ingest failed before meta artifact creation: ${ingest_status}"
  echo "${ingest_response}" | jq . >&2
  exit 1
fi

draft_ontology="$(echo "${ingest_response}" | jq -c '.semantic_artifacts.draft_ontology_candidate // {"ontology_name":"demo","classes":[],"relationships":[]}')"
draft_shacl="$(echo "${ingest_response}" | jq -c '.semantic_artifacts.draft_shacl_candidate // {"shapes":[]}')"
draft_vocab="$(echo "${ingest_response}" | jq -c '.semantic_artifacts.draft_vocabulary_candidate // {"schema_version":"vocabulary.v2","profile":"skos","terms":[]}')"
artifact_name="meta_demo_${target_db}_$(date +%Y%m%d%H%M%S)"

draft_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg artifact_name "${artifact_name}" \
    --argjson ontology_candidate "${draft_ontology}" \
    --argjson shacl_candidate "${draft_shacl}" \
    --argjson vocabulary_candidate "${draft_vocab}" \
    '{
      workspace_id: $workspace_id,
      name: $artifact_name,
      ontology_candidate: $ontology_candidate,
      shacl_candidate: $shacl_candidate,
      vocabulary_candidate: $vocabulary_candidate,
      source_summary: {source: "pipeline_meta_artifact", demo: true}
    }'
})"

draft_response="$(http_post_json "$(api_base)/semantic/artifacts/drafts" "${draft_payload}")"
artifact_id="$(echo "${draft_response}" | jq -r '.artifact_id // empty')"
draft_status="$(echo "${draft_response}" | jq -r '.status // "unknown"')"

if [[ -z "${artifact_id}" || "${draft_status}" != "draft" ]]; then
  err "failed to create semantic artifact draft"
  echo "${draft_response}" | jq . >&2
  exit 1
fi

approve_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg reviewer "${reviewer}" \
    '{workspace_id: $workspace_id, approved_by: $reviewer, approval_note: "beginner meta pipeline demo approval"}'
})"

approve_response="$(http_post_json "$(api_base)/semantic/artifacts/${artifact_id}/approve" "${approve_payload}")"
approved_status="$(echo "${approve_response}" | jq -r '.status // "unknown"')"
if [[ "${approved_status}" != "approved" ]]; then
  err "failed to approve semantic artifact: ${artifact_id}"
  echo "${approve_response}" | jq . >&2
  exit 1
fi

read_response="$(http_get_json "$(api_base)/semantic/artifacts/${artifact_id}?workspace_id=${workspace_id}")"

summary_payload="$({
  jq -n \
    --arg workspace_id "${workspace_id}" \
    --arg database "${target_db}" \
    --arg artifact_id "${artifact_id}" \
    --argjson ingest "${ingest_response}" \
    --argjson draft "${draft_response}" \
    --argjson approved "${approve_response}" \
    --argjson artifact "${read_response}" \
    '{
      workspace_id: $workspace_id,
      database: $database,
      artifact_id: $artifact_id,
      ingest_result: $ingest,
      draft_result: $draft,
      approved_result: $approved,
      artifact_readback: $artifact
    }'
})"

output_path="${output_dir}/02_meta_artifact_lifecycle.json"
write_json_output "${output_path}" "${summary_payload}"

log "meta pipeline complete: artifact_id=${artifact_id}, status=${approved_status}"
log "next: run scripts/demo/pipeline_neo4j_load.sh --workspace ${workspace_id} --db ${target_db}"
