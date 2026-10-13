# FinDER failure and coordination research loop

## Purpose and scope


Build a runnable, Recorder-backed quality experiment pipeline for MARA models.
A failure taxonomy distinguishes provider/interface faults, evidence/answer faults,
coordination faults and evaluator conflicts. Controlled synthetic variants retain
source lineage and document-group splits. Coordination shares a precommitted
public task plan, with independent, sequential-context, peer-review and adaptive
review conditions. Budget interventions and prompt/context order experiments
produce paired observations, not claims about hidden private reasoning.

## Progress


- [x] Inspect current Recorder, SDK harness, evaluator and repository contracts.
- [x] Attempt `bd ready --limit 1`; no Beads database exists. Do not initialize it.
- [x] Implement coordination/context transfer and versioned semantic judging.
- [x] Implement controlled variants, failure reports, paired budget/robustness analysis.
- [x] Validate deterministic contracts, bounded MARA gates and full basic CI.

## Context and orientation


Canonical code lives in `src/seocho/eval/agent_observability/`; the Recorder proxy
in `src/seocho/recorder/` stores actual provider requests and causal events.
Public cases contain questions, fixed sources and agent references. Gold answers,
domain labels and gold references remain evaluation-only. Private corpora and
results stay under ignored `outputs/` and `.seocho/`. No graph backend or new
retrieval policy is introduced. Existing host-orchestrated controls remain.

## Decision log


Professor lens: compare the same fixed public tasks and source set when isolating
coordination; synthetic transformations need explicit answer-preservation contracts
or independently derived new answers. Original and derived questions from a filing
share a split. Over/under-thinking labels require paired interventions and repeated
support; token length alone cannot diagnose them. Same-model judges are fallible.

Engineer lens: add opt-in configuration and an independent quality-lab CLI; preserve
legacy defaults. Record transferred findings, peer review and adaptive choices as
explicit events. Reject unsupported replay/Pi combinations. Keep deterministic
failures and semantic judgments separate; preserve contradictory originals.

Systems lens: every LLM call crosses Recorder with workspace tags; enforce a shared
suite call/output reservation limit, per-run call caps and fixed evaluator caps.
Randomize paired run order reproducibly; save partial results on errors. Live
compatibility needs real MARA runs; mocks establish contracts only. Existing dirty
checkout and server source checkout must not be overwritten or staged wholesale.

## Plan of work


Add typed coordination settings and shared tasks, ordered prompt rendering and
independent evaluation caps. Implement sequential context delivery, peer review and
bounded adaptive review. Add deterministic data transformations and source manifests,
then a suite executor and offline failure/paired reports. Run fixtures that inspect
actual Recorder request bodies, context transfers, admitted calls and private-label
isolation. Transfer only the experiment snapshot to the authorized isolated server,
read credentials there without printing/copying them, execute a bounded pilot and
bring receipts back. Document live gaps, including missing human calibration.

## Validation and acceptance


Run `python -m seocho.eval.agent_observability.quality_lab --help` for preparation,
execution and analysis. Generated manifests show the purpose, gold contract, source
hash and split for each variant. Recorder events show which agent received which
context. Reports include failed trials, unknown labels, evaluator costs and paired
budget/order changes. Focused tests, `bash scripts/ci/run_basic_ci.sh`, doc contracts,
ADR index and diff checks must pass. A real MARA pilot must identify models, dataset,
SDK versions, call limits, concurrency and unavailable provider hardware/prices.

## Recovery and remaining evidence


Execution writes each trial before the next begins, stops when the suite limit is
reached and closes the harness. No automatic retries, public deployment, retraining
or promotion to champion. Synthetic fixtures do not replace original FinDER scores.
Human review and independent judges are needed before claiming robust improvement.

## Discoveries and implementation update


The actual model catalog now lists V3.1, V3.2, MiniMax-M3, gemma-4-31B-it and
gpt-oss-120b. First pilot: 32 trials and 45 calls, with extensive V3.1 HTTP 429.
Several other models completed inference but failed at the V3.1 evaluator; reports
must attribute these to evaluation, not answer-model quality. MiniMax-M3 returned
JSON that did not satisfy the required Findings schema in two cases and provider
HTTP 400 truncation in another. An explicit prompt-schema challenger is therefore
implemented as an independent factor, preserving the original API-schema contract.

The first proposed Company overview case was excluded by the existing conservative
ticker resolver. Use eligible original case 4e13063f (Company overview, True,
Compositional) instead, plus 1303017a (Financials, False, None) and original MSI
subtraction case 7e6c28fd. Do not alter original questions or labels to force eligibility.
Prepared 27 variants with source manifests and pending human-review queues. A
separate V3.2 cohort uses only the complex case; evaluator model changes are an
explicit cohort amendment, with no cross-judge quality pooling. Combined request
limit remains 96. Provider failures are evidence gaps, not replaced by Mock scores.

Validation update: eight quality-lab tests cover source inventory/grouping, gold
isolation, all four coordination modes, shared admission exhaustion, provider-stage
classification, prompt block order, prompt-JSON output mode and report tampering.
The full basic CI currently passes 756 tests with 5 skipped and 16 warnings.
First-cohort offline analysis validates all Recorder identities/receipts, keeps
31 incomplete/failed quality outcomes unknown, and distinguishes eight failures
at the V3.1 evaluator from actual inference failures. A numeric single-case outcome
is available for gemma; no model ranking follows from this failed cohort.


Final outcome: 54 trials and exactly 96 actual requests across four separate cohorts.
All four coordination modes completed with gemma; source/context and review events
audit valid. MiniMax prompt-JSON recovered an API-schema interface failure at the
same inference cap. No general quality advantage or over/under-thinking diagnosis
was established; same-source evaluator contradictions require human review. Final
generator adds a separate fictional company/period distractor (30 v2 candidates),
without changing the 27 artifacts already executed. Human review and calibrated
heldout improvement remain future evidence, not silently completed work.

Full basic CI passed 756 tests, 5 skipped, 16 warnings. Eight focused quality tests
pass. Public docs, ADR index and whitespace checks pass. Detailed live report is
`docs/experiments/finder-quality-lab-live-2026-10-11.md`. No paid process remains
running. A local read-only UI may remain for inspection. No commit/push was made
in the pre-existing dirty checkout; unrelated work was preserved.
