# FinDER model-selected tools: live gate — 2026-10-11

One actual MARA DeepSeek-V3.1 run completed through a fresh cache-off Recorder on
Tailscale host hardy-lab, using the isolated experiment checkout. Same FinDER case
7e6c28fd (Financials, reasoning=True, Subtract), unchanged six fixed passages,
seed 42. OpenAI Agents SDK 0.18.2, openai 2.45.0, Python 3.12.13, DuckDB 1.5.6.
One root run, no warmup, no graph database, SDK cloud tracing disabled. Provider
revision, price and hardware limits are unknown; this is compatibility evidence,
not throughput evidence. Shared hard cap: ten LLM calls, 2048 output tokens per
call; tool execution cap twelve; root timeout 180 seconds. No paid process remains.

The model selected search, two source-number extractions, then subtraction. It
used returned receipt IDs, executed all four tools successfully with no exact
duplicates or execution errors, and answered $633 million (2294 - 1661).
Five inference calls used 26,600 input and 289 output tokens; one evaluator call
used 13,902 input and 1,041 output tokens. No specialist delegation was selected:
this gate validates autonomous tool use, not live agent collaboration. The
contract fixture separately exercises specialist context delivery and return.

The same-model judge returned correct and passed tool relevance and dependency
handling. It incorrectly passed handoff quality despite no specialist delegation,
interpreting tool receipt passing as handoff. One claim contained a fabricated
citation ID; the exact-quote checks were true, false, false, true. Arithmetic
passed, but operand/company/period semantics remain model judgments. These errors
are retained in immutable raw output. Derived reports preserve raw handoff labels,
flag the contradiction and exclude that handoff label from pass-rate scoring.
The subsequent rubric explicitly distinguishes agent delegation from tool receipt
passing; that wording change has contract coverage but has not been live rerun.

Raw call/event audits passed (six calls, 22 events). A separately saved analysis
manifest supplies all six adapter attempt IDs to the coverage audit. Original
provider records and judgments remain unchanged. Local ignored artifacts are
`outputs/agent-observability/tool-live-20261011/`, including configs, source
manifest, cases/private evaluation, raw store, results, audits and offline report.
This BFCL-inspired FinDER gate is not an official BFCL score. No minimal-path or
precommitted goal-state score was assigned, and no architecture advantage can be
inferred from one case. Human calibration remains necessary.
