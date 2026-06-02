# CLAUDE.md

Agent execution guide for this repository.
Use this file as the primary operational contract when implementing changes.

## 1. Current Product Consensus

- Agent runtime: **OpenAI Agents SDK**
- Trace/evaluation contract: **vendor-neutral**, with **Opik preferred** for team observability
- Graph DB backend: **DozerDB** (fixed)
- Tenancy mode: **single-tenant MVP** with `workspace_id` propagated end-to-end
- Ontology governance: **Owlready2 in offline path only** (no heavy reasoning in request hot path)

## 2. Source Of Truth Docs

Read in this order before significant changes:

1. `README.md`
2. `docs/WORKFLOW.md`
3. `docs/GRAPH_MODEL_STRATEGY.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`

When architecture changes, add/update ADRs under `docs/decisions/`.

## 3. Control Plane vs Data Plane

## Control Plane

Responsibilities:

- agent routing/instructions
- runtime policy/authorization
- quality gates and release/landing workflow
- architecture decisions and governance

Primary surfaces:

- `runtime/agent_server.py`
- `runtime/policy.py`
- `runtime/memory_service.py`
- `docs/decisions/*`
- `docs/ISSUE_TASK_SYSTEM.md`

## Data Plane

Responsibilities:

- document ingestion and extraction
- entity linking and deduplication
- rule inference/validation lifecycle
- graph storage/query on DozerDB

Primary surfaces:

- `src/seocho/rules.py` — canonical rule inference/validation (shared by SDK + server)
- `src/seocho/index/pipeline.py` — canonical indexing pipeline (rule + embedding support)
- `src/seocho/index/linker.py` — canonical embedding-based entity linker
- `extraction/pipeline.py` — legacy batch pipeline
- `extraction/rule_constraints.py` — re-export shim to `seocho.rules`
- `extraction/rule_api.py` — HTTP endpoints for rule operations
- `extraction/rule_profile_store.py`
- `extraction/rule_export.py`
- `extraction/vector_store.py` — adapter shim to `seocho.store.vector`

## 4. Mandatory Workflow For Agents

## 4.1 Start

1. inspect the current GitHub issue, PR, or maintainer-provided work item
2. keep scope tight to that request
3. if creating new public work, use GitHub issues or the maintainer-designated
   tracker
4. if using local agent coordination tools, keep their state in the local
   workspace; do not commit `.beads/`, `.agents/`, `.claude/`, or similar
   private tool directories

Tracking and notes split:

- public GitHub issues and PRs are the canonical public review trail
- local agent trackers may help coordination, but are not part of the tracked
  public repository contract
- `/home/hadry/my_local_work/obsidian/seocho` is the default home for internal
  design notes, failure analysis, experiment logs, and feature ideation
- repo docs should stay reserved for contracts and instructions that must ship
  with the repository

## 4.2 During Implementation

- keep scope tight to one issue/feature slice
- split or hand off work instead of letting multiple agents write the same
  shared seam at once
- preserve `workspace_id` in new runtime-facing contracts
- add/adjust tests for modified behavior
- update repo docs only for user-visible or operator-visible contract changes
- prefer Obsidian notes over repo docs for working analysis and speculative
  design thinking

## 4.3 Before Landing

1. run focused tests
2. run sprint lint when applicable:
   - `scripts/pm/lint-items.sh --sprint <id>`
3. close or handoff issue
4. `git pull --rebase`
5. `git push`
6. `git status` must show up-to-date with `origin/main`

Push target is always `main`.

When using git worktrees, prefer `bd --sandbox ...` for local issue commands.

## 5. Issue/Task Governance

For active work items (`open`, `in_progress`, `blocked`), collaboration labels are required:

- `sev-*`
- `impact-*`
- `urgency-*`
- `sprint-*`
- `roadmap-*`
- `area-*`
- `kind-*`

See `docs/ISSUE_TASK_SYSTEM.md` for full policy.

## 6. Runtime/API Guardrails

## 6.1 Workspace-Aware Contracts

- runtime write/compute APIs must include `workspace_id`
- validate format with policy checks

## 6.2 Rules API Surface

Current endpoints:

