# Budgeted Graph-RAG collaboration experiment

This living ExecPlan follows `.PLANS.md`.

## Purpose / Big Picture


Add an opt-in Graph-RAG experiment that compares one retrieval worker, planned sequential workers, planned parallel workers, and reviewer-directed additional retrieval. A graph is the answer substrate: answers cite records returned by bounded, workspace-scoped recipes.

## Progress


- [x] Read repository and Graph-RAG contracts; inspect Agents SDK and MARA adapter.
- [x] Implement typed orchestration, planner-assigned task call allowances, shared admission budget, graph receipts, and reports.
- [x] Add the original synthetic regression fixture and MARA/DozerDB entry point.
- [x] Switch the primary workload to an original-10-K FinDER pilot with seeded selection, keyword search, source preflight and gold-only evaluation.
- [x] Validate 40 real FinDER cases and 40 source filings (6669 passages) without model or graph calls.
- [x] Add 33 offline regression tests covering orchestration and FinDER integration.
- [x] Run focused tests and basic CI; record live gaps.
- [x] Add native SDK Manager/as_tool and Router/handoff modes with fixed packet context, shared nested budgets and transition metrics.
- [ ] Run real MARA/DozerDB comparison after service credentials are configured.

## Surprises & Discoveries


The generic execute_cypher tool does not enforce a workspace filter. This experiment instead exposes only host-owned validated query recipes. The finance tutorial is onboarding-only. The user selected FinDER as the primary workload; the synthetic incident data remains a regression fixture. Official FinDER supplies both parquet QA records and a 10-k.zip HTML archive. Existing backbone experiments often ingest gold snippets; this experiment instead constructs the graph solely from original visible HTML. Query-ticker resolution excludes name-only and ambiguous cases; IR collided with an interest-rate acronym during pilot review and now requires explicit company context.

## Decision Log


DEV-DECISION: Use a seeded 40-case FinDER pilot across single-reference, multi-reference, compositional and numeric slices as the primary workload. Retain synthetic incident data for deterministic regressions. Gold reference text and answer membership never shape the source graph or retrieval ranking. DEV-CONSTRAINT: Keep the public SDK ask path unchanged. Use Agents SDK structured outputs and MARA Chat Completions. Use a shared, event-loop-atomic reservation before each model request; retain reservations when usage is absent. Reservation units estimate request size and are not an exact model tokenizer guarantee. Disable hidden transport retries in the example.

## Outcomes & Retrospective


The opt-in FinDER implementation and offline validation are complete. Focused tests pass (33). The real-data dry-run selects ten cases per slice, forty original filings and 6669 source passages. Gold mapping succeeds for 45 of 51 reference spans; the six unmapped references remain explicit evaluation gaps. The resolver excludes 2242 upstream cases and seven lack required evaluation fields, so the pilot is not representative of all FinDER. Basic CI passes (683 tests, five existing skips); exact results are recorded below. Real MARA/DozerDB execution remains unverified because MARA_API_KEY and NEO4J connection variables are unset in the current shell. No quality, latency, throughput, or production claim is authorized by deterministic tests.

## Context and Orientation


src/seocho/agent/collaboration.py owns orchestration. src/seocho/eval/collaboration.py owns trusted read recipes. src/seocho/eval/finder_collaboration.py owns source parsing, seeded selection, gold mapping and the financial read boundary. examples/graphrag-collaboration/run_finder.py owns the primary FinDER CLI; run.py and workload.json remain synthetic regressions. A task is a bounded subquestion; a relation path is the graph edge sequence needed to answer it; provenance is the source ID returned in a query row.

## SEOCHO Evidence Contract


DEV-API-CONTRACT: Each task carries required slots, relation paths, earlier-task dependencies, and a model-call allowance. Packets retain actual query rows, evidence IDs, missing slots, task IDs, and workspace identity. Worker references must resolve to its retrieved rows. Answer references must resolve to the assembled bundle. Missing slots survive synthesis. Model-proposed slot fills remain model judgments, not verified facts. Traces contain counts and references, not question or row contents.

## SEOCHO Review Panel


Professor lens: compare configurations on identical original-file evidence, separate conditional reference coverage from answer correctness, retain unmapped gold, and disclose ticker-resolution exclusions. Reference-count and type slices are proxies for workload structure, not verified graph-hop difficulty. Engineer lens: typed outputs, validated DAGs, runtime-bound recipes and citation validation. Systems lens: reserve before parallel dispatch, cap time and calls, disable hidden retries, disclose unknown usage. A live MARA/DozerDB run is necessary to falsify interface assumptions and measure performance.

## Cost, Latency, and Provider Policy


