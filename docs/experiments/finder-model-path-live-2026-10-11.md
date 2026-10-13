# MARA public execution paths: FinDER pilot, 2026-10-11

This gate compares public plans and actual SDK model/tool calls, not private
reasoning traces. Five live MARA catalog IDs were verified before execution:
DeepSeek-V3.1, DeepSeek-V3.2, MiniMax-M3, gemma-4-31B-it, gpt-oss-120b.
Each primary model received direct, explicit ordered decomposition and adaptive
planning/tools conditions on the same two original FinDER cases. No specialists,
KG, ontology service, Pi, embeddings or database were used. Every model turn and
judge passed through Recorder; cache off and SDK retries disabled.

Workload: 7e6c28fd (Financials, reasoning=True, Subtract) and 4e13063f (Company
overview, reasoning=True, Compositional). Six question-selected fixed original
filing passages per case, gold and original metadata evaluator-only. One seed
42 and sample1, precommitted shuffled order, serial trials. The prepared manifest
pins source lineage and artifact hashes. No held-out or reasoning=False coverage.

Inference cap1024, evaluator cap2048, max10 physical calls/run, max6 successful
tool executions across path steps. Excess tool attempts are recorded and rejected;
an agent may finish after a tool-budget error. Plans have at most three ordered
subquestions with required slots, dependencies, completion criteria and requested
capabilities. The same solver model handles planning, child solves and final
answer; host-owned context packets carry only declared dependency results.
Adaptive can choose no decomposition. No general plan revision loop is implemented.

The fixed evaluator is Gemma in this new cohort, unlike earlier DeepSeek judge
cohorts. A gold-free path actor judges coverage, dependency use, unnecessary work,
missing evidence and synthesis. A separate answer actor receives private gold
and evaluates answer equivalence/grounding/units. Path-judge failures are preserved
independently. Gemma self-judging is a bias, and these scores are uncalibrated.
Original output caps/inputs/call identities are in immutable Recorder stores.

## Observed paths

| Model | Observations on these two questions |
|---|---|
| DeepSeek-V3.1 | Explicit MSI plan: values → change calculation plus a third driver-analysis step. Final numeric answer was produced but a corrupted citation ID was blocked. PANW plan: personnel components → headcount impact → relationship/formula. Some other arms hit429. |
| DeepSeek-V3.2 | MSI explicit: values → difference/percentage, with dependency delivery. MSI adaptive chose direct; PANW planned cost components and headcount separately → relationship synthesis. Complex tool execution encountered context growth and then repeated search/invalid extraction under a call ceiling. |
| Gemma | MSI adaptive chose direct; explicit used values → change. PANW explicit/adaptive used three steps checking whether a formula exists, cost/headcount facts and relationship/assumptions. No actual tools in those completed complex adaptive runs. |
| gpt-oss-120b | MSI explicit: values → difference; direct and adaptive-direct also completed. Complex plan responses hit provider JSON truncation errors; this is not evidence of an incorrect answer. |
| MiniMax-M3 | Primary API schema arms all failed. Prompt-JSON amendment recovered direct answers and MSI explicit (two ordered steps). A later tool-output amendment recovered adaptive direct-with-tools on both questions: extract operands → calculate. Complex explicit still had a local validation failure. |

For MSI, gpt-oss reported 153 inference reasoning tokens in direct and 1376
summed across the explicit policy's model turns; both were judged correct.
This is one paired observation with different stage counts, not proof of
internal thought quality or over-thinking. Gemma reported0, while DeepSeek
reasoning usage was absent and stays unknown. Missing prices stay unknown.

## Gates, failures and repairs

| Cohort | Trials | Physical calls | Completed / failed | Reserved output tokens |
|---|---:|---:|---:|---:|
| Primary fixed API schema | 30 | 90 | 14 / 16 | 116736 |
| MiniMax prompt-JSON contract | 6 | 22 | 3 / 3 | 26624 |
| Remove duplicate evidence fields | 4 | 27 | 3 / 1 | 33792 |
| Deduplicate tool replies / MiniMax tool JSON | 4 | 30 | 3 / 1 | 36864 |
| Total operational workload | 44 | 169 | 23 / 21 | 214016 |

All44 call-manifest coverage checks and all44 agent-event audits passed.
Totals fit the originally declared240 calls/491520 reserved output tokens.
Reservation excludes input tokens and is not monetary cost. No paid worker or
Recorder on ports8831–8834 remains running. Read-only primary UI is8815.

The primary step/final prompt accidentally repeated the fixed evidence in two
fields. Actual DeepSeek-V3.2 HTTP400 said input14548 exceeded8192. The first repair
removed the duplicate field, preserving source text/IDs and caps. A repeated
search then re-delivered full passages; actual input15087 still exceeded8192.
The final repair returns existing IDs for unchanged already-visible passages,
retaining full host evidence and prior context. This final gate had no HTTP400
context overflow, but DeepSeek-V3.2 still made repeated searches and invalid
number extractions and failed after ten admitted calls, with APIConnectionError.
The old gate did not persist the admission cause, so call-ceiling exhaustion is
an audited observation and the exception cause remains unconfirmed. Afterwards,
the harness was hardened to record blocked admission enums and preserve
BUDGET_EXHAUSTED when the OpenAI client wraps hook errors. That final diagnostic
change has deterministic budget tests, not a new live gate.

MiniMax's first prompt-JSON amendment changed only structured-turn output; its
tool loop still used API schema and failed. The final amendment also changed the
tool-loop output contract to prompt JSON plus local schema validation. It
completed both adaptive cases. On PANW it attempted seven tools: six executed,
one calculation was rejected by the tool cap, and the agent still finalized.
These separate amendments are interface recovery evidence, not model rankings.

## Interpretation and evidence access

The original source text explicitly gives MSI's $633M increase, so a longer
plan is not required to obtain that value. PANW's symbolic cost formula requires
assumptions about average personnel cost and whether R&D/S&M deltas represent
total cost. Judge-correct labels must retain the prior human-adjudication flag.
One sample, correlated judge, provider failures and serialization/contract changes
prevent accuracy pooling, domain-policy recommendations, causal tool-benefit
claims and a general over/under-thinking verdict. Future controlled input/output
budget sweeps and human calibration remain necessary.

Private reproducible artifacts are under outputs/agent-observability/:
model-path-live-20261011, model-path-contract-live-20261011,
model-path-contextfix-live-20261011 and model-path-toolfix-live-20261011.
The derived model-path-comparison-20261011.md lists every public plan, actual
subquestion result, citations, provider error and independent evaluation.
model-path-comparison-20261011.json records cohort counts/limits. Original records
were never overwritten by amendments. Legacy silver alignment is disabled for
these path schemas; read-only graph views expose declared dependencies instead.

Environment: isolated Tailscale execution tree, Python3.12.13, openai2.45.0,
openai-agents0.18.2, DuckDB1.5.6. Local contract CI uses Python3.10.20. Provider
model revisions, deployment hardware/container limits and pricing are unknown;
no warmup/performance claim is made. Focused contract ladder and basic CI passed;
final basic CI: 763 passed,5 skipped,16 warnings.
