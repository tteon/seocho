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
    echo "Forbidden module-ownership pattern found: $pattern" >&2
    exit 1
  fi
}

check_present() {
  local pattern="$1"
  shift

  if ! search_fixed "$pattern" "$@" >/dev/null; then
    echo "Required module-ownership pattern missing: $pattern" >&2
    exit 1
  fi
}

echo "Checking canonical indexing ownership..."
check_present "from seocho.index import CanonicalExtractionEngine" \
  extraction/pipeline.py
check_present "from seocho.store.llm import create_llm_backend" \
  extraction/pipeline.py
check_absent "from extractor import" \
  extraction/pipeline.py
check_present "from .index.ingestion_facade import IngestRequest, IngestionFacade" \
  seocho/client.py
check_present "self._ingestion = IngestionFacade(self._indexing, publisher=self._events)" \
  seocho/client.py

echo "Checking extraction shim ownership..."
check_present "from seocho.rules import (" \
  extraction/rule_constraints.py
check_absent "def infer_rules_from_graph" \
  extraction/rule_constraints.py
check_absent "def apply_rules_to_graph" \
  extraction/rule_constraints.py
check_present 'Compatibility adapter — delegates to ``seocho.store.vector`` (canonical).' \
  extraction/vector_store.py
check_present 'from seocho.store.vector import FAISSVectorStore as _SDK' \
  extraction/vector_store.py
check_present 'from seocho.store.vector import LanceDBVectorStore as _SDK' \
  extraction/vector_store.py

echo "Checking focused ownership tests..."
check_present "test_rule_constraints_shim_exports_canonical_symbols" \
  extraction/tests/test_rule_constraints_shim.py
check_present "monkeypatch.setattr(canonical_vector_store, \"FAISSVectorStore\", _FakeCanonicalStore)" \
  extraction/tests/test_vector_store_shim.py
check_present "assert store._store.kwargs == {\"api_key\": \"test\", \"dimension\": 3}" \
  extraction/tests/test_vector_store_shim.py
check_present "assert restored._store._index.ntotal == 1" \
  extraction/tests/test_vector_store_shim.py
check_present "assert loaded_graph[\"relationships\"][0][\"type\"] == \"ACQUIRED\"" \
  extraction/tests/test_pipeline_canonical_engine.py
check_present "test_ingestion_facade_publishes_lifecycle_events" \
  seocho/tests/test_internal_design_seams.py
check_present "test_query_proxy_validates_and_publishes_success" \
  seocho/tests/test_internal_design_seams.py

echo "Checking basic CI coverage..."
check_present "extraction/rule_constraints.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/vector_store.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/collector.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_rule_constraints.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_rule_constraints_shim.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_vector_store_shim.py" \
  scripts/ci/run_basic_ci.sh
check_present "extraction/tests/test_pipeline_canonical_engine.py" \
  scripts/ci/run_basic_ci.sh
check_present "runtime/agent_state.py" \
  scripts/ci/run_basic_ci.sh
check_present "seocho/events.py" \
  scripts/ci/run_basic_ci.sh
check_present "seocho/index/ingestion_facade.py" \
  scripts/ci/run_basic_ci.sh
check_present "seocho/query/query_proxy.py" \
  scripts/ci/run_basic_ci.sh
check_present "seocho/query/agent_factory.py" \
  scripts/ci/run_basic_ci.sh
check_present "seocho/tests/test_internal_design_seams.py" \
  scripts/ci/run_basic_ci.sh
check_present "scripts/ci/check-module-ownership-contract.sh" \
  scripts/ci/run_basic_ci.sh

echo "Checking docs and decisions..."
check_present "INTERNAL_CLASS_DESIGN.md" \
  docs/README.md
check_present "docs/INTERNAL_CLASS_DESIGN.md" \
  docs/WORKFLOW.md \
  docs/decisions/ADR-0080-internal-orchestration-seams-for-modular-monolith.md

echo "Module ownership contract checks passed."
