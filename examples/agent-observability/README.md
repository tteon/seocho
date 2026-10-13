# Local research Recorder

See [the Recorder contract](../../docs/AGENT_OBSERVABILITY.md) for setup,
inspection, cache modes and limitations. `recorder.mock.yaml` is the local profile;
`recorder.mara.yaml` loads upstream credentials from the environment. Both default
to cache off. `acceptance.py` uses a fresh temporary profile with cache enabled to
verify 100 distinct records, 99 cache hits and content deduplication over real local
HTTP servers. No paid API is required.

Phase 2: `experiment.mock.yaml` defines six strategy variants × three forced tiers
plus two agent-decided routing conditions. `acceptance_phase2.py` starts real local
services and audits all decision/realization pairs and transport links. The mock
cases and evaluation answer are separate files. The local FinDER preparation and
YAML execution commands are in the contract above; no paid run is automatic.

Phase 3: `acceptance_phase3.py` runs a base case and repeats the same reference
knock-in twice with N=2 over real local servers. Prefix origins are pinned to the
base calls; downstream cache reuse is disabled. Inspect the same Recorder events
and CLI reports for base↔derived links and cost reservations.

Phase 4: after stopping the Recorder writer, use `python -m seocho.recorder signals`
with a workspace, source directory, separate derived DuckDB path and optionally
`signals.yaml`. Inspect rows with `inspect-signals`. Analysis never supplies silver
labels to inference agents. Mock correctness is unknown. The default lexical
embedding is labeled as a proxy; exported local semantic vectors can be supplied
with model/revision/content-hash provenance.

Phase 5: `obs report report.mock.yaml` produces offline CSV tables, PNG figures
and a source/code provenance manifest. Unknown outcomes and prices remain unknown.
Adjust the YAML source/output paths for your saved acceptance dataset.

Phase 6: `obs ui --workspace WORKSPACE --directory SOURCE_STORE --port 8810`
opens the local explorer at `http://127.0.0.1:8810`. Stop the Recorder writer first.
Add `--experiment EXPERIMENT.yaml` to enable isolated Mock intervention jobs.
For legacy records missing upstream identity, add
`--origin-recorder-config ORIGINAL_RECORDER.yaml` from the saved run. The graph
starts collapsed; select a node to inspect complete requests/responses. Population
view shows derived proxies and explicit gaps. The browser needs no CDN. No UI
operation invokes a paid provider; original records remain read-only.

Optional Pi comparison: the default `harness_backend` is `openai_agents`. Set
`harness_backend: pi`, `pi_executable: pi` (or your installed executable), and
`pi_artifacts: outputs/pi-harness` for an isolated CLI inference backend. Use a
real HTTP Recorder; in-process Mock transport, UI intervention jobs and streaming
replay are unsupported for Pi. The evaluator remains the common SDK evaluator.
See the contract for schema-placement and streaming differences before comparing.

Evaluator v2: use `experiment.evaluator.mock.yaml` for the no-service contract
profile, or set `evaluator_version: v2` in a live experiment. `prepare-finder`
creates a separate private evaluation object with gold, references, original
reasoning boolean/type and domain; inference `cases.json` stays separate. Run
`prepare_evaluator.py --dataset DATASET.parquet --output outputs/evaluator-setup`
for an offline stratum inventory. `obs report` exports stratified_quality.csv;
unknown labels and unavailable domain/T/F cells are not synthesized.

For model-selected tools, set a condition to `strategy: tool_agent` with
`tier_policy: forced`, `tier: T1_RETRIEVAL`, `max_calls: 10`,
`max_tool_calls: 12`, and `hard_output_limit: 2048`; set `evaluator_version: v2`.
Use the existing private FinDER preparation artifacts. The model chooses search,
number extraction, calculation and optional specialist delegation. Fixed passages
stay fixed; this does not add semantic retrieval or a graph backend. Pi and
intervention replay are unsupported for this condition. The ten-call cap includes
the evaluator and specialist calls. Inspect raw events and versioned tool
judgments, retaining unknown scores and judge contradictions.

For the failure/coordination research loop, first prepare a controlled bundle:

```sh
python -m seocho.eval.agent_observability.quality_lab prepare \
  --cases examples/agent-observability/mock-cases.json \
  --evaluation examples/agent-observability/mock-evaluation.json \
  --output outputs/agent-observability/quality-mock/prepared
python -m seocho.eval.agent_observability.quality_lab run \
  --config examples/agent-observability/quality-lab.mock.yaml \
  --output outputs/agent-observability/quality-mock/results.json
python -m seocho.eval.agent_observability.quality_lab analyze \
  --results outputs/agent-observability/quality-mock/results.json \
  --directory outputs/agent-observability/mock \
  --output outputs/agent-observability/quality-mock/analysis
```

Start the existing Mock backend and Recorder first; use their configured store
path in `--directory`. Mock outcomes remain unknown, not financial quality scores.
For actual FinDER, prepare private cases/evaluation with the existing preparation
command, pass those files to `quality_lab prepare`, and explicitly declare MARA
models/evaluator in a separate suite file. Review the generated manifest and
review queue. `pilot_all` is exploratory; use document-group splits for development,
calibration and heldout evaluations. No automatic golden-label acceptance or
champion promotion occurs.

For the single-agent metacognition arm, use evaluator v2 and this condition in a
Recorder-backed experiment or quality-lab arm:

```yaml
condition_id: single_metacognition
strategy: tool_agent
tier_policy: forced
tier: T1_RETRIEVAL
metacognition: true
hard_output_limit: 2048
evaluator_output_limit: 2048
max_calls: 12
max_tool_calls: 6
```

This condition requires a tool-capable live model; the generic mock backend is
not a tool-policy simulator. Focused tool tests supply scripted tool trajectories
for no-service contract verification. Read the [single-agent live gate](../../docs/experiments/finder-single-metacognition-live-2026-10-11.md)
for real execution and evaluator HTTP429 gaps. Ontology/KG capabilities remain
unavailable, and specialist delegation is disabled in this arm.

For model-path research, use vanilla strategy and evaluator v2 with
`execution_path: direct`, `explicit`, or `adaptive`. Explicit plans contain at
most three earlier-dependent subquestions; adaptive can choose direct execution
or bounded tools. Every stage uses the solver model. Path judging is a separate
gold-free evaluation actor; answer judging uses private gold. Configure identical
fixed sources and per-role output caps/call ceilings across arms. Public plans
are not private reasoning traces. Pi/replay are unsupported for these arms.
