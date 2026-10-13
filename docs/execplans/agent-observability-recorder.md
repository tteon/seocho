# Build a research Recorder before expanding agent-design experiments

This living ExecPlan follows `.PLANS.md`. Status: Phase 1 implemented after explicit user confirmation. The directive also requires a stop and
confirmation at the end of every Phase. Do not silently proceed across these gates.

## Purpose / Big Picture


The researcher must inspect the complete JSON input actually delivered to a model
and the returned API output, across different agent designs. A Recorder is a local
OpenAI-compatible HTTP intermediary between experiment agents and their configured
model service. It records every attempted API call, its run/agent/task identity,
original request/response, and observed usage. It provides the authoritative
transport evidence for reproducing experiments; it cannot observe provider-private
reasoning or internal state that the provider does not return.

Keep this optional research instrument separate from SEOCHO's existing Opik/OTel
operational observability and normal SDK answer route. Phase 1 creates the Recorder,
local deterministic backend, immutable storage, response cache and experiment
adapter. No UI, intervention-effect claims or paid API experiment belongs to this
initial phase. Current A–E experiment results cannot be retroactively upgraded into
complete API records; later re-execution must pass through the Recorder.

## Progress


- [x] Read the supplied implementation directive and current experiment code.
- [x] Inspect repository module boundaries and existing observability contracts.
- [x] Propose Phase 1 scope, directory structure, research caveats and acceptance.
- [x] Obtain confirmation for the proposed boundaries before Phase 1 coding.
- [x] Implement Recorder, store, deterministic backend and cache semantics.
- [x] Implement opt-in strict Recorder adapters for the existing experiment runners.
- [x] Validate 100 calls, raw capture, deduplication, cache and isolation offline.
- [x] Stop, report Phase 1 results and obtain Phase 2 confirmation.

## Surprises & Discoveries


`src/seocho/observability.py` already exists; creating a package with the same name
would create an import collision. The existing local observability example is an
OTel/Tempo/Prometheus/Grafana stack. It is not a full immutable API recorder.
FastAPI and httpx are installed in the current venv; DuckDB and Typer are absent.
The pyproject already has an optional DuckDB dependency group. Package installation
and lockfile changes are Phase 1 work after the boundary gate.

The latest FinDER pilot uses standard prompting, CoT prompting, one-Solver
subtask work, sequential one-Specialist delegation, and parallel multi-Specialist
delegation. Its ledger captures counts and timings, not serialized HTTP bodies.
429 retries are explicit and budgeted, but each attempt needs its own Recorder
identity. Evaluator calls also need recording, with gold-isolated access.

## Decision Log


DEV-DECISION-PROPOSED: Add `src/seocho/recorder/` as a framework-neutral research
package and `runtime/recorder_server.py` as the deployment shell. Framework-specific
code belongs only in `src/seocho/recorder/adapters/`. Keep DuckDB as this recorder's
research metadata store without changing DozerDB as the SDK graph baseline.

DEV-CONSTRAINT-PROPOSED: No Phase 1 UI and no unrequested paid API reruns. Live
sampling defaults to cache off. Replay remains explicit and labeled. Preserve
unknown reasoning tokens, prices, model revisions and failed usage as unknown,
not zero or guessed values. Authorization values are never persisted.

DEV-API-CONTRACT-PROPOSED: Every strict experiment call must carry workspace_id,
run_id, agent_id, step_id, tier, and, where applicable, subtask_id/parent_step.
Missing required tags are rejected by default; configured quarantine keeps calls
out of valid scientific run aggregates. A Recorder URL is mandatory for strict
recorded runs, including planner/router/synthesis/evaluator and explicit retries.

## Outcomes & Retrospective


Phase 1 is implemented and locally accepted. Actual loopback HTTP and DuckDB
acceptance passed 100 calls with 99 explicit cache hits. Full basic CI passed
710 tests with 5 skips; focused regression tests passed 60. No paid API was called.
Phase 2 remains pending the directive's end-of-phase confirmation.

## Context and Orientation


Proposed ownership:

- `src/seocho/recorder/config.py`, `models.py`: typed profiles, tags and API records.
- `src/seocho/recorder/proxy.py`: HTTP forwarding, failure/SSE handling and capture.
- `src/seocho/recorder/store.py`, `blobs.py`: DuckDB single-writer store and SHA-256
  content-addressed request/response/message storage. Content addressing stores
  identical content once and reconstructs complete normalized inputs.
