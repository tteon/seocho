#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile \
  runtime/__init__.py \
  runtime/policy.py \
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
  seocho/models.py \
  seocho/client.py \
  seocho/local_engine.py \
  seocho/events.py \
  seocho/ontology_context.py \
  seocho/api.py \
  seocho/session.py \
  seocho/__init__.py \
  seocho/evaluation.py \
  seocho/index/ingestion_facade.py \
  seocho/query/query_proxy.py \
  seocho/query/agent_factory.py

uv run pytest \
  extraction/tests/test_runtime_package_aliases.py \
  extraction/tests/test_agent_readiness.py \
  extraction/tests/test_middleware.py \
  extraction/tests/test_memory_service.py \
  extraction/tests/test_runtime_ingest.py \
  extraction/tests/test_semantic_run_store.py \
  extraction/tests/test_semantic_query_flow.py \
  extraction/tests/test_rule_constraints.py \
  extraction/tests/test_rule_constraints_shim.py \
  extraction/tests/test_vector_store_shim.py \
  extraction/tests/test_pipeline_canonical_engine.py \
  extraction/tests/test_api_endpoints.py \
  extraction/tests/test_sdk_client.py \
  seocho/tests/test_internal_design_seams.py \
  seocho/tests/test_ontology_context.py \
  seocho/tests/test_session_agent.py \
  extraction/tests/test_sdk_evaluation.py \
  -q

git diff --check
scripts/ci/check-runtime-shell-contract.sh
bash scripts/ci/check-module-ownership-contract.sh
scripts/pm/lint-agent-docs.sh