MARA via the existing provider spec and Agents SDK, DozerDB via Neo4jGraphStore. No embeddings or OpenAI model calls. Report observed usage separately from estimated reservation units and elapsed time; no guessed USD prices. Timeout cancellation cannot guarantee provider-side cancellation or absence of billing.

## Plan of Work


Implement the orchestrator and shared admission ledger, trusted recipe retriever, FinDER original-source graph and primary CLI. Build the graph once per corpus, randomize mode order deterministically and preserve source snapshots across all modes. Add regression coverage for concurrent budget admission, DAG errors, citations, missing evidence and reviewer retrieval. Update the handoff spec and ADR index.

## Concrete Steps


From the repository root run:

    uv run pytest tests/seocho/test_collaboration.py tests/seocho/test_finder_collaboration.py -q
    uv run python examples/graphrag-collaboration/run_finder.py --download --dry-run
    bash scripts/ci/run_basic_ci.sh

Live command and required environment will be documented in the example README.

## Validation and Acceptance


DEV-ACCEPTANCE: All deterministic tests pass; dry-run validates workload without credentials or service calls. Live acceptance requires seeding the isolated workspace in DozerDB and running all four modes through MARA, inspecting JSON packets, usage, statuses and citation coverage. Live service versions and container limits must accompany any published comparison.

## Idempotence and Recovery


Original-filing source seeding uses MERGE on workspace, corpus identity and stable source IDs. Verify source contents and topology before paid model calls. No deletions or production ask-path changes. Outputs go to ignored outputs/. Repeating a run produces a separate report. Do not reset an existing graph.

## Artifacts and Notes


No credentials, generated reports, or editor state enter version control. Validation on 2026-10-05:

    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync pytest tests/seocho/test_collaboration.py -q
    # 18 passed
    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync ruff check src/seocho/agent/collaboration.py src/seocho/eval/collaboration.py tests/seocho/test_collaboration.py examples/graphrag-collaboration/run.py
    # All checks passed
    PATH="$PWD/.venv/bin:$PATH" PYTHONPYCACHEPREFIX=/tmp/seocho-pycache UV_CACHE_DIR=/tmp/seocho-uv-cache UV_NO_SYNC=1 bash scripts/ci/run_basic_ci.sh
    # 668 passed, 5 skipped; ownership/root/docs lint gates passed
    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync python examples/graphrag-collaboration/run.py --dry-run
    # six cases, four modes; no external calls
    bash scripts/ci/check-doc-contracts.sh
    .venv/bin/python scripts/ci/check_adr_index.py
    git diff --check
    # all passed

The writable temporary cache overrides and existing virtual environment avoid sandbox-restricted global caches. Initial attempts using global caches failed; temporary cache overrides solved them. Downloading the official public FinDER parquet and HTML archive required approved network escalation after sandbox DNS failed. No automatic approval rejection occurred. The working branch is feat/memory-framework-benchmark; changes remain local for review. No landing or service writes occurred.

## Interfaces and Dependencies


OpenAI Agents SDK Model, Runner, Agent, function_tool and Pydantic typed models. GraphStore.query must support workspace_id and enforce_workspace_filter. Models are injected for offline contract tests; these tests are never live evidence.


Revision note (2026-10-05): Completed the first implementation, added MARA JSON-object compatibility via the existing robust parser, task call allowances, retained failure receipts, and recorded offline results and the live service gap.

FinDER continuation validation (2026-10-05):

    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync pytest tests/seocho/test_collaboration.py tests/seocho/test_finder_collaboration.py -q
    # 33 passed
    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync python examples/graphrag-collaboration/run_finder.py --dry-run --output outputs/collaboration-finder/pilot-40-dry-run.json
    # 40 cases, 40 original filings, 6669 source passages; no graph/model calls

The latest source manifest has corpus_id finder:66e63a1c8fe1369f3065abad; dataset SHA-256 213395beb38a075dcc7b23f8e213ccbd2768df0f5c615479c33e10ce8cc267f1 and archive SHA-256 4e1bff2b5e37ba3429d3ddb8a15d447c7903d4fafd59a4efdbefba08a2036aa0. These identify source bytes, not live service versions. No MARA or graph service claim follows from this dry-run.

Revision note: Promoted FinDER to the primary workload at the user’s request, kept synthetic fixtures for regressions, corrected ticker/acronym ambiguity, allowed the single worker to spend the common call budget, and separated exact gold mapping gaps from model errors.

