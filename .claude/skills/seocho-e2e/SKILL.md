---
name: seocho-e2e
description: Author and run SEOCHO end-to-end YAML runs (seocho run / seocho sweep) over a user's documents — scaffold the ontology + run spec, dry-run to validate, execute index→query→report, and compare configuration variants. Use when the user wants to index their own documents into SEOCHO and ask questions, build/edit a seocho.run.yaml or seocho.sweep.yaml, compare models/enforcement/agent patterns, or run an ontology-aligned graph-RAG e2e without writing Python.
---

# SEOCHO e2e (run / sweep)

Drive SEOCHO's YAML-declared e2e: one ontology + one documents folder + questions
→ index → query → report. No Python; everything is a `seocho run` or `seocho sweep`
invocation over a run spec. This skill scaffolds the YAML, validates it offline,
runs it, and reads the report back.

The YAML is **orchestration only**. Extraction prompts come from the ontology
(node/relationship descriptions) plus optional design specs; the agent pattern is
a config key. Keep that separation — do not hand-write prompt strings into the run
spec.

## 0. Resolve the CLI first (do this before any seocho command)

The global `seocho` on PATH is often a stale build without `run`/`sweep`. Prefer the
editable venv in the repo:

```bash
SEOCHO_BIN=/home/hadry/lab/seocho/.venv/bin/seocho
[ -x "$SEOCHO_BIN" ] || SEOCHO_BIN=seocho
"$SEOCHO_BIN" run --help >/dev/null 2>&1 || echo "needs: cd /home/hadry/lab/seocho && uv sync --extra local --extra ci"
```

If `run --help` fails (old build), tell the user to run
`cd /home/hadry/lab/seocho && uv sync --extra local --extra ci` and stop — do not
fall back to the stale global binary.

Load the API key the same way the repo does (MARA-first):
```bash
set -a && source /home/hadry/lab/seocho/.env && set +a   # exports MARA_API_KEY
```
Embeddings/LLM default to MARA + local fastembed (bge). Do not introduce OpenAI
unless the user asks.

## 1. Understand the request, then scaffold

Establish three things before writing YAML:

1. **Documents** — a folder (or file) of `.txt/.md/.csv/.json/.jsonl/.pdf`. If the
   user has none, point them at the bundled demo (`examples/run/`).
2. **Ontology** — the node/relationship types to extract. If absent, draft a
   small `schema.yaml` from the user's domain (see §2), or run `seocho init` for
   the interactive builder.
3. **Intent knobs** — model(s), enforcement mode, agent pattern, questions (§3).

Fastest start (bundled, zero setup) — use this to confirm the toolchain works:
```bash
"$SEOCHO_BIN" run /home/hadry/lab/seocho/examples/run/quickstart.yaml --dry-run
```

`seocho run --init` writes a fully commented `seocho.run.yaml` template into the
current directory — read it for the authoritative key list before editing.

## 2. Ontology (schema.yaml)

Separate file the run spec points at. YAML / JSON-LD / TTL. The descriptions are
load-bearing — they become the extraction prompt.

```yaml
name: my-domain
nodes:
  Company:
    description: A business organization        # ← travels into the prompt
    properties:
      name: { type: STRING, constraint: UNIQUE }
  Person:
    properties:
      name: { type: STRING, constraint: UNIQUE }
relationships:
  CEO_OF:
    source: Person
    target: Company
    description: Person leads the company as chief executive
```

Validate an ontology on its own: `"$SEOCHO_BIN" ontology check --schema schema.yaml`.

## 3. Run spec (seocho.run.yaml)

Every key maps to an SDK parameter. Minimal is three keys:

```yaml
ontology: ./schema.yaml
documents: ./docs/
questions:
  - Who is the CEO of Acme?
```

