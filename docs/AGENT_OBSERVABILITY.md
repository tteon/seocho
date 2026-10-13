# Research agent Recorder

Phase 1 adds a local OpenAI-compatible proxy as the transport evidence source for
research experiments. It is separate from operational Opik/OTel tracing and does
not change DozerDB as the graph backend. See [ADR-0237](decisions/ADR-0237-research-recorder.md).

## Run and inspect

Install the optional dependencies and run the deterministic local acceptance:

```sh
uv sync --extra recorder
uv run --extra recorder python examples/agent-observability/acceptance.py --help
uv run --extra recorder python examples/agent-observability/acceptance.py --directory outputs/agent-observability/new-acceptance
```

Use a fresh directory for each acceptance run. It starts actual loopback mock and
proxy processes, sends 100 calls, stops both servers, audits all 100 caller IDs,
checks 99 cache hits and blob deduplication, and exports raw events to Parquet.
This verifies local protocol/storage behavior, not MARA compatibility or throughput.

For manual mock calls, start these in separate terminals:

```sh
uv run --extra recorder python -m seocho.recorder mock
uv run --extra recorder python -m seocho.recorder serve --config examples/agent-observability/recorder.mock.yaml
```

After stopping the Recorder writer, inspect or export its immutable records:

```sh
uv run --extra recorder python -m seocho.recorder inspect --call CALL_ID --directory outputs/agent-observability/mock
uv run --extra recorder python -m seocho.recorder audit --run RUN_ID --directory outputs/agent-observability/mock --manifest attempts.json
uv run --extra recorder python -m seocho.recorder export --output outputs/agent-observability/raw.parquet --directory outputs/agent-observability/mock
```

DuckDB has one writer per directory. Separate-process inspection requires that
writer to stop. The CLI reads originals without modifying them. No UI exists yet.

## Transport and storage contract

Supported inference endpoints are `/v1/chat/completions` and `/v1/responses`,
including ordered SSE body capture. `/v1/models` is discovery. Required headers are
`X-Workspace-Id`, `X-Run-Id`, `X-Agent-Id`, `X-Step-Id`, `X-Tier` and
`X-Template-Version`; optional task/parent/condition tags are preserved. Missing
tags reject by default; quarantine records are excluded from valid audits.

Raw request bytes are forwarded to the configured upstream. Authorization comes
only from Recorder configuration, never caller headers; credentials are not stored.
Malformed JSON and unsupported routes are rejected before inference recording.
Call starts are stored before dispatch; storage failure blocks dispatch. Start and
terminal events are append-only; incomplete calls remain visible. Application-level
immutability does not provide protection against someone editing files directly.

SHA-256 blobs store full request/response bodies, normalized JSON and individual
messages in separate inference/evaluation domains. Identical blobs are stored once;
full envelopes still contain repeated message text. Audits verify referenced blob
hashes. Parquet contains event metadata and blob references; retain blobs too.

Cache modes are `off`, `read`, `read_write`, and `replay_only`. Default `off` keeps
repeated samples independent. Cache keys cover all JSON fields, upstream URL,
namespace and domain; only volatile transport tags/auth are excluded. Give each
account/profile a separate namespace. A replay-only miss returns an error without
upstream work. Streaming cache reuse is unsupported. Each cache hit has a distinct
call ID, its origin ID, inherited usage and zero new provider cost. Live cost and
unreported reasoning tokens remain unknown rather than guessed.

Usage, model identity, HTTP status and latency are recorded when observable.
Hard output limits and declared soft budgets are distinct fields. Tier headers are
caller declarations, not proof that the corresponding information was delivered.
Provider-private reasoning cannot be reconstructed from API responses.

## Experiment adapter

Current `examples/graphrag-collaboration` live runners require `--recorder-url`
(or `SEOCHO_RECORDER_URL`), for example `http://127.0.0.1:8800/v1`. Their client
has no provider credential. The OpenAI Agents SDK adapter supplies context-local
agent/run headers, disables hidden client retries, and checks returned call IDs.
The caller attempt manifest supports coverage comparison; an audit without a
manifest checks record integrity only. It cannot prove that an unrelated process
made no direct provider calls. Standard SDK consumers outside these experiments
are unchanged.

`recorder.mara.yaml` reads `MARA_API_KEY` from the Recorder process environment.
Keep that key out of runner configuration and source. Paid reruns require the
explicit phase authorization described in the supplied implementation directive.
Historical MARA reports were generated before this Recorder and are not complete
transport evidence. Phase 1 executed no paid provider requests.

Evaluator records use an explicit evaluation domain. This is storage/cache
separation, not a gold-leak detector or authenticated access-control system.
Phase 2 adds explicit decisions, realizations, fetch results, delegation returns
and conservative gold checks. Phase 3 adds prefix replay/interventions; Phase 6 adds the read-only graph UI.
Full agent lineage is not claimed from HTTP records alone.