- `POST /rules/infer`
- `POST /rules/validate`
- `POST /rules/assess`
- `POST /rules/profiles`
- `GET /rules/profiles`
- `GET /rules/profiles/{profile_id}`
- `POST /rules/export/cypher`
- `POST /rules/export/shacl`
- `POST /semantic/artifacts/drafts`
- `GET /semantic/artifacts`
- `GET /semantic/artifacts/{artifact_id}`
- `POST /semantic/artifacts/{artifact_id}/approve`

Rules constraints:

- `required` and `datatype` map to Cypher constraints
- `enum` and `range` are returned with fallback `validation_query` hooks in `unsupported_rules`

## 6.3 Owlready2 Boundary

- allowed: offline ontology validation/compilation flows
- forbidden: synchronous heavy ontology reasoning in request hot path

## 7. Coding Standards

- use type hints on function signatures
- prefer deterministic, testable behavior
- no hardcoded credentials
- centralized config only (`extraction/config.py`)
- logging over print
- no destructive git commands

## 8. DozerDB/Graph Safety Rules

- database names must pass registry validation
- dynamic labels/properties must be validated before Cypher interpolation
- query tools should remain read-safe unless write mode is explicitly required
- prefer `elementId(...)` over deprecated `id(...)` in query-time/runtime Cypher paths

## 9. Observability Requirements

- use `@track` for critical orchestration functions
- include `workspace_id` and user context in trace metadata where applicable
- keep trace artifacts vendor-neutral; prefer JSONL as the portable record and Opik as the optional team exporter

## 10. Frontend-Driven Upload Flow (Target)

Product expectation for upload flow:

1. user uploads document(s)
2. structure/chunk extraction
3. ontology candidate + graph extraction
4. SHACL-like rule inference/validation
5. profile save and export plan
6. graph persisted/queryable in DozerDB

Reference: `docs/GRAPH_MODEL_STRATEGY.md`

## 11. Definition Of Done

- code changes implemented
- tests for changed behavior pass
- docs updated (README/docs/ADR as needed)
- issue/task state updated
- changes pushed to `origin/main`

## 12. Commit Conventions & Website Sync

### Commit Conventions (Semantic Versioning)
We enforce STRICT Semantic Versioning style commit prefixes to keep our history clean and to fuel the automated `Updates` section of the website changelog.
- `feat:` — New features or significant additions (e.g., `feat: Add Parallel Debate orchestrator`)
- `fix:` — Bug fixes (e.g., `fix: Resolve FAISS index out of bounds error`)
- `docs:` — Documentation and website changes (e.g., `docs: Update CLAUDE.md for agent collaboration`)
- `refactor:` — Code restructuring without logic changes
- `chore:` — Tooling, dependency, or minor configuration updates
- `test:` — Adding or updating test suites

### Website Syncing
The `seocho` primary repository acts as the source of truth for core docs.
The tracked Astro/Starlight site now lives in `website/`.
Selected docs are generated into `website/src/content/docs/` at build time via
`website/scripts/generate-docs.mjs`, and repo-side GitHub Actions validate and
deploy the site from this repository.

## 13. Reliability Notes (2026-02-20)

- `Makefile` quality gates must target `extraction-service` (not `engine`).
- Neo4j/DozerDB procedure privileges must stay scoped to `apoc.*,n10s.*` (no wildcard unrestricted).
- API/middleware tests should prefer `httpx.ASGITransport` + `AsyncClient` over `TestClient` in this repo environment.
- When local `bd` workspace state is noisy, run lint via sandbox mode (`bd --sandbox ...`) to avoid auto-sync side effects during validation.
- `website/` is tracked; generated mirrors under `website/src/content/docs/docs/`
  are derived artifacts and should not be edited directly.
- Repo-side automation is intentionally narrow:
  - `.github/workflows/ci-basic.yml` is the required GitHub check surface
  - Codex PR automation is limited to bounded daily/periodic draft PR workflows
  - automation PRs must keep the `Feature/Why/Design/Expected Effect/Impact Results/Validation/Risks` structure
  - `/go` merge is maintainer-triggered and should not replace review judgment

## 14. Philosophy Alignment

All significant implementation changes should align with `docs/PHILOSOPHY.md`.

Critical alignment checks:

- heterogeneous-source extraction should produce ontology-governed semantics (rules + entity links), not plain unstructured outputs only.
- graph instance lifecycle and graph-agent lifecycle should remain 1:1 unless an ADR explicitly changes this.
- router/supervisor request allocation should be grounded in ontology-backed graph metadata.
- backend trace topology metadata is a contract for frontend DAG rendering, not an optional hint.
- JSONL traces and, when enabled, Opik traces should preserve enough metadata to audit routing, semantic disambiguation, and synthesis paths.

