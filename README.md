# SEOCHO

**Ontology-driven knowledge graph library for Python**

[![PyPI](https://img.shields.io/pypi/v/seocho)](https://pypi.org/project/seocho/)
[![Tests](https://img.shields.io/badge/tests-107%20passed-green)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Define your schema once — it drives extraction, querying, validation, and graph-governance artifacts from one contract.

## Install

```bash
pip install seocho
```

Optional offline ontology governance tooling:

```bash
pip install "seocho[ontology]"
```

## Quick Start

```python
from seocho import Seocho, Ontology, NodeDef, RelDef, P
from seocho.store import Neo4jGraphStore, OpenAIBackend

# 1. Define your schema
ontology = Ontology(
    name="my_domain",
    package_id="org.example.my_domain",
    nodes={
        "Person":  NodeDef(properties={"name": P(str, unique=True)}),
        "Company": NodeDef(properties={"name": P(str, unique=True)}),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company"),
    },
)

# 2. Connect
s = Seocho(
    ontology=ontology,
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password"),
    llm=OpenAIBackend(model="gpt-4o"),
)

# 3. Index
s.add("Marie Curie worked at the University of Paris.")

# 4. Query
print(s.ask("Where did Marie Curie work?"))
```

## What the Ontology Controls

| Stage | What happens |
|-------|-------------|
| **Extraction** | Entity types + relationships in LLM prompt |
| **Querying** | Schema-aware Cypher generation and repair prompts |
| **Validation** | SHACL shapes derived → catches type/cardinality errors |
| **Constraints** | UNIQUE/INDEX generated from ontology and can be applied to Neo4j |
| **Denormalization** | Cardinality rules determine safe flattening |
| **Reasoning** | Optional low-quality retry re-extracts with ontology guidance |

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
s = Seocho(ontology=onto, ..., agent_config=AGENT_PRESETS["strict"])
```

## SDK Package Structure

```
seocho/
├── index/           ← Data Plane: putting data IN
│   ├── pipeline.py  ← chunk → extract → validate → write
│   └── file_reader.py ← .txt/.md/.csv/.json/.jsonl/.pdf
├── query/           ← Control Plane: getting data OUT
│   ├── strategy.py  ← ontology → LLM prompt generation
│   └── cypher_builder.py ← deterministic Cypher from intent
├── store/           ← Storage backends
│   ├── graph.py     ← Neo4j/DozerDB
│   ├── vector.py    ← FAISS / LanceDB
│   └── llm.py       ← OpenAI, DeepSeek, Kimi, Grok
├── ontology.py      ← Schema: JSON-LD + SHACL + denormalization
├── experiment.py    ← Workbench for parameter exploration
├── agent_config.py  ← Agent design: presets + custom strategies
├── tracing.py       ← Pluggable observability
└── client.py        ← Seocho unified interface
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
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Operational workflow |
| [docs/ISSUE_TASK_SYSTEM.md](docs/ISSUE_TASK_SYSTEM.md) | Sprint/task governance |

## Automation Model

- GitHub CI is deterministic:
  - `.github/workflows/ci.yml` for PR checks
  - `.github/workflows/nightly-e2e-smoke.yml` for scheduled runtime smoke
- Codex CLI is the bounded PR author:
  - `scripts/codex/run_feature_improvement.sh`
  - `scripts/codex/run_refactor.sh`
  - `scripts/codex/run_e2e_investigation.sh`
- Jules is PR-fixer-first:
  - fix failing CI on existing PRs
  - keep scope narrow
  - do not widen into architecture work
- Maintainers remain the merge gate:
  - review the draft PR
  - mark it ready for review
  - merge with `/go`

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

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full server setup guide.

## Contributing

```bash
git clone git@github.com:tteon/seocho.git && cd seocho
pip install -e ".[dev]"
python -m pytest seocho/tests/ -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

## License

MIT — see [LICENSE](LICENSE).
