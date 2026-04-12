#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ACTIVE_DOCS=(
  "README.md"
  "CLAUDE.md"
  "docs/README.md"
  "docs/QUICKSTART.md"
  "docs/PYTHON_INTERFACE_QUICKSTART.md"
  "docs/APPLY_YOUR_DATA.md"
  "docs/TUTORIAL_FIRST_RUN.md"
  "docs/OPEN_SOURCE_PLAYBOOK.md"
  "docs/WORKFLOW.md"
)

for path in "${ACTIVE_DOCS[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Required active doc missing: $path" >&2
    exit 1
  fi
done

search_fixed() {
  local pattern="$1"
  shift

  if command -v rg >/dev/null 2>&1; then
    rg -n --fixed-strings "$pattern" "$@"
    return
  fi

  grep -RFn -- "$pattern" "$@"
}

check_absent() {
  local pattern="$1"
  shift
  if search_fixed "$pattern" "$@"; then
    echo
    echo "Forbidden docs pattern found: $pattern" >&2
    exit 1
  fi
}

check_present() {
  local pattern="$1"
  shift
  if ! search_fixed "$pattern" "$@" >/dev/null; then
    echo "Required docs pattern missing: $pattern" >&2
    exit 1
  fi
}

echo "Running agent docs lint..."
scripts/pm/lint-agent-docs.sh

echo "Checking active docs for stale endpoint and sync-contract patterns..."
check_absent "http://localhost:8501/api/chat/send" "${ACTIVE_DOCS[@]}"
check_absent "sync-docs-website.yml" "${ACTIVE_DOCS[@]}"
check_absent "repository_dispatch" "${ACTIVE_DOCS[@]}"
check_absent "seocho-docs-sync" "${ACTIVE_DOCS[@]}"
check_absent "Synced automatically from" "${ACTIVE_DOCS[@]}"

echo "Checking active docs for required current runtime guidance..."
check_present "http://localhost:8001/platform/chat/send" \
  "docs/TUTORIAL_FIRST_RUN.md" \
  "docs/OPEN_SOURCE_PLAYBOOK.md"
check_present "tteon.github.io/" \
  "docs/README.md"
check_present ".github/workflows/docs-consistency.yml" \
  "README.md" \
  "docs/WORKFLOW.md"
check_present "npm run build" \
  "docs/WORKFLOW.md"
check_present "website repository will run its own docs quality workflow" \
  "docs/decisions/ADR-0036-documentation-consistency-ci-contract.md"

echo "Docs contract checks passed."