## 15. Architecture Priority Execution (Active)

Execution order (highest first):

1. runtime contract stability (SDK adapter + contract tests)
2. real-database-only agent provisioning and degraded-state reporting
3. graph query durability migration (`id` -> `elementId`)
4. runtime vs batch process/health isolation
5. agent readiness state machine for routing/supervision
6. `/rules/assess` governance automation in promotion flows

Implementation note:

- route all direct Agent SDK execution through `extraction/agents_runtime.py`; avoid calling `Runner.run` directly in feature modules.

## 16. User-First Release Gate

Any user-facing change must preserve a reproducible quickstart path:

1. ingest raw records (`/platform/ingest/raw`)
2. ensure fulltext (`/indexes/fulltext/ensure`)
3. run semantic and debate chat (`/api/chat/send`)
4. verify strict integration smoke (`make e2e-smoke`)

If this path is broken, do not treat the release as complete.

## 17. Documentation Sync Contract

For seocho.blog publishing, keep these docs current as first-class release
artifacts:

- `docs/README.md`
- `docs/RUNTIME_DEPLOYMENT.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`
- `docs/TUTORIAL_FIRST_RUN.md`
- `docs/OPEN_SOURCE_PLAYBOOK.md`

Docs updates that change user behavior or architecture intent must include a decision log update (and ADR when non-trivial).

Do not treat repo docs as the default notebook for implementation thinking.

### 3-Way Documentation Split

| Layer | Location | Role | Authority |
|-------|----------|------|-----------|
| `docs/` | in repo | **Contract** (what IS) | external users, contributors |
| GitHub issues/PRs | public project | **Execution** (what/when) | public task state, review trail |
| Obsidian | `/home/hadry/my_local_work/obsidian/seocho` | **Interpretation** (why/how) | design thinking, trade-offs, open questions |

Rules:
- Obsidian wiki (`wiki/topics/`) **interprets** `docs/` decisions — never duplicates them.
  Link to ADRs by path; write only the reasoning, background, and open questions that `docs/` doesn't carry.
- public GitHub issues and PRs are the source of truth for public task progress.
- `docs/` is the source of truth for architecture and API contracts — never contradict it from Obsidian.
- When finishing a work session, update relevant Obsidian `wiki/topics/*.md` pages
  with new insights or state changes. Keep `[[wikilinks]]` between topics current.
- See `vault-schema.md` in the Obsidian vault for full conventions.

## 18. Baseline Defaults (Robustness, Performance, Scalability, Prompts)

`docs/BASELINE_INSTRUCTIONS.md` is the single normative document for
SDK + agent defaults across:

- robustness — silent-fallback discipline, idempotency, retry, atomic writes, failure-mode classification
- performance — KV-cache for multi-turn (Anthropic-style `cache_control` breakpoints, ≥85% hit-ratio target)
- scalability — workspace_id partitioning, cache key shape, per-Session resource caps
- middleware-aware design — ordered chain (Validation → Policy → Cache → Budget → Retry → Observability)
- agent system-prompt discipline — output envelope, cache-friendly ordering, tool-use parallelism, no-fabrication, refusal contract

Every rule is structured as **Default → Why → Override** so users can recognize the baseline and customize without monkeypatching. Sections marked 🚧 describe target architecture not yet landed; cross-link them to public issues or PRs.

Pairs with `docs/SDK_CONTRACT.md` (current vs. target SDK guarantees) and `tests/seocho/test_user_facing_edge_cases.py` (regression anchors).

## 19. Active Experiment: Vector vs. Graph Retrieval on FinDER (2026-05-30)

### Goal

Use SEOCHO to determine **which question types graph retrieval wins and which
vector retrieval wins** on financial QA, **and how that depends on the ontology
used to build the knowledge graph**. This is a comparative study, not a demo:
every claim about "graph is better here / vector is better there" must be backed
by per-slice measured evidence on the same cases, same answers, same metric.

### Ontology is a first-class variable

Knowledge-graph extraction is **not a single fixed pipeline** — the resulting
graph (and therefore the answer) changes with the ontology that guides
extraction: its presence/absence, which FIBO modules, and how much of FIBO is
loaded. We treat ontology size as a deliberate dial:

- **Too much ontology** → over-rich / noisy schema, extraction overwhelmed by
  irrelevant classes.
- **Too little ontology** → over-constrained, can't represent the evidence, poor
  graph.
- The sweet spot is empirical, hence the sweep below.

**4 graph-build arms (the comparison groups):**

Confirmed module assignment (nested supersets — `small ⊂ medium ⊂ large`, only
add modules between adjacent arms so differences are cleanly attributable):

| Arm | Modules | n_mod | ~nodes | Intent |
|-----|---------|-------|--------|--------|
| `non-ontology` | `compose_modules([])` → `Entity`/`RELATED_TO` | 0 | 1 | no-ontology floor |
| `small` | `be, ind` | 2 | 11 | **under**: financial core only; S3/S4/S5 have no backing schema (over-constrained zone) |
| `medium` | `be, ind, fbc, dbt, acc` | 5 | 20 | **matched**: every graph-favorable slice (S1·S2 via ind, S3·S4 via fbc, S5 via dbt+acc) has its backing module, nothing extra → Goldilocks candidate |
| `large` | `be, ind, fbc, dbt, acc, fnd, sec, mkt, corp` (all 9) | 9 | 31 | **over**: adds slice-irrelevant fnd·sec·mkt·corp → noise hypothesis |

```python
small  = compose_modules(["be", "ind"])
medium = compose_modules(["be", "ind", "fbc", "dbt", "acc"])
large  = compose_modules(["be", "ind", "fbc", "dbt", "acc", "fnd", "sec", "mkt", "corp"])
```

Dependency safety: `be` (LegalEntity anchor) is in all arms — almost every rel
`source` is `LegalEntity`; `acc` (`GOVERNS → FinancialMetric`) always travels
with `ind`. No dangling rel sources in any arm.

Available FIBO modules (`examples/finder/datasets/fibo_modules/*.yaml`,
`KNOWN_MODULES`): `be, fbc, sec, fnd, ind, dbt, mkt, acc, corp`. Each arm must be
built and evaluated identically except for the ontology; the ontology is the
only variable that moves across arms.

Module → slice backing: `ind`→S1,S2 · `fbc`→S3,S4 · `dbt`+`acc`→S5 ·
`be`=shared anchor · `fnd`/`sec`/`mkt`/`corp`=peripheral (large only).

### Opik observability contract — ONLY what's necessary (revised 2026-05-30)

Target: **self-hosted Opik** (`OPIK_URL_OVERRIDE=http://localhost:5173/api`,
workspace `default`, project `yitae-0530-grok`; cloud retired 2026-05-30 on usage
limit).

**Experiment-traces-only.** Do NOT call `configure_tracing_from_env()` in the
benchmark runners — enabling SEOCHO's `OpikBackend` floods the project with
`sdk.extraction`/`sdk.query` traces AND wraps the LLM via `track_openai`
(`chat_completion_create` traces). Experiment traces come solely from
`bench_common.run_under_opik_track` (`@track`) + `set_opik_trace_metadata`, which
use opik's own env/config. Result: **one clean root trace per run**.

**Minimal tag set (≤7, filterable, human-readable):**
`retrieval:<vector|graph|vector_graph>` · `ontology:<non-ontology|small|medium|large|n-a>`
· `slice:<S#>` · `model:<short>` · `flow:<graphrag|vector|hybrid>` ·
`graph_quality:<raw|…>` · `run:<run_prefix>`. Everything else
(case_id, category, hashes, seed, workspace_id, provider, k) → **metadata**, not tags.

**Metrics go to `feedback_scores`, not tags** (so the Opik UI shows sortable,
chartable columns — this, not the tag set, is what makes vector vs graph
comparable): `number_overlap`, `judge_score`, `token_f1`, `contains_match`
(0..1). Attach `number_overlap` live via `update_current_trace(feedback_scores=…)`;
backfill `judge_score`/`token_f1` from the offline judge via
`Opik().log_traces_feedback_scores(...)`.

**Span shape:** one root trace per run; optional ≤3 collapsed child spans
(`retrieve` → `answer` → `score`) via `opik_context.span()` *inside* the existing
`@track` — never as separate top-level traces.

