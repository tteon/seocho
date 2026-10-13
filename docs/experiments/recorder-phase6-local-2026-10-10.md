# Recorder Phase 6 local acceptance — 2026-10-10

Implemented a loopback graph explorer with packaged D3 assets, fixed-workspace
read-only Recorder projections, complete request/response inspection, population
views and explicit isolated Mock intervention jobs. No MARA, OpenAI or graph backend
was called during Phase 6 acceptance.

Run from the repository:

```sh
.venv/bin/obs ui --workspace phase2-mock-lab \
  --directory outputs/agent-observability/phase2-reviewed-acceptance/store \
  --experiment examples/agent-observability/experiment.mock.yaml \
  --origin-recorder-config outputs/agent-observability/phase2-reviewed-acceptance/recorder.yaml \
  --port 8810
```

Open `http://127.0.0.1:8810`. Stop any Recorder writer first. Omit `--experiment`
for inspection without execution. Original records remain read-only. Jobs create
separate stores under `outputs/agent-observability-ui-jobs`. Execution uses an
in-process deterministic Mock even when an old recorded upstream URL is retained
as hash identity; there is no connection to that endpoint.

Browser acceptance exercised run selection, collapsed/expanded graph, sub-task
inspection, role call ledger, full recorded bodies, node operator selection,
reference knock-in submission and inspection of the completed derived run. A saved
20-run Phase 2 source remained at source hash
`be093abe0da81eae08e85e823d010ab53a12823075cb714adf353ebf97050080`.
Each successful reference branch inherited two pinned prefix calls and performed
three fresh calls (target solver, synthesis, evaluator): 84 observed Mock tokens,
including evaluation, under the explicit conservative reservation cap. This is
scripted accounting evidence, not provider performance or answering quality.

The first legacy-source browser attempt rejected the mismatched cache namespace;
a subsequent attempt also revealed the upstream URL was part of request identity.
The source Recorder configuration now restores both exactly. No fresh-prefix fallback
or raw-source rewrite was used. Regression fixtures cover non-default namespaces;
new Recorder start rows retain upstream identity for later replay. Failed jobs and
original error bodies remain inspectable.

A second browser viewed eight Recorder-backed scripted runs whose verdicts are
known by construction. It showed first silver divergence, entropy 1 bit, silver
mismatch and paired knock-in rescue score 1. Population view displayed six paired
tier transitions and an aligned failure-flow Sankey. These synthetic labels verify
rendering/projection only. The ordinary Mock source still shows unknown outcomes.
Screenshots are local ignored artifacts under
`outputs/agent-observability/phase6-ui`.

Validation:

- `pytest -q tests/seocho/test_recorder_ui.py`: 3 passed, including workspace/domain
  isolation, exact bodies, CSRF/host checks, graph paging, successful and cap-failed
  branches, immutable source and positive silver/Sankey projections.
- `bash scripts/ci/run_basic_ci.sh`: 736 passed, 5 skipped, 16 warnings; runtime shell,
  module ownership, root hierarchy and agent-document checks passed.
- Ruff on new Python UI/projection/tests and `git diff --check` passed.
- `uv build --wheel`: packaged UI HTML, JS, CSS, D3/Sankey distributions, licenses
  and version/hash manifest (eight static files). No browser CDN is used.

Limits: this is an offline viewer; source analysis loads the population at startup.
Graph pages are bounded but no large-population throughput claim was tested. The
current harness supports independent subtasks; dependency edges are rendered only
when present in recorded plans. Silver divergence, entropy and Sankey are proxies,
not causal diagnoses. Modeled optimum remains unknown without usable sweep fits;
allocation distribution requires measured E3 regret, not additive error predictions.
Job discovery is session-local; receipts/stores persist. Paid intervention execution
is disabled. Live FinDER/MARA research conclusions need separately authorized paid
runs and recorded quality evidence.