## Phase 2 YAML harness

A YAML file declares dataset and separate evaluation file, role-to-model mapping,
conditions, seeds and sample count. The six named variants are `vanilla`,
`global_budget`, `planned_vanilla`, `planned_global_budget`, `plan_and_budget` and
`routed_plan_and_budget` (the directive groups the two planned variants together).
Forced tier and agent-decided tier are separate condition IDs. Independent samples
require Recorder cache off; a cached profile is rejected before execution.

`vanilla` uses a single solver, without planner or budget instruction. Planned
variants use a structured planner, solver delegation and synthesis. Delegation is
sequential (`single`) or concurrent (`parallel`), with returned findings explicitly
fed into synthesis. This is an SDK-backed harness coordinator; no supervisor LLM
is silently added to the vanilla condition. Soft global instructions are measured
across all inference output; local allocations use the declared difficulty schedule.
Hard API output caps and hard shared call admission are recorded separately.
The shared call cap includes evaluation. Unknown token counts yield unknown compliance.

Static routing selects optional `solver_easy` / `solver_hard` role mappings by
estimated difficulty, falling back to `solver`. LLM routing chooses from declared
solver model keys. Forced tiers remain enforced even with routing. Agent-decided
conditions include a separate recorded information-choice call. Default fixed
retrieval keeps the original passages unchanged across questions/subtasks. T1/T2
protocols allow injected plugins; no graph service is required.

Run local protocol acceptance:

```sh
uv run --extra recorder python examples/agent-observability/acceptance_phase2.py --directory outputs/agent-observability/new-phase2
```

It runs 18 forced strategy/tier combinations and two additional agent-decided
router conditions over actual loopback servers. Mock evaluations are explicitly
`mock_contract_only`; they are not answer-quality scores.

Prepare the existing FinDER pilot without model calls:

```sh
uv run --extra recorder python -m seocho.eval.agent_observability prepare-finder --dataset .seocho/datasets/finder/data/train-00000-of-00001.parquet --documents .seocho/datasets/finder/10-k.zip --case 7e6c28fd --output outputs/agent-observability/finder-phase2
```

This freezes the same question-only top six source passages, writes an agent
reference artifact after exact/substring gold filtering and writes gold in a
separate evaluation file. It does not generate reference facts with an LLM or
claim human curation. If no safe reference remains, preparation fails.
A runtime leak match blocks delivery and records `LEAK_SUSPECTED` in the failed
run realization, without copying gold into inference diagnostics. Numeric/short
gold can create conservative false positives. Semantic paraphrase leakage is not
detected. Contamination variants were not supplied and the report says not measured.

For an authorized experiment, copy `experiment.mock.yaml`, set its dataset,
evaluation and model mappings, then run against the configured Recorder:

```sh
uv run --extra recorder python -m seocho.eval.agent_observability run --config experiment.yaml --output outputs/agent-observability/trials.json
```

`POST /events` records explicit orchestration payloads as immutable blobs.
Realizations contain actual call IDs, observed usage/model/latency, returned context
and budget compliance when measurable. A run stores its condition snapshot, seed,
sample and terminal flags. `GET /calls/{id}` supplies transport facts scoped by
workspace/run for the harness. This is a local trusted research API, not public
multi-tenant authentication. Full inference/evaluation payloads remain separated.

After Recorder shutdown:

```sh
uv run --extra recorder python -m seocho.recorder inspect-events --run RUN_ID --directory outputs/agent-observability/new-phase2/store
uv run --extra recorder python -m seocho.recorder audit-events --run RUN_ID --directory outputs/agent-observability/new-phase2/store
```

Event audits check pairing, parent presence, blob integrity and exact transport
step/agent/workspace/condition links. Export writes separate transport and agent
event Parquet files. Raw originals remain authoritative; later analytics can derive
entity tables and causal graphs. The harness currently asks for independent subtasks;
full dependent DAG scheduling and cross-run subtask alignment are not implemented.
Phase 3 prefix interventions and Phase 6 UI remain gated.

## Phase 3 replay and interventions

A base must be a completed, audited run in the same Recorder workspace. The local
snapshot API returns inference-only original requests and orchestration events;
evaluator gold is excluded. Pinned replay verifies the origin's exact request hash,
upstream namespace, workspace and domain. It works for bases recorded with cache
off and rejects misses/mismatches instead of dispatching. Ordinary independent runs
still use cache off. Every replay has a new call ID and explicit origin ID.

The boundary is causal across independent branches, not an HTTP timestamp cutoff:
planner and unaffected worker branches are replayed. The changed worker and its
synthesis/evaluation descendants are fresh. Frozen retrieval/reference outputs are
reused with origin-event IDs; modified information fetches run normally and still
pass the gold guard. Prefix requests retain the base seed/sample bytes exactly;
derived run metadata identifies its new sample. Downstream requests use the derived
sample identity. Cached usage is inherited and does not consume the cost ledger.