**opik-mcp: not adopted** (self-hosted exposes only read/list/write; needs Python
3.13 while this box is 3.10; TS server sunsets 2026-11). Query traces via simple
REST (the `urllib` pattern in `bench_common`) when debugging.

### Retrieval comparison design (2026-05-30, revised)

Three retrieval modes compared on the SAME data (identical 60-case sample, gold
`references_joined`, grok-4.3, and number-aware metric — only retrieval differs):

- `retrieval:vector` — dense top-k over the case's reference chunks
  (`text-embedding-3-small`, 1536d). Ontology-independent → `ontology:n-a`.
  Runner: `scripts/benchmarks/finder_vector_arm.py`.
- `retrieval:graph` — the case's extracted subgraph (per ontology arm) serialized
  to text and given to grok as context. **Graph-as-context, not structured
  Cypher Q&A**: SEOCHO's hardcoded `(:Company)-[]-(:FinancialMetric)` lookup is
  brittle (anchor `LegalEntity` nodes / company–metric edges are often missing
  from extraction), so structured traversal returns empty even when the metric
  data is present. Serializing the workspace subgraph (typed nodes + values +
  periods + relationships) is robust and measures what each arm actually
  extracted. Tag `ontology:<arm>`.
- `retrieval:vector_graph` — vector chunks + graph-as-context concatenated.

Provenance is shared: vector embeddings and the graph both derive from the same
gold references, so the only moving parts are retrieval mode and (for graph/hybrid)
ontology arm. A graph lane scoring below vector because its arm dropped a needed
metric (e.g. no GrossProfit node) is a *real finding about extraction
completeness*, not a retrieval bug — report it honestly (§20.8).

Fallback (only if a richer graph retrieval is attempted and underperforms): front
the pipeline with a query router classifying each query **fact-based** vs
**reasoning-based** (single-lookup vs multi-source/multi-step), filter first, then
do query understanding. Version that classifier as a `prompt:` if used.

FIBO grounding/conventions: `docs/ontology/ONTOLOGY_GUIDE.md` (EDM Council best
practices — IRI/label/definition conventions, polyhierarchy, OWL 2 DL
consistency). Keep any new/edited module YAML consistent with these conventions.
Owlready2 reasoning stays in the **offline** path only (CLAUDE.md §6.3) — never
in the request hot path.

Combined with the retrieval comparison, the full per-case condition set is:
**vector** (baseline) vs **graph × {non-ontology, small, medium, large}**. Report
all arms; the ontology sweep is the point, not an afterthought.

### Dataset

- File: `examples/datasets/finder/all_slices.csv` — **910 rows**, columns:
  `slice, _id, category, type, reasoning, n_refs, query_words, query, answer, references_joined`
- Context/derivation: `examples/datasets/finder/manifest.json` (source = FinDER train parquet,
  5703 rows; sampled with `seed=42`; categories: Financials, Company overview,
  Footnotes).
- `query` = the question, `answer` = gold answer, `references_joined` = the gold
  evidence passages (financial statement tables / 10-K text). `_id` is the case
  id; `n_refs` = number of gold reference passages.

### The 6 scenarios (hypotheses)

| Slice | n | Definition | Hypothesis |
|-------|---|-----------|-----------|
| S1_FIN_COMP | 277 | Financials ∧ Compositional | **graph** — multi-year/multi-row arithmetic over FinancialMetric{Company,Year} |
| S2_FIN_NONQUANT_MULTI | 165 | Financials ∧ type∈{None} ∧ n_refs≥2 | **graph** — cross-statement (IS+BS) synthesis via shared Company |
| S3_CO_COMP | 163 | Company overview ∧ Compositional | **graph** — part-whole (Segment/Region/Role) via HAS_SEGMENT |
| S4_CO_MULTI_NONQUANT | 39 | Company overview ∧ type∈{None} ∧ n_refs≥2 | **graph** — cross-segment narrative matrix; vector likely misses chunks |
| S5_FN_MULTI | 116 | Footnotes ∧ n_refs≥2 | **graph** — 10-K Item-spanning integration via shared entities |
| S6_BASELINE_SINGLE | 150 | target cats ∧ n_refs==1 ∧ ¬Compositional | **vector** — single-passage lookup; control slice |

S1–S5 are graph-favorable hypotheses; **S6 is the vector-baseline control**.
The experiment is meaningful only if S6 behaves as a control (no graph edge) —
treat a graph "win" on S6 as a red flag to investigate, not a result to report.

