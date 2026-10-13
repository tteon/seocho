# FinDER Recorder-backed MARA pilot — 2026-10-11

Executed on Tailscale-connected `hardy-lab` in an isolated experiment directory,
without changing the existing server Git checkout. Eight shuffled conditions ran
on FinDER case `7e6c28fd`, one seed (42), one sample, unchanged six fixed passages,
DeepSeek-V3.1 in every role, cache off, no automatic retries or graph service.
Four completed; four failed with provider HTTP 429. This is a pilot, not a complete
quality comparison or evidence of general latency superiority.

| Completed condition | Inference calls | Input + output tokens | Evaluator tokens | Inference wall time (s) |
| --- | ---: | ---: | ---: | ---: |
| Standard | 1 | 5786 | 503 | 5.904 |
| Global budget | 1 | 5817 | 503 | 2.645 |
| Static router / local budget / parallel delegation | 10 | 48286 | 432 | 11.298 |
| Planned sequential delegation | 10 | 48010 | 432 | 32.544 |

All four final answers state the $633 million increase (about 38%), consistent
with 2294 - 1661 = 633 and the original evidence. The MARA evaluator returned
`correct` or `Correct`. The current analytics accepts lowercase verdicts only,
so the two capitalized judgments remain unknown in derived metrics; no raw
judgment was rewritten. This ingestion gap requires a regression fix before
aggregating quality comparisons. The static router mapped every subtask to the
same model, so this run does not test heterogeneous model selection.

Failed conditions: planned parallel, planned global budget, LLM router and local
budget. Recorded provider message says DeepSeek-V3.1-16k is experiencing high
demand. Failed-request token usage and dollar prices remain unknown. Failed runs
are excluded from completed-only analysis but retained in the ledger and gaps.
Total experiment elapsed time was 118.66 seconds, including failed conditions.

All eight call audits passed. Seven event audits passed. The failed LLM-router
run has two route decisions without matching realizations: a harness error-path
observability gap, not a missing LLM request record. Do not claim complete event
lineage for that run. Original records remain untouched for debugging.

Python 3.12.13, OpenAI Python 2.45.0, OpenAI Agents SDK 0.18.2, DuckDB 1.5.6.
No warmup; shared server without reserved CPU/memory limits; provider service
revision/hardware unavailable. Each condition has a 20-call admission cap and
1024 output tokens per call; the 512-token soft instruction is distinct. No
pricing estimate or dollar cap was used. No OpenAI cloud API or SDK tracing.

Remote contract validation: `python -m pytest tests/seocho/test_recorder.py
 tests/seocho/test_agent_observability_harness.py -q` — 14 passed. Full basic CI
was not rerun for this experiment-only operation. Offline `obs report` succeeded
once the transferred local absolute report paths were changed to server-relative
paths; the initial report attempt failed before analysis and did not call MARA.

Ignored local artifacts: `outputs/agent-observability/finder-live-20261011/` holds
experiment/config/data hashes, environment versions, trials, raw DuckDB + blobs,
audits, logs and report tables/figures. The server retains the same artifacts under
its isolated lab directory. No API credential was copied into these artifacts.

CoT, faithful upstream P-and-B single-call reproduction, repeated seeds, multiple
FinDER cases, tier comparisons and successful reruns of the failed conditions are
still needed. Preserve this first attempt rather than mixing its surviving runs
with retries into a supposedly complete balanced comparison.
