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
check_present "from runtime.agent_readiness import summarize_readiness" \
  runtime/agent_server.py
check_present "from runtime.middleware import RequestIDMiddleware" \
  runtime/agent_server.py
check_present "from runtime.middleware import get_request_id" \
  runtime/agent_server.py \
  runtime/public_memory_api.py
check_present "from runtime.memory_service import GraphMemoryService" \
  runtime/server_runtime.py
check_present "from runtime.runtime_ingest import RuntimeRawIngestor" \
  runtime/server_runtime.py \
  runtime/memory_service.py
check_absent "from agent_readiness import summarize_readiness" \
  runtime/agent_server.py
check_absent "from middleware import" \
  runtime/agent_server.py \
  runtime/public_memory_api.py
check_absent "from memory_service import GraphMemoryService" \
  runtime/server_runtime.py
check_absent "from runtime_ingest import RuntimeRawIngestor" \
  runtime/server_runtime.py \
  runtime/memory_service.py

echo "Checking compatibility alias surface..."
check_present "alias_runtime_module(alias_name: str, runtime_module: str)" \
  extraction/_runtime_alias.py
check_present "repo_root = Path(__file__).resolve().parent.parent" \
  extraction/_runtime_alias.py
check_present "_alias_runtime_module(__name__, \"runtime.agent_readiness\")" \
  extraction/agent_readiness.py
check_present "_alias_runtime_module(__name__, \"runtime.agent_server\")" \
  extraction/agent_server.py
check_present "_alias_runtime_module(__name__, \"runtime.middleware\")" \
  extraction/middleware.py
check_present "_alias_runtime_module(__name__, \"runtime.memory_service\")" \
  extraction/memory_service.py
check_present "_alias_runtime_module(__name__, \"runtime.policy\")" \
  extraction/policy.py
check_present "_alias_runtime_module(__name__, \"runtime.public_memory_api\")" \
  extraction/public_memory_api.py
check_present "_alias_runtime_module(__name__, \"runtime.runtime_ingest\")" \
  extraction/runtime_ingest.py
check_present "_alias_runtime_module(__name__, \"runtime.server_runtime\")" \
  extraction/server_runtime.py
check_present "cwd=os.path.join(ROOT_DIR, \"extraction\")" \
  extraction/tests/test_runtime_package_aliases.py
check_present "import runtime.agent_readiness as runtime_agent_readiness" \
  extraction/tests/test_runtime_package_aliases.py
check_present "import runtime.middleware as runtime_middleware" \
  extraction/tests/test_runtime_package_aliases.py
check_present "import runtime.memory_service as runtime_memory_service" \
  extraction/tests/test_runtime_package_aliases.py
check_present "import runtime.runtime_ingest as runtime_runtime_ingest" \
  extraction/tests/test_runtime_package_aliases.py

echo "Checking local compose runtime visibility..."
check_present "./runtime:/app/runtime:ro" \
  docker-compose.yml
check_present "./seocho:/app/seocho:ro" \
  docker-compose.yml

echo "Checking repo-owned runtime tests..."
check_present "from runtime.agent_readiness import summarize_readiness" \
  extraction/tests/test_agent_readiness.py
check_present "from runtime.middleware import RequestIDMiddleware, get_request_id" \
  extraction/tests/test_error_responses.py \
  extraction/tests/test_middleware.py
check_present "from runtime.memory_service import GraphMemoryService" \
  extraction/tests/test_memory_service.py
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
check_present '`runtime/memory_service.py`' \
  docs/ARCHITECTURE.md \
  docs/RUNTIME_PACKAGE_MIGRATION.md
check_absent '`extraction/runtime_ingest.py`' \
  docs/AGENT_DEVELOPMENT.md

echo "Checking basic CI coverage..."
check_present "runtime/memory_service.py" \
  scripts/ci/run_basic_ci.sh
check_present "runtime/runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/memory_service.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_memory_service.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_runtime_ingest.py" \
  scripts/ci/run_basic_ci.sh

echo "Runtime shell contract checks passed."