- `src/seocho/recorder/cache.py`, `usage.py`: explicit replay/cache policy and
  provider-reported token fields, reasoning-token adapters, optional price versions.
- `src/seocho/recorder/cli.py`: Typer commands for serve, audit, inspect and export.
- `src/seocho/recorder/adapters/openai_agents.py`: per-call context tagging and
  strict client creation for SDK-backed experiments; no agent imports in the core.
- `runtime/recorder_server.py`: server startup and routing composition only.
- `examples/agent-observability/`: deterministic mock backend and local example.
- `tests/seocho/test_recorder_*.py`: focused proxy/storage/cache/adapter contracts.
- `docs/AGENT_OBSERVABILITY.md`: recorder usage, audit meanings and limitations.
- `outputs/agent-observability/`: ignored local DuckDB, blobs and exports.

A blob is content stored under its hash. A canonical request hash covers semantic
request fields and the upstream profile/cache namespace, not volatile transport
IDs. A recorded call ID identifies one actual attempt; identical requests still
produce distinct call records. A lineage edge connects causal steps and is derived
from explicit raw events; proxy timestamps alone cannot prove causality.

## SEOCHO Evidence Contract


workspace_id propagates in tags, raw records and derived exports. The proxy captures
LLM transport facts. Local retrieval/tool results, planner assignments, soft/hard
budget decisions, tier delivery and agent-to-agent returns require explicit adapter
events; these are not inferred solely from model request text. The event-ingestion
contract can be reserved in Phase 1 and completed with decision/realization entities
in Phase 2. Do not call an HTTP-only trace a complete agent execution graph.

Original append-only events and immutable content references are the source for
all derived graphs/metrics. Preserve request and response envelopes, full message
contents, tool schemas/results sent to the model, sampling fields, stop settings,
seed presence and output constraints. Do not remove unknown request fields while
normalizing. Proxy framing changes must be documented; auth/tag headers are not
model-prompt content. Never promise access to provider-private hidden reasoning.

## SEOCHO Review Panel


Professor lens: repeated responses from one cache entry are not independent N
samples. Live repeated experiments therefore default off; prefix replay for
interventions is explicit and downstream samples must be fresh. Same seed/request
hash does not guarantee provider determinism. Cost must distinguish actual
new provider work from inherited/replayed usage. One successful replay proves a
mechanism, not intervention efficacy. Preserve full joint task/seed/sample keys.

Engineer lens: core Recorder must have no OpenAI Agents SDK dependency; adapters
supply context-local headers for concurrent calls. Single-writer serialization
protects DuckDB. Raw call-start and terminal records are appended rather than
updating/deleting originals, including upstream failures and interrupted streams.
Blob creation is atomic and verifiable. Inspection/export do not mutate originals.

Systems lens: capture before forwarding and fail closed when storage fails;
otherwise a recorded run could silently contain unlogged provider work. Never
hold the DB writer lock across network calls. Cache hits still create call records,
identify their source call, and do not masquerade as paid model work. Instrument
capture overhead separately before claiming latency improvements.

## Cost, Latency, and Provider Policy


Phase 1 tests use the deterministic local backend, not MARA or OpenAI. Later live
MARA calls require the user to explicitly request a paid experiment, per the supplied
directive. Upstream URL and credentials are runtime configuration. Do not reuse
or persist credentials from the chat in source, blobs, database or shell files.
Bearer credentials must only go to the configured upstream, not arbitrary request
URLs. Unknown prices give null cost; a configured price schedule needs an ID,
version and effective date. Proxy-served latency and inherited upstream latency
are distinct. Streaming usage remains unknown when the provider does not report it.

## Plan of Work


Phase 1, after confirmation, builds the proxy and local mock endpoint first,
then metadata/blob storage and cache modes, then typed header adapters and offline
acceptance. Supported initial inference routes should include Chat Completions
and Responses; `/models` is discovery rather than an inference token charge.
Record non-streaming bodies and streaming chunks in order, with terminal/disconnect
status. Defaults reject untagged calls and disable response reuse during live work.

Phase 2 adds YAML experiment conditions, templates, tier policies and full
plan/subtask/decision/realization/tool/synthesis/evaluation entities. Resolve the
user's section 9 questions when this phase is authorized. Phase 3 introduces
prefix replay, interventions, N samples and cost limits. Phase 4 derives surrogate
signals and alignment. Phase 5 adds statistical population reports. Phase 6 alone
adds a read-only graph UI. Each phase has its own stop/report/confirmation gate.

