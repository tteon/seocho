# Contributing to SEOCHO

Welcome. This guide gets you from zero to your first PR.

## Quick Setup

```bash
git clone git@github.com:tteon/seocho.git
cd seocho
pip install -e ".[dev]"
python -m pytest seocho/tests/ -q   # 87 tests, should pass in <1s
```

## Where to Look

The SDK is split into three domain packages. **The directory name tells you what it does.**

```
seocho/                     ← Canonical domain engine (pip install seocho)
├── index/              ← Data Plane: putting data IN
│   ├── pipeline.py     ← chunk → extract → validate → rule inference → write
│   ├── linker.py       ← embedding-based entity relatedness (canonical)
│   └── file_reader.py  ← read .txt/.csv/.json/.jsonl files
│
├── query/              ← Control Plane: getting data OUT
│   └── strategy.py     ← ontology → LLM prompt generation (cached)
│
├── store/              ← Storage backends
│   ├── graph.py        ← Neo4j/DozerDB (write + query + schema cache)
│   ├── vector.py       ← FAISS / LanceDB
│   └── llm.py          ← OpenAI, DeepSeek, Kimi, Grok
│
├── rules.py            ← SHACL-like rule inference + validation (canonical)
├── ontology.py         ← Schema definition (shared across all planes)
├── client.py           ← Seocho class (unified interface)
├── models.py           ← Shared response types
└── tests/              ← SDK test suite

extraction/                 ← HTTP transport layer (server-only)
├── agent_server.py     ← FastAPI endpoints
├── rule_constraints.py ← re-export shim → seocho.rules
├── vector_store.py     ← adapter shim → seocho.store.vector
└── runtime_ingest.py   ← server ingest (converging toward seocho.index)
```

### I want to...

| Goal | Start here |
|------|-----------|
| Improve entity extraction quality | `seocho/index/pipeline.py` |
| Add a new file format (e.g. PDF) | `seocho/index/file_reader.py` |
| Improve Cypher generation | `seocho/query/strategy.py` |
| Add a new graph database backend | `seocho/store/graph.py` |
| Add a new LLM provider | `seocho/store/llm.py` |
| Extend ontology features | `seocho/ontology.py` |
| Fix a bug in the HTTP client | `seocho/client.py` |

Looking for a concrete place to contribute? Pick a usecase and add a starter
ontology or sample docs alongside it: [`docs/USECASES.md`](docs/USECASES.md).

## Running Tests

```bash
# SDK tests (fast, no external dependencies)
python -m pytest seocho/tests/ -v

# Server-side tests (need extraction/ dependencies)
python -m pytest extraction/tests/ -v
```

## Commit Conventions

We use strict semantic versioning prefixes:

| Prefix | When |
|--------|------|
| `feat:` | New feature or significant addition |
| `fix:` | Bug fix |
| `refactor:` | Code restructuring, no behavior change |
| `docs:` | Documentation only |
| `test:` | Adding or updating tests |
| `chore:` | Tooling, deps, config |

Example: `feat: add PDF support to file indexer`

## PR Process

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes
4. Run tests: `python -m pytest seocho/tests/ -q`
5. Commit with conventional prefix
6. Push and open a PR against `main`

PRs are reviewed by Claude (automated weekly) and maintainers.

## Architecture

Two planes, one ontology:

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
```

- **Data Plane**: file reading → chunking → LLM extraction → SHACL validation → entity dedup → graph write
- **Control Plane**: prompt strategy → Cypher generation → answer synthesis → reasoning repair
- **Ontology**: shared — drives extraction prompts AND query prompts

## Key Design Decisions

- JSON-LD is the canonical ontology storage format
- SHACL shapes are derived (never hand-written)
- Denormalization safety is determined by relationship cardinality
- `seocho/ontology.py` is the single source of truth (extraction/ uses a bridge)

See `docs/decisions/DECISION_LOG.md` for all ADRs.
