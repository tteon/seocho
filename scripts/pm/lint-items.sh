#!/usr/bin/env bash
set -euo pipefail

required_prefixes=(sev- impact- urgency- sprint- roadmap- area-)

normalize() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' /:.' '----' | tr -cd 'a-z0-9_-'
}

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/lint-items.sh [--sprint 2026-S03]
EOF
}

sprint_label=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sprint) sprint_label="sprint-$(normalize "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -n "${sprint_label}" ]]; then
  echo "Running issue/task collaboration lint for ${sprint_label}..."
else
  echo "Running issue/task collaboration lint..."
fi

issue_rows="$(bd --no-daemon list --json --all --limit 0)"

ISSUE_ROWS="${issue_rows}" python3 - <<'PY' > /tmp/pm_lint_ids.txt
import json, sys
import os

rows = json.loads(os.environ["ISSUE_ROWS"])
for row in rows:
    status = row.get("status")
    issue_type = row.get("issue_type")
    issue_id = row.get("id", "")
    if status in {"open", "in_progress", "blocked"}:
        if issue_type not in {"bug", "task", "feature", "chore"}:
            continue
        if "-wisp-" in issue_id:
            continue
        print(issue_id)
PY

missing=0
while IFS= read -r issue_id; do
  [[ -z "${issue_id}" ]] && continue
  labels_json="$(bd --no-daemon label list "${issue_id}" --json)"
  if [[ -n "${sprint_label}" ]]; then
    in_sprint="$(LABELS_JSON="${labels_json}" SPRINT_LABEL="${sprint_label}" python3 - <<'PY'
import json, os
labels = json.loads(os.environ["LABELS_JSON"])
print("yes" if os.environ["SPRINT_LABEL"] in labels else "no")
PY
)"
    if [[ "${in_sprint}" != "yes" ]]; then
      continue
    fi
  fi

  for prefix in "${required_prefixes[@]}"; do
    has_prefix="$(LABELS_JSON="${labels_json}" PREFIX="${prefix}" python3 - <<'PY'
import json
import os
labels = json.loads(os.environ["LABELS_JSON"])
prefix = os.environ["PREFIX"]
print("yes" if any(lbl.startswith(prefix) for lbl in labels) else "no")
PY
)"
    if [[ "${has_prefix}" != "yes" ]]; then
      echo "Missing label prefix '${prefix}' on ${issue_id}"
      missing=1
    fi
  done
done < /tmp/pm_lint_ids.txt

rm -f /tmp/pm_lint_ids.txt

if [[ "${missing}" -ne 0 ]]; then
  echo "Lint failed: missing required collaboration labels."
  exit 1
fi

echo "Lint passed: required collaboration labels present."
