#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DOCS_ROOT="src/content/docs"

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
    echo "Forbidden website docs pattern found: $pattern" >&2
    exit 1
  fi
}

check_present() {
  local pattern="$1"
  shift
  if ! search_fixed "$pattern" "$@" >/dev/null; then
    echo "Required website docs pattern missing: $pattern" >&2
    exit 1
  fi
}

echo "Checking website docs for stale sync wording and stale runtime endpoints..."
check_absent "Synced automatically from" "$DOCS_ROOT"
check_absent "Synced automatically from" "scripts/generate-docs.mjs"
check_absent "http://localhost:8501/api/chat/send" "$DOCS_ROOT"

echo "Checking mirrored website docs for current guidance..."
check_present "Source mirrored from" \
  "src/content/docs/docs/index.md" \
  "src/content/docs/docs/quickstart.md" \
  "src/content/docs/docs/apply_your_data.md" \
  "src/content/docs/docs/python_sdk.md" \
  "src/content/docs/docs/tutorial.md" \
  "src/content/docs/docs/open_source_playbook.md"
check_present "http://localhost:8001/platform/chat/send" \
  "src/content/docs/docs/open_source_playbook.md"
check_present "/docs/python_sdk/" \
  "src/content/docs/docs/quickstart.md" \
  "src/content/docs/docs/index.md"

echo "Website docs quality checks passed."
