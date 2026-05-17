# Build your own seocho project

A short guide for users who want to take what they learned from the FinDER tutorial bundle and turn it into their own project — with a consistent name, traceable authorship, and metadata that flows into the graph and the traces automatically.

## 1. Naming convention

Name your project `seocho-{{model_provider}}` where `{{model_provider}}` is the provider whose LLM you used. Concrete examples:

| Project name | LLM you used |
|---|---|
| `seocho-openai` | `openai/gpt-4o-mini` |
| `seocho-grok` | `grok/grok-4.20-reasoning` |
| `seocho-kimi` | `kimi/kimi-k2.5` |
| `seocho-deepseek` | `deepseek/deepseek-chat` |
| `seocho-qwen` | `qwen/qwen-plus` |

Why this scheme: it pairs *what you built* with *the model that made it*. When somebody finds your project on GitHub or in a paper, they immediately know which model generated the extractions — important for reproducibility and for honest comparison against another project that used a different model.

If you ran a head-to-head between two providers, name your project `seocho-{{primary}}-vs-{{other}}` (e.g., `seocho-grok-vs-openai`).

## 2. Required `.env` fields

Copy `.env.project.example` to `.env` in your project root and fill it in:

```bash
# === Identity ===
SEOCHO_PROJECT_NAME=seocho-openai          # the seocho-{{model_provider}} name
SEOCHO_LLM=openai/gpt-4o-mini              # provider/model — flows everywhere

# === LLM provider key (the matching one — only one is required) ===
OPENAI_API_KEY=sk-...
# DEEPSEEK_API_KEY=sk-...
# MOONSHOT_API_KEY=sk-...                  # for kimi
# XAI_API_KEY=xai-...                      # for grok
# DASHSCOPE_API_KEY=sk-...                 # for qwen

# === Author metadata ===
SEOCHO_AUTHOR_NAME=Hardy Jeong
SEOCHO_AUTHOR_EMAIL=hardy.jeong@example.com
SEOCHO_AUTHOR_GITHUB=tteon                 # GitHub username (no @, no URL)
SEOCHO_AUTHOR_AFFILIATION=Xcena            # company / lab / personal

# === Optional run metadata ===
SEOCHO_RUN_NOTES=baseline run on FinDER subset, no prompt tweaks
```

The four `SEOCHO_AUTHOR_*` values give your project provenance. They land:

- on every Opik trace span (Tutorial 4 demonstrates this) so traces are auditable to *who*;
- on every extracted entity via the `extracted_by` property (Tutorial 2 advanced section); and
- in the project name itself (`SEOCHO_PROJECT_NAME` becomes the default workspace_id prefix).

## 3. How the metadata flows

Once `.env` is filled in, three things happen automatically when you run a notebook in this bundle:

**Workspace separation** — set `WORKSPACE_ID = SEOCHO_PROJECT_NAME` at the top of any cell that creates a `Seocho` client or `IndexingPipeline`. Every node and relationship gets stamped with `_workspace_id="seocho-openai"` (or whatever you chose), so two projects can share a Neo4j without their data colliding.

**Trace identity** — Tutorial 4's `traced(...)` helper reads `SEOCHO_USER_ID` and the rest of the identity dict from env at startup. Add the four `SEOCHO_AUTHOR_*` values to that dict and they show up on every Opik span:

```python
IDENTITY = {
    "user_id": os.environ.get("SEOCHO_AUTHOR_GITHUB", "anonymous"),
    "workspace_id": os.environ["SEOCHO_PROJECT_NAME"],
    "llm_provider": LLM_PROVIDER,
    "llm_model": LLM_MODEL,
    "author_name": os.environ.get("SEOCHO_AUTHOR_NAME"),
    "author_email": os.environ.get("SEOCHO_AUTHOR_EMAIL"),
    "author_affiliation": os.environ.get("SEOCHO_AUTHOR_AFFILIATION"),
    "run_notes": os.environ.get("SEOCHO_RUN_NOTES"),
}
```

**Entity provenance** — Tutorial 2's advanced section demonstrates the `on_before_write` callback that stamps `extracted_at` and `extracted_by` on every entity. Extend it to also record the project name:

```python
def stamp_runtime_metadata(nodes, rels):
    for n in nodes:
        props = n.setdefault("properties", {})
        props.setdefault("project", os.environ["SEOCHO_PROJECT_NAME"])
        props.setdefault("extracted_at", RUN_AT)
        props.setdefault("extracted_by", f"{LLM_PROVIDER}/{LLM_MODEL}")
        props.setdefault("author", os.environ.get("SEOCHO_AUTHOR_GITHUB"))
    return nodes, rels
```

