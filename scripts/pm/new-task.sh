#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/new-task.sh \
    --title "Implement rules profile pagination" \
    --goal "Paginate profile list endpoint" \
    --severity medium \
    --impact medium \
    --urgency this_sprint \
    --sprint 2026-S03 \
    --roadmap platform-workflow \
    --area backend \
    [--priority P2] \
    [--parent hq-123] \
    [--estimate 180] \
    [--assignee name] \
    [--due +3d] \
    [--dry-run]
EOF
}

normalize() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' /:.' '----' | tr -cd 'a-z0-9_-'
}

title=""
goal=""
severity=""
impact=""
urgency=""
sprint=""
roadmap=""
area=""
priority="P2"
parent=""
estimate=""
assignee=""
due=""
dry_run=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) title="$2"; shift 2 ;;
    --goal) goal="$2"; shift 2 ;;
    --severity) severity="$2"; shift 2 ;;
    --impact) impact="$2"; shift 2 ;;
    --urgency) urgency="$2"; shift 2 ;;
    --sprint) sprint="$2"; shift 2 ;;
    --roadmap) roadmap="$2"; shift 2 ;;
    --area) area="$2"; shift 2 ;;
    --priority) priority="$2"; shift 2 ;;
    --parent) parent="$2"; shift 2 ;;
    --estimate) estimate="$2"; shift 2 ;;
    --assignee) assignee="$2"; shift 2 ;;
    --due) due="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

for required in title goal severity impact urgency sprint roadmap area; do
  if [[ -z "${!required}" ]]; then
    echo "Missing required argument: --${required}" >&2
    usage
    exit 1
  fi
done

sev_label="sev-$(normalize "$severity")"
impact_label="impact-$(normalize "$impact")"
urgency_label="urgency-$(normalize "$urgency")"
sprint_label="sprint-$(normalize "$sprint")"
roadmap_label="roadmap-$(normalize "$roadmap")"
area_label="area-$(normalize "$area")"
labels="kind-task,${sev_label},${impact_label},${urgency_label},${sprint_label},${roadmap_label},${area_label}"

body_file="$(mktemp)"
cat > "${body_file}" <<EOF
## Goal
${goal}

## Scope
- In scope:
- Out of scope:

## Collaboration Context
- Severity: ${severity}
- Impact: ${impact}
- Urgency: ${urgency}
- Sprint: ${sprint}
- Roadmap: ${roadmap}
- Area: ${area}

## Acceptance Criteria
- [ ] Implementation complete
- [ ] Tests/validation complete
- [ ] Documentation updated
EOF

metadata="$(python3 - <<PY
import json
print(json.dumps({
  "collab": {
    "severity": "${severity}",
    "impact": "${impact}",
    "urgency": "${urgency}",
    "sprint": "${sprint}",
    "roadmap": "${roadmap}",
    "area": "${area}",
    "kind": "task"
  }
}))
PY
)"

if [[ "${dry_run}" == true ]]; then
  echo "[dry-run] type=task priority=${priority} labels=${labels}"
  echo "[dry-run] title=${title}"
  echo "[dry-run] description:"
  cat "${body_file}"
  rm -f "${body_file}"
  exit 0
fi

cmd=(bd create --title "${title}" --type task --priority "${priority}" --body-file "${body_file}" --labels "${labels}" --silent)
if [[ -n "${parent}" ]]; then cmd+=(--parent "${parent}"); fi
if [[ -n "${estimate}" ]]; then cmd+=(--estimate "${estimate}"); fi
if [[ -n "${assignee}" ]]; then cmd+=(--assignee "${assignee}"); fi
if [[ -n "${due}" ]]; then cmd+=(--due "${due}"); fi

issue_id="$("${cmd[@]}")"
bd update "${issue_id}" --metadata "${metadata}" >/dev/null 2>&1 || true
rm -f "${body_file}"

echo "${issue_id}"
