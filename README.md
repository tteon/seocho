# SEOCHO

**Ontology-aligned middleware between your agents and your graph database.**

[![PyPI](https://img.shields.io/pypi/v/seocho)](https://pypi.org/project/seocho/)
[![Tests](https://img.shields.io/badge/tests-139%20passed-green)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

You declare the ontology. You call `add()` and `ask()`. Under the hood, SEOCHO
runs the agent reasoning and policy layer that keeps the graph database, the
semantic artifacts, and the agent behavior all consistent with your one
schema contract.

What this means in practice:

- **Agents stay in-schema.** Every extraction, every Cypher query, every
  answer is grounded in the ontology you defined — not in a prompt template
  that drifts.
- **The database stays in-schema.** Constraints, indexes, SHACL-like rules,
  and runtime semantic artifacts are all derived from the same ontology.
- **You stay in control.** One schema change propagates everywhere: agent
  prompts, query planning, validation, and governance artifacts.

Compared to peer libraries:

| Library | Core value |
|---------|------------|
| mem0 | generic memory for agents |
| Graphiti (Zep) | temporal knowledge graph |
| LlamaIndex | ecosystem + integrations |
| **SEOCHO** | **ontology alignment between agent and graph DB** |

## Quick Start

```bash
uv pip install "seocho[embedded]"    # zero-config, embedded LadybugDB
# or: uv pip install "seocho[local]" # with Neo4j/DozerDB
```

```python
from seocho import Seocho, Ontology, NodeDef, RelDef, Property

# 1. Define your schema
ontology = Ontology(
    name="my_domain",
    nodes={
        "Person":  NodeDef(properties={"name": Property(str, unique=True)}),
        "Company": NodeDef(properties={"name": Property(str, unique=True)}),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company"),
    },
)

# 2. Zero-config local client — uses embedded LadybugDB, no server needed
s = Seocho.local(ontology)

# 3. Index
s.add("Marie Curie worked at the University of Paris.")

# 4. Query
print(s.ask("Where did Marie Curie work?"))
```

Override the defaults when needed:

```python
s = Seocho.local(
    ontology,
    llm="deepseek/deepseek-chat",               # or "kimi/kimi-k2.5", "openai/gpt-4o-mini"
    graph="bolt://neo4j.internal:7687",
    neo4j_user="neo4j",
    neo4j_password="••••",
)
```

HTTP client mode (no local DB needed):

```python
from seocho import Seocho
s = Seocho.remote("http://localhost:8001")
```

Read next:

- [Why SEOCHO](docs/WHY_SEOCHO.md)
- [Quickstart (docs)](docs/QUICKSTART.md)
- [Python SDK Quickstart](docs/PYTHON_INTERFACE_QUICKSTART.md)
- [Files and Artifacts](docs/FILES_AND_ARTIFACTS.md)
- [OntologyRunContext Strategy](docs/ONTOLOGY_RUN_CONTEXT_STRATEGY.md)
- [PropertyGraphLens Strategy](docs/PROPERTY_GRAPH_LENS_STRATEGY.md)
- [Benchmarks](docs/BENCHMARKS.md)

## Install Paths

| Path | Install | What else you need |
|------|---------|--------------------|
| HTTP client mode | `pip install seocho` | a running SEOCHO runtime (`base_url=...`) |
| Local SDK engine (published package) | `pip install "seocho[local]"` | a reachable DozerDB/Neo4j instance and provider credentials |
| Repository development | `pip install -e ".[dev]"` | local clone + test/tooling deps |
| Offline ontology governance | `pip install "seocho[ontology]"` | local ontology files only |

Notes:

- `pip install seocho` is intentionally thin. It is enough for HTTP client mode and bundle consumption.
- Local engine mode is where DozerDB/Neo4j is core: `Seocho.local(...)` wires the store and llm for you, or pass them explicitly with `Seocho(ontology=..., graph_store=..., llm=...)`.
- `pip install "seocho[local]"` adds the dependencies needed for published-package local engine use without pulling the full repo development toolchain.
- The fastest full local stack is still `make setup-env && make up`.

## Why SEOCHO

Most memory libraries optimize for the fastest generic demo. SEOCHO optimizes
for graph-native teams that need a stronger contract between ontology, runtime,
and agent behavior.

- ontology-first, not prompt-first
- graph-native, not vector-only
- schemaless property graph plus agent-visible semantic overlay
- governed artifacts, not ad hoc schema drift
- local SDK authoring and runtime consumption on one contract

## Architecture Overview

Internally, the ontology layer is split into clear boundaries:

- `seocho/ontology.py`: public schema facade
- `seocho/ontology_serialization.py`: JSON-LD persistence
- `seocho/ontology_artifacts.py`: runtime artifact and prompt-context promotion
- `seocho/ontology_governance.py`: offline diff/check/export path

`Seocho` itself is a facade: canonical query, agent, ontology, transport, and
artifact helpers live under `seocho/*`, while `client.py` stays focused on the
public SDK entrypoints. The server side follows the same rule:
`runtime/agent_server.py` is the transport entrypoint, while shared runtime
service composition lives in `runtime/server_runtime.py`. The legacy
`extraction/*` modules now preserve flat import compatibility while the
deployment shell migrates to `runtime/`. The current local compose service is
still named `extraction-service`; it bind-mounts `runtime/` and `seocho/` so
the historical flat entrypoint can delegate to canonical runtime code.

Long-term, the overloaded `extraction/` package name is being retired in favor
of a thinner `runtime/` deployment shell. The staged plan is documented in
[docs/RUNTIME_PACKAGE_MIGRATION.md](docs/RUNTIME_PACKAGE_MIGRATION.md).

Legacy extraction modules are being reduced to transport or compatibility
adapters as canonical engine code moves under `seocho/*`. The shared extraction
seam lives at `seocho/index/extraction_engine.py` — both the SDK and the server
runtime call it instead of keeping a second prompt/normalization path.
Deterministic runtime memory-graph shaping and semantic-artifact helpers live
under `seocho/index/runtime_memory.py` and `seocho/index/runtime_artifacts.py`
so runtime-only wrappers no longer own that logic outright.

## Choose Your Runtime Shape

| Mode | Constructor | Best for |
|------|-------------|----------|
| HTTP client mode | `Seocho(base_url="http://localhost:8001", workspace_id="default")` | consume an existing runtime over HTTP |
| Local engine mode | `Seocho(ontology=..., graph_store=..., llm=...)` | SDK authoring, experiments, direct graph access |
| Local platform runtime | `make up` or `seocho serve` | UI + API + DozerDB on one machine |

For local engine mode, `Neo4jGraphStore` works against both Neo4j and DozerDB over Bolt:

```python
from seocho.store import Neo4jGraphStore

store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
```

Core runtime parameters you need to understand early:

- `base_url`: remote SEOCHO runtime root for HTTP client mode
- `workspace_id`: logical scope passed through runtime-facing requests
- `graph_store`: Bolt-backed graph store for local engine mode
- `reasoning_mode`: bounded semantic repair loop for hard questions
- `repair_budget`: max additional repair attempts when retrieval is insufficient

## Common Use Cases

### 1. Consume an existing SEOCHO runtime over HTTP

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")
print(client.ask("What do we know about ACME?"))
```

### 2. Build locally against your own ontology and graph

```python
from seocho import Seocho, Ontology
from seocho.store import Neo4jGraphStore, OpenAIBackend

client = Seocho(
    ontology=Ontology.from_jsonld("schema.jsonld"),
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password"),
    llm=OpenAIBackend(model="gpt-4o-mini"),
    workspace_id="default",
)

client.add("ACME acquired Beta in 2024.")
print(client.ask("Who did ACME acquire?", reasoning_mode=True, repair_budget=2))
```

The same ontology object can also be promoted into the runtime artifact
contract instead of maintaining a second schema representation:

```python
artifacts = client.approved_artifacts_from_ontology()
prompt_context = client.prompt_context_from_ontology(
    instructions=["Prefer finance ontology labels and relationships."]
)
draft = client.artifact_draft_from_ontology(name="finance_core_v1")

client.add_with_details(
    "ACME acquired Beta in 2024.",
    prompt_context=prompt_context,
    approved_artifacts=artifacts,
)
```

### 3. Run the local platform stack with UI + API + graph DB

```bash
make setup-env
make up
```

Then open:

- UI: `http://localhost:8501`
- API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

See [docs/FILES_AND_ARTIFACTS.md](docs/FILES_AND_ARTIFACTS.md) for where
`schema.jsonld`, graph data, rule profiles, semantic artifacts, and traces live.

## What the Ontology Controls

| Stage | What happens |
|-------|-------------|
| **Extraction** | Entity types + relationships in LLM prompt |
| **Querying** | Schema-aware Cypher generation and repair prompts |
| **Validation** | SHACL shapes derived → catches type/cardinality errors |
| **Constraints** | UNIQUE/INDEX generated from ontology and can be applied to Neo4j |
| **Denormalization** | Cardinality rules determine safe flattening |
| **Glossary** | SKOS-style vocabulary terms, aliases, and hidden labels are compiled into the ontology context identity |
| **Reasoning** | Optional low-quality retry re-extracts with ontology guidance |
| **Runtime parity** | The same ontology can be converted into approved semantic artifacts and typed prompt context |
| **Agent context** | A stable ontology context hash follows indexing, graph writes, query traces, and agent hand-off metadata |

Local SDK writes now persist compact `_ontology_*` graph properties on nodes and
relationships. Local queries and agent query tools compare the active ontology
context hash with hashes already indexed in the graph and surface any mismatch
in trace/tool metadata. This is a guardrail, not a hard blocker: it tells you
when a graph may need re-indexing under the current ontology profile. HTTP
runtime `search_with_context(...)`, `chat(...)`, and `semantic(...)` responses
also expose `ontology_context_mismatch` so client code can audit graph/profile
drift without dropping to raw API payloads. Router, debate, execution-plan, and
platform chat responses expose the same field as a top-level typed SDK value,
so application code can treat ontology/database parity as middleware metadata
instead of scraping nested runtime payloads.

## Key Features

```python
# Index files from a directory
s.index_directory("./my_data/")         # .txt, .md, .csv, .json, .jsonl, .pdf

# Category-specific extraction (auto-selects prompt)
s.add(text, category="Financials")      # 8 FinDER domain presets

# Query with reasoning mode
s.ask("question", reasoning_mode=True, repair_budget=2)

# Multiple LLM providers
from seocho.store import OpenAIBackend
llm = OpenAIBackend(model="gpt-4o-mini")                              # OpenAI
llm = OpenAIBackend(model="deepseek-chat", base_url="https://api.deepseek.com/v1")  # DeepSeek

# Multi-ontology per database
s.register_ontology("finance_db", finance_ontology)

# Schema as code (JSON-LD canonical storage)
ontology.to_jsonld("schema.jsonld")
ontology = Ontology.from_jsonld("schema.jsonld")

# Apply generated Neo4j constraints explicitly in local mode
s.ensure_constraints(database="neo4j")

# Offline ontology governance helpers
# seocho ontology check --schema schema.jsonld
# seocho ontology export --schema schema.jsonld --format shacl --output shacl.json
# seocho ontology diff --left schema_v1.jsonld --right schema_v2.jsonld
# diff output now includes package_id, recommended version bump, and migration warnings

# Build runtime-safe semantic artifacts from the same ontology contract
artifacts = s.approved_artifacts_from_ontology()
draft = s.artifact_draft_from_ontology(name="finance_core_v1")
prompt_context = s.prompt_context_from_ontology(
    instructions=["Treat finance.core as authoritative."]
)

# Experiment workbench
from seocho.experiment import Workbench
wb = Workbench(input_texts=["text..."])
wb.vary("ontology", ["v1.jsonld", "v2.jsonld"])
wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
results = wb.run_all()
print(results.leaderboard())

# Pluggable tracing
from seocho import enable_tracing, configure_tracing_from_env
enable_tracing(backend="none")          # disable tracing explicitly
enable_tracing(backend="console")       # stdout only
enable_tracing(backend="jsonl")         # canonical neutral trace artifact
enable_tracing(backend="opik")          # optional exporter (hosted or self-hosted)
configure_tracing_from_env()            # SEOCHO_TRACE_BACKEND=none|console|jsonl|opik

# Agent design configuration
from seocho import AgentConfig, AGENT_PRESETS
onto = Ontology.from_jsonld("schema.jsonld")
s = Seocho(ontology=onto, ..., agent_config=AGENT_PRESETS["strict"])

# Agent-level session (context persists across operations)
with s.session("my_analysis") as sess:
    sess.add("ACME acquired Beta in 2024.")
    sess.add("Beta provides risk analytics to ACME.")
    answer = sess.ask("What does ACME own or use?")
    # → the same ontology from schema.jsonld drives indexing, query prompts, and session context

# Optional: name the shared ontology profile used by indexing/query/agent metadata
s = Seocho(ontology=onto, ..., ontology_profile="finance-core")

# Supervisor with sub-agent hand-off (explicit opt-in)
from seocho import RoutingPolicy
s = Seocho(ontology=onto, ..., agent_config=AgentConfig(
    execution_mode="supervisor", handoff=True,
    routing_policy=RoutingPolicy(latency=0.1, token_efficiency=0.3, information_quality=0.6),
))
with s.session("auto") as sess:
    sess.run("ACME acquired Beta in 2024.")  # → IndexingAgent
    sess.run("What does ACME know about Beta?")  # → QueryAgent

# Ontology merge (combine two schemas)
finance = Ontology.from_jsonld("finance.jsonld")
legal = Ontology.from_jsonld("legal.jsonld")
combined = finance.merge(legal)  # union of nodes + relationships
combined.to_jsonld("combined.jsonld")
```

## SDK Package Structure

```
seocho/
├── index/              ← Data Plane: putting data IN
│   ├── pipeline.py     ← chunk → extract → validate → rule inference → write
│   ├── linker.py       ← embedding-based entity relatedness (canonical)
│   └── file_reader.py  ← .txt/.md/.csv/.json/.jsonl/.pdf
├── query/              ← Control Plane: getting data OUT
│   ├── strategy.py     ← ontology → LLM prompt generation (cached)
│   └── cypher_builder.py ← deterministic Cypher from intent
├── store/              ← Storage backends
│   ├── graph.py        ← Neo4j/DozerDB (with schema cache)
│   ├── vector.py       ← FAISS / LanceDB
│   └── llm.py          ← OpenAI, DeepSeek, Kimi, Grok
├── rules.py            ← SHACL-like rule inference + validation (canonical)
├── ontology.py         ← Schema: JSON-LD + SHACL + merge + migration + coverage
├── session.py          ← Agent session: context cache + hand-off
├── agents.py           ← IndexingAgent / QueryAgent / Supervisor
├── tools.py            ← @function_tool definitions for agents
├── agent_config.py     ← AgentConfig, RoutingPolicy, presets
├── experiment.py       ← Workbench for parameter exploration
├── tracing.py          ← Pluggable observability
└── client.py           ← Seocho unified interface
```

## Three Ways to Use

### Python SDK (developers)
```python
from seocho import Seocho, Ontology, NodeDef, P
```

### CLI (no code needed)
```bash
seocho init                    # create ontology interactively
seocho index ./data/           # index files
seocho ask "your question"     # query
seocho status                  # graph stats
seocho experiment --input ...  # parameter exploration
```

### Jupyter Notebook (data analysts)
```
examples/quickstart.ipynb
examples/bring_your_data.ipynb
```

## LPG and RDF Support

```python
# LPG mode (default) — Cypher queries
onto = Ontology(name="finance", graph_model="lpg", ...)

# RDF mode — n10s Cypher (DozerDB + neosemantics)
onto = Ontology(name="fibo", graph_model="rdf",
                namespace="https://spec.edmcouncil.org/fibo/", ...)
```

## Documentation

| Doc | Description |
|-----|-------------|
| [seocho.blog](https://seocho.blog) | Full documentation site |
| [SDK Overview](https://seocho.blog/sdk/) | SDK features and quick start |
| [Ontology Guide](https://seocho.blog/sdk/ontology-guide/) | Schema design, JSON-LD, SHACL |
| [API Reference](https://seocho.blog/sdk/api-reference/) | Complete method reference |
| [Examples](https://seocho.blog/sdk/examples/) | Real-world patterns |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture |
| [docs/FILES_AND_ARTIFACTS.md](docs/FILES_AND_ARTIFACTS.md) | Where ontology, rule, trace, and runtime files live |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | FinDER and GraphRAG-Bench evaluation tracks |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Operational workflow |
| [docs/ISSUE_TASK_SYSTEM.md](docs/ISSUE_TASK_SYSTEM.md) | Sprint/task governance |

## Observability Modes

- `none`: no tracing; smallest surface and lowest data retention risk.
- `console`: ephemeral stdout debugging for local development.
- `jsonl`: canonical neutral trace artifact for local files, replay, and vendor-neutral retention.
- `opik`: optional exporter/backend for hosted or self-hosted team observability.

Recommended defaults:

- sensitive data or simple local usage: `none` or `jsonl`
- team debugging and evaluation: `jsonl + opik`
- private infra: self-hosted Opik with `SEOCHO_TRACE_OPIK_MODE=self_host`

Retention and privacy guidance:

- JSONL retention follows your filesystem policy; rotate or delete trace files explicitly.
- Opik retention follows the target Opik deployment policy, whether hosted or self-hosted.
- prompts, retrieval evidence, and metadata may appear in traces; avoid remote exporters for sensitive workloads unless governance is approved.

## Server Mode (Platform Operators)

For the full platform with multi-agent debate, web UI, and Docker services:

```bash
make setup-env && make up
# UI: http://localhost:8501
# API: http://localhost:8001/docs
# DozerDB: http://localhost:7474
```

Default `make up` starts the core local stack only:

- `neo4j`
- `extraction-service`
- `evaluation-interface`

The old `semantic-service` remains available as an opt-in legacy profile:

```bash
docker compose --profile legacy-semantic up -d semantic-service
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full server setup guide.

## Contributing

```bash
git clone git@github.com:tteon/seocho.git && cd seocho
pip install -e ".[dev]"
scripts/pm/install-git-hooks.sh
python -m pytest seocho/tests/ -q
```

For runtime-package migration work, also run
`bash scripts/ci/check-runtime-shell-contract.sh`.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/WORKFLOW.md](docs/WORKFLOW.md)
for the full guide.

## License

MIT — see [LICENSE](LICENSE).