### Label caveat (carry forward)

`reasoning` and `type` are only meaningfully populated in **Financials** and
**Company overview**; Footnotes/baseline have them mostly null. Do not filter or
interpret these labels naively across categories. (See memory: FinDER label bias.)

### Infrastructure (verified 2026-05-30)

- LLM keys (OpenAI/xAI/DeepSeek/Moonshot): valid.
- DozerDB 5.26.3 enterprise up at `bolt://localhost:7687`, clean (0 nodes) —
  ready for ingest.
- Opik: self-hosted, workspace `default`, project `yitae-0530-grok` (`.env` + `~/.opik.config` aligned).
- Embeddings: OpenAI `text-embedding-3-small`, 1536-dim (repo standard, live-verified).

### Graph property discipline ("only necessary info")

Keep node properties minimal and reliably-fillable. Measured fill-rates drove these rules:
- **Tier-1 (keep, structured):** `name`, `value`/`amount`, `period`, `basis`
  (metrics), `segment` (Revenue), `currency` (MonetaryAmount/DebtInstrument only),
  `ticker` (LegalEntity). These are read by retrieval/answer and reliably filled.
- **Fold into the `value` string** (do NOT keep as separate, mostly-empty keys):
  `scale`, `unit`, and metric-level `currency`. Keep the figure exactly as written
  ("$125 million").
- **Forbidden — per-year property KEYS** (`value_2023`, `"2021"`, `amount_2024`,
  `principal_amount_2023`): one figure per node, use `value` + `period` instead.
  Enforced in `grok_meta_system_prompt.md`; the generic `Entity` baseline arm's
  key-explosion is the expected control failure mode — do not "fix" it.
- Drop free-text catch-alls (`LegalEntity.description`) and undefined props
  (`ProductOrService.classification`) that invite hallucination.

## 20. Experimenter Ethics (STRONG — binding for the vector-vs-graph study)

This is a measurement, not a narrative. Hold to these or the comparison is void:

1. **Data-grounded only.** Every reported number traces to a row in
   `examples/datasets/finder/all_slices.csv` and a run artifact. Never state a win/loss from
   intuition, from the manifest's *hypothesis*, or from a partial run. The
   manifest's "graph wins"/"vector wins" notes are **hypotheses to test, not
   findings to confirm**.
2. **No fabrication, no silent fallback.** If a retrieval call, embedding, or
   graph query fails, record it as a failure for that case — do not substitute a
   default, skip silently, or impute a score. Report N attempted vs N scored.
3. **Fair comparison.** Vector and graph lanes must see the **same cases, same
   gold answers, same judge/metric, same LLM where the pipeline uses one**.
   Any asymmetry (different prompt, different model, different top-k budget) is
   disclosed up front, not buried.
4. **Pre-register direction.** The §19 table is the pre-registered hypothesis
   per slice. Report results against it including the cases where the hypothesis
   was **wrong** — disconfirming evidence is the point, not noise to filter. The
   ontology sweep carries its own pre-registered hypothesis (Goldilocks: a
   middle ontology size beats both `non-ontology` and an over-large one); report
   the full non-ontology→small→medium→large curve even if it is flat or
   monotonic, and do not retrofit the small/medium/large module split after
   seeing results.
9. **Ontology is the only moving part across graph arms.** non-ontology / small
   / medium / large must differ **only** in the ontology passed to extraction —
   same documents, same extractor model, same chunking, same graph store, same
   retrieval and judge. Any other difference voids the ontology comparison.
5. **No cherry-picking / no p-hacking.** Report all 6 slices every time. Do not
   drop a slice, reselect cases, or tune the metric after seeing results. If the
   sample is too small to conclude (e.g. S4, n=39), say "underpowered", don't
   over-claim.
6. **Quantify uncertainty.** Per-slice n is known and uneven (277…39). Pair
   point estimates with a spread (CI or per-case win/loss counts), and never
   compare slices of very different n without noting it.
7. **Reproducible.** Fixed seed (42), pinned models, persisted run artifacts
   (JSONL traces + Opik). Another run on the same inputs must reproduce the
   reported numbers.
8. **Separate observation from interpretation.** State the measured result
   first; offer mechanism ("graph wins S1 likely because…") explicitly as
   interpretation, clearly labeled.