After indexing, every entity in your graph is now traceable back to:
- which project it came from
- which model produced it
- when it was produced
- who ran the project

That's enough provenance for honest reproducibility and for citing your work later.

## 4. Recommended project layout

```
seocho-openai/                          ← your project root
├── README.md                           ← describe what you built + what FinDER subset
├── .env                                ← (gitignored) your filled-in secrets + identity
├── .env.example                        ← copy of this guide's template, no secrets
├── notebooks/
│   ├── 01_my_extraction.ipynb          ← your customized version of T2
│   ├── 02_my_analytics.ipynb           ← your customized version of T3
│   └── ...
├── ontology/
│   ├── base.jsonld                     ← your ontology in JSON-LD
│   └── overlays/                       ← TTL overlays you compose in (`+` / `-`)
├── results/
│   ├── extraction_metrics.json         ← entity counts, confidence histograms
│   ├── network_metrics.json            ← PageRank top-N, communities, etc.
│   ├── traces/                         ← JSONL traces or Opik export
│   └── viz/                            ← saved matplotlib figures
└── seocho_pinned_version.txt           ← `pip freeze | grep seocho` snapshot
```

The `seocho_pinned_version.txt` matters: seocho's API stabilizes over time but extraction prompts and default behaviors evolve. A reader six months later wants to know exactly which seocho they need to reproduce your numbers.

## 5. When you publish

Three things to include in your project README so others can build on your work:

1. **Provider + model** — already in `SEOCHO_PROJECT_NAME` and `SEOCHO_LLM`, but say it again in plain English at the top of the README.
2. **Ontology** — link to the file in your repo, *and* mention if you composed it from FIBO modules or TTL overlays. Other readers want to fork the ontology, not just the notebook.
3. **Cost / token / latency budget** — the metrics from your `results/` directory. Your replication baseline.

Optional but generous: link back to the upstream `tteon/seocho` repo and the FinDER tutorial bundle so readers know the origin of the patterns.

## 6. Sharing back

If you find a pattern that should be in the upstream tutorial — a new ontology module, a useful helper for `examples/finder/lib/`, a fix to one of the four notebooks — open a PR against `tteon/seocho`. Mark your project name and provider in the PR description so reviewers can see what context the change came from.

## 7. Tracing and data privacy

When tracing is on, **the LLM prompts and completions appear in your Opik traces**. For the FinDER tutorial that's fine (synthetic 10-K snippets), but if you point the same notebooks at your own corporate documents, those documents' text shows up in Opik. Decide where it lands before you turn tracing on.

Three correct configurations, in increasing order of privacy concern:

| Option | What you set | Where traces go | Sharing scope |
|---|---|---|---|
| **A. No tracing** (default) | nothing | `./.seocho/private_<user>/traces.jsonl` | local file only |
| **B. Self-hosted Opik** | `OPIK_URL_OVERRIDE=http://your-host:5173/api/` (note `/api/` suffix), `OPIK_WORKSPACE=default` | your own server | whoever can reach the URL |
| **C. Per-user Opik cloud account** | `OPIK_API_KEY=<your-own-key>` and `OPIK_WORKSPACE=<your-workspace>` — leave `OPIK_URL` *unset* | Comet/Opik cloud, your workspace | only you (one account per person) |

> **Common mistake.** Setting `OPIK_URL=https://www.comet.com/opik` (the UI URL) breaks with `405 Not Allowed` from nginx — Opik's API lives at `/opik/api/`, not `/opik/`. For cloud, leave the URL unset; the SDK auto-routes to the right endpoint.

> **Do not share `OPIK_API_KEY`.** A typical key has read access to *all* projects in the workspace — handing it to a colleague leaks every other project you have. If multiple people need to land traces in the same place, use option B (self-hosted) or have each person sign up for their own cloud account in option C. **Never** commit the key; `.env` is gitignored for a reason.

## 8. Centralized Opik for sharing tutorial results

If your team wants one place where everyone's `seocho-{provider}` runs land together — so you can compare traces side by side, cite each other's work, or do code review on extraction quality — the right setup is **one self-hosted Opik that everyone points at**. No keys to share, no per-person cloud accounts.

### Operator setup (do this once on a host you control)

```bash
# On the host machine — say, internal-tools.your-company.local
git clone https://github.com/tteon/seocho.git && cd seocho
cp .env.example .env
# Edit .env: set OPIK_VERSION pin, optionally non-default OPIK_*_PASSWORD
make opik-up                                   # starts the full Opik stack
# UI:           http://that-host:5173
# Backend API:  http://that-host:8000
```

