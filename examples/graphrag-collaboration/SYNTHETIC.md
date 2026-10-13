# Budgeted Graph-RAG collaboration

Compare four opt-in orchestration modes on the same MARA model and DozerDB
fixture. The normal `Seocho.ask` route is unchanged. This is a small engineering
workload for inspecting behavior, not a representative benchmark or production
readiness gate.

| Mode | Evidence assembly |
| --- | --- |
| `single` (A) | One retrieval worker, then the fixed answerer |
| `planned_sequential` (B) | Planner, dependency-ordered sequential workers, answerer |
| `planned_parallel` (C) | Planner, concurrent independent workers, answerer |
| `reviewed_parallel` (D) | C plus one reviewer round and additional retrieval |

A uses the same separate answerer as B–D to hold answer synthesis constant.
Workers assemble graph rows, not independent answer proposals. D permits one
bounded repair plan; recursive replanning and answer debate are out of scope.
Each question/mode receives fresh, identical limits. In this first version,
the planner assigns a model-call allowance to each task. The runtime enforces
that allowance and the common ledger, so task budgets cannot expand the global
budget. Token admission and time limits remain shared.

## Workload

`workload.json` defines two companies, three incidents, and four attestations.
Six cases cover single-hop lookup, a three-edge relation path, company comparison,
missing remediation evidence, an as-of date, and conflicting same-date evidence.
Source IDs are synthetic. Gold answers and expected evidence references are used
only for evaluation, never sent to agents. Seeding directly writes known graph
facts, so extraction quality is deliberately held out of this experiment.

The two trusted recipes follow `Company → Incident` and
`Company → Incident → Control → Attestation`. Their labels, relationships,
properties, row limits and workspace filters are validated before graph reads.
Workers choose recipes and permitted company IDs; they cannot execute arbitrary
Cypher. This isolates planning and evidence assembly. Free Text2Cypher can be
studied later as a separate axis after this baseline is validated.

## Run

From the repository root, validate without keys or service calls:

```bash
uv run python examples/graphrag-collaboration/run.py --dry-run
```

Configure `MARA_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in your
shell. `NEO4J_URI` must point to your DozerDB instance. Select a MARA model your
account can access with `--model`. Run a small first live case:

```bash
uv run python examples/graphrag-collaboration/run.py \
  --seed --case path --model MiniMax-M2.5 \
  --workspace-id collaboration-lab-v1 \
  --service-context 'DozerDB VERSION; MARA MODEL; hardware/limits; cold fixture; no warmup'
```

`--seed` explicitly MERGEs this fixture, with dedicated `Collab*` labels, into
the given workspace. Use an isolated experiment workspace. It never deletes
nodes. The preflight checks all fixture query rows against the source data and
fails before paid model calls if they differ. Repeat without `--seed` to reuse
the same graph. Remove `--case path` to run all six cases (24 question/mode runs).
Outputs are written incrementally under ignored `outputs/collaboration/`.

Lower the common budget or select modes:

```bash
uv run python examples/graphrag-collaboration/run.py \
  --case compare --modes single reviewed_parallel \
  --max-calls 12 --admission-units 120000 --timeout 90 --concurrency 2
```

## Budget and reports

The runtime reserves before every Agents SDK model turn, including repair
turns. Reservations remain charged after a failure or missing usage. The
example disables OpenAI client transport retries; injected model clients must
also disable hidden retries if their physical requests are to match this
ledger. Tool invocations share a separate cap. Streaming is unsupported.

`admission_units_estimated` uses serialized UTF-8 request size plus an output
allowance. It is a conservative admission heuristic, **not a strict provider
token ceiling**: tokenizer, wire conversion, and protocol overhead differ.
The per-request output token limit (4096 by default) is sent to the model.
The MARA runner uses JSON-object mode with a prompt schema and the existing
reasoning/fence parser, followed by Agents SDK typed validation, per ADR-0120.
Native JSON-schema support is not assumed. Actual reported input
and output tokens are recorded separately; missing usage remains unknown.
There is no guessed dollar cost. Timeouts cancel local work, but cannot promise
that an external model or an already-running synchronous database query stops
or incurs no charge.

Reports include task dependencies, required slots, relation paths, actual rows,
source IDs, selected references, missing slots, reviewer issues, repair tasks,
claim citations, call receipts and status. Existing reference-based exchange
traces contain metadata only; Agents SDK cloud tracing is disabled. Treat report
files as potentially sensitive when substituting your own workload.

Unknown worker references or uncited/unknown answer references fail the run.
A valid reference proves that the record was retrieved, not that the claim is
entailed by it. Slot completion remains model judgment. Missing slots are
conservatively retained across packets, including repairs. `completed` means
execution completed, not that the answer is correct or complete.

`expected_evidence_recall` and `expected_citation_recall` are diagnostics.
`answer_correctness` stays null pending human or a separately budgeted,
source-grounded judge review. Inspect the supplied gold answers for temporal,
conflict and abstention correctness. Do not treat evidence recall as accuracy.

Before drawing conclusions, enlarge this workload to multiple independent
cases per category, randomize mode order, run repeated trials at several equal
budgets, and evaluate claim entailment. Promote to GraphRAG-Bench or your private
finance corpus for broader validity. Live latency/cost claims require real
MARA/DozerDB runs with service versions, dataset revision, concurrency, hardware,
container limits, warmup, and skipped components recorded.

## Recorder transport requirement

Live runners now require `--recorder-url http://127.0.0.1:8800/v1` or
`SEOCHO_RECORDER_URL`. Start the proxy using the
[Recorder guide](../../docs/AGENT_OBSERVABILITY.md). `MARA_API_KEY` belongs only
in the Recorder environment; graph connection variables remain in graph runners.
Earlier commands and reports predate this mandatory transport requirement.
