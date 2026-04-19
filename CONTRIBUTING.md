# Contributing to SEOCHO

Welcome. This guide gets you from zero to your first PR.

## Quick Setup

```bash
git clone git@github.com:tteon/seocho.git
cd seocho
pip install -e ".[dev]"
python -m pytest seocho/tests/ -q   # ~305 tests, should pass in a couple of seconds
```

## Pick a usecase to contribute around

The fastest way to get oriented is to read
[`docs/USECASES.md`](docs/USECASES.md) and run one of the working demos.
A usecase entry lands there only when the accompanying `examples/<name>/`
runs end-to-end — so what you see is what actually works. The current set
is intentionally small (finance compliance is the first). We welcome PRs
that add new usecases, additional mock docs, or Q&A evaluations against
existing ones.

## Branch + PR Workflow

We do **not** push directly to `main`. All changes go through PRs.

```bash
# 1. Create a feature branch
git checkout -b feat/my-feature

# 2. Make changes, run tests locally
python -m pytest seocho/tests/ -q

# 3. Commit with conventional prefix
git commit -m "feat: add PDF support to file indexer"

# 4. Push and open PR
git push -u origin feat/my-feature
# Open PR on GitHub → CI runs → review → /go to merge
```

### Branch naming

| Prefix | Purpose |
|--------|---------|
| `feat/` | New feature |
| `fix/` | Bug fix |
| `refactor/` | Code restructuring |
| `ci/` | CI/workflow changes |
| `docs/` | Documentation only |

### CI checks (must pass before merge)

| Check | What it does |
|-------|-------------|
| **lint** | py_compile syntax/import sanity on critical SDK and ingestion modules |
| **sdk-tests** | `seocho/tests/` on Python 3.10, 3.11, 3.12 |
| **server-tests** | `extraction/tests/` on Python 3.11 |
| **docs** | Doc contract + agent doc lint |

### Nightly integration smoke

- `.github/workflows/nightly-e2e-smoke.yml` runs the dockerized runtime smoke path
- it validates `make e2e-smoke` against the platform stack on a schedule or by manual dispatch
- when the smoke job fails, treat that as input for the `e2e-investigation` Codex lane

## Local Codex CLI Automation

Codex authors bounded draft PRs from a local clean clone. GitHub Actions does
not run Codex directly.

Available lanes:

```bash
scripts/codex/run_feature_improvement.sh
scripts/codex/run_refactor.sh
scripts/codex/run_e2e_investigation.sh
```

Rules:

- run from a clean clone checked out to `main`
- each run opens or updates one draft PR only
- each run must choose exactly one lane
- PR bodies must include `Feature`, `Why`, `Design`, `Expected Effect`,
  `Impact Results`, `Validation`, and `Risks`
- CI remains deterministic; Codex is the PR author, not the CI gate

Jules should treat those PRs as the primary unit of work and only repair
failing CI or directly related narrow issues.

### Merging

- Maintainer comments `/go` on an approved PR to squash-merge
- PRs require CI to pass (branch protection enforced)
- Linear history only (squash merge, no merge commits)

## Where to Look

```
seocho/
├── index/              ← Data Plane: putting data IN
│   ├── pipeline.py     ← chunking → extract → validate → write
│   ├── linker.py       ← embedding-based entity relatedness (canonical)
│   └── file_reader.py  ← read .txt/.csv/.json/.jsonl/.pdf files
│
├── query/              ← Control Plane: getting data OUT
│   ├── strategy.py     ← ontology → LLM prompt generation
│   └── cypher_builder.py ← deterministic Cypher from intent
│
├── store/              ← Storage backends
│   ├── graph.py        ← Neo4j/DozerDB + embedded LadybugDB
│   ├── vector.py       ← FAISS/LanceDB similarity search
│   └── llm.py          ← OpenAI-compatible completions
│
├── rules.py            ← SHACL-like rule inference + validation
├── ontology.py         ← Schema: JSON-LD + SHACL + merge + migration
├── session.py          ← Agent-level session with context + tracing
├── agents.py           ← IndexingAgent / QueryAgent / Supervisor
├── tools.py            ← @function_tool definitions for agents
├── agent_config.py     ← AgentConfig, RoutingPolicy, presets
├── tracing.py          ← Pluggable tracing (Opik, JSONL, console)
├── experiment.py       ← Workbench for parameter exploration
├── local_engine.py     ← Local-mode orchestration behind the SDK facade
├── client_remote.py    ← HTTP transport behind the facade
├── client_bundle.py    ← Runtime-bundle import/export behind the facade
├── client.py           ← Public SDK facade (Seocho class)
└── tests/              ← SDK test suite (~305 tests)
```