Full surface (only add what the user's intent needs):

```yaml
name: my-run
ontology:
  path: ./schema.yaml
  enforcement: guided          # guided (default) | strict | open  — see §6
documents: { path: ./docs/, recursive: true }
models:
  default: mara/MiniMax-M2.5   # provider/model; MARA-first
  indexing: mara/MiniMax-M2    # optional per-phase override (cheaper indexing)
  query: mara/MiniMax-M2.5
graph: bolt://localhost:7687   # omit → embedded LadybugDB (no server). Or mapping:
graph:
  kind: dozerdb                # neo4j | dozerdb | ladybug
  uri: bolt://localhost:7687
  password: ${NEO4J_PASSWORD}  # secrets via ${ENV}, never inline
vector:                        # optional hybrid search; omit for graph-only
  kind: faiss                  # faiss (in-memory) | lancedb (on-disk)
  embedding: fastembed         # local bge default | provider preset
agent:
  execution_mode: pipeline     # pipeline (default) | agent | supervisor
  routing_policy: balanced     # fast | balanced | thorough
  design: ./agent_designs/x.yaml      # optional AgentDesignSpec (pattern)
indexing:
  design: ./indexing_designs/x.yaml   # optional IndexingDesignSpec (extraction strategy)
query:
  reasoning_mode: true
  repair_budget: 1
  answer_style: concise        # concise | evidence | table
questions:
  - What product does Acme offer?
  - question: Who is the CEO of Acme?
    expect: Jane Park          # recorded in report, not auto-graded
```

Agent patterns (via `agent.design` → an AgentDesignSpec; bundled in
`examples/agent_designs/`): `reflection_chain`, `planning_multi_agent`,
`memory_tool_use`. Extraction strategy/domain presets live in an IndexingDesignSpec
(`examples/indexing_designs/`, `ingestion.extraction_strategy: general|domain|multi_pass`).
Reference these files rather than re-deriving their internals.

## 4. Validate, then run

Always dry-run first — it runs the full preflight (ontology loads, documents
scanned, API key present, graph reachable, vector deps) with **no LLM calls**:

```bash
"$SEOCHO_BIN" run seocho.run.yaml --dry-run
```

Fix whatever preflight reports (its messages name the exact fix), then run:

```bash
"$SEOCHO_BIN" run seocho.run.yaml
```

Report lands at `runs/<name>-<timestamp>/report.md` (+ `report.json`). Read
`report.md` and summarize: files indexed, nodes/relationships, and per-question
answers — flag any `empty` answer or `error`.

Useful flags: `--only index` / `--only query` (reuse the existing graph),
`--force` (re-index unchanged files), `-o DIR`, `--output-json`.
Exit codes: `0` ok · `1` runtime/preflight failure · `2` invalid config.

Templating: a `*.yaml.j2` run spec is rendered with Jinja2 — pass `--var key=value`
(dotted keys, YAML values) / `--vars file.yaml`, and `--show-rendered` to inspect.
Secrets stay in `${ENV}`, resolved after rendering.

## 5. Compare variants — seocho sweep

When the user wants to compare configurations (models, enforcement, prompts), use a
sweep: one `run.yaml.j2` template × N named variants → N isolated runs → one table.

```bash
"$SEOCHO_BIN" sweep --init     # writes seocho.sweep.yaml + run.yaml.j2
"$SEOCHO_BIN" sweep examples/run/sweep-enforcement/sweep.yaml --dry-run
"$SEOCHO_BIN" sweep examples/run/sweep-enforcement/sweep.yaml
```

```yaml
# seocho.sweep.yaml
template: ./run.yaml.j2
vars: { model: mara/MiniMax-M2.5 }   # shared
variants:
  - name: guided
    vars: { enforcement: guided }
  - name: strict
    vars: { enforcement: strict }
```

Each variant gets an isolated graph, workspace, and `runs/<sweep>-<ts>/<variant>/`
dir; the summary table compares files/nodes/answered/empty/errors. Flags:
`--only-variant NAME`, `--fail-fast` (default keeps going), `--var`/`--vars`.
Variants run sequentially. Use `seocho sweep`, not `seocho experiment` (that one is
extraction-only, no query phase).

## 6. Ontology enforcement (the most-asked knob)

`ontology.enforcement` is the admission policy for extracted data:

- **guided** (default) — ontology guides extraction, off-ontology material is
  written with a warning. Max recall; most QA workloads.
- **strict** — closed vocabulary: only declared types admitted, chunks with
  validation errors rejected, no `Entity` fallback. Trust > recall (audit/regulated).
  Expect fewer nodes than guided.
- **open** — admit everything, but stamp out-of-vocabulary nodes/relationships with
  `_out_of_ontology: "true"` — the signal for "what should I add to the schema?".

Full reference: https://seocho.blog/sdk/enforcement-modes/

## Guardrails

- Resolve `$SEOCHO_BIN` and verify `run --help` works before any run (§0).
- Always `--dry-run` before a real run; never claim a run succeeded without showing
  the report or the exit code.
- MARA-first: don't switch to OpenAI embeddings/models unless asked.
- Don't invent prompt text in the run spec — prompts come from the ontology +
  design specs.
- Secrets go through `${ENV}`; never write a literal key into YAML.
- If preflight fails, surface its message verbatim and fix that — don't bypass it.
```