Gold handling needs a Phase 2 decision: trusted evaluators receive gold but
inference agents must not. Evaluator request blobs therefore need an isolated
gold/evaluation access domain; logging the same gold into ordinary inference blobs
would violate isolation. The researcher's gold store and ordinary agent logs must
not be conflated. Exact/substring checks flag LEAK_SUSPECTED, not automatically
prove deliberate leakage or remove legitimate supporting facts.

The supplied BAM normalized allocation is generally optimal only when beta is
shared across subtasks. With different beta_j, stationary allocations require
b_j = (c_j beta_j / lambda)^(1/(beta_j+1)) and a common lambda satisfying sum b_j=B.
Validate the constrained optimum in Phase 5 rather than labeling the supplied
normalization a general oracle. Subtask final-answer proxies are not subtask gold.

## Concrete Steps


After module-boundary confirmation, add optional recorder dependencies and the
listed files. Intended interfaces, not existing commands yet:

    uv run --extra recorder python -m seocho.recorder serve --config recorder.yaml
    uv run --extra recorder python -m seocho.recorder audit --run RUN_ID
    uv run --extra recorder python -m seocho.recorder inspect --call CALL_ID
    uv run --extra recorder python -m seocho.recorder export --run RUN_ID --format parquet

Exact command syntax will be finalized with implementation and documented together.
Tests must start a real local proxy and deterministic HTTP upstream; HTTP mocking
also covers deterministic cancellation/failure paths but does not replace local
service acceptance or external compatibility evidence.

## Validation and Acceptance


Run focused tests first and basic CI after behavior/config changes:

    uv run pytest tests/seocho/test_recorder_*.py -q
    bash scripts/ci/run_basic_ci.sh
    bash scripts/ci/check-doc-contracts.sh

Expected behaviors: 100 tagged local calls create 100 distinct attempt records;
repeated prompts/messages deduplicate in the blob layer; repeated semantic requests
hit explicitly enabled cache; replay_only misses error without upstream dispatch;
cache off reaches upstream for independent sampling. Tags do not enter model input.
Concurrent agents retain their own tags. Every request includes all original
sampling/output/seed fields in its canonical hash. Upstream profile isolation
prevents cross-provider/account cache reuse. Errors, cancellation and streaming
partial results have terminal records and unknown usage remains unknown. An audit
compares caller attempt IDs with Recorder records, rejects missing/duplicate records
and separates untagged/replay/evaluation events. This can verify adapter coverage,
not prove the absence of an unrelated uninstrumented process's network traffic.

Gold-aware inference checks and full decisions/realizations are Phase 2 acceptance,
not claimed as complete in Phase 1. Report the exact implemented surface at every
gate. Do not invent tests passed before the corresponding files exist.

## Idempotence and Recovery


Use new ignored run directories without deleting earlier experiment artifacts.
Persist immutable content atomically; incomplete streams or crashes must not create
apparently complete calls. Derived cache indexes/views can be rebuilt from raw
records. One proxy writer owns a DuckDB file; a second writer must refuse or be
explicitly serialized. Preserve failed calls and previous trials. Do not rewrite
existing FinDER reports into purported complete transport records.

## Artifacts and Notes


Source directive: the user's pasted implementation instruction, sections 2, 5,
9 and 10. Module boundaries and dependency availability were inspected locally.
Phase 1 code and actual 100-call acceptance now exist. The explicit next action
is the Phase 1 report and confirmation for Phase 2.

## Interfaces and Dependencies


Core dependencies: Python 3.11+, Pydantic v2, FastAPI/httpx, optional DuckDB, Typer
and an ASGI runner. OpenAI Agents SDK only in the adapter. Future graph/analysis
surfaces derive from raw records, not OTel metrics or the old per-run summaries.
Any ADR and repository ownership/doc updates should land with the corresponding
approved implementation, not assert acceptance of this proposed plan now.

## Phase 1 implementation result (2026-10-09)

Actual loopback acceptance: 100 tagged calls, 99 cache hits, one upstream call,
100 manifest-matched records and deduplicated bodies in actual DuckDB 1.5.6.
Focused tests: 60 passed, including SDK concurrency and evaluation domain isolation.
Current commands and limitations supersede proposed interfaces above; see
[Recorder guide](../AGENT_OBSERVABILITY.md). No paid API rerun. Phase 2 authorized by the user and in progress. Full basic CI: 710 passed, 5 skipped.

