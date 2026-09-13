# Reproducible experiment environment

Use this entry point for an isolated SDK, DozerDB client, Agents SDK and OTel
environment. It preserves an existing `.venv` and keeps generated state under
the ignored `.seocho/` directory.

## Setup and validation

Install `uv`, then run from the repository root:

```bash
make platform-setup
make platform-check
make platform-ci
```

`platform-setup` installs Python 3.11 with the committed `uv.lock` and the `dev`
and `otel` extras into `.seocho/venvs/platform`. The dev extra includes the
Oxigraph Python client used for RDF experiments. `platform-check` checks the
lock, imports the SDK/agent/graph packages, and saves the installed package list
to `.seocho/platform/environment.txt`. `platform-ci` runs basic CI in that same
environment. Override `PLATFORM_ENV` to use another isolated location.

The project still supports Python 3.10, 3.11 and 3.12. `.python-version` selects
the contributor default; CI explicitly selects each matrix interpreter with
`UV_PYTHON`. CI and scheduled maintenance resolve from the same committed lock.
After an intentional dependency change, run `uv lock`, review both
`pyproject.toml` and `uv.lock`, and repeat validation.

## Services and evidence

Use [the deployment guide](RUNTIME_DEPLOYMENT.md) for DozerDB/runtime setup and
[the observability example](../examples/observability/README.md) for OTLP
collection. `make up` starts the configured images. To build current source
into the runtime image, use `make up-build`. An import check proves neither
deployed source parity nor live graph compatibility.

Before any benchmark, follow the continuity gate in [BENCHMARKS.md](BENCHMARKS.md):
read prior manifests and local memory, identify the closest arm, record the
incremental hypothesis, and use a fresh result directory. Keep datasets,
credentials, traces and receipts out of Git. Read-only planning and analysis
must remain distinguishable from paid generation/judging.

The specialist graph-agent runner and trace-analysis commands currently under
local development are a separate review surface. This main-branch entry point
installs and validates their prerequisites; it does not advertise commands that
depend on uncommitted scripts. A completed local run is evidence for its recorded
source snapshot and service configuration, not for a different checkout or image.

Tracing is vendor-neutral (`none`, `console`, `jsonl`, `otlp`), per ADR-0172.
Select an OTLP backend explicitly. Preserve original receipts when
exporting saved spans so later verification does not rewrite what the original
run actually observed.

## Your data: run, diagnose, compare

The experiment platform is the existing `seocho run` / `seocho sweep` workflow
plus saved evidence. Its purpose is to show what changed after a SEOCHO update
and where a user's own E2E run failed. It does not add a hosted UI or a new
metadata-store plugin. The SDK, runtime and backend contracts remain unchanged.

Start with `seocho new my-experiment`, replace `docs/` with your own supported
text/Markdown/CSV/JSON/JSONL/PDF files, and edit `schema.yaml` and
`seocho.run.yaml`. Give each question a stable `id`; optionally supply `expect`
as a reference. An expected string is a containment proxy, not a gold quality
judgment. The data and generated reports may contain private content; keep them
in your local project, outside Git.

Configure an existing DozerDB/Neo4j endpoint and target database. Environment
interpolation keeps credentials out of the config:

```yaml
name: my-experiment
ontology: ./schema.yaml
documents: ./docs/
graph:
  uri: ${NEO4J_URI:-bolt://localhost:7687}
  user: ${NEO4J_USER:-neo4j}
  password: ${NEO4J_PASSWORD}
database: ${EXPERIMENT_DATABASE}
workspace_id: ${EXPERIMENT_WORKSPACE}
models:
  default: mara/MiniMax-M2.7
questions:
  - id: company-ceo
    question: Who is the CEO of Acme Corp?
    expect: Jane Park
```

Use your own schema/questions instead of these illustrative names. Provision
separate empty experiment databases using your graph administrator's normal
workflow. The runner never clears data, creates databases, or rolls back partial
graph writes. Workspace labels alone do not prove arbitrary-query isolation.

```bash
# Set provider key and graph credentials in your environment first.
export EXPERIMENT_DATABASE=experimentbaseline
export EXPERIMENT_WORKSPACE=baseline
seocho run seocho.run.yaml --dry-run
seocho run seocho.run.yaml --no-track --output runs/baseline

# After the intended SEOCHO change, keep documents/questions/model/settings fixed.
export EXPERIMENT_DATABASE=experimentcandidate
export EXPERIMENT_WORKSPACE=candidate
seocho run seocho.run.yaml --no-track --output runs/candidate

# Replace these paths with the actual unique run directories printed above.
seocho runs compare runs/baseline/RUN_ID runs/candidate/RUN_ID \
  --change source \
  --hypothesis 'The indexing fix reduces failed documents without losing answer support' \
  --output-dir comparisons/indexing-fix
```

From a checkout, prefix with `uv run`. `--output` selects a parent directory;
each execution creates `<name>-<timestamp>-<id>/`. `--no-track` bypasses the
file-change cache so every experiment indexes its inputs. It does not erase old
graph facts; fresh targets are still required. A query-only experiment can use
`--only query` with a separately frozen graph, and its lack of indexing evidence
remains visible.

