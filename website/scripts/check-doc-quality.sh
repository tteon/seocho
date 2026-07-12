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

check_no_relative_markdown_links() {
  local pattern='\]\((\.\./)?[A-Za-z0-9_./-]+\.md(#[^)]+)?\)'
  if command -v rg >/dev/null 2>&1; then
    if rg -n "$pattern" "$DOCS_ROOT/docs"; then
      echo
      echo "Relative markdown links found in generated website docs; use site routes or GitHub URLs." >&2
      exit 1
    fi
    return
  fi

  if grep -REn "$pattern" "$DOCS_ROOT/docs"; then
    echo
    echo "Relative markdown links found in generated website docs; use site routes or GitHub URLs." >&2
    exit 1
  fi
}

echo "Checking website docs for stale sync wording and stale runtime endpoints..."
check_absent "Synced automatically from" "$DOCS_ROOT"
check_absent "Synced automatically from" "scripts/generate-docs.mjs"
check_absent "http://localhost:8501/api/chat/send" "$DOCS_ROOT"
check_no_relative_markdown_links

echo "Checking mirrored website docs for current guidance..."
check_present "Source mirrored from" \
  "src/content/docs/docs/index.md" \
  "src/content/docs/docs/quickstart.md" \
  "src/content/docs/docs/runtime_deployment.md" \
  "src/content/docs/docs/apply_your_data.md" \
  "src/content/docs/docs/python_sdk.md" \
  "src/content/docs/docs/run_specs.md" \
  "src/content/docs/docs/tutorial.md" \
  "src/content/docs/docs/open_source_playbook.md" \
  "src/content/docs/docs/release_and_community_operations.md"
check_present "http://localhost:8001/platform/chat/send" \
  "src/content/docs/docs/open_source_playbook.md"
check_present "/docs/python_sdk/" \
  "src/content/docs/docs/quickstart.md" \
  "src/content/docs/docs/index.md"
check_present "/docs/runtime_deployment/" \
  "src/content/docs/docs/quickstart.md" \
  "src/content/docs/docs/index.md"
check_present "/docs/release_and_community_operations/" \
  "src/content/docs/docs/index.md"

echo "Website docs quality checks passed."
