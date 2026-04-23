# Benchmarks

SEOCHO should be measured with two benchmark tracks, not one blended score.

This page is for engineering evaluation and regression loops. It is not the
onboarding guide.

## Benchmark Tracks

### Track 1: Private Finance Corpus

Use a private finance corpus for:

- ontology-governed extraction quality
- graph write quality
- finance-domain question answering
- local SDK vs runtime overhead on the same workload

This track should be reported in two contract views, not one blended score.

#### Private Finance Corpus Indexing Contract

Use the indexing view to validate:

- ontology extraction and ontology-context metadata on written records
- `rule_profile`, `semantic_artifacts`, and fallback vs non-fallback ingest behavior
- graph write counts and graph projection quality
- embedded local path (`LadybugGraphStore`) vs server runtime path (`Neo4j`/DozerDB)
- memory lifecycle operations tied to indexed graph state:
  - `archive_memory`
  - `delete_source`
  - approved-artifact / ontology-management promotion flows

#### Private Finance Corpus Query Contract

Use the query view to validate:

- semantic routing and multi-step reasoning behavior
- ontology-context propagation and mismatch reporting
- evidence bundle quality and support assessment quality
- debate preflight/fallback behavior against the same indexed records
- reference-grounded QA outcomes against corpus-specific expected answers
- reference-grounded QA outcomes against corpus-specific expected answers
- local provider-matrix smoke runs on a fixed subset:
  - `openai`
  - `deepseek`
  - `kimi`
  - `grok`
  - `qwen`

Current first-slice baseline command:

```bash
python scripts/benchmarks/run_finance_benchmark.py --mode local
```

Supported modes:

- `local`
- `remote`
- `both`

Default tutorial smoke dataset:

- `examples/datasets/tutorial_filings_sample.json`

Production benchmark input:

- a user-supplied private finance corpus passed with `--dataset`

Rule:

- the bundled tutorial sample is for onboarding and smoke checks only
- do not report tutorial-sample results as benchmark evidence

FinDER-specific engineering loop:

- use a user-supplied FinDER-format dataset with `scripts/benchmarks/run_finder_benchmark.py`
- split runs into:
  - `--scenario beginner` for mostly single-hop qualitative onboarding flows
  - `--scenario advanced` for financial delta/compositional/legal synthesis flows
- treat embedded local (`Ladybug`) and runtime HTTP (`Neo4j`/DozerDB) as separate baselines

Example commands:

```bash
uv run python scripts/benchmarks/run_finder_benchmark.py \
  --dataset /path/to/finder_sample.json \
  --mode local \
  --scenario beginner \
  --provider openai \
  --model gpt-4o-mini \
  --limit-per-category 2

uv run python scripts/benchmarks/run_finder_benchmark.py \
  --dataset /path/to/finder_sample.json \
  --mode remote \
  --scenario advanced
```

Interpretation:

- `--provider` selects the OpenAI-compatible provider preset for local mode.
- `--model` may be either a plain model name such as `gpt-4o-mini` or a
  `provider/model` shorthand such as `deepseek/deepseek-chat`.
- `--limit-per-category 2` is the recommended model-comparison setting when
  checking category coverage without running the full FinDER dataset.
- `local` without `--graph` means embedded `LadybugGraphStore`
- `local` without `--graph` now uses an isolated per-run Ladybug file under `.seocho/benchmarks/local/`
  - this avoids false zero-write runs caused by reusing the default `.seocho/local.lbug` dedup state
  - the auto-generated benchmark file is reset at the start of each run, even if the same `workspace_id` is reused
- `local --graph bolt://...` means SDK path against Neo4j/DozerDB
- `remote` means runtime HTTP benchmark against canonical semantic/debate/platform endpoints, not the memory-first `ask()` facade
- `remote` now uses canonical runtime endpoints:
  - `/platform/ingest/raw`
  - `/run_agent_semantic`
  - `/run_debate`
  - `/platform/chat/send` with `mode=semantic`
