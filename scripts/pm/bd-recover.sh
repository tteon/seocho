#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BEADS_DIR="${REPO_ROOT}/.beads"

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/bd-recover.sh [--fix]

Diagnose and, with --fix, repair the local Beads Dolt server lifecycle.

Default mode is read-only:
  - runs bd doctor
  - shows bd dolt status
  - reports any Dolt SQL server listening on the configured Beads port

Fix mode is intentionally narrow:
  - only targets a dolt sql-server process on the Beads port
  - stops that process when bd does not consider the server healthy
  - restarts via bd dolt start
  - reruns bd doctor
EOF
}

fix_mode=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix)
      fix_mode=1
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

if [[ ! -d "${BEADS_DIR}" ]]; then
  echo "No .beads directory found at ${BEADS_DIR}" >&2
  exit 1
fi

port="$(tr -cd '0-9' < "${BEADS_DIR}/dolt-server.port" 2>/dev/null || true)"
if [[ -z "${port}" ]]; then
  port="$(grep -E '^[[:space:]]*port:' "${BEADS_DIR}/dolt/config.yaml" 2>/dev/null | head -n 1 | awk '{print $2}' || true)"
fi
if [[ -z "${port}" ]]; then
  echo "Unable to determine Beads Dolt port." >&2
  exit 1
fi

echo "Repository: ${REPO_ROOT}"
echo "Beads dir:  ${BEADS_DIR}"
echo "Dolt port:  ${port}"
echo

doctor_output="$(mktemp)"
doctor_status=0
if bd doctor >"${doctor_output}" 2>&1; then
  doctor_status=0
else
  doctor_status=$?
fi

cat "${doctor_output}"
echo

echo "bd dolt status:"
status_output="$(mktemp)"
if bd dolt status >"${status_output}" 2>&1; then
  status_exit=0
else
  status_exit=$?
fi
cat "${status_output}"
echo

mapfile -t pids < <(
  ss -tnlp 2>/dev/null \
    | awk -v port=":${port}" '$4 ~ port {print $NF}' \
    | grep -oE 'pid=[0-9]+' \
    | cut -d= -f2 \
    | sort -u
)

if [[ "${#pids[@]}" -eq 0 ]]; then
  echo "No process is listening on ${port}."
else
  echo "Processes listening on ${port}:"
  for pid in "${pids[@]}"; do
    cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    cwd="$(readlink "/proc/${pid}/cwd" 2>/dev/null || true)"
    echo "  pid=${pid}"
    echo "    cmd=${cmd:-unknown}"
    echo "    cwd=${cwd:-unknown}"
  done
fi

echo
echo "bd dolt connection:"
show_output="$(mktemp)"
if bd dolt show >"${show_output}" 2>&1; then
  show_status=0
else
  show_status=$?
fi
cat "${show_output}"

needs_recovery=0
recovery_reason=""
if [[ "${doctor_status}" -ne 0 ]]; then
  needs_recovery=1
  recovery_reason="bd doctor failed"
elif [[ "${show_status}" -ne 0 ]]; then
  needs_recovery=1
  recovery_reason="bd dolt show could not connect"
elif [[ "${#pids[@]}" -eq 0 && "${status_exit}" -ne 0 ]]; then
  needs_recovery=1
  recovery_reason="no Dolt process is listening on the configured port"
fi

if [[ "${needs_recovery}" -eq 0 ]]; then
  echo
  if grep -q "Dolt server: not running" "${status_output}" && [[ "${#pids[@]}" -gt 0 ]]; then
    echo "Beads is reachable. Note: bd dolt status reports not running, but the SQL connection is healthy."
  else
    echo "Beads doctor and Dolt connection passed. No recovery required."
  fi
  rm -f "${doctor_output}" "${status_output}" "${show_output}"
  exit 0
fi

if [[ "${fix_mode}" -ne 1 ]]; then
  echo
  echo "Recovery needed: ${recovery_reason}."
  echo "Re-run with --fix to restart only the Dolt sql-server on port ${port}."
  rm -f "${doctor_output}" "${status_output}" "${show_output}"
  exit 1
fi

echo
echo "Fix mode enabled: ${recovery_reason}."
for pid in "${pids[@]}"; do
  cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  if [[ "${cmd}" == *"dolt sql-server"* ]]; then
    echo "Stopping stale Dolt sql-server pid=${pid}"
    kill "${pid}" || true
  else
    echo "Refusing to stop non-Dolt process pid=${pid}: ${cmd:-unknown}" >&2
  fi
done

sleep 2
echo "Starting Beads Dolt server..."
bd dolt start
sleep 4
echo
echo "Post-recovery doctor:"
bd doctor

rm -f "${doctor_output}" "${status_output}" "${show_output}"
