#!/usr/bin/env bash
set -euo pipefail

FIX=0
RIG=""
LOG_FILE="logs/ops/history.md"

usage() {
  cat <<USAGE
Usage: scripts/ops-check.sh [--fix] [--rig <name>] [--log <path>]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix) FIX=1; shift ;;
    --rig) RIG="${2:-}"; [[ -n "$RIG" ]] || { echo "error: --rig requires value" >&2; exit 2; }; shift 2 ;;
    --log) LOG_FILE="${2:-}"; [[ -n "$LOG_FILE" ]] || { echo "error: --log requires value" >&2; exit 2; }; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$LOG_FILE")" logs/ops/raw

stamp="$(date -u +"%Y%m%dT%H%M%SZ")"
raw_log="logs/ops/raw/${stamp}.log"

cmd=(gt doctor)
[[ "$FIX" -eq 1 ]] && cmd+=(--fix)
[[ -n "$RIG" ]] && cmd+=(--rig "$RIG")

set +e
"${cmd[@]}" 2>&1 | tee "$raw_log"
rc=${PIPESTATUS[0]}
set -e

summary_line="$(grep -E '^✓ [0-9]+ passed' "$raw_log" | tail -n 1 || true)"
[[ -n "$summary_line" ]] || summary_line="summary unavailable"

failed_lines="$(grep -E '^  ✖  ' "$raw_log" || true)"
warn_lines="$(grep -E '^  ⚠  ' "$raw_log" || true)"

{
  echo "## ${stamp}"
  echo "- Scope: ${RIG:-town}"
  echo "- Command: ${cmd[*]}"
  echo "- Exit code: ${rc}"
  echo "- Summary: ${summary_line}"
  if [[ -n "$failed_lines" ]]; then
    echo "- Failed checks:"
    while IFS= read -r line; do echo "  - ${line}"; done <<< "$failed_lines"
  fi
  if [[ -n "$warn_lines" ]]; then
    echo "- Warning checks:"
    while IFS= read -r line; do echo "  - ${line}"; done <<< "$warn_lines"
  fi
  echo "- Raw log: ${raw_log}"
  echo
} >> "$LOG_FILE"

echo "Saved summary to ${LOG_FILE}"
echo "Saved raw output to ${raw_log}"
exit "$rc"
