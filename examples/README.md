# Examples

Hands-on Jupyter notebooks for learning SEOCHO by climbing one layer at a time.
This is the canonical home for runnable notebooks and example assets.
Legacy exploratory notebooks now live under `examples/labs/legacy/` and should
not receive new onboarding or public example content.

## The teaching arc

The three numbered tutorials form a strict-superset stack — each layer adds capability without changing the layer beneath it. Stop at whichever layer is enough for your problem.

| # | Notebook | Layer | What it adds |
|---|----------|-------|--------------|
| 1 | [tutorial_01_vanilla_llm.ipynb](tutorial_01_vanilla_llm.ipynb) | **Vanilla LLM** | `backend.complete(system, user)` over five providers; the instruction-design lever; provider-quirk handling (Kimi temp clamp, JSON-mode fallback, reasoning-content fallback). |
| 2 | [tutorial_02_agent_enhancement.ipynb](tutorial_02_agent_enhancement.ipynb) | **Agent** | Three pillars — tool use (8 typed tools), multi-turn cache (`Session.add/ask/run`), Opik observability. RoutingPolicy (`fast/balanced/thorough`). Composition patterns: sequential, parallel, supervisor. Adaptive escalation + trace-driven policy selection. |
| 3 | [tutorial_03_ontology_indexing.ipynb](tutorial_03_ontology_indexing.ipynb) | **Ontology** | Replace the hand-written prompt with a versioned schema. Load FIBO Turtle → `seocho.Ontology`. Extend with `merge()`. Diff with `migration_plan()`. Stamp every span with `context_hash`. Pin inferred rules with `ontology_identity_hash`. |

Companion entry points:

| Notebook | When to open |
|----------|------------------|
| [quickstart.ipynb](quickstart.ipynb) | The "run-everything-once" tour: ontology, indexing/agent design YAML, four-provider comparison, observability. |
| [bring_your_data.ipynb](bring_your_data.ipynb) | Plug in your own data — text files, CSV, JSON. Reuse the patterns from tutorials 1–3. |

Supporting subtrees:

- `demos/` — demo scripts and tracing-focused examples.
- `labs/legacy/` — older exploratory notebooks preserved for reference.
- `teaching/` — longer-form teaching curriculum, slide decks, and chapter notes.

## What each tutorial actually teaches

### Tutorial 1 — the floor

- `create_llm_backend(provider, model)` over OpenAI / xAI / Kimi / DeepSeek / Qwen behind one interface.
- `backend.complete(system, user)` — one primitive, one shape.
- The **instruction-design lever**: same model, three system prompts, three different output shapes (role + scope · JSON-mode constraint · few-shot).
- Why hand-written prompts don't scale → motivates the next layer.

### Tutorial 2 — agents in three pillars

- **Pillar 1, tool use** — the LLM picks among 8 typed tools (extract, validate, score, link, write, text2cypher, execute, search).
- **Pillar 2, multi-turn cache** — `Session` keeps an entity index + query cache; same question = no LLM call.
- **Pillar 3, observability** — JSONL + Opik spans for every `add() / ask() / run()`, stamped with `user_id` and `workspace_id`.
- **RoutingPolicy** — three named modes (`fast()` 0.55/0r/no-repair · `balanced()` 0.70/1r/1-repair · `thorough()` 0.85/2r/3-repair). Same call, different trace shape.
- **Composition patterns** (§6): sequential basic/advanced · parallel basic/advanced (vote across policies) · supervisor basic/advanced · adaptive escalation (`fast()` → `thorough()` on `degraded`) · trace-driven policy selection (last-N Opik traces feed the next policy choice).

### Tutorial 3 — schema as compiled context

- Load `examples/datasets/fibo_be_minimal.ttl` with `Ontology.load(...)` or `Ontology.from_ttl(...)` → `seocho.Ontology` (rdflib pre-converts Turtle for owlready2).
- **Section A** — vanilla LLM + ontology: drop `ontology.to_extraction_context()` into the system prompt.
- **Section B** — agent + ontology: `Seocho.local(ontology, ...)` with `compile_ontology_context()` stamping `context_hash` on every span.
- **Section C** — evolution: extend FIBO with a non-FIBO `Product` node via `merge(strategy='union')`; produce a Cypher migration plan with `migration_plan(v1, v2)`; verify that v1 and v2 hashes differ.
- **Rule inference** — `infer_rules_from_graph()` returns a `RuleSet` carrying `ontology_identity_hash`; promotion gates can match / block / require review on hash drift.
- **§8 Why seocho** — the ~1,000 lines of glue (Turtle dispatch, Cypher injection guard, asyncio-in-Jupyter, fallback contract, multi-tenant span metadata, migration diff…) that the SDK absorbs so you don't pay the bill more than once. Includes an honest "when NOT to use seocho" disclaimer.

