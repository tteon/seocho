#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile \
  runtime/__init__.py \
  runtime/policy.py \
  runtime/public_memory_api.py \
  runtime/server_runtime.py \
  runtime/runtime_ingest.py \
  runtime/agent_server.py \
  extraction/runtime_ingest.py \
  extraction/semantic_query_flow.py \
  extraction/semantic_run_store.py \
  extraction/semantic_profile_packages.py \
  extraction/agent_server.py \
  seocho/models.py \
  seocho/client.py \
  seocho/api.py \
  seocho/session.py \
  seocho/__init__.py \
  seocho/evaluation.py

uv run pytest \
  extraction/tests/test_runtime_package_aliases.py \
  extraction/tests/test_runtime_ingest.py \
  extraction/tests/test_semantic_run_store.py \
  extraction/tests/test_semantic_query_flow.py \
  extraction/tests/test_api_endpoints.py \
  extraction/tests/test_sdk_client.py \
  seocho/tests/test_session_agent.py \
  extraction/tests/test_sdk_evaluation.py \
  -q

git diff --check
scripts/pm/lint-agent-docs.sh
