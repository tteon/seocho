#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

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
    echo "Forbidden runtime-shell pattern found: $pattern" >&2
    exit 1
  fi
}

check_present() {
  local pattern="$1"
  shift

  if ! search_fixed "$pattern" "$@" >/dev/null; then
    echo "Required runtime-shell pattern missing: $pattern" >&2
    exit 1
  fi
}

echo "Checking canonical runtime shell imports..."
check_present "from runtime.runtime_ingest import RuntimeRawIngestor" \
  runtime/server_runtime.py \
  extraction/memory_service.py
check_absent "from runtime_ingest import RuntimeRawIngestor" \
  runtime/server_runtime.py \
  extraction/memory_service.py

echo "Checking compatibility alias surface..."
check_present "_import_module(\"runtime.runtime_ingest\")" \
  extraction/runtime_ingest.py
check_present "import runtime.runtime_ingest as runtime_runtime_ingest" \
  extraction/tests/test_runtime_package_aliases.py

echo "Checking repo-owned runtime tests..."
check_present "importlib.import_module(\"runtime.runtime_ingest\")" \
  extraction/tests/test_runtime_ingest.py
check_absent "importlib.import_module(\"runtime_ingest\")" \
  extraction/tests/test_runtime_ingest.py
check_present "\"runtime.runtime_ingest\"" \
  extraction/tests/test_integration_runtime_flow.py

echo "Checking active runtime migration docs..."
check_present '`runtime/runtime_ingest.py`' \
  docs/AGENT_DEVELOPMENT.md \
  docs/ARCHITECTURE.md \
  docs/RUNTIME_PACKAGE_MIGRATION.md
check_absent '`extraction/runtime_ingest.py`' \
  docs/AGENT_DEVELOPMENT.md

echo "Checking basic CI coverage..."
check_present "runtime/runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh

echo "Runtime shell contract checks passed."
