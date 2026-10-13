# FinDER Graph-RAG planning and collaboration

FinDER is the primary workload for this opt-in experiment. The small
[synthetic incident workload](SYNTHETIC.md) remains a regression fixture for
budget exhaustion, missing evidence, and contradictions.

Compare A–D on the same model, question, original-filing graph, and fresh equal
limits: `single`, `planned_sequential`, `planned_parallel`, and
`reviewed_parallel`. A uses one retrieval worker and the same separate answerer
as B–D. D adds one bounded review/repair round. The normal SDK answer route is
unchanged. Planner tasks carry dependencies, required slots, relation paths and
call allowances; the runtime enforces those allowances plus global limits.


Two native SDK coordination modes extend those code-scheduled baselines:

| Mode | Control flow |
| --- | --- |
| `sdk_manager` | Manager calls EvidenceCoordinator through `Agent.as_tool()`, inspects returned packets, and may delegate again. |
| `sdk_handoff` | Router transfers control through SDK `handoff()` to EvidenceCoordinator. |

EvidenceCoordinator proposes and executes bounded tasks through `execute_task`.
Both modes reuse the same RetrievalWorker and separate Answerer as A–D. They
are sequential coordinator loops; they do not claim to isolate parallelism or
agent count. Additional coordinator turns spend the same shared budget.
Native transfers, agent-tool calls and task dispatches also spend the tool budget.

The initial context policy is fixed: host-owned question, slots, recipes and
anchors, plus completed evidence packets on subsequent delegation. Manager
free-form delegated text is replaced by that packet context. Handoff filtering
passes the same trusted initial context and excludes router conversation history.
Workers continue receiving only their task and dependency packets. Full-history
and summarized-history policies are future independent comparison axes.
SDK intra-agent tool history is retained, and its serialized UTF-8 size is checked
before every model dispatch; exceeding the bound fails explicitly rather than
silently truncating evidence.

Reports record `coordination_events` (actual SDK handoffs and agent-tool calls),
`agent_designs` (versioned instructions, tools, output types and call limits),
`context_policy`, per-call `context_bytes`, and `coordination_metrics` with task,
transfer, duplicate-read and total context-byte counts. Context bytes are not
provider tokens. Duplicate reads mean repeated recipe/anchor/search tuples;
quality diagnostics remain separate from coordination activity.

To compare coordination patterns with identical resource settings:

```bash
uv run python examples/graphrag-collaboration/run_finder.py \
  --modes planned_sequential sdk_manager sdk_handoff --repeats 3
```

## Inputs and pilot

