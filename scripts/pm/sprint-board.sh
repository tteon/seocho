#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/sprint-board.sh --sprint 2026-S03
EOF
}

normalize() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' /:.' '----' | tr -cd 'a-z0-9_-'
}

sprint=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sprint) sprint="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${sprint}" ]]; then
  echo "Missing required argument: --sprint" >&2
  usage
  exit 1
fi

sprint_label="sprint-$(normalize "$sprint")"

echo "== Sprint Board: ${sprint} (${sprint_label}) =="
echo

echo "-- Count by Status --"
bd count --label "${sprint_label}" --by-status
echo

echo "-- Count by Priority --"
bd count --label "${sprint_label}" --by-priority
echo

echo "-- Roadmap Labels in Sprint --"
SPRINT_LABEL="${sprint_label}" python3 - <<'PY'
import json, os, subprocess

def run(args):
    out = subprocess.check_output(args, text=True)
    return json.loads(out)

sprint_label = os.environ["SPRINT_LABEL"]

label_counts = run(["bd", "count", "--json", "--label", sprint_label, "--by-label"])
groups = label_counts.get("groups", [])
roadmaps = [g for g in groups if g.get("group", "").startswith("roadmap-")]
if not roadmaps:
    print("(none)")
else:
    for g in sorted(roadmaps, key=lambda x: x["group"]):
        print(f"{g['group']}: {g['count']}")
PY
echo

echo "-- P0/P1 Open Work --"
bd list --label "${sprint_label}" --status open --priority-max P1 --sort priority --limit 0
echo

echo "-- In Progress --"
bd list --label "${sprint_label}" --status in_progress --sort priority --limit 0
echo

echo "-- Blocked --"
bd list --label "${sprint_label}" --status blocked --sort priority --limit 0
