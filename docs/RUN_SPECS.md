# Run Specs

`seocho run` executes one end-to-end flow — index documents, ask questions,
write a report — from a single YAML file. A run spec is the run-scoped layer
on top of the existing design dialects:

- run-scoped vocabulary (models, graph connection, inputs, output) lives here
- agent behavior is delegated to [agent design specs](AGENT_DESIGN_SPECS.md)
- ingestion behavior is delegated to [indexing design specs](INDEXING_DESIGN_SPECS.md)

A run spec never redefines a key those dialects already own; it references or
embeds their documents.

## Quickstart

```bash
seocho run --init           # writes a commented seocho.run.yaml template
# edit ontology/documents/questions, then:
seocho run                  # or: seocho run path/to/config.yaml
```

A working example ships in the repo:

```bash
export MARA_API_KEY=...
seocho run examples/run/quickstart.yaml
```

## Minimal spec

With no graph server and no model keys beyond one provider env var:

```yaml
ontology: ./schema.yaml
documents: ./docs/
questions:
  - Which companies reported revenue growth?
  - Who is the CEO of Acme?
```

Defaults: `mara/MiniMax-M2.5` for both phases, embedded LadybugDB
(`.seocho/local.lbug`), `guided` enforcement, `pipeline` execution mode.

## Full spec

```yaml
name: filings-demo                  # default: config filename stem
description: Optional free text.

ontology:
  path: ./schema.yaml               # YAML / JSON-LD / TTL (Ontology.load)
  enforcement: guided               # strict | guided | open (default: guided)

documents:
  path: ./docs/                     # .txt .md .csv .json .jsonl .pdf
  recursive: true

models:
  default: mara/MiniMax-M2.5        # provider/model for both phases
  indexing: mara/MiniMax-M2         # per-phase override → separate client
  query: mara/MiniMax-M2.5

graph: bolt://localhost:7687        # omit for embedded LadybugDB
graph_user: neo4j
graph_password: ${NEO4J_PASSWORD:-password}
database: neo4j                     # omit to derive from the ontology name
workspace_id: filings_demo          # default: derived from name

indexing:
  design: ./indexing_design.yaml    # optional IndexingDesignSpec (path or inline)
  category: filing
  force: false

agent:
  design: ./agent_design.yaml       # optional AgentDesignSpec (path or inline)
  execution_mode: pipeline          # pipeline | agent | supervisor
  routing_policy: balanced          # fast | balanced | thorough

query:
  reasoning_mode: true
  repair_budget: 1
  answer_style: concise             # concise | evidence | table
  limit: 5

questions:                          # strings, or mappings with expectations
  - Which companies reported revenue growth?
  - question: Who is the CEO of Acme?
    expect: Jane Park               # recorded in the report, not auto-graded
    id: ceo-check

output:
  dir: runs                         # report lands in runs/<name>-<timestamp>/
```

Omitting `questions` entirely makes the run index-only.

## Ontology enforcement

`ontology.enforcement` declares the admission policy for extracted graph
data against the ontology vocabulary:

| Mode | Behavior |
| --- | --- |
| `strict` | Closed vocabulary. A constant closed-vocabulary instruction is appended to extraction prompts; the relaxed retry and the `Entity`/heuristic fallbacks are disabled (an empty extraction is a legitimate outcome); validation runs closed (no `Entity` exemption, dangling-endpoint and domain/range conformance checks via the `broader` chain); chunks with errors are rejected, not written; linking output is re-checked and reverted on regression. Default `validation_on_fail` becomes `reject` (`relax`/`warn` are rejected as incoherent). |
| `guided` | Default — the tuned behavior the FinDER experiments validated. The ontology guides extraction prompts; relaxed retry and `Entity` fallback stay available; validation errors are reported but content is written. |
| `open` | Admit everything: same write behavior as `guided`, plus every out-of-vocabulary node/relationship is stamped with `_out_of_ontology: "true"` — the triage signal for offline ontology-evolution governance. `validation_on_fail: reject` is rejected as incoherent. |

The policy is compiled by `seocho.EnforcementPolicy` from
`AgentConfig.ontology_enforcement`; agent design specs may declare
`ontology.enforcement` too, and an explicit run-spec value overrides the
design (the implicit `guided` default never does). These are admission
policies for extracted data — not CWA/OWA inference semantics; query-time
entailment is unchanged in every mode.

## Per-phase models

When `models.indexing` and `models.query` differ, the runner builds two
clients sharing one graph store, ontology, and workspace — per-phase model
separation without per-call plumbing. Env-driven routing
(`SEOCHO_MODEL_ROUTING`) still applies within each phase if configured.

## Environment variables in values

Any string value may interpolate `${VAR}` or `${VAR:-default}`. An unset
variable without a default is a config error. Put env var *names* in YAML,
never literal API keys; provider keys are read from the provider's standard
env var (`MARA_API_KEY`, `OPENAI_API_KEY`, ...).

## CLI

```
seocho run [CONFIG] [options]

  CONFIG            Run spec YAML (default: ./seocho.run.yaml)
  --init            Write a commented template and exit (refuses to overwrite)
  --dry-run         Validate config + offline preflight; no LLM calls
  --only index|query  Run a single phase (query reuses the existing graph)
  -o, --output DIR  Report directory (default: runs/<name>-<timestamp>/)
  --force           Re-index files even if unchanged
  --output-json     Machine-readable output

Exit codes: 0 ok · 1 runtime/preflight failure · 2 invalid config
```

Every run starts with a preflight that reports **all** failing checks
(ontology loads, documents found, API key present, graph reachable) before
anything spends tokens. `--dry-run` is the same preflight without the graph
connection attempt.

## Report

Each run writes `report.json` (machine-readable: run metadata, per-file
indexing stats, per-question records with latency and errors) and
`report.md` (human summary) under `output.dir/<name>-<timestamp>/`. Empty
answers and per-question errors are surfaced in the summary table; a
question error marks the run as failed (exit 1) without aborting remaining
questions.