Use the official [Linq-AI-Research/FinDER dataset](https://huggingface.co/datasets/Linq-AI-Research/FinDER)
and its `10-k.zip` original HTML archive. The upstream dataset card supplies
attribution and usage terms (CC BY-NC 4.0). Dataset files and generated artifacts
stay in ignored local directories; no upstream records are bundled here.
See [the paper](https://arxiv.org/abs/2504.15800) for the benchmark definition.

The default pilot chooses ten questions each from mutually exclusive slices:

| Slice | Selection rule |
| --- | --- |
| `single_reference` | One reference, non-compositional and non-arithmetic |
| `multi_reference` | Multiple references, non-compositional and non-arithmetic |
| `compositional` | Upstream `type=Compositional` |
| `numeric` | Addition, subtraction, multiplication or division type |

Selection is seeded and uses category round-robin sampling. These are workload
proxies, not verified graph-hop difficulty labels. The resolver uses supported
uppercase ticker symbols in the **question**, never the answer or gold passages.
One-character symbols are excluded; common acronym collisions such as `IR`
require an explicit company context. Name-only or unresolved questions are
excluded and counted in the manifest. This pilot is not representative of every
FinDER query. Review the case manifest before a paid run.

The source corpus consists of all original HTML text for companies named by the
selected questions. Other passages in those filings remain distractors. Gold
reference count/type may affect sampling, but gold text is never used to build,
rank, or filter the graph. Dataset and document archive SHA-256 hashes, selected
case IDs, source corpus identity, exclusions, and parser settings are saved.

## Prepare and run

From the repository root, download official inputs and prepare the 40-case
manifest without graph or model calls:

```bash
uv run python examples/graphrag-collaboration/run_finder.py --download --dry-run
```

Inputs default to `.seocho/datasets/finder/data/train-00000-of-00001.parquet` and
`.seocho/datasets/finder/10-k.zip`. If already downloaded, omit `--download`.
Explicit `--dataset` and `--documents` paths are supported. Dataset input can be
upstream parquet or JSON with raw/normalized FinDER rows. Parquet requires the
`pyarrow` dependency, which is included in the repository dev dependencies;
the existing installation may already have it. Neither pandas nor the datasets library is required for local parquet.

Configure `MARA_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in your
shell. `NEO4J_URI` should point to DozerDB. Build the source graph once and run
all six modes:

```bash
uv run python examples/graphrag-collaboration/run_finder.py \
  --seed --model MiniMax-M2.5 \
  --workspace-id finder-collaboration-lab-v1 \
  --service-context 'DozerDB VERSION; MARA MODEL; hardware/container limits; warmup/skips'
```

Start with a smaller manifest if needed: `--per-slice 1 --dry-run` prepares four
questions. Use that same selection for its first `--seed` run. `--case ID`
selects one eligible upstream question and the companies it names. Changing
selection, documents, or chunk settings can change the corpus ID and requires
seeding that corpus. Reusing the same configuration without `--seed` verifies
and queries the existing graph. Seeding MERGEs dedicated `Fin*` nodes and edges
in an isolated experiment workspace and never deletes data.

Use `--repeats 3` for repeated trials or `--modes single reviewed_parallel`
for a focused comparison. Mode order is shuffled deterministically inside each
case/repetition. The graph is frozen across modes. Outputs are written atomically
and incrementally under `outputs/collaboration-finder/`. Reports include the
manifest, source verification identity, seed time, per-run limits, task packets,
retrieval receipts, role-level call times/usage, claims and evaluation diagnostics.
The report's `expected_answer` is added **after** agent execution for review.

## Retrieval and evidence

This first version uses a typed document graph:

`Company → Filing → FilingSection → Passage`

Visible HTML is parsed without execution or archive extraction. Hidden inline
XBRL duplicates, scripts and styles are excluded; table row boundaries are
preserved. Sections are identified heuristically from SEC Item headings; unknown
sections remain visible. Chunking is fixed at 3500 characters with 350-character
overlap. The graph preserves text, source archive member and character offsets.
It does not yet extract FinancialMetric facts or verified arithmetic receipts.

Workers choose bounded trusted recipes for all filing passages, financial
sections (Items 7/7A/8), or risk disclosures (Item 1A). They can issue subquestion
keyword searches; an empty search defaults to the original question. Ranking is
a deterministic keyword-overlap heuristic over original text, capped at four
rows per tool call. Sections can be missing or misclassified; the all-passages
recipe is the fallback. There are no embedding calls or free Text2Cypher in this
comparison. The canonical Cypher validator checks each read recipe and every
participating node/edge is workspace-scoped and corpus-bound.

Before paid calls, preflight compares all passage contents/offsets and graph
paths with the source-derived corpus. Missing, partial, changed, or extra source
rows and broken topology fail the gate. No answer or query-to-gold membership is
stored in graph nodes or model prompts. Claim references must resolve to actual
retrieved rows; that check does not establish claim entailment.

## Budgets and evaluation

Each case/mode gets the same global call, tool-call, time, context and estimated
admission budget. Task call allowances cannot enlarge it. Model turns, including
tool follow-ups, pass through the ledger before dispatch. Transport retries are
disabled. `--max-calls`, `--admission-units`, `--output-tokens`, `--timeout`, and
`--concurrency` control limits. Admission units estimate serialized UTF-8 request
size plus an output allowance; they are **not an exact provider-token ceiling**.
Actual tokens are reported separately; absent usage stays unknown. Local timeout
cancellation cannot guarantee cancellation or absence of remote billing.

MARA uses JSON-object output plus the existing reasoning/fence parser, then SDK
type validation. Agent cloud tracing is disabled; reference exchange telemetry
is content-free. Local reports contain source text and should stay private when
using non-public inputs. Keep the model and all limits fixed when comparing modes.

After execution, gold passages are mapped to exact whitespace-normalized source
spans. `retrieved_reference_coverage`, `selected_reference_coverage` and
`cited_reference_coverage` measure complete span coverage on mapped references.
Overlapping chunks are alternatives. Unmapped gold is counted separately, and
coverage is null when none can be mapped; unmapped gold is not a model failure
and must not silently disappear from a quality report.

Exact/contains answer matches and existing answer-slot scores are diagnostics.
`answer_correctness` remains null pending manual or separately budgeted judge
review of entailment, units, arithmetic, dates, and abstention. Numerical answers
are instructed to state operands, operation, units and periods. This version
cannot guarantee arithmetic correctness. Do not equate string matches or
reference coverage with answer accuracy.

Source parsing and dry-run preparation can be verified with real FinDER locally.
External compatibility, answer quality and latency/cost comparisons still require
actual MARA/DozerDB runs, service versions, dataset hashes, concurrency, hardware,
container limits, warmup and skipped components. The workload manifest records
excluded cases so conclusions stay scoped to the selected pilot.


## MARA-only agent-as-tool smoke

No graph service is needed for this live context and reply probe:

```bash
uv run python examples/graphrag-collaboration/mara_tool_smoke.py --model DeepSeek-V3.1
```

Configure MARA_API_KEY locally before running. Manager receives a fresh context
marker and must pass it to a specialist through Agent.as_tool. Only a local tool
available to the specialist knows a separate fresh reply marker. Success requires
exact marker preservation in the specialist response and the Manager final
response. All model calls use MARA, tracing is disabled, retries are disabled,
and a shared eight-call budget applies. The local tool is a probe fixture;
a successful run establishes this MARA/SDK exchange, not graph compatibility.
The report records observed usage and verified steps under
outputs/collaboration-mara/tool-smoke.json. Missing credentials stop before any
provider calls and do not produce a live-success report.


For a supervisor that decomposes two independent requests and dispatches two
specialist agent tools in parallel:

```bash
uv run python examples/graphrag-collaboration/mara_tool_smoke.py \
  --parallel --model DeepSeek-V3.1 \
  --output outputs/collaboration-mara/parallel-smoke.json
```

Each specialist receives a distinct context marker and private reply. The live
probe requires correct returns, exact Supervisor aggregation, and overlapping
agent-tool invocation intervals; a sequential run fails the parallel gate.
This small, preidentified two-task workload verifies dispatch and integration;
it does not establish arbitrary decomposition quality or FinDER accuracy.
See [the 2026-10-09 live report](../../docs/experiments/mara-agent-tools-live-2026-10-09.md).
Model availability depends on the account plan; the model-list endpoint is more
reliable than assuming every model in the documentation is enabled.


## Five designs on one fixed-context FinDER case

Run Standard prompting, CoT prompting, single-agent decomposition, sequential
single-Specialist delegation, and parallel multi-Specialist delegation without
a database:

```bash
uv run python examples/graphrag-collaboration/run_fixed_finder.py --dry-run
uv run python examples/graphrag-collaboration/run_fixed_finder.py \
  --model DeepSeek-V3.1 \
  --output outputs/collaboration-mara/finder-five-designs.json
```

The default case is `7e6c28fd`: “Change in operating earnings for MSI in 2022-23.”
Six original 10-K passages are selected once using question terms, never expected
answers or gold reference text. The same evidence bytes, model, final Answer
schema and full-run resource caps are used in every arm. There is no runtime
search and no graph service. The fixed passage hash is saved in the report.

The Standard and CoT arms each request one answer generation. CoT differs only
in a private stepwise-check instruction; private reasoning is not exposed or
scored. Decomposition-only uses a single Solver that declares two or three
subtasks, performs each itself, records its own partial result through a local
recording tool, and generates the final answer. Recording tools do not call LLMs.
Single delegation reuses one Specialist identity with fresh task contexts and a
host lock enforcing sequential execution. Multi delegation uses separate
Specialist identities and asks the Supervisor to call independent tools in the
same turn. Both return typed findings, evidence references and missing fields to
the Supervisor. Every Specialist receives the same full fixed evidence bundle.
Overlap is observed, not assumed, and is recorded per run.

The plan uses explicit `task_1`, `task_2`, optionally `task_3` IDs. Plans can differ
across C/D/E: this compares complete designs including planning, not just the
scheduler. Limit controls are currently fixed in the CLI: 24 model dispatches,
24 tool calls, 2048 output tokens per call, 100000 serialized context bytes,
1000000 estimated admission units and 120 seconds per arm. All nested calls
spend the same ledger; low-call designs are not padded with extra generations.
Transport retries are disabled. Explicit 429 retries allow at most three attempts
per model turn with five/ten-second waits. Each attempt reserves another shared
call and retains unknown failed usage. Retries do not extend the wall-time cap.
Consequently observed token totals may be incomplete after failed requests.

Mode order is shuffled with seed 42. All candidate outputs freeze before gold is
used by the separately budgeted evaluator. Each judgment has a three-dispatch,
ninety-second cap and uses the same model at temperature zero; self-preference
bias and evaluator failure remain explicit. `--no-judge` skips this stage.
Provided-reference coverage means whether the frozen input covered mapped gold;
cited-reference coverage measures the candidate citations. String/slot matches
are diagnostics; model judgments do not become verified ground truth.

One case and one accepted trial per arm cannot establish generalized quality,
latency or cost superiority. Keep failed prototype attempts and service errors;
do not silently replace them with successful rows. This slice is separate from
retrieval/planning experiments in run_finder.py and does not modify normal SDK
answering. Dataset inputs must already be present; reuse run_finder.py --download
--dry-run to fetch official data into the ignored cache.


The [2026-10-09 five-design pilot report](../../docs/experiments/finder-five-designs-live-2026-10-09.md)
records the completed MSI case, preserved failures, shared budgets, actual parallel
execution, numerical answer and evaluation limitations.

## Recorder transport requirement

Live runners now require `--recorder-url http://127.0.0.1:8800/v1` or
`SEOCHO_RECORDER_URL`. Start the proxy using the
[Recorder guide](../../docs/AGENT_OBSERVABILITY.md). `MARA_API_KEY` belongs only
in the Recorder environment; graph connection variables remain in graph runners.
Earlier commands and reports predate this mandatory transport requirement.