## Phase 2 execution plan and decisions (2026-10-09)

Purpose: one YAML defines conditions, model roles, dataset/evaluation paths, seeds
and sample count. Framework-neutral conditions and explicit Recorder events must
explain the delivered information and actual SDK calls, including decomposition,
parallel delegation, returned contexts, synthesis and evaluation.

User decisions: prepare separate agent reference artifacts; use existing fixed
passages rather than new retrieval ranking; prioritize static routing with LLM
routing as a separate condition; no contamination variants available. Existing
FinDER workload and evaluator approach remain authorized. Endpoint is local mock
for acceptance; MARA remains the later live target without an unrequested paid run.

Professor lens: forced and agent-decided policies are distinct conditions. Mock
verdicts prove contracts only. Exact gold substring filtering flags suspicion,
not semantic disjointness; no contamination estimate without variant questions.
Engineer lens: SDK imports stay in adapters; T1/T2 are backend protocols with
frozen evidence implementations. Store explicit decision/realization pairs and
check transport identities rather than infer causality from timestamps.
Systems lens: cache off is required for independent Phase 2 samples. Planner,
router, synthesis and evaluator calls count in the common call cap; provider
cost remains unknown. Soft budget allocation is a declared schedule, not a hard
API cap or an allocation oracle.

Progress:

- [x] Obtain Phase 2 authorization and resolve the current section 9 questions.
- [x] YAML conditions/model maps/seeds/N and versioned packaged prompt template.
- [x] Recorder event ingestion, explicit pairing and transport call links.
- [x] Six strategy variants, forced/agent-decided tiers, static/LLM model routing.
- [x] Fixed passage plugins, separate FinDER agent/evaluation artifacts, leak block.
- [x] SDK Mock matrix tests and first actual loopback acceptance (20 conditions).
- [x] Final acceptance rerun, full CI and Phase 2 report.
- [x] Stop for Phase 3 authorization.

Commands and current limitations are in `docs/AGENT_OBSERVABILITY.md`. Artifacts
are under ignored `outputs/agent-observability/`; no local research data is tracked.
Replay interventions and graph UI remain outside Phase 2. Earlier proposed
module filenames are not normative; implementation uses typed `events.py` and
`eval/agent_observability/` contracts.

Phase 2 outcome: final loopback acceptance passed 20 conditions, 88 calls and 388
agent events. Recorder/harness tests passed 14; basic CI passed 715 with 5 skips.
The isolated MSI reference artifact was prepared without model calls. No paid
provider run, contamination estimate or quality claim. CLI failure manifests and
raw run flags preserve unsuccessful trials. Local `bd status` found no database;
no initialization was performed. The user subsequently authorized Phase 3.

Runtime note: acceptance used the existing Python 3.10.20 repository venv rather
than raising the SDK's Python baseline for this optional research feature. The
directive recommends 3.11+; source remains compatible with the repository baseline.
No wheel build was performed; prompt files are declared in setuptools package data
and execution was validated from the current installed source checkout.

## Phase 3 replay/intervention ExecPlan (2026-10-09)

Purpose: reproduce the frozen causal prefix of a recorded base run, intervene at
a declared subtask boundary and draw N fresh downstream samples. User explicitly
authorized Phase 3. Keep all original calls immutable; derived runs get new IDs.

Decision: use pinned source-call replay, separate from ordinary response caching.
Phase 2 bases have cache off, so a generic cache lookup cannot replay them. The
Recorder verifies the exact canonical request, namespace, domain and workspace
against the selected successful origin. A mismatch fails closed, without provider
work. Prefix replay still creates a new call record with origin ID and zero new
provider cost. Downstream stays cache off.

Causal boundary: independent task branches are already explicit in Phase 2. Freeze
planner, information-choice/router calls and unaffected branches; rerun the changed
solver, synthesis and evaluator. This is branch causality, not a cutoff inferred
from interleaved HTTP timestamps. Freeze non-target fetch outputs as explicit
origin-event references. Schedule changes rerun all solvers; subanswer replacement
replays original solvers then changes returned context; plan replacement replaces
the planner output and reruns all workers. Dependent task DAGs remain unsupported.

