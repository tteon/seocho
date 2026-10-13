# Server Pi versus SDK harness — 2026-10-11

Both harnesses completed actual MARA inference and the common SDK evaluator on
FinDER case `7e6c28fd`. Both answered $633 million operating earnings increase,
about 38%; both evaluator verdicts were `correct`. This single-case pilot verifies
an observable integration, not a general quality, cost or latency advantage.

| Harness | Inference calls | Input + output tokens | Output tokens | Evaluator tokens | Inference wall time (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Server Pi | 1 | 5716 | 72 | 460 | 4.886 |
| OpenAI Agents SDK | 1 | 5786 | 100 | 503 | 4.874 |

The installed server command resolves to `@earendil-works/pi-coding-agent` 1.0.0,
not an assumed upstream badlogic version. Inference used DeepSeek-V3.1, unchanged
six fixed source passages, forced T1, seed 42, temperature 0 and a 1024-token hard
output cap. Both conditions were rerun; earlier eight-condition pilot results were
not reused. Pi ran first, then SDK, one trial each with no warmup. Execution order
was fixed in advance; the protocol's `order_seed` is descriptive, not evidence of
an implemented randomization procedure. Total runner times (including evaluation
and startup/teardown work within execute) were 6.84 and 7.84 seconds, respectively.
Use recorded inference wall time above for the defined inference latency metric.

Pi tools, skills, context files, automatic compaction, extensions other than the
explicit Recorder guard, cache warming, telemetry, retries and saved sessions
were disabled in an isolated HOME/agent directory. No existing user configuration
or credential store was modified. The common evaluator uses the SDK and receives
gold after each candidate freezes. No graph service, dynamic retrieval, OpenAI
cloud API or semantic embedding service participated. This compares controlled
harness substitution, not native Pi planning/tools or collaborative execution.

Pi uses streamed Chat Completions and carries the JSON schema in its user input;
SDK uses non-streamed structured response formatting. Therefore wire prompts are
not identical, despite the shared evidence, question and role instruction. Prompt
assembly, streaming and process overhead are part of this end-to-end comparison.
Neither speedup nor population accuracy can be inferred from the two observations.
Both raw requests contain temperature 0, seed 42, max_tokens 1024 and zero tools.
All four actual model calls and both run event audits passed, including expected
call-ID matching. No 429 occurred in this pair. Prices and provider revision are
unknown; raw token counts are used rather than Pi's catalog price estimates.

Before paid execution, the actual Pi CLI passed a local HTTP fixture contract:
one streamed call, Recorder tags/acknowledgement, expected cap/seed/temperature and
usage extraction. This fixture is contract evidence only. Five local parsing tests
cover Unicode JSONL, hidden thinking exclusion, truncated/error responses and
multiple-turn rejection. Existing SDK and UI contract tests also passed (13 total
in the focused run). Full CI outcome is recorded in the living ExecPlan.

The server ran Python 3.12.13, OpenAI Python 2.45.0, Agents SDK 0.18.2 and DuckDB
1.5.6 on a shared machine without reserved hardware/container limits. Source is
an isolated snapshot of the local working tree, not a pushed Git revision. Raw
Recorder source hash is available in the generated report manifest. API credentials
remain in the pre-existing server environment file and Recorder process memory.

Ignored artifacts: `outputs/agent-observability/pi-live-20261011/` holds both
experiment snapshots, trials, raw DuckDB/blobs, CLI events, audits, protocol and
report. `outputs/agent-observability/pi-contract-20261011/` holds the local fixture
receipt. The server retains its independent lab copies. No paid process remains
running. Remaining work: replicated multi-case comparisons, original P-and-B
reproduction, and separately labeled Pi-native planning/tool conditions.