Final FinDER validation:

    PATH="$PWD/.venv/bin:$PATH" PYTHONPYCACHEPREFIX=/tmp/seocho-pycache UV_CACHE_DIR=/tmp/seocho-uv-cache UV_NO_SYNC=1 bash scripts/ci/run_basic_ci.sh
    # 683 passed, 5 skipped; runtime/module/root contracts and agent docs lint passed
    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync ruff check src/seocho/agent/collaboration.py src/seocho/eval/finder_collaboration.py examples/graphrag-collaboration/run_finder.py tests/seocho/test_collaboration.py tests/seocho/test_finder_collaboration.py
    bash scripts/ci/check-doc-contracts.sh
    .venv/bin/python scripts/ci/check_adr_index.py
    git diff --check
    # all passed

MARA_API_KEY, NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD remain unset in the current shell. The paid/live gate was not run and source graph writes were not issued. Implementation and real-source dry-run preparation are complete; service compatibility, answer entailment and measured costs/latency remain unverified.


Revision (2026-10-05): The user authorized native SDK coordination comparisons.
Add sdk_manager and sdk_handoff to MODES and both CLIs. EvidenceCoordinator
executes validated tasks through a typed tool and calls the existing worker.
Manager delegates via Agent.as_tool and receives actual packets; Router transfers
control via handoff with a filtered host-owned context. The same Answerer runs
outside both coordinator loops. Manager delegated free text is replaced by the
host packet context to hold the context policy fixed. Keep all nested calls,
control tools and handoffs inside the existing ledger. SDK-wrapped budget errors
are classified through exception causes. Record versioned agent definitions,
actual SDK transitions, duplicate retrieval and per-request context byte sizes.
Professor lens: this compares whole coordination designs; it does not isolate
agent count or parallelism. Engineer lens: real SDK scripted-model tests prove
handoff/as_tool contract behavior and nested budget exhaustion. Systems lens:
extra coordination turns spend the same budget, and full accumulated input is
bounded on every turn. Full-history/summary context sweeps remain future axes.
Beads CLI is installed but bd list reports no local database; no tracker reset
or initialization was attempted. The ExecPlan retains this implementation state.


Native coordination validation (2026-10-05):

    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync pytest tests/seocho/test_collaboration.py tests/seocho/test_finder_collaboration.py -q

Observed 38 focused tests passing. Four added parameterized cases execute the
real Agents SDK runner with scripted model responses: Manager/as_tool and
Router/handoff, each with native structured output and MARA JSON-object mode.
They validate actual control flow, fixed delegated context, receipt accounting,
workspace propagation, citation grounding and shared nested budget exhaustion.
An additional test proves accumulated context is rejected before model dispatch.
These are offline contract tests, not MARA compatibility or quality evidence.

    PATH="$PWD/.venv/bin:$PATH" PYTHONPYCACHEPREFIX=/tmp/seocho-pycache UV_CACHE_DIR=/tmp/seocho-uv-cache UV_NO_SYNC=1 bash scripts/ci/run_basic_ci.sh

Basic CI passed with 687 tests and five skips. The subsequent context-boundary
test and agent-design max_turns metadata passed the focused command and Ruff.
The CI log is /tmp/seocho-native-coordination-ci.log. Module/runtime/root and
agent-doc contract checks passed. Ruff and git diff --check passed.

    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run --no-sync python examples/graphrag-collaboration/run_finder.py --dry-run --modes planned_sequential sdk_manager sdk_handoff --output outputs/collaboration-finder/native-coordination-dry-run.json

Real FinDER preparation succeeded: 40 cases, 40 filings, 6669 passages. The
manifest records the requested three coordination modes. No graph or provider
calls occurred. MARA_API_KEY, NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD remain
unset in this shell; live compatibility, answer quality and latency/cost are
still gaps. No push or commit was performed.


Execution follow-up (2026-10-05): The user authorized all remaining execution.
Prepared the six-mode, forty-case, three-repeat comparison (720 trials) in
outputs/collaboration-finder/full-comparison-preflight.json. Original data again
produced forty filings and 6669 passages. Saved a local execution sequence in
outputs/collaboration-finder/execution-plan.json: first four-case/six-mode smoke,
then full repeated comparison in a separate experiment workspace. Attempted the
live smoke command; the CLI exited before graph writes or provider requests
because MARA_API_KEY, NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD are absent.
No configured local credential file was found in the project; the Docker daemon
socket is absent as well. Requested the existing service configuration path
from the user without asking them to paste API keys in chat. Live compatibility,
performance and answer quality remain unverified. Final basic CI including the
new context-boundary test passed (688 tests, five skips); log is
/tmp/seocho-collaboration-final-ci.log. Do not mark the live milestone complete
until the same FinDER workloads actually run against MARA and DozerDB.


