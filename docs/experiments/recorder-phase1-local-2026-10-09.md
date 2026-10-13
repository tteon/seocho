# Recorder Phase 1 local acceptance — 2026-10-09

Implemented framework-neutral Recorder, real DuckDB append-only events, immutable
content-addressed bodies/messages, cache modes, SDK adapter, deterministic backend,
CLI inspection/audit/Parquet export and mandatory transport in research runners.

Validation:

- `python examples/agent-observability/acceptance.py --directory outputs/agent-observability/phase1-acceptance`: passed actual loopback HTTP acceptance, 100 distinct calls, 99 cache hits, one mock upstream dispatch, one unique request/response blob each; manifest audit passed.
- Focused Recorder/collaboration/FinDER tests: 60 passed.
- `bash scripts/ci/run_basic_ci.sh`: 710 passed, 5 skipped; Ruff and ownership/root/doc lint passed.
- `python scripts/ci/check_adr_index.py` and `bash scripts/ci/check-doc-contracts.sh`: passed.
- Recorder tests rerun after extending hash audits to normalized/message blobs: 9 passed.

Actual storage dependency: DuckDB 1.5.6. Local deterministic services only; no
MARA, OpenAI or graph service was exercised in this phase. No latency, throughput,
production readiness or external compatibility claim follows from these results.

See [usage and limits](../AGENT_OBSERVABILITY.md). Full decision/realization/tool
lineage, gold-leak checks and experiment YAML belong to Phase 2. Streaming cache
reuse and inspection from another process while the writer is running are not
supported. Historical FinDER results remain historical, without retroactive raw
transport evidence. Phase 2 awaits the required confirmation.