The deployment shell lives under `runtime/` (transport entrypoint
`runtime/agent_server.py`, composition in `runtime/server_runtime.py`).
Legacy `extraction/*` modules are being reduced to transport/compat
adapters as canonical engine code moves under `seocho/*`. See
[docs/RUNTIME_PACKAGE_MIGRATION.md](docs/RUNTIME_PACKAGE_MIGRATION.md).

### I want to...

| Goal | Start here |
|------|-----------|
| Improve entity extraction quality | `seocho/index/pipeline.py` |
| Add a new file format (e.g. PDF) | `seocho/index/file_reader.py` |
| Improve Cypher generation | `seocho/query/cypher_builder.py` |
| Add a new graph database backend | `seocho/store/graph.py` |
| Add a new LLM provider | `seocho/store/llm.py` |
| Extend ontology features | `seocho/ontology.py` |
| Add a new agent tool | `seocho/tools.py` |
| Add a routing policy | `seocho/agent_config.py` |
| Improve session context | `seocho/session.py` |
| Add a starter ontology for a new domain | new directory under `examples/` + link from `docs/USECASES.md` |

## Running Tests

```bash
# SDK tests (fast, no external services)
python -m pytest seocho/tests/ -v

# Server-side tests (need extraction/ dependencies)
python -m pytest extraction/tests/ -v

# Single test file
python -m pytest seocho/tests/test_session_agent.py -v

# With coverage
python -m pytest seocho/tests/ --cov=seocho --cov-report=term-missing
```

## Commit Conventions

Strict semantic versioning prefixes (enforced by convention):

| Prefix | When | Example |
|--------|------|---------|
| `feat:` | New feature | `feat: add ontology merge` |
| `fix:` | Bug fix | `fix: Kimi temperature auto-safety` |
| `refactor:` | No behavior change | `refactor: explicit execution_mode` |
| `docs:` | Documentation | `docs: update quickstart` |
| `test:` | Tests only | `test: add merge conflict tests` |
| `chore:` | Tooling, deps | `chore: sync beads state` |
| `ci:` | CI/workflow | `ci: add Python 3.12 matrix` |

## Architecture

```
                    Ontology (seocho/ontology.py)
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         Data Plane            Control Plane
        (seocho/index/)       (seocho/query/)
              │                     │
              ▼                     ▼
         seocho/store/         seocho/store/
        (graph write)          (graph query)
              │                     │
              └──────────┬──────────┘
                         ▼
                    Session + Agents
                   (seocho/session.py)
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         Indexing    Query     Supervisor
          Agent      Agent     (hand-off)
```

### Key concepts

- **Ontology-first**: Ontology drives extraction prompts AND query prompts
- **3 execution modes**: `pipeline` (deterministic), `agent` (tool use), `supervisor` (hand-off)
- **RoutingPolicy**: 3-axis trade-off (latency, token_efficiency, information_quality)
- **Structured context**: Entities/relationships cached across session rounds
- **JSON-LD canonical**: Ontology stored as JSON-LD, SHACL derived automatically

## Key Design Decisions

- JSON-LD is the canonical ontology storage format
- SHACL shapes are derived (never hand-written)
- Denormalization safety is determined by relationship cardinality
- `seocho/ontology.py` is the single source of truth
- Temperature constraints are handled at the LLM backend level (not per call-site)
- Neo4j defaults to `bolt://localhost:7687` (Docker services override internally)

See [`docs/decisions/DECISION_LOG.md`](docs/decisions/DECISION_LOG.md) for all ADRs.
New architecture-level changes land an ADR under
`docs/decisions/ADR-NNNN-<slug>.md` plus a DECISION_LOG entry before merge.

## Issue + task tracking (`.beads`)

Day-to-day task tracking lives in `.beads/` via the `bd` CLI, not in GitHub
Issues. Common commands:

```bash
bd ready                       # what's unblocked right now
bd show <id>                   # open a specific bead
bd update <id> --status in_progress   # claim a bead
scripts/pm/new-task.sh         # create a new task with required labels
```

Bead labels (`sev-*`, `impact-*`, `urgency-*`, `sprint-*`, `roadmap-*`,
`area-*`, `kind-*`) are required on active work. See
[`docs/ISSUE_TASK_SYSTEM.md`](docs/ISSUE_TASK_SYSTEM.md) for the full policy.

## Code Style

- Type hints on function signatures
- `logging.getLogger(__name__)` (no `print()`)
- No hardcoded credentials
- Max line length: 120 chars
- Parameter names: use `extraction_prompt` (not `prompt_template`) at public API boundaries