Professor lens: prefix usage is inherited, not independent provider work. N samples
have fresh downstream dispatches; mechanism acceptance does not prove an effect.
Engineer lens: intervention specs are typed and source runs are audited before use.
Recorder replay rejects missing/mismatched origins. Explicit base/derived links are
stored in immutable events and an `interventions` SQL view, not mutable originals.
Systems lens: one shared reservation ledger covers N and sweep levels. Token limits
reserve full serialized request bounds plus hard output cap before dispatch. Dollar
caps require a supplied versioned upper-bound token price; no unknown price is
invented. Unknown usage retains reservations and stops subsequent dispatches.

Progress:

- [x] Authorize Phase 3 and inspect the supplied completion contract.
- [x] Pinned replay, snapshot API and immutable base/derived event/view links.
- [x] Budget, schedule, route, tier/reference, plan and subanswer operators.
- [x] N sampling, budget sweep and shared conservative cost admission.
- [x] SDK/mock operator matrix and actual local HTTP repeated knock-in acceptance.
- [x] Final CI and Phase 3 report.
- [x] Stop for Phase 4 authorization.

Validation commands, supported operator semantics and limits are in
`docs/AGENT_OBSERVABILITY.md`. First actual HTTP acceptance: base plus four derived
runs, 25 call records, 8 cache hits, 17 actual mock dispatches. No paid API request.

Phase 3 outcome: reviewed local HTTP acceptance passed five runs, 25 calls and 118
agent events; all eight prefix calls were cached and all twelve derived downstream
calls were new provider dispatches. The actual mock counter was 17 including the
five base calls. Focused tests passed 17; final basic CI passed 718 with 5 skips.
Injected replacement gold was rejected without persisting it to inference metadata.
All operators, source-run plan replacement, sweeps, dollar pricing requirements,
token caps and mismatched replay origins are covered. No paid API or intervention
quality-effect claim. Phase 4 remains pending the end-of-phase confirmation.

## Phase 4 surrogate-signal ExecPlan (2026-10-09)

The user authorized Phase 4. Read section 6 of the supplied directive: matched tier
transitions/retrieval harm, silver consensus and earliest divergence, paired
knock-in rescue, answer-cluster entropy and information/subtask mapping.

Decision: analyze immutable audited Recorder records only. Store each derived job
in a separate DuckDB database, keyed by source fingerprint, algorithm/config and
encoder identity. Identical recomputation is idempotent; changed configuration or
sources creates another version. Do not mutate or backfill original events.

Professor lens: task/seed/sample/design matching avoids Cartesian tier pairs;
unknown/mock verdicts cannot become correct or incorrect labels. Silver support
counts distinct original solver call IDs, not replayed copies, and excludes
interventions. Report unresolved alignment/prefix instead of inventing the first
mistake. Knock-in rescue is a paired observation, not causal proof.
Engineer lens: alignment combines text embedding with position and records its
method, anchor, threshold and confidence. Deterministic one-to-one mapping rejects
ambiguous matches. Versioned encoders are injectable; the default lexical hash
embedding is explicitly not semantic. Separate privileged analysis never supplies
silver/evaluation data to inference agents.
Systems lens: no new LLM call or remote model download is needed for default
analysis. Existing Mock correctness remains unknown; local services validate record
reading/storage only. Every result can be recomputed from its source hashes.

Progress:

- [x] Authorization and supplied signal contract reviewed.
- [x] Read audited records, original cache-origin lineage and final verdicts.
- [x] Alignment, transition, silver/divergence, rescue, entropy and mapping APIs.
- [x] Separate derived DuckDB jobs and CLI analysis/inspection.
- [x] Deterministic mathematical tests and Recorder-backed integration.
- [x] Real stored-run recomputation, full CI and report.
- [x] Phase 5 authorization received.

Known constraint: no local semantic model weights were found and
`sentence_transformers` is absent from the current venv. Lexical entropy is labeled
as such; injectable semantic encoders support the semantic computation contract
without claiming validated semantic quality. Default normalized exact consensus is
conservative. No paid judge is invoked and no Mock score is treated as accuracy.

Phase 4 validation outcome: actual earlier loopback datasets (25 runs) produced
44 subtask alignment rows and 28 information mappings; repeated jobs had identical
IDs. Unknown Mock verdicts yielded no invented correctness-based labels. Scripted
Recorder-backed fixtures verified all positive signal paths without a quality claim.
Eight signal tests and 25 combined focused tests passed. Full basic CI passed 726
with 5 skips. Semantic vector computation is supported with model/revision/file-hash
provenance, but actual pretrained semantic model quality remains unverified. No new
LLM calls or paid provider work. Final report is
`docs/experiments/recorder-phase4-local-2026-10-09.md`.