## Failure receipts

Every admitted execution writes `report.json` (`seocho.run_report.v2`) and
`report.md`. Reports retain the existing run/indexing/queries/agent_scorecard
fields and add `outcome`, `active_stage`, `diagnostics`, and `reproducibility`.
A checkpoint precedes build/index/query work, so interrupted work remains visible.
JSON is the authoritative checkpoint; if interrupted during Markdown rendering,
the Markdown file may represent the previous checkpoint. Existing report targets
are refused. A hard process kill can leave `running`; it is never interpreted as
completed evidence. Disk failure cannot guarantee persistence.

Outcomes distinguish `completed`, `partial`, `failed`, and `interrupted`.
Preflight failures on a real run also save diagnostics. Dry-run remains a local
check with no saved run or model calls. Invalid YAML exits 2; runtime/preflight
failure, partial indexing and empty answers exit 1. Successful execution exits 0
without asserting answer correctness. A nonempty evidence-backed abstention is
not classified as an empty answer.

Inspect diagnostics in this order: document reader/format, indexing/extraction or
graph write, then query/provider/evidence. Empty or invalid JSONL files fail local
preflight, including the first bad line number. Online preflight checks the exact
target database before extraction. If no documents were indexed or reused, query
is skipped so old graph content cannot mask a wholly failed ingest. If some
files fail, remaining query results are retained with an overall partial status.
Failed indexing is not cached as successfully unchanged on the next attempt.

## Interpreting comparisons

The comparison command is offline. It matches content fingerprints for documents,
question/reference sets, ontology, model choices, configurations, local package
versions and installed SEOCHO source. Credentials are excluded from the graph
endpoint identity. Source hashes include Python implementation content, including
uncommitted edits, rather than trusting a Git revision alone. Referenced designs
and bundles are hashed; unresolved adaptive ontology selection is an evidence gap.

Declare intended changes with repeatable `--change` and explain them with
`--hypothesis`. Documents/questions must remain fixed. Mismatched or missing
receipts, omitted questions, interrupted stages, or reuse of tracked indexing
produce `incomparable`, exit 1, and unavailable aggregate deltas. Old reports
remain useful for diagnostics but cannot establish matched conditions retroactively.
Question transitions and failures remain visible even when aggregate comparison
is rejected. Comparison outputs always require a new directory.

Matched reports show descriptive differences in indexing failures, answer/empty
rates, support/evidence proxies and recorded wall time. Missing evidence or
usage/cost data stays unavailable. There is no automatic "SEOCHO improved"
verdict. The receipt cannot prove initial graph identity, provider revision,
hardware, cache state, warmup, or external service versions. Record these separately
for live experiments; use held-out references or blinded judging for grounded
correctness and repetitions/uncertainty before claiming a quality or speed gain.
Do not pool hosted-provider and self-hosted inference evidence.

## Maintenance boundaries

`e2e.py` owns execution; `run_outcomes.py` owns status rules;
`run_reporting.py` owns report presentation and file persistence behind an
internal `ReportStore` protocol; `run_evidence.py` owns input fingerprints;
`run_comparison.py` owns offline comparison using the existing semantic scorecard.
The CLI registers `runs` through the existing command-group interface. These
boundaries receive strict incremental typing, Python lint, and deterministic
failure/compatibility tests in basic CI. Contract tests are not live backend or
performance evidence.

## Inspect results visually

Export a standalone local HTML view from a saved run:

```bash
seocho runs view runs/candidate/RUN_ID --output views/candidate.html
seocho runs view runs/candidate/RUN_ID --baseline runs/baseline/RUN_ID \
  --change source --hypothesis 'The indexing fix preserves completed work' \
  --output views/comparison.html
```

Open the generated HTML file in your browser. It contains run status and failure
diagnostics, baseline/candidate metric rows, searchable question answers and
supporting records, and an expandable module map. The map explains module
responsibilities; it does not claim every module executed. No module-level timing
is inferred from run totals. Missing cost/tokens stay unavailable.

The view uses no server, remote assets, uploads or model calls. HTML export exits
0 when rendering succeeds, including diagnostic views of incomparable reports;
inspect the displayed verdict. Existing output files are refused. The HTML
contains the selected report's answers and evidence, so share it with the same
care as the original report. Only the export is new; receipts remain unchanged.

Evidence v2 adds `runtime_settings` to the matched conditions. It fingerprints the
explicit environment switches listed in `run_evidence.RUNTIME_ENV_KEYS` and the
contents of configured hint/admission files. Declare intentional changes using
`--change runtime_settings` and a hypothesis. Older v1 receipts cannot prove this
condition and remain diagnostic-only. Mutable graph/cache state and unknown
external library settings are still unverified.

Both query paths checkpoint completed questions individually, with
`active_question` identifying the question running at the last checkpoint.
An interrupted run retains earlier answers but is still rejected for aggregate
comparison. Degraded extraction retains its failure reason and is not cached as
successful indexing. Failure diagnostics redact endpoint userinfo/query tokens
and known credential values before saving.