Typed operators:

- `set_budget`: change one local solver's soft allocation; planned local-budget strategy required.
- `set_schedule`: `uniform` or `difficulty_proportional`; all solvers rerun.
- `set_route`: use a declared `model_key` or `agent` role alias; agent aliases change actual worker identity as well as the mapped model.
- `set_tier` / `knock_in_reference`: change only the targeted solver's delivered information; knock-in uses T2 and checks gold before delivery.
- `replace_plan`: supply a typed inline plan, or `plan_source` naming another audited run with one recorded plan. Planner output is explicitly replaced without a fabricated planner call; all workers rerun.
- `replace_subanswer`: replay the original solver output and substitute typed returned findings before synthesis. Injected context passes the gold guard and evidence-reference checks.

Snapshots do not support streaming calls, retries with multiple successful calls
per step, dependent DAG scheduling or retroactive replay of historical runs that
lack Recorder evidence. Plan-source replacement uses its recorded decomposition;
it does not silently pay for a stronger planner. These operations manipulate the
existing single-layer SDK coordinator; they do not claim a general framework replay engine.

Prepare a base run using the Phase 2 YAML command, then invoke:

```sh
uv run --extra recorder python -m seocho.eval.agent_observability intervene --config experiment.yaml --base-run BASE_RUN_ID --spec examples/agent-observability/intervention.mock.yaml --limits examples/agent-observability/cost-limits.mock.yaml --samples 2 --output outputs/agent-observability/intervention.json
```

A budget sweep uses the same command with `--budgets 32 64 128` and a specification
containing a target `subtask`. The cost ledger is shared across all levels and N
samples in that invocation. Reports retain failed run manifests and stop state;
separate invocations have separate declared caps.

`max_tokens` is a conservative reservation cap for new work, distinct from each
call's hard output limit. Admission sees the full serialized SDK request. Set
`input_token_reservation` high enough to cover its UTF-8 byte length, including
schema and messages; larger inputs fail before provider dispatch. Each new call
reserves this allowance plus its hard output cap. Reservations are never released
from unknown/failed usage. This can stop well before the provider's actual token
bill. It is not a model-specific tokenizer or a provider billing guarantee.

A dollar cap (`max_usd`) additionally requires `price_version` and an explicit
`usd_per_token_upper_bound` covering every chosen model. Admission applies that
upper bound to reservations. Provider-reported costs remain unknown unless the
provider supplies them; configured upper bounds are not presented as actual prices.
Unknown usage stops further dispatches. No live provider price or throughput claim
is derived from local mock tests.

Actual local acceptance:

```sh
uv run --extra recorder python examples/agent-observability/acceptance_phase3.py --directory outputs/agent-observability/new-phase3
```

This repeats the same reference knock-in twice with N=2: each derived run has two
prefix hits and three fresh calls. The mock's own request counter verifies provider
dispatches, alongside all Recorder manifests and event audits. Base/derived links
are queryable through the derived `interventions` DuckDB view and CLI event inspection.
Phase 4 surrogate labels and Phase 5 population statistics remain separate gates.

Phase 3's initial adapter replay supports one successful non-streaming structured
Chat Completions call per orchestration step. Responses/streaming capture remains
available in the Recorder, but those transports are not claimed as harness replay
compatibility. Replacement plan/subanswer content is omitted from intervention
metadata until validated; rejected content never becomes an inference payload.

## Phase 4 derived subtask signals

Run analysis after stopping the Recorder writer. It audits source calls/events,
reads subtask questions, delivered evidence, returned findings and final evaluator
verdicts, and writes a separate derived DuckDB. It does not mutate raw records or
make an LLM/API call. Analysis is a privileged researcher surface; silver labels
and evaluator material are not automatically fed back to inference agents.

```sh
uv run --extra recorder python -m seocho.recorder signals --workspace phase2-mock-lab --directory outputs/agent-observability/phase2-reviewed-acceptance/store --output outputs/agent-observability/phase4/signals.duckdb --config examples/agent-observability/signals.yaml
uv run --extra recorder python -m seocho.recorder inspect-signals --database outputs/agent-observability/phase4/signals.duckdb --job JOB_ID --kind alignment
```

The Python API is `analyze(directory, output, workspace_id, config=..., run_ids=...,
encoder=...)` in `seocho.eval.agent_observability.signal_store`. Pure `derive(runs,
config, encoder)` supports notebook/test computations and does not mutate its input
objects. `AnalysisStore.rows(job_id, kind)` returns ordinary records. DataFrame and
population reports belong to Phase 5.

Available row kinds:

- `alignment`: one-to-one subtask matching using text embeddings and position; method, anchor, confidence and unmatched status are explicit.
- `tier_transitions`: same workspace/task/seed/sample/design pairs, including T0-correct→T1-incorrect retrieval harm. Ambiguous duplicates and unknown verdicts are excluded with gaps.
- `silver_labels`: normalized exact or embedding agreement from at least k distinct original solver calls in successful, uniformly T2, non-intervention runs. Replayed copies and interventions cannot add independent votes; conflicting qualifying groups yield no label.
- `first_divergence`: earliest mismatch against an available silver label in plan order. A missing alignment/label earlier in the plan yields unresolved prefix rather than an invented first error.
- `knock_in`: paired incorrect-base→correct-derived rescue fraction for verified target T2 delivery, matching workspace/task/seed/design and distinct new target origins. It is an observed bottleneck proxy, not proof of a causal mechanism.
- `entropy` / `entropy_change`: answer-cluster entropy by aligned subtask, seed, design and tier, deduplicated by original call. Fewer than the configured independent samples yields unknown entropy. Tier-to-T2 reduction classifies knowledge-gap or reasoning-difficulty proxies, never diagnoses.
- `information_mapping`: actual evidence availability to a subtask, content hash, source, tier, delivery method and text similarity. Delivery confidence does not establish relevance or use.
- `gaps`: source audit failures, unknown outcomes, missing pairs/consensus or unverified attribution.

Evaluator verdicts `correct`, `incorrect` and `partial` map to true, false and
false. Other values, including `mock_contract_only`, remain unknown; they never
become scientific correctness labels. These final outcomes support the specified
signals without introducing subtask gold. A single rescue observation is retained
as an observation, with its denominator, rather than an effect estimate.

Alignment uses the longest observed plan as a deterministic anchor, with text/ID
tie-breaking, then greedy one-to-one matching. Exact text+position identity scores
1; other matches combine cosine similarity and positional proximity and reject
ambiguous alternatives. Confidence is a heuristic, not a calibrated probability.
A different decomposition can remain unmatched. No cross-run conceptual identity
is inferred solely from repeated `task_1` IDs.

Default embedding: `lexical-hash-word-bigram-256-v1`, fully local and deterministic.
Its entropy is explicitly named lexical disagreement. No pretrained semantic
weights are installed or downloaded by this command. For semantic computations,
supply an immutable exported vector file with `--embedding-vectors vectors.json`:

```json
{"model":"local-model-name","revision":"immutable-version","kind":"semantic",
 "vectors":[{"text":"subtask or answer or reference text","vector":[0.1,0.2,0.3]}]}
```

All analyzed question/answer/reference texts must have finite nonzero vectors of
the same dimension. Missing vectors fail rather than silently falling back.
The file digest, model/revision and semantic flag are persisted. Programmatic local
encoders can implement the same `Encoder` protocol. Semantic numerical tests use
a toy fixture; no semantic model accuracy or FinDER answer-quality claim follows.
No LLM judge is invoked for agreement in this phase.

Jobs are keyed by raw-source/cache-origin fingerprints, implementation hash,
algorithm version, configuration, Python version and encoder identity. Identical
recomputation returns the same job; changed sources/settings/code create another
job, leaving earlier results intact. Tables `analysis_jobs` and `signal_rows` are
separate from Recorder originals. Retain originals and blobs to reproduce results.

Reference mapping quality limits knock-in interpretation: observed availability
is certain under valid source audits, but relevance similarity and subtask alignment
are approximate. Population confidence intervals and paired statistical tests remain
Phase 5 work. No UI is added before Phase 6.

## Phase 5 population metrics and offline reports

Install the `recorder` extra and run `obs report examples/agent-observability/report.mock.yaml`.
The report configuration selects a Recorder directory, workspace, output directory,
logical/physical cost basis, bootstrap repetitions and multiple comparison correction.
`records_frame()` returns pandas run/gap DataFrames plus source provenance;
`efficiency()`, `paired_comparisons()`, `bam_sweeps()`, `allocation_sweeps()`,
`minimum_tier()` and `observed_regrets()` are separately callable analysis APIs.
Reports write CSV tables, PNG figures, Markdown and a JSON provenance manifest.

E3 is A²/T and the companion measure is A/T. A and T are task/seed means;
planner, router, solver, fetching wall time and synthesis belong to inference work.
Evaluator tokens are separate. Missing verdicts or prices never become zeros.
Logical tokens include replayed prefix work; physical tokens count new dispatches.
Logical replay latency remains unknown because a sum of call durations does not
reconstruct the critical path. Milliseconds are the latency unit.

The population contains audited completed runs. Failed or invalid source runs stay
in the gap table; this is explicitly not an intention-to-treat estimate. Paired tests
require identical sample coverage for each task/seed. McNemar uses one binary
sample per unit; repeated samples use paired bootstrap of unit means. A single
paired unit yields an effect but no confidence interval. Holm correction is default.

