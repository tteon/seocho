#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile \
  runtime/__init__.py \
  runtime/policy.py \
  runtime/identity.py \
  runtime/audit.py \
  runtime/agent_readiness.py \
  runtime/middleware.py \
  runtime/memory_service.py \
  runtime/public_memory_api.py \
  runtime/server_runtime.py \
  runtime/runtime_ingest.py \
  runtime/agent_server.py \
  extraction/_runtime_alias.py \
  extraction/agent_readiness.py \
  extraction/middleware.py \
  extraction/memory_service.py \
  extraction/policy.py \
  extraction/public_memory_api.py \
  extraction/runtime_ingest.py \
  extraction/server_runtime.py \
  extraction/semantic_query_flow.py \
  extraction/semantic_run_store.py \
  extraction/semantic_profile_packages.py \
  extraction/agent_server.py \
  extraction/collector.py \
  extraction/rule_constraints.py \
  extraction/vector_store.py \
  runtime/agent_state.py \
  src/seocho/models.py \
  src/seocho/client.py \
  src/seocho/client_bundle.py \
  src/seocho/client_remote.py \
  src/seocho/local_engine.py \
  src/seocho/events.py \
  src/seocho/ontology_context.py \
  src/seocho/api.py \
  src/seocho/session.py \
  src/seocho/__init__.py \
  src/seocho/evaluation.py \
  src/seocho/index/ingestion_facade.py \
  src/seocho/routing/model_router.py \
  src/seocho/agent/reflection.py \
  src/seocho/agent/matchmaker.py \
  src/seocho/ontology_context_map.py \
  src/seocho/query/query_proxy.py \
  src/seocho/query/agent_factory.py \
  src/seocho/tracing.py \
  src/seocho/store/graph.py \
  src/seocho/query/cypher_builder.py \
  src/seocho/index/extraction_engine.py \
  src/seocho/index/file_reader.py \
  src/seocho/run_spec.py \
  src/seocho/run_preflight.py \
  src/seocho/e2e.py \
  src/seocho/cli.py

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
  -q

git diff --check
scripts/ci/check-runtime-shell-contract.sh
bash scripts/ci/check-module-ownership-contract.sh
scripts/ci/check-root-hierarchy-contract.sh
scripts/pm/lint-agent-docs.sh
