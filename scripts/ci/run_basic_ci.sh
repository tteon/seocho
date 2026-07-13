#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

py_compile_files="$(
  git ls-files \
    'runtime/**/*.py' \
    'runtime/*.py' \
    'extraction/**/*.py' \
    'extraction/*.py' \
    'src/seocho/**/*.py' \
    'src/seocho/*.py' \
    'scripts/ci/*.py'
)"

# shellcheck disable=SC2086 # tracked repo paths here are whitespace-free.
python3 -m py_compile $py_compile_files

uv run ruff check \
  scripts/ci \
  src/seocho/cli.py \
  src/seocho/e2e.py \
  src/seocho/run_spec.py \
  src/seocho/scaffold.py \
  tests/seocho/test_e2e_runner.py \
  tests/seocho/test_run_spec.py \
  tests/seocho/test_scaffold.py \
  tests/seocho/test_sweep.py

uv run pytest \
  extraction/tests/test_runtime_package_aliases.py \
  extraction/tests/test_identity.py \
  extraction/tests/test_policy.py \
  extraction/tests/test_audit.py \
  extraction/tests/test_agent_readiness.py \
  extraction/tests/test_middleware.py \
  extraction/tests/test_memory_service.py \
  extraction/tests/test_runtime_ingest.py \
  extraction/tests/test_semantic_run_store.py \
  extraction/tests/test_semantic_query_flow.py \
  extraction/tests/test_approve_governance_gate.py \
  extraction/tests/test_rule_constraints.py \
  extraction/tests/test_rule_constraints_shim.py \
  extraction/tests/test_vector_store_shim.py \
  extraction/tests/test_pipeline_canonical_engine.py \
  extraction/tests/test_api_endpoints.py \
  extraction/tests/test_sdk_client.py \
  tests/seocho/test_client_boundaries.py \
  tests/seocho/test_runtime_bundle.py \
  tests/seocho/test_internal_design_seams.py \
  tests/seocho/test_query_proxy_workspace_enforcement.py \
  tests/seocho/test_ontology_context.py \
  tests/seocho/test_session_agent.py \
  tests/seocho/test_response_cache_wiring.py \
  tests/seocho/test_user_facing_edge_cases.py \
  tests/seocho/test_semantic_query_phase_a.py \
  extraction/tests/test_sdk_evaluation.py \
  tests/seocho/test_agent_design.py \
  tests/seocho/test_benchmarking.py \
  tests/seocho/test_finder_benchmark_script.py \
  tests/seocho/test_indexing_design.py \
  tests/seocho/test_llm_backends.py \
  tests/seocho/test_llm_model_override.py \
  tests/seocho/test_model_router.py \
  tests/seocho/test_reflection.py \
  tests/seocho/test_matchmaker.py \
  tests/seocho/test_ontology_context_map.py \
  tests/seocho/test_promotion_boundary_gate.py \
  tests/seocho/test_debate_quorum.py \
  tests/seocho/test_graph_loop_model_routing.py \
  tests/seocho/test_tracing.py \
  tests/seocho/test_tracing_opik_regression.py \
  tests/seocho/test_cypher_builder.py \
  tests/seocho/test_cypher_builder_ontology_aware.py \
  tests/seocho/test_extraction_engine.py \
  tests/seocho/test_graph_ensure_database.py \
  tests/seocho/test_graph_writer_lww.py \
  tests/seocho/test_agents_runtime_packaging.py \
  tests/seocho/test_finder_eval_helpers.py \
  tests/seocho/test_finder_judge.py \
  tests/seocho/test_finder_synergy.py \
  tests/seocho/test_finder_cache_synergy.py \
  tests/seocho/test_ontology_extraction_firewall.py \
  tests/seocho/test_ontology_lint.py \
  tests/seocho/test_ontology_subclass_ttl.py \
  tests/seocho/test_ontology_reasoner.py \
  tests/seocho/test_ontology_iso704_cq.py \
  tests/seocho/test_run_spec.py \
  tests/seocho/test_e2e_runner.py \
  tests/seocho/test_scaffold.py \
  tests/seocho/test_ontology_enforcement.py \
  tests/seocho/test_run_template.py \
  tests/seocho/test_sweep.py \
  tests/seocho/test_entity_identity.py \
  tests/seocho/test_triage_metadata.py \
  -q

git diff --check
scripts/ci/check-runtime-shell-contract.sh
bash scripts/ci/check-module-ownership-contract.sh
scripts/ci/check-root-hierarchy-contract.sh
scripts/pm/lint-agent-docs.sh