2026-10-09 follow-up: Added examples/graphrag-collaboration/mara_tool_smoke.py
for the requested MARA-only Agent.as_tool live check. It uses independent random
context/reply markers, verifies actual specialist tool input, specialist return,
and Manager final response; no Neo4j imports or service calls are needed.
Attempted its default CLI command. MARA_API_KEY remains absent, and the command
stopped before provider dispatch. Ruff passed; live validation remains pending.


2026-10-09 live milestone: The user supplied MARA credentials and the official
MARA documentation URL. Keys were handled through hidden process input without
saving them. The documented MiniMax-M2.7 and default MiniMax-M2.5 each returned
422/model_not_in_plan. Querying the model-list endpoint identified DeepSeek-V3.1.
The MARA-only single-specialist context/return probe passed with four real model
calls. Added --parallel to the standalone smoke script: Supervisor constructs
two delegated task descriptions, calls two specialist agent tools, and must
return their distinct markers exactly. This passed with six actual model calls
and overlapping invocation intervals. Actual receipts are local ignored outputs;
public interface findings and scoped evidence are documented in
 docs/experiments/mara-agent-tools-live-2026-10-09.md.
The two smoke probes have different caps and only one trial each; they do not
constitute a fair design comparison. FinDER supervisor-parallel and decentralized
arms remain a separate implementation milestone; do not conflate the standalone
probe with the existing single-coordinator sdk_manager arm. Graph/live FinDER
quality and performance remain unverified. No credential is committed.


2026-10-09 fixed-context milestone: The user authorized A–E comparison on one
FinDER case. Implemented src/seocho/eval/fixed_context_designs.py and
examples/graphrag-collaboration/run_fixed_finder.py. Standard and CoT each perform
one generation; decomposition-only uses one Solver with plan/result-recording
tools and no delegated agents. Sequential delegation reuses one Specialist
identity with fresh bounded calls; multi-delegation uses separate Specialist
identities and records actual overlap. All arms receive the same six original
passages selected from question terms for MSI case 7e6c28fd. Final Answer schema,
model and shared limits are identical; instructions and inferred plans differ.
The first live attempt exposed insufficient task-ID guidance and provider 429.
Explicit ordered ID descriptions were added to plan schemas/instructions; first
attempt artifacts are preserved. Focused offline contract tests passed (49),
including identity validation, same-evidence delivery, sequential versus parallel
execution, unknown citations, and incomplete decomposition. Extended basic CI.
Second live attempt is in progress; service-side 429 remains an observable gap,
not a mock result. Candidate outputs freeze before separately budgeted same-model
judge evaluation. This initial workload does not establish general design quality.


Fixed-context execution discovery: After explicit task-ID instructions, the
second attempt completed decomposition-only, multi-delegation and Standard.
The single-Specialist path obtained two factual returns but a third request
failed with provider 429; CoT also received 429. One evaluator request failed.
Preserved those artifacts. Added a transparent bounded 429 retry wrapper around
BudgetedModel so every attempt is separately reserved in the same ledger and
failed unknown usage is retained. All designs use the same three-attempt policy
with five/ten-second waits; timeout caps are unchanged. Evaluator has its own
three-call limit. A deterministic test verifies retries consume shared calls.
Focused tests now pass (50). Third five-arm execution is in progress with the
same case, evidence and model, and the revised common retry policy.


Five-arm live acceptance (2026-10-09): All final-protocol arms completed on case
7e6c28fd with the same six-passage evidence hash. Model dispatches A/B/C/D/E were
1/1/4/8/6, including one 429 retry for E; its failed usage remains unknown.
D planned three subtasks; C/E planned two, so this is a complete-design comparison
rather than isolated scheduling. E's two delegated intervals overlapped by
4.006 seconds; D was sequential. All five separate model judgments completed
and marked the answers correct and supported. Primary operands were independently
checked against source p69: 2294 - 1661 = 633 million, about 38.11% growth.
Gold reference matching yields zero coverage because the alternative supporting
table lies outside the gold-mapped p91/p92 span. This limitation is preserved,
not patched by selecting gold passages. The public evidence report is
 docs/experiments/finder-five-designs-live-2026-10-09.md; detailed local receipts
are outputs/collaboration-mara/finder-five-designs-live-r3-20261009.json.
Initial contract/provider failures and the second attempt remain preserved.
Focused tests pass (51). Final basic CI passed (700 tests, five skips) before
the final gold-invariance test, which passed with the focused command. Ruff,
ADR-index, docs contracts and git diff --check pass. No performance/general-quality
claim is made from this single case. Graph comparison milestone remains pending.