## Phase 5 — population analysis and report artifacts

The user authorized this phase with “네 다음으로 진행해주세요”.
Professor lens: task/seed pairing, exact McNemar, paired bootstrap and Holm correction;
unknown outcomes remain unknown. Final-error proxies and fitted BAM assumptions are
explicit. Unequal replicate coverage is excluded and one unit has no bootstrap CI.
Engineer lens: pandas APIs expose audited sources, E3/A-over-T, error decomposition,
budget behavior, BAM residuals, constrained allocation and observed tier/route regret.
Best-of-N sums all candidate costs and requires a declared selection policy.
Systems lens: separate logical/physical replay accounting, evaluator separation,
source/code fingerprints and offline static report artifacts; no paid rerun or UI.

- [x] Phase 5 authorization and statistical contract reviewed.
- [x] DataFrame metrics, conservative unknown handling and fair-comparison receipts.
- [x] Budget fit/allocation and explicitly labeled prediction versus measurement.
- [x] Offline `obs report` tables, figures and provenance manifest.
- [x] Recorder-backed acceptance, complete CI and experiment report.
- [ ] Stop for Phase 6 authorization.

Phase 5 acceptance: 7 analytics tests passed; full basic CI passed 733 with 5
skips. Offline report CLI read 25 saved runs across two source stores, with all
unknown Mock verdicts retained. No new provider requests. Report:
`docs/experiments/recorder-phase5-local-2026-10-09.md`.

## Phase 6 — local graph explorer

Authorized by the user: “네 진행해주세요”.
Professor lens: show recorded plan dependencies only; current independent-task
harness has no invented cross-task DAG. Failure highlighting and Sankey use silver
alignment proxies, with unknown states preserved. No model-quality claim follows
from rendered Mock records.
Engineer lens: D3 7.9.0 + d3-sankey 0.12.3 are packaged locally, including licenses;
no browser CDN or frontend build service is needed. Fixed workspace/read-only raw
projections expose graph pages, complete call bodies, derived population tables and
an explicit evaluation-domain switch. Text rendering never treats prompts as HTML.
Systems lens: default collapsed graph, failure-first ordering and bounded pages.
UI binds loopback. Mutation token and same-origin checks protect Mock job submission.
Phase 3 jobs copy the offline source into separate branch stores and use in-process
Mock transports only. Cache namespace is inherited from the source to validate pinned
origins. Paid-provider execution remains disabled in the UI.

- [x] Review Phase 6 contract and authorize execution.
- [x] Read-only graph/call/aggregate projections and packaged local D3 assets.
- [x] Collapsed plan view, metrics/badges, population distributions and Sankey.
- [x] Node intervention form connected to isolated Phase 3 Mock replay.
- [x] Browser acceptance, positive proxy fixtures, packaging and full CI.
- [x] Phase completion report.

Phase 6 validation: 3 UI tests passed; full CI passed 736 with 5 skips.
Browser reference knock-in on the saved source completed after preserving its
original URL/namespace identity. Positive scripted silver/Sankey views were inspected.
Eight static files are packaged. No paid provider calls. Final report:
`docs/experiments/recorder-phase6-local-2026-10-10.md`.

## Post-phase reference audit — official P-and-B (2026-10-10)

The user supplied https://github.com/junhongmit/P-and-B and asked that it be used
as a reference. Inspected weighted/uniform inference and shared budget/driver code.
Professor lens: original solver supplies all prepared steps and word budgets in one
call; current specialist-per-task plus synthesis is an extension, not reproduction.
Engineer lens: original schedules and integer remainder policy differ from current
difficulty-only/last-task remainder behavior. Preserve old recorded semantics and
introduce any reproduction through separately identified conditions.
Systems lens: distinguish offline prepared steps/levels from charged online planner
calls, soft word instructions from token instructions and API output caps. No paid
execution and no imports of the upstream program were needed for the audit.
Detailed source links and experiment-design gaps are in AGENT_OBSERVABILITY.md.


## Live comparison pilot — 2026-10-11

