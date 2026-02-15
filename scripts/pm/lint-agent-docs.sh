#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/lint-agent-docs.sh

Checks agent-facing docs for required baseline content and references.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

missing=0

require_file() {
  local file="$1"
  if [[ ! -f "${file}" ]]; then
    echo "Missing required file: ${file}"
    missing=1
  fi
}

require_pattern() {
  local file="$1"
  local pattern="$2"
  local description="$3"
  if ! rg -n --fixed-strings "${pattern}" "${file}" >/dev/null 2>&1; then
    echo "Missing '${description}' in ${file}"
    missing=1
  fi
}

echo "Running agent docs lint..."

# Required files
require_file "CLAUDE.md"
require_file "AGENTS.md"
require_file "README.md"
require_file "docs/WORKFLOW.md"
require_file "docs/ISSUE_TASK_SYSTEM.md"
require_file "docs/decisions/DECISION_LOG.md"

# Required scripts existence
require_file "scripts/pm/new-issue.sh"
require_file "scripts/pm/new-task.sh"
require_file "scripts/pm/sprint-board.sh"
require_file "scripts/pm/lint-items.sh"

# CLAUDE baseline checks
require_pattern "CLAUDE.md" "OpenAI Agents SDK" "runtime baseline"
require_pattern "CLAUDE.md" "Opik" "trace baseline"
require_pattern "CLAUDE.md" "DozerDB" "database baseline"
require_pattern "CLAUDE.md" "workspace_id" "workspace propagation guardrail"
require_pattern "CLAUDE.md" "Owlready2" "ontology boundary"
require_pattern "CLAUDE.md" "POST /rules/infer" "rules endpoint documentation"
require_pattern "CLAUDE.md" "Push target is always \`main\`." "main push policy"

# AGENTS baseline checks
require_pattern "AGENTS.md" "CLAUDE.md" "agent read order"
require_pattern "AGENTS.md" "scripts/pm/new-issue.sh" "issue workflow command"
require_pattern "AGENTS.md" "scripts/pm/lint-items.sh" "sprint lint command"
require_pattern "AGENTS.md" "Push target is always \`main\`." "main push policy"

# README/docs cross-link checks
require_pattern "README.md" "docs/WORKFLOW.md" "workflow doc link"
require_pattern "README.md" "docs/ISSUE_TASK_SYSTEM.md" "issue/task system link"
require_pattern "docs/README.md" "ISSUE_TASK_SYSTEM.md" "docs index coverage"

if [[ "${missing}" -ne 0 ]]; then
  echo "Lint failed: agent docs baseline is incomplete."
  exit 1
fi

echo "Lint passed: agent docs baseline is complete."
