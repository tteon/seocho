# ADR-0236: Opt-in budgeted Graph-RAG collaboration experiments

Date: 2026-10-05
Status: Accepted for experimental implementation; live validation pending

## Context

SEOCHO needs a controlled way to compare planning, parallel evidence assembly,
and reviewer-directed retrieval without changing the production answer route.
The generic execute tool is insufficient as an experiment boundary because a
model must not choose another workspace or execute arbitrary database writes.

## Decision

Use OpenAI Agents SDK agents and one common admission ledger per question/mode.
Compare a single retrieval worker, sequential planned workers, parallel planned
workers, and one reviewer repair round. Hold the answerer, model, graph fixture,
question and resource limits constant. Preserve earlier dependency packets.
Also compare native SDK Manager (`Agent.as_tool`) and Router-to-coordinator
(`handoff`) modes using the same workers and answerer. Fix cross-agent context
to host-owned task and evidence packets; full-history/summary policies remain
separate future axes. Native control flow may consume extra calls and tools;
charge those to the common ledger rather than granting extra budget.
Record actual SDK transitions, agent definitions and serialized context size.
Bound context before every model turn, not only initial invocation.

Start with trusted, bounded read recipes validated by the canonical Cypher
validator. Bind every participating node and edge to `workspace_id`. Require
actual row receipts and resolve model citations against those receipts. Keep
missing slots visible; do not equate valid references with claim entailment.

Use FinDER as the primary workload: seed a shared typed company/filing/section/
passage graph from the official original 10-K HTML archive. Select a seeded
40-case pilot across single-reference, multi-reference, compositional and numeric
slices. Resolve company tickers from questions; report excluded cases. Never
construct the source graph or rank retrieval from gold reference membership.
Preserve a small synthetic incident fixture for deterministic missing evidence,
conflict and budget regressions. Gold stays evaluation-only.
Expose diagnostic evidence recall, while answer correctness remains unknown
until a human or separately budgeted judge evaluates entailment.

Reserve estimated request units and output allowance before parallel dispatch.
Limit calls, tool calls, output tokens, concurrency, context size, and wall time.
Record provider tokens only when reported. Do not call estimated admission units
an exact provider-token ceiling. Disable hidden transport retries in the runner.

## Fixed-context design slice

Before graph retrieval comparisons, compare standard prompting, stepwise prompting
(CoT instruction only), single-agent decomposition, sequential delegation to one
Specialist, and parallel delegation to multiple Specialists on the same original
FinDER passage bundle. Select the source bundle once using only question terms;
no source is retrieved during the trial. Hold the model, per-run shared budgets
and final Answer schema fixed. A single Solver performs all decomposition-only
subtasks itself, recording typed partial results; a Supervisor in delegation arms
receives typed Specialist returns and generates the final answer. Every agent
receives the full same evidence bundle. Subtask plans may differ across designs,
so this is an end-to-end design comparison, not isolation of scheduling alone.
Use random mode ordering and preserve provider failures. For the fixed-context
CLI, allow explicit 429 retries with three attempts and five/ten-second waits;
each attempt reserves another shared model call and retains unknown failed usage.
The wall-time cap is unchanged, and transport-level retries remain disabled.
Judge only after all
candidates are frozen; give the evaluator its own budget and disclose same-model
self-preference. One question cannot establish generalized quality or speedup.

## Consequences

This supports reproducible engineering trials without altering `Seocho.ask` or
runtime policy APIs. Fixed recipes intentionally narrow agent freedom; generated
Cypher is a later independent experiment axis. There is one repair round, no
recursive planner, and no autonomous debate. Planner-assigned task call
allowances are enforced alongside the shared admission ledger.
The small pilot and ticker-resolution exclusions cannot establish generalized
quality gains. Exact normalized gold-to-source mappings can fail; report those
gaps separately from retrieval failures. This first document graph does not
extract verified financial metrics or arithmetic receipts. Local timeout
cancellation cannot guarantee provider-side cancellation. Live MARA/DozerDB
validation and repeated, randomized trials remain required before performance
or quality claims.