The user authorized actual comparison experiments. Prepared an isolated live profile at `outputs/agent-observability/finder-live-20261011/experiment.yaml`: the existing FinDER case 7e6c28fd, unchanged fixed six passages, eight conditions, one seed/sample, shuffled condition order with seed 42, DeepSeek-V3.1 for every role. All calls use a cache-off Recorder on loopback port 8820; no Neo4j, runtime retrieval, hidden retries or SDK cloud tracing. Gold is evaluator-only. This is a provider/observability pilot, not a population quality comparison or faithful upstream P-and-B reproduction. CoT and upstream single-call reproduction are not included in this eight-condition harness pilot and remain explicit follow-up gaps.

The professor_agent lens favors holding model and evidence fixed and including sequential versus parallel delegation, while interpreting one case only as a pilot. The software_engineer_agent lens requires existing transport and event audits with preserved failed attempts. The computer_systems_agent lens requires provider-reported input/output usage, separately labeled evaluator cost, no pricing invention and no latency superiority claim without replication. Each condition admits at most 20 calls with 1024 output tokens per call (160 calls/163840 output tokens maximum across eight conditions; input usage additional). Actual admission is normally much smaller; provider prices are unavailable, so no dollar cap or dollar cost is claimed.

Credential environment is absent in the current process. Configuration and data preparation are complete, but no paid calls have been issued at this checkpoint. The private Beads database is unavailable (`bd status`: no beads database found); it was not initialized or reset. Retry into a new dated attempt directory after failures; do not overwrite original traces.

Revision note: added the authorized live pilot protocol and current credential/preparation checkpoint.


Revision note (2026-10-11): executed the live pilot on the authorized Tailscale server using its existing credential environment file, in an isolated lab directory. Four of eight conditions completed; provider 429 failures were preserved. All call audits passed; one failed router run had two missing route realizations. Two capitalized evaluator verdicts are not normalized by analytics and remain unknown. See `docs/experiments/finder-recorder-live-2026-10-11.md` for measured results and remaining comparison gaps. No paid jobs remain running; the Recorder writer was stopped and artifacts were copied locally.


## Pi harness comparison — 2026-10-11

User requested comparison against the server command-line Pi harness. Identified the installed package as `@earendil-works/pi-coding-agent` 1.0.0, reachable through the server user-local executable (not default noninteractive SSH PATH). Add an opt-in `harness_backend: pi` experiment configuration and isolated subprocess adapter; default remains OpenAI Agents SDK. The evaluator always uses the common SDK path. Pi inference disables tools, extensions except the explicit Recorder guard, context-file discovery, skills, telemetry, cache warming, retries, compaction and saved sessions. A fresh HOME and agent directory prevent unrelated auth/MCP/session discovery. Every provider request carries Recorder tags, hard token cap, temperature zero and seed; a guard permits one request and checks the Recorder response acknowledgement. Streaming requests remain visible in raw Recorder data; Pi replay is explicitly rejected.

The professor_agent lens separates harness substitution from native Pi tool/plan affordances: first compare standard single-turn inference on unchanged FinDER evidence; later compare native orchestration as a separate factor. The software_engineer_agent lens requires typed JSON parsing, completed single-turn responses, recorded failures and identical evaluation. The computer_systems_agent lens requires bounded requests and process timeouts, original tokens rather than Pi catalog cost, and capture of final actual prompts. SDK structured response formatting and Pi user-carried schema differ on the wire; this is an end-to-end harness comparison, not an identical-payload experiment.

Progress: adapter and five JSON framing/failure tests implemented; focused tests passed 10 including existing harness tests. Remote contract and actual MARA paired comparison are the next gates. Record source fingerprints and provider failures; do not combine old eight-condition results with the new pair.

Revision note: added the user-authorized server Pi harness comparison protocol and opt-in implementation.


Pi comparison outcome (2026-10-11): actual server Pi contract gate passed (one streamed fixture request, tags/acknowledgement, seed/temperature/output cap/usage), then both new MARA single-turn conditions completed and passed call/event audits. Pi: 5716 inference tokens, 72 output tokens, 4.886166 s inference wall time; SDK: 5786 inference tokens, 100 output tokens, 4.874280 s. Both common evaluator judgments are correct. Full final basic CI: 741 passed, 5 skipped, 16 warnings; focused Pi/harness/UI: 13 passed. Doc contracts, ADR index and diff whitespace checks passed. Initial CI attempts failed on restricted Python/uv cache locations; corrected environment and approved cache access completed the full gate. In-process transport and UI Mock jobs reject Pi; default SDK behavior remains unchanged. All paid processes and Recorder writers stopped; a read-only local UI may be started separately. Comparison report: `docs/experiments/finder-pi-harness-live-2026-10-11.md`. Native tool/planning capabilities and generalized advantages are not established.