The default `SEOCHO_BIND_HOST=127.0.0.1` keeps Opik bound to localhost. To expose it to teammates on the same VPN, set `SEOCHO_BIND_HOST=0.0.0.0` in `.env` and re-run `make opik-up`. **Do not expose it to the public internet without a reverse proxy that adds auth and TLS** — the default Opik install has no built-in user authentication.

### What each user puts in their `.env`

```bash
# Centralized Opik instance (the URL the operator gave you)
OPIK_URL=http://internal-tools.your-company.local:5173

# A workspace shared by the whole team
OPIK_WORKSPACE=seocho-tutorials

# Project name = your seocho-{provider}-{your-handle} so projects don't collide
OPIK_PROJECT_NAME=seocho-openai-tteon
```

### What it looks like in the Opik UI

```
seocho-tutorials/                           ← shared workspace
├── seocho-openai-tteon                     ← Hardy's OpenAI run
├── seocho-openai-alice                     ← Alice's OpenAI run (same model, different ontology)
├── seocho-grok-bob                         ← Bob's Grok run
└── seocho-kimi-tteon-vs-grok               ← Hardy's head-to-head comparison
```

Every span carries the author identity from the `SEOCHO_AUTHOR_*` env vars (Section 3), so even within one project you can tell *which user* generated which run. Comparing two providers becomes a single Opik filter: `tag:user:tteon`.

### Project name = composite identifier

The `OPIK_PROJECT_NAME` value should encode *what was built* + *who built it* + *which model*. Recommended template:

    seocho-{provider}-{author_github}

That keeps the `seocho-{{model_provider}}` repo-naming convention (Section 1) intact while still distinguishing two people who used the same provider. For comparison runs, append the second provider:

    seocho-{primary}-vs-{other}-{author_github}

### Cleanup / maintenance

- **Trim old projects.** Opik never auto-deletes; expose a janitor script or set a retention policy in the Opik admin UI.
- **Backups.** The state lives in `data/opik-mysql` + `data/opik-clickhouse` + `data/opik-minio`. Snapshot those volumes if the traces are decision-critical.
- **Version pin.** Set `OPIK_VERSION` in the operator's `.env` and *don't bump it without warning users* — minor versions occasionally rename span fields, and your historical traces won't migrate.

## 9. Adding observability to your own code

Once you start a `seocho-{provider}` project on top of the tutorial bundle, you'll want tracing on the new code you write — not just the parts borrowed from T4. Three ways, in increasing manual effort:

### A. Let an AI coding agent do it (fastest)

The Opik team ships a skill that any agent supporting the skills protocol (Claude Code, Codex, Cursor, OpenCode, Aider, …) can install. The skill reads your code, picks the right Opik integration for the framework you're using, and writes the tracing wiring for you.

```bash
# 1. Install the skill once into your agent
npx skills add comet-ml/opik-skills

# 2. In your agent, ask it to instrument the project
#    "Instrument my agent with Opik using the /instrument command."
```

The agent will: detect your LLM SDK + agent framework, pick the matching Opik integration (`opik.integrations.openai`, `opik.integrations.langchain`, etc.), wrap the entry points with `@track`, thread the project name through, and update your `.env` with the right keys. Re-run; traces show up in your Opik project.

This works on top of what T4 already wires up — the skill won't fight your existing `enable_tracing(...)` call, it'll just add `@track` decorators and integration wrappers to the new files you've written.

### B. Opik Connect

For frameworks the Opik SDK already integrates with directly (OpenAI, LangChain, LlamaIndex, LangGraph, Anthropic, etc.), you don't need an agent. Add the matching wrapper near your client construction:

```python
from openai import OpenAI
from opik.integrations.openai import track_openai

client = track_openai(OpenAI())   # every chat.completions.create() now traced
```

This is the minimum for projects that don't have unusual control flow — `track_openai`/`track_anthropic`/etc. capture the LLM I/O without you having to write the span yourself.

### C. Manual integration (what T4 does)

When the integration wrappers don't cover your code path — custom retrieval pipelines, tool dispatch, multi-agent orchestration — you write spans by hand. T4's `traced(name, input_data=..., output_data=..., tags=...)` helper is the pattern: it wraps `seocho.tracing.log_span` to stamp the active `IDENTITY` on every span. Use it where the integration wrappers can't see — around your retrieval call, your tool selection, your synthesis step.

```python
traced(
    "retrieval.graph",
    input_data={"question": q},
    output_data={"records": len(records)},
    tags=["routing:graph"],
)
```

After running, every span lands in the same Opik project under your `workspace_id`, regardless of whether it came from the skill, an integration wrapper, or a hand-written `traced(...)` call. **Ollie**, the Opik coding agent, can then read those traces and propose code changes for the failing assertions — that's the loop the test suite in T4 §8 sets up.