Tier errors, under/overthinking and routing are final-outcome proxies, not subtask
diagnoses. Cheapest sufficient tier means cheapest *observed successful* condition.
BAM fits positive error rates at three or more distinct positive budgets, reports
R²/residuals, and refuses to clip zero errors. The continuous allocation optimum
satisfies equal marginal error reduction even when subtask beta values differ.
Predicted additive error regret is not E3. Predicted E3 requires explicit modeled
accuracy/cost values and model identity; measured E3 regret requires measured inputs.
No report triggers a paid rerun or applies predicted allocations automatically.

`fairness()` checks declared source, tools, models and admission budget metadata;
matching declarations do not prove matched consumed compute. `best_of_n()` requires
an explicit non-gold score or labels gold selection as an oracle upper bound. It
charges all candidates, including discarded generations; latency needs a scheduling
receipt. It is a baseline analysis API, not an automatic generation/selection job.
Older records without information fingerprints fail fairness matching transparently.
Mock acceptance validates computation/storage only, never model accuracy or latency.

## Phase 6 local graph explorer

```sh
obs ui --workspace phase2-mock-lab \
  --directory outputs/agent-observability/phase2-reviewed-acceptance/store \
  --experiment examples/agent-observability/experiment.mock.yaml \
  --origin-recorder-config outputs/agent-observability/phase2-reviewed-acceptance/recorder.yaml
```

Open `http://127.0.0.1:8810`. Stop the Recorder writer before opening its offline
DuckDB source. The fixed workspace explorer lists runs including incomplete/failed
records, keeps graphs collapsed initially, and retrieves at most twelve sub-tasks
per graph page. First silver divergence is ordered first for a known incorrect run.
Recorded `depends_on` relationships form dependency edges; the current harness
requires independent subtasks, so no artificial dependency chain is drawn.
Routing and returned-context edges show solver dispatch and final synthesis.

Sub-task cards show assigned budget, observed logical output tokens, explicitly
modeled optimal allocation, information tier, sample entropy, silver agreement and
knock-in rescue score. Missing samples remain unknown. Node/call inspection retrieves
complete recorded request and response bodies plus metadata. Planner/router and
synthesis calls are in the ledger. Evaluation bodies require selecting the separate
evaluation domain and are visibly marked as containing gold; they are never copied
into an inference prompt by this UI.

Population view shows paired tier transitions, error proxies, measured escalation
and allocation regret distributions, aligned failure-flow Sankey and evidence gaps.
Sankey represents aligned order flow, not recorded dependency or causality. Only
measured E3 allocation regret enters its distribution; fitted error regret remains
explicitly labeled in the table. The default signal encoder is lexical, not semantic.

Optional `--experiment` enables node intervention submission for `mock-fixed` source
runs. A job copies the offline source into an isolated output store and runs the
Phase 3 engine through in-process deterministic Mock transports. It never connects
to the original upstream URL. That URL and namespace are nevertheless required to
validate the exact pinned request hash. New Recorder rows retain upstream identity;
legacy sources require their original `--origin-recorder-config`. Failed replay
is not silently replaced by a fresh prefix. N and conservative token reservation
caps are explicit. Completed branch runs can be inspected and the original view
restored. Jobs are serial; UI job discovery is session-local, while job receipts
and branch stores persist under `outputs/agent-observability-ui-jobs`.

Without an experiment, all views remain available and execution is disabled. Paid
MARA/OpenAI execution is disabled in this UI. The host is loopback-only, browser
mutations require a per-session capability and same origin, and raw prompt text is
rendered as text. D3 7.9.0 and d3-sankey 0.12.3 are packaged with licenses, so the
browser requires no CDN. Source records are loaded once at startup; this version is
an offline research viewer and has no large-population performance claim.

## Official P-and-B reference audit (2026-10-10)