## Evaluator v2 setup — 2026-10-11

The user requested full evaluator setup for observable domain/reasoning design
comparison and clarified that T/F is FinDER's reasoning column. Add an opt-in v2
rubric and private evaluation object, preserving v1 default/legacy gold strings.
Separate semantic correctness, delivered-source support, citation validity, public
reasoning validity, units/periods and abstention. Typed claims/calculations receive
separate deterministic `verify_receipts` evaluation events; raw judge output is
never rewritten. Public fingerprints and inference events exclude metadata/gold
references. Reports aggregate observed strata by task/seed units, with unknown
and failure exclusions explicit. Original boolean labels are independent of type.

Professor_agent: the dataset confounds reasoning=True with two domains, so compare
architectures within observed strata and never manufacture missing cells or infer
boolean labels from type. Code receipt validity is not semantic support. Engineer:
legacy files and v1 stay compatible, sentinel tests enforce private reference and
metadata isolation, casing normalization fixes known live verdict ingestion.
Systems: offline inventory costs zero API calls; the live v2 gate admits only two
MARA calls with 2048 output tokens each, cache off, gold/evaluator cost separate.
SDK remains default; Pi-native affordances remain a separate experimental factor.

Progress: offline inventory and private case preparation completed; actual MARA
v2 gate completed with call/event audits valid. Final answer/semantic judge pass,
calculation pass, two non-exact table quotes retained as receipt conflicts. The
one-case same-model gate is not calibration or a general quality comparison.
See `docs/experiments/finder-evaluator-v2-live-2026-10-11.md`. No paid process remains
running. Human adjudication/independent judges and repeated within-stratum design
experiments remain needed before selecting a learned router.

Revision note: recorded user clarification, schema inspection, evaluator setup,
private information boundary, actual gate evidence and pending calibration.


Evaluator validation outcome: full basic CI passed 744 tests, 5 skipped, 16 warnings. The subsequently added false-versus-unknown stratum regression passed with all four evaluator tests in the focused run. Earlier focused evaluator/analytics/UI run passed 13 tests. Doc contracts, ADR index and diff whitespace checks passed. Live report retained 5786 inference tokens and 8624 evaluator tokens separately; evaluated one case only. Detailed judging is materially more expensive than the old brief rubric on this case and must be budgeted/calibrated before large sweeps. Both raw audits valid; exact-quote conflicts retained.


BFCL-inspired tool-agent extension (2026-10-11): Scientist lens: keep fixed source
conditions and distinguish deterministic interface checks from semantic model
judgment; do not score arbitrary exact call sequences or uncommitted goal states.
Engineer lens: add an opt-in model-selected SDK tool loop with observable
search/extract/calculate/delegate events, source-ID guards and shared request
admission. Test parallel independent operands and specialist context return through
Recorder. Systems lens: cap physical requests and tool executions independently,
disable SDK cloud tracing, bound root runtime, and reject unsupported Pi/replay.
No database backend or new retrieval policy is introduced.

Validation: focused tool/evaluator/intervention/signals tests passed 17 tests.
Integration fixture covers five tool executions, seven recorded model requests,
parallel operands, specialist return, evaluation-only trajectory judgment and both
raw audits. This fixture is not live MARA compatibility or performance evidence.

Live tool gate completed: one fixed FinDER case, four model-selected tools, five
inference turns plus one evaluator call; no specialist selected. Six expected
adapter call IDs matched Recorder coverage, and all 22 events audited valid.
Same-model evaluator returned correct but invented one claim citation ID and
scored agent handoff without a delegation. Immutable originals remain unchanged;
derived analytics flag the handoff contradiction and keep its pass rate unknown.
Subsequent rubric clarification and budget-exhaustion regression are contract
verified; clarified wording has not received another paid live gate. Details in
`docs/experiments/finder-tool-agent-live-2026-10-11.md`.

Final validation: basic CI passed 748 tests, 5 skipped, 16 warnings; Ruff, doc
contracts, ADR index and diff whitespace checks passed. Live offline report loads
one audited run with no source gaps; tool handoff pass rate stays unknown after
conflict detection. No public commit/push was made in the existing dirty checkout.
