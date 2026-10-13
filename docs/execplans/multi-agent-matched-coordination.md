# Matched single-agent and coordination experiment

## Purpose

Identify question structures where collaboration helps, separating decomposition, context delivery, parallel execution and peer correction. Twelve original FinDER cases and fixed evidence are already prepared in model-path-expanded-native-20261011. The360-run public-path cohort is complete (229 completed,131 failed,1553 physical calls); all call/event audits passed. Do not treat completion as correctness or public plans as private reasoning.

## Progress

- [x] Select matched fixed-task control and five execution designs.
- [x] Add single-actor fixed-task control with recorded topology.
- [x] Focused contract tests and basic CI.
- [ ] Run bounded MARA cohort and audit raw calls/context events.
- [ ] Produce within-case comparisons, human-review queue and limitations.

## Decision log and three review lenses

2026-10-12: Use direct, one-actor sequential fixed decomposition, independent parallel agents, sequential agents with prior findings, and peer review. Two fixed public tasks (source facts, independent verification/derivation) are identical across planned arms. This generic decomposition tests role allocation, not optimally tailored question-specific decomposition. Dynamic plan selection remains the previous cohort; do not pool it as matched.

professor_agent: A one-actor context chain and a multi-actor context chain can have identical model-visible payloads. Actor names alone are not cooperation evidence. Explicitly treat the identity-only contrast as a negative control; independent/peer conditions change information flow. Twelve exploratory cases, two samples, original reasoning annotations evaluator-only; semantic context use requires human/model judgment. No causality from judge agreement alone.

software_engineer_agent: Opt-in worker identity control in Condition/Harness; preserve old defaults and source receipts. Single identity requires fixed tasks and sequential context; no replay or Pi support. Same solver/synthesis model and schema, host-managed explicit contexts, no persistent SDK session memory or native handoff claim. Tests must prove source context and actual actor IDs, no gold leakage, and audits.

computer_systems_agent: Recorder sole transport, no cache/retries; per-run eight physical calls, inference1024/evaluation2048 caps, suite finite request/output reservation. Parallelism exists only within independent/peer arms and is measured using recorded intervals; no throughput/scalability claim. Monetary prices/input reservation unknown. Small two-model actual gate before wider catalog (Gemma and gpt-oss have usable interface evidence); provider failures separate from answer errors. No automatic design promotion.

## Evidence contract

Fixed source passages identical by case. Task results carry evidence IDs and source child/event receipts; receiving worker gets original sources and explicit prior results, peer gets other task result, synthesis receives task results. Missing evidence stays stated. Gold and original domain/reasoning labels never enter inference. Existing v2 judge evaluates answer and optional context-use/faithfulness; these are model judgments. Distinct instances are host-scheduled SDK agents, not Agent.as_tool/native handoff. Same-actor baseline is a host-managed context chain, not persistent private memory.

## Work and validation

Add Condition.worker_identity per_task(default)/single; reject unsupported combinations. Derive single_solver actor IDs for solver/synthesis under single mode, preserve exact same context prompts. Expose topology in reports. Reuse existing mock coordination test with single mode and validate inference identities and returned-context provenance. Run pytest tool/quality/UI focused, ruff, then bash scripts/ci/run_basic_ci.sh. Update Recorder and Graph-RAG research docs and ADR0154/decision log for the opt-in control.

Live cohort:12 questions ×2 models ×5 arms ×2 samples=240 trials. Eight calls/run, whole suite1920 calls/3932160 reserved output tokens. Same per-role ceilings and admission policy; actual consumption differs by arm and is reported. Fixed evaluator DeepSeek-V3.2 avoids self-judge for the two inference models; judge availability remains measured. Freeze shuffled order and protocol before execution. Stop proxy in finally. Automated per-run audits/report/sync can finish the active job; no recurring schedule.

## Acceptance and recovery

Reject identity-only improvement claims. Include inference completion separately from judge completion, per-case paired outcomes, duplicate work and context payloads, actual token usage/unknown reasoning tokens, actual request latency and aggregate costs only when known. Preserve every failure and partial run. Never overwrite earlier stores. Audit all finalized runs, distinguish unfinished requests if externally interrupted. Source/data/results remain private ignored outputs. No production routing/database changes.

## Outcomes

Pending. Beads database absent in this checkout; do not initialize or reset private state. No coding subagents requested or used; review lenses are written analysis.

2026-10-12 amendment: matched arms set judge_coordination=False to use the same
DetailedEvaluation answer rubric/schema. Default True preserves existing
coordination judgment. Additional semantic context-use review is separate and
pending; actual transfers/revisions are always recorded. This removes the
answer-rubric confound while keeping context measurements honest.

Implementation accepted: focused22 passed, basic CI764 passed/5 skipped, ruff/ADR/diff checks passed. Live deployment attempts timed out; server absent current Tailscale network map. User connectivity question pending. New cohort has zero live trials; source bundle, guarded runner and audit/report pipeline ready locally. No paid worker was started.

Connectivity follow-up2026-10-12: at user request, switched Tailscale from the
work tailnet to the already logged-in personal account. Server<private experiment server> is
now in the network map and Tailscale ping succeeds. Ordinary SSH, Tailscale SSH
and daemon nc ProxyCommand all time out (last at banner exchange). Thus the
remaining live gate is SSH reachability/service, not missing network selection.
No new paid trial has started. Keep the personal account selected as requested.