## The end-to-end contract these tutorials prove

| Field | Set in | Read in |
|-------|--------|---------|
| `context_hash` | `compile_ontology_context()` | every span metadata · `IndexingResult` · `RuleSet` |
| `ontology_identity_hash` | `infer_rules_from_graph()` | `RuleSet` promotion gate |
| `user_id` | `Seocho(user_id=...)` | every span metadata |
| `workspace_id` | `Seocho(workspace_id=...)` | every span metadata + Cypher partition |
| `session_id` | auto, per `s.session(...)` | parent trace ID |
| `degraded` / `fallback_from` / `fallback_reason` | `Session._add_via_agent` on exception | span metadata |

If the FIBO version changes, every artifact tagged with the old hash is traceable as such. **That is the contract.**

## Companion deck

A 14-slide HTML deck rendering the same arc against the **FinDER** financial-QA benchmark (5,703 expert-annotated 10-K queries) and the **FIBO** best-practices guide:

- [`tutorial_slides.html`](tutorial_slides.html) — the arc as taught in these three notebooks.
- [`teaching/seocho_finder_fibo_teaching.html`](teaching/seocho_finder_fibo_teaching.html) — apply that arc to a real benchmark dataset (FinDER) with the FIBO subset shipped here.

Open either with any browser; both use Reveal.js from CDN — no build step.

## Prerequisites

```bash
uv pip install "seocho[local]" python-dotenv jupyter
cp ../.env.example ../.env
```

The notebooks load provider credentials from `../.env`. Tutorial 3 also needs `owlready2` and `rdflib` (the Tutorial 3 setup cell installs them on demand).

- default first run: embedded LadybugDB
- optional production-like path: set both `NEO4J_URI` and `NEO4J_PASSWORD` in `../.env` and the notebook switches to Bolt-backed Neo4j/DozerDB automatically
- if `NEO4J_URI` is present but `NEO4J_PASSWORD` is empty, the notebook keeps using LadybugDB and prints the fallback reason

## Running

```bash
cd examples
jupyter notebook
```

Then open the notebook and run cells top to bottom. All three tutorials are **idempotent** — setup cells install only what's missing, `load_dotenv()` won't override existing keys, `nbformat` validates clean.

## Common gotchas

| Symptom | Cause | Fix |
|---------|-------|-----|
| `(none — set at least one *_API_KEY)` | No provider key in env or `.env` | Re-run setup cell, or set via the `getpass` prompt cell |
| `ModuleNotFoundError: No module named 'agents'` | Installed bare `seocho`; agent mode needs `[local]` extra | Re-install with `seocho[local]` |
| `OwlReadyOntologyParsingError` on `.ttl` | owlready2 dispatches Turtle to NTriples parser | Already fixed in 0.3.1+: rdflib pre-converts to RDF/XML |
| `asyncio.run() cannot be called from a running event loop` | Pre-0.3.1 SDK with Jupyter | Upgrade to 0.3.1+ — uses a loop-aware `_run_sync` |
| `degraded=True` / `fallback_from='agent'` | Agent path raised; pipeline picked up | Read `fallback_reason` on the result; common cause: model rate-limit |

## Datasets shipped here

See [`datasets/README.md`](datasets/README.md). Headlines:

- `datasets/tutorial_filings_sample.json` — 10 filings cases, **same eight categories as FinDER** (Financials, Company Overview, Governance, Legal, Risk, Shareholder Return, Accounting, Footnotes). Use for onboarding/smoke; do not publish results as benchmark evidence.
- `datasets/fibo_be_minimal.ttl` — 5 classes / 4 datatype / 2 object properties; every class carries `skos:exactMatch` back to canonical FIBO.
- `datasets/fibo_base.jsonld` · `fibo_minus.jsonld` · `fibo_plus.jsonld` — JSON-LD variants used by `bring_your_data.ipynb`.