Reference: [junhongmit/P-and-B](https://github.com/junhongmit/P-and-B), inspected
from its main branch. No upstream program or provider call was executed.

The official [weighted solver](https://github.com/junhongmit/P-and-B/blob/main/inference/_5_planned_local_weighted_model.py)
combines sub-question credits with constant/linear/polynomial/exponential/cosine
position weights and normalizes the product. Integer allocations are floored;
remaining units go to the largest combined weights. Cosine adds 0.1, polynomial
uses power 2 and exponential uses base 0.9. The current research harness instead
allocates by planner difficulty alone and gives the rounding remainder to the last
task. It supports uniform as an intervention, but does not reproduce the complete
official decay schedule family.

The weighted and [uniform solver](https://github.com/junhongmit/P-and-B/blob/main/inference/_4_planned_local_uniform_model.py)
place every step and its budget into one final solving prompt. The budget is a
word-count instruction; local sub-question budgets are not separate API caps.
Our current harness executes separate specialist calls and final synthesis with
soft output-token instructions plus independent hard API output limits. These are
different experiment designs and must not share a faithful-reproduction label.

The official `process_question` receives prepared steps, credits and level; its
online decomposition/level evaluation calls are commented out. Our planner is
executed and measured during each run. The official total instruction budget uses
[a level-to-budget mapping](https://github.com/junhongmit/P-and-B/blob/main/utils/utils.py)
(200, 250, 350, 450, 600 for levels 1 through 5); our total is an explicit experiment
parameter. FinDER has no adopted equivalent level mapping, so none is inferred.

Before a paper-reproduction comparison, add a separately named single-solving-call
baseline with pinned upstream revision, declared prepared/online planning policy,
word-versus-token budget unit and the original allocation/rounding rules. Keep the
current multi-agent variant separate. Charge preparation overhead when planning is
online, or declare and report amortized preparation separately. Preserve fixed
passages and gold isolation in both conditions. This audit does not certify current
behavior as an exact P-and-B reproduction or change existing run semantics.


## Optional server Pi harness comparison

The SDK remains the default. Set `harness_backend: pi`, `pi_executable` to the
installed Pi CLI, and `pi_artifacts` to an ignored output directory to substitute
Pi for inference turns. The common evaluator still uses the SDK. The adapter was
authored against server package `@earendil-works/pi-coding-agent` 1.0.0; compatibility
with other Pi implementations is not implied. Each turn runs with isolated HOME
and configuration, no tools/context discovery/skills/background work, no retries,
and a one-request guard with Recorder acknowledgement. Exact CLI JSON events and
stderr are retained alongside Recorder records. A failed or truncated response
is not accepted as an answer. Streaming replay is not supported for this backend.

Pi receives the schema in the user prompt whereas the SDK uses response formatting;
inspect raw requests before interpreting this as an identical-prompt experiment.
Compare controlled harness substitution first and native Pi tools/planning as a
separate experimental factor. Provider token counts are the measurement source;
Pi catalog prices are not used.

## FinDER evaluator v2 and stratified design evaluation

Use `evaluator_version: v2` explicitly; old experiments retain v1 semantics.
The evaluator separates answer equivalence, delivered-evidence support, citation
validity, public reasoning validity, units/periods and abstention. Each dimension
is pass/fail/unknown/not_applicable; the primary correctness verdict is separate.
Gold wording and gold excerpt location are not the only valid grounded answers.
Untrusted answers and passages are data, never instructions. The evaluator does
not request or assess private chain of thought. Quotes and explicit calculations
produce `verify_receipts` decision/realization events in the evaluation domain.
Exact quote presence and decimal arithmetic are deterministic checks; they do not
verify semantic entailment, operand extraction, or units/periods. Conflicts remain
visible and never silently overwrite raw model judgments.

`prepare-finder` now writes an evaluation-only object per case: `gold_answer` and
`metadata` with domain, original `reasoning_required` boolean, reasoning type and
gold references. Legacy gold string files remain supported. These private fields
are excluded from inference contexts, events and public case fingerprints. Only
the evaluator receives them. Model routing cannot use these annotations by
default; an oracle-annotation router must be explicitly labeled as such.

Run the offline inventory without inference or provider calls:

    python examples/agent-observability/prepare_evaluator.py --dataset .seocho/datasets/finder/data/train-00000-of-00001.parquet --output outputs/agent-observability/evaluator-setup

`reasoning` is an original FinDER column, not inferred from operation type or
reference count. The inspected 5703-row snapshot contains true labels only in
Financials and Company overview; 204 true rows have type None and two false rows
have type Compositional. Empty domain/boolean cells are not synthesized. Inspect
inventory hashes and mismatch rows before selecting a balanced workload.

Reports export `stratified_quality.csv`: observed domain × original reasoning ×
operation type × evaluator version × harness × condition, with task/seed mean
accuracy, dimension pass rates, and known/unknown counts. Legacy runs have unknown
stratum labels rather than invented false labels. Completed-only population and
failure gaps remain explicit; this is not an intention-to-treat estimate. V1
verdict case normalization is versioned as `signals-v2-evaluator-normalization`;
raw verdicts remain unchanged. Detailed dimensions require v2 outputs.

Initial architecture hypotheses: directly answer single-source tasks; extract
operands then verify arithmetic for numeric tasks; decompose/delegate/reconcile
multi-reference compositional tasks; preserve qualifications for legal/risk and
accounting interpretation. Domain alone and reasoning=True alone do not establish
that multi-agent designs help. Compare every architecture within the same
observed stratum and budget/model/evidence regime; select routing policies on
held-out companies after evaluator calibration, not on benchmark gold labels.

Calibration remains required: review representative correct, incorrect, partial,
unsupported and abstaining candidates across available strata; compare judge
labels with human adjudication, record disagreement, and use an independent judge
model where available. The one-case live gate establishes integration only. The
current same-model judge is not human ground truth. See
`docs/experiments/finder-evaluator-v2-live-2026-10-11.md` for original evidence.

### Model-selected tool-agent condition

`strategy: tool_agent` is an opt-in OpenAI Agents SDK experiment with a forced
information tier. The model chooses `search_evidence`, `extract_number`,
`calculate` and `delegate`; the existing host-orchestrated conditions remain
controls. Search delivers the same fixed passages regardless of query, so this
is a tool-selection experiment, not a retrieval-quality claim. Delegation passes
only previously delivered sources and returns typed findings.

Every SDK model request, including tool-loop turns and specialist requests, goes
through Recorder and consumes the shared `max_calls` cap; evaluator requests also
consume this cap. `max_tool_calls` independently limits tool execution. Root tool
loops time out after 180 seconds. Pi and intervention replay are unsupported for
this condition and fail explicitly. Tool decisions/results are immutable events;
arguments, returned IDs and specialist contexts are inspectable in raw records.

Deterministic checks cover source-number occurrence, receipt-ID dependencies,
arithmetic, execution failures and exact duplicate calls. Unit/period semantics,
minimal valid paths and final state success without precommitted goals remain
unscored. In particular, a number appearing somewhere in a passage does not prove
that it belongs to the requested company, metric or period. With evaluator v2,
`finder-evaluator-v2-tools-v1` separately judges tool relevance, dependency handling
and handoff quality; unknown and not-applicable remain distinct from pass.
This is BFCL-inspired domain evaluation, not an official BFCL score.

### FinDER quality experiment loop

Run `python -m seocho.eval.agent_observability.quality_lab prepare --cases
CASES.json --evaluation EVALUATION.json --output PREPARED` to create ten variants
per source case: original, question-selected evidence at front/middle/back, reversed
evidence, question instruction wrapper, fictional wrong-company/period distractor, document instruction injection, empty
evidence and a new fictional numeric question with independently computed gold.
The wrapper is not a diverse semantic paraphrase generator. Position tests preserve
the exact evidence inventory. Source manifests, immutable artifact hashes, document
groups, splits, purposes and gold contracts are retained. Inputs are gold-free;
evaluation artifacts and review queues remain local/private. Human validation is
pending, not implicitly passed. Changed-gold tasks are distinct from robustness
pairs, and original/synthetic scores are never pooled as original FinDER accuracy.

Use `quality_lab run --config SUITE.yaml --output RESULTS.json`, then
`quality_lab analyze --results RESULTS.json --directory RECORDER_STORE --output
ANALYSIS`. `Suite` declares models, evaluator model, arms, seeds, samples and split.
An arm declares a normal `Condition`, variants and optional model/source-case
subsets. Trial order is shuffled using `order_seed`, persisted before execution;
partial results are saved after each trial. `max_calls` caps physical suite requests;
`max_reserved_output_tokens` reserves each API output cap before dispatch. Input
tokens, monetary price and hidden reasoning costs are not covered by that output
reservation. No automatic retry or automatic champion promotion is introduced.

Planned conditions can specify public `fixed_tasks` with `{question}` placeholders.
`coordination` is independent, sequential_context (single delegation), peer_review
or adaptive_review (manager chooses zero or more existing tasks for one review
round). Context transfers retain source child steps and full typed findings. Peers
review original findings against delivered sources; final synthesis uses revised
findings. `returned_context_order: reverse` changes only synthesis input order;
`prompt_order: evidence_first` moves source/context blocks before the question
inside the same user JSON message. Message roles and system instructions stay
unchanged. Pi and causal replay reject these new conditions pending integration.
`evaluator_output_limit` keeps judging caps fixed while inference caps vary.

Version `finder-evaluator-v2-coordination-v1` judges context use, contradiction
handling and synthesis faithfulness separately. Code verifies IDs, arithmetic and
quote presence without asserting semantic correctness. The offline report checks
trial lineage, identities, failures, judgments and receipts against Recorder
originals. Provider HTTP 429, output truncation, interface errors, answer/evidence
failures and evaluator conflicts have explicit bases. Evaluator failures are not
counted as answer-model errors. Quality remains unknown for unaudited/incomplete
trials. Reports include model failures, domain/reasoning/variant/arm strata, paired
robustness and budget observations, unexecuted trials and unvalidated improvement
hypotheses. Budget recovery and extra work without observed gain are candidates
requiring repeated seeds/cases and human adjudication; neither reveals private
chain of thought. Ontology/KG quality is not exercised by this fixed-source suite.

For models that fail to honor API JSON schemas, `output_contract: prompt_json`
is a separate inference-only challenger: it sends the exact schema in system
instructions, disables SDK API structured output, then validates the returned raw
JSON locally. Original API-schema records are retained. Evaluation still uses API
schema output and its fixed cap. This factor is distinct from a budget change;
recovery is not proof that multi-agent reasoning improved. Tool-agent, Pi and
replay reject this new contract pending dedicated integration coverage.

Quality reports retain run wall time (including orchestration/evaluator), per-domain
sums of call latency, reasoning-token counts and monetary cost. Parallel call sums
are not wall time. Missing prices/reasoning usage remain unknown. Trial checkpoints
are atomically replaced so inspection cannot read a partially written report.

### Single-agent metacognition arm

With evaluator v2, `strategy: tool_agent` and `metacognition: true`, the same
solver model first receives fixed passages and emits an initial answer plus
public required slots, evidence IDs, unsupported assumptions, confidence and
requested capabilities. Gold/domain/reasoning labels remain evaluator-only.
The single agent then selects search/extract/calculation tools; specialist
`delegate` is absent. Fixed-source search returns the existing passages and
cannot recover intentionally removed evidence. Ontology, knowledge graph and
clarification are explicitly unavailable capabilities, recorded as unmet needs.
The revised and initial answers receive separate blind model judgments. Initial
judging uses only initially delivered evidence. Both judgments consume physical
call admission and evaluator output caps. Reports expose paired recovery,
regression, confidence-based failure-detection agreement (threshold < 0.5) and
confidence Brier observations, with
partial/unknown judgments preserved as unknown. These are model-labeled
observations requiring human calibration, not metacognition ground truth.
No-tool initial vs revised is a within-run comparison; a randomized no-tool
control is still needed to attribute improvements specifically to tools rather
than extra model turns. Tool necessity, ontology/KG benefit and private thinking
traces are not inferred from token counts or self-reported uncertainty.

### Model public execution-path comparison

Opt-in `execution_path: direct|explicit|adaptive` requires vanilla strategy,
evaluator v2, SDK and no replay. Direct answers without an explicit plan.
Explicit asks the same solver model for up to three ordered subquestions, each
with required slots, earlier dependencies, completion criteria and requested
capabilities. Sequential model turns receive only their declared dependencies
and identical fixed evidence. Adaptive chooses whether to decompose and permits
search/extract/calculation tools; specialist delegation is absent. Source IDs
are validated per step. The final solver receives completed subquestion results.
Public plans and findings are inspectable execution artifacts, not private
reasoning traces. Same model across planner/worker/final roles does not imply
persistent agent memory: contexts are explicit host-owned packets.

`execution_path` retains original plan, step/source identities, findings,
missing slots and tool receipts, including partial paths on errors. Separate
`path_evaluation` uses a gold-free evaluation actor and checks decomposition,
dependencies, unnecessary work, missing evidence and synthesis. Its failure is
recorded independently; answer judging still runs if budget permits. Answer
judging has private gold and remains separate. A planned completion claim is
not an execution-success or entailment check. Shared caps bound physical calls
and output reservation; actual work differs across policies. Do not infer tool
causality, private reasoning, model ranking or over-thinking from one pilot.

The read-only Recorder graph supports public path steps and their declared
ordered dependencies. Final-answer nodes select the actual final solver turn;
plan output remains inspectable. Legacy silver subanswer alignment is explicitly
disabled for this new path schema instead of manufacturing worker labels.

Repeated search of unchanged already-visible fixed passages returns source IDs
instead of adding identical text again; full tool state and earlier context
remain available for extraction and provenance. Path prompt-JSON mode also
supports a schema-validated tool-loop final output without API response schema.
Tool schemas remain typed. Budget admissions record bounded failure reasons so
SDK-wrapped hook errors do not masquerade as ordinary provider connection errors.
See the [live model-path pilot](experiments/finder-model-path-live-2026-10-11.md)
for original failures, separate repairs and limits on comparisons.

### Matched single-worker coordination control

`Condition.worker_identity="single"` opts fixed sequential-context tasks into
one `single_solver` identity for solving and synthesis. Default `per_task`
retains separate solver task identities and synthesis. Both receive host-managed
explicit result packets, not persistent session history; changing identity alone
is a negative control, not new reasoning capability. Fixed tasks and sequential
context are required; replay/Pi remain unsupported. Recorded coordination metrics
name `worker_identity` and `host_scheduled_explicit_context` orchestration.
Independent parallel and peer-review arms change information flow; source and
budget equality must be checked separately. This is code-scheduled SDK agent
coordination, not native handoff or Agent.as_tool transport. See the matched
coordination ExecPlan; evaluator judgments require human calibration.

`judge_coordination=False` keeps the common v2 answer rubric/schema for matched
arms instead of adding coordination dimensions to the gold-based answer judge.
Default True preserves previous behavior. Actual context-delivery/review receipts
remain recorded; semantic context-use and correction require separate review.
This prevents changing the answer judge's rubric simply because an arm exchanges
context. Do not infer semantic cooperation from transport counts.
