#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OPS_CHECK_CMD="${OPS_CHECK_CMD:-scripts/ops-check.sh}"
DOCTOR_CMD="${DOCTOR_CMD:-scripts/gt-doctor.sh}"
PATH_GUARD_CMD="${PATH_GUARD_CMD:-scripts/beads-path-guard.sh}"
GT_LAND_CMD="${GT_LAND_CMD:-scripts/gt-land.sh}"

usage() {
  cat <<'EOF'
Usage:
  scripts/land.sh --task-id <id> [options]

Pipeline:
  1) ops-check
  2) gt-doctor (optional auto-fix)
  3) gt-land

Options:
  --task-id <id>         Task/issue ID for the run.
  --run-id <id>          Existing run ID to reuse across ops-check/gt-land.
  --scope <name>         Event scope (default: town).
  --rig <name>           Rig name metadata (default: seocho).
  --events-file <path>   JSONL output path (default: logs/context/events.jsonl).
  --pull                 Run git pull --rebase in gt-land.
  --push                 Run git push in gt-land.
  --skip-ops-check       Skip ops-check step.
  --skip-doctor          Skip doctor step.
  --skip-bd-sync         Skip best-effort bd bootstrap in gt-land.
  --skip-branch-check    Skip branch check in gt-land.
  --fix                  Apply auto-fix for single-beads-path doctor failure.
  --dry-run              Pass dry-run mode to gt-land.
  --quiet-events         Pass quiet-events mode to ops-check/gt-land.
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
skip_ops_check=0
skip_doctor=0
skip_bd_sync=0
skip_branch_check=0
fix_mode=0
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
    --skip-ops-check)
      skip_ops_check=1
      shift
      ;;
    --skip-doctor)
      skip_doctor=1
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
    --fix)
      fix_mode=1
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
  run_id="run_${task_id}_$(date -u +"%Y%m%dT%H%M%SZ")"
fi

if [[ "${skip_ops_check}" -eq 0 ]]; then
  ops_cmd=(
    "${OPS_CHECK_CMD}"
    --task-id "${task_id}"
    --run-id "${run_id}"
    --scope "${scope}"
    --rig "${rig}"
    --events-file "${events_file}"
  )
  if [[ "${quiet_events}" -eq 1 ]]; then
    ops_cmd+=(--quiet-events)
  fi
  echo "[land] running ops-check..."
  if ! (cd "${REPO_ROOT}" && "${ops_cmd[@]}"); then
    echo "[land] failed: ops-check step failed." >&2
    exit 1
  fi
else
  echo "[land] skip ops-check"
fi

doctor_failed_checks=""
single_beads_path_failed=0

if [[ "${skip_doctor}" -eq 0 ]]; then
  echo "[land] running gt-doctor..."
  doctor_json="$(
    cd "${REPO_ROOT}" && "${DOCTOR_CMD}" \
      --issues-file .beads/issues.jsonl \
      --beads-dir .beads \
      --repo-root . \
      --json \
      --no-strict
  )"

  doctor_failed_checks="$(
    DOCTOR_JSON="${doctor_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DOCTOR_JSON"])
fails = [check.get("name", "") for check in payload.get("checks", []) if check.get("status") == "fail"]
print(",".join([name for name in fails if name]))
PY
  )"

  single_beads_path_failed="$(
    DOCTOR_JSON="${doctor_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DOCTOR_JSON"])
failed = any(
    check.get("name") == "single-beads-path" and check.get("status") == "fail"
    for check in payload.get("checks", [])
)
print("1" if failed else "0")
PY
  )"

  if [[ -n "${doctor_failed_checks}" ]]; then
    if [[ "${fix_mode}" -eq 1 && "${single_beads_path_failed}" -eq 1 ]]; then
      echo "[land] doctor failed on single-beads-path, applying auto-fix..."
      if ! (cd "${REPO_ROOT}" && "${PATH_GUARD_CMD}" --beads-dir .beads --auto-clean >/dev/null); then
        echo "[land] failed: auto-fix step failed." >&2
        exit 1
      fi
      doctor_json="$(
        cd "${REPO_ROOT}" && "${DOCTOR_CMD}" \
          --issues-file .beads/issues.jsonl \
          --beads-dir .beads \
          --repo-root . \
          --json \
          --no-strict
      )"
      doctor_failed_checks="$(
        DOCTOR_JSON="${doctor_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["DOCTOR_JSON"])
fails = [check.get("name", "") for check in payload.get("checks", []) if check.get("status") == "fail"]
print(",".join([name for name in fails if name]))
PY
      )"
    fi
  fi

  if [[ -n "${doctor_failed_checks}" ]]; then
    echo "[land] failed: doctor checks failed (${doctor_failed_checks})." >&2
    echo "[land] hint: run '${DOCTOR_CMD}' and resolve failing checks before landing." >&2
    exit 1
  fi
else
  echo "[land] skip doctor"
fi

land_cmd=(
  "${GT_LAND_CMD}"
  --task-id "${task_id}"
  --run-id "${run_id}"
  --scope "${scope}"
  --rig "${rig}"
  --events-file "${events_file}"
)
if [[ "${with_pull}" -eq 1 ]]; then
  land_cmd+=(--pull)
fi
if [[ "${with_push}" -eq 1 ]]; then
  land_cmd+=(--push)
fi
if [[ "${skip_bd_sync}" -eq 1 ]]; then
  land_cmd+=(--skip-bd-sync)
fi
if [[ "${skip_branch_check}" -eq 1 ]]; then
  land_cmd+=(--skip-branch-check)
fi
if [[ "${dry_run}" -eq 1 ]]; then
  land_cmd+=(--dry-run)
fi
if [[ "${quiet_events}" -eq 1 ]]; then
  land_cmd+=(--quiet-events)
fi

echo "[land] running gt-land..."
if ! (cd "${REPO_ROOT}" && "${land_cmd[@]}"); then
  echo "[land] failed: gt-land step failed." >&2
  exit 1
fi

echo "[land] completed successfully."