- runtime database names used by benchmark harnesses must match the runtime contract:
  - lowercase alphanumeric only
  - start with a letter
  - length 3-63 chars
  - use values like `kgruntimea`, not `FinderRuntimeA` or `finder_runtime_a`
- remote benchmark artifacts include `runtime_setup` plus per-endpoint summaries:
  - `remote-semantic`
  - `remote-debate`
  - `remote-platform-semantic`
- local benchmark records should retain indexing-path hints per case:
  - `fallback_used`
  - `deduplicated`
- remote benchmark records retain query-path and agent-loop hints per case:
  - `route`
  - `support_status`
  - `support_coverage`
  - `missing_slots`
  - `trace_step_count`
  - `tool_call_count`
  - `reasoning_attempt_count`
  - `semantic_reused`
  - `token_usage`
- treat `support_answer_gap_count` as a first-order regression signal:
  `support_status=supported` but `contains_match=false` means the evidence
  contract claimed enough support while answer synthesis still missed the
  reference answer
- benchmark artifacts from active diagnosis runs stay local-only

### Track 2: GraphRAG-Bench

Use GraphRAG-Bench for:

- retrieval quality
- evidence quality
- reasoning quality
- query latency

This is the query/reasoning benchmark track. It should not be used as the only
measure of ingestion quality.

## Peer Systems

The current peer baseline set is:

- Graphiti
- Cognee
- mem0 graph memory

These are useful peer systems, but they are not identical products. Results
must be reported by track and by capability, not as a single overall winner.

## Measurement Order

Run benchmarks in this order:

1. `SEOCHO local SDK baseline`
2. `SEOCHO runtime HTTP baseline`
3. peer system baselines

Why:

- the SDK path is the canonical engine baseline
- the runtime path measures deployment overhead and policy/transport cost
- peer comparisons are not meaningful if the internal baseline is unstable

## Private Finance Corpus Metrics

Report at minimum:

- documents per second
- add latency p50/p95
- ask latency p50/p95
- retrieval latency p50/p95
- generation latency p50/p95
- nodes per document
- relationships per document
- exact-match rate
- contains-match rate
- slot-level answer diagnostics:
  - token recall
  - numeric slot recall
  - period/year slot recall
- support status counts
- average evidence coverage
- missing evidence slot counts
- agent pattern counts
- token usage or token estimate payloads
- failure count

Also report split contract findings:

- indexing findings by code and affected-case count
- query findings by code and affected-case count
- whether the run was local embedded (`Ladybug`) or runtime server (`Neo4j`/DozerDB)
- whether fallback or non-fallback paths were exercised
- whether the provider matrix was exercised or skipped

FinDER artifacts now include an observability envelope per record when the
query path exposes it:

- `latency_breakdown_ms`: stage timings such as retrieval, generation, and total
- `support_status`: supported, partial, or unsupported
- `evidence_coverage` and `missing_slots`
- `slot_metrics`: deterministic token/numeric/period recall against the reference answer
- `token_usage`: provider usage when available, otherwise deterministic estimates
- `agent_pattern`: selected pattern receipt such as `semantic_direct` or `reflection_chain`

Use these fields to decide whether a regression is caused by retrieval,
evidence coverage, answer synthesis, or agent orchestration. Do not tune model
choice from exact/contains score alone.

## GraphRAG-Bench Metrics

Report at minimum:

- retrieval quality
- evidence coverage
- answer quality
- unsupported-claim rate
- query latency p50/p95

## Artifact Rule

Benchmark outputs should be saved under:

- `outputs/evaluation/finance_benchmark/`
- `outputs/evaluation/finder_benchmark/`
- `outputs/evaluation/graphrag_bench/`

JSON output should remain the default artifact so results can be compared over
time without depending on a specific trace backend.

Local benchmark and diagnostic artifacts may live under `.seocho/benchmarks/results/`
while iterating, but those files are local-only and must not be committed.
