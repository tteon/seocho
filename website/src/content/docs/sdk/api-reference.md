---
title: API Reference
description: Complete method reference for the SEOCHO Python SDK
---

# API Reference

## Seocho

### Construction

```python
from seocho import Seocho
from seocho.store import Neo4jGraphStore, OpenAIBackend

s = Seocho(
    ontology=ontology,
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass"),
    llm=OpenAIBackend(model="gpt-4o"),
    extraction_prompt=PRESET_PROMPTS["finance"],  # optional
    vector_store=vs,                               # optional
)
```

### Indexing

| Method | Description |
|--------|-------------|
| `s.add(content, *, database, category, metadata, strict_validation)` | Index text with chunking + validation |
| `s.add_batch(documents, *, database, on_progress)` | Batch index multiple documents |
| `s.index_file(path, *, database, force)` | Index a file (.txt, .md, .csv, .json, .jsonl, .pdf) |
| `s.index_directory(directory, *, database, recursive, force)` | Index all supported files in a directory |
| `s.reindex(source_id, content, *, database)` | Delete old data and re-index fresh |
| `s.delete_source(source_id, *, database)` | Remove all graph data for a source |
| `s.extract(content, *, category)` | Extract without writing (inspect/debug) |

### Querying

| Method | Description |
|--------|-------------|
| `s.ask(message, *, database, reasoning_mode, repair_budget)` | Natural language → Cypher → answer |
| `s.query(cypher, *, params, database)` | Execute raw Cypher |
| `s.search_similar(query, *, limit)` | Vector similarity search |

### Sessions

| Method | Description |
|--------|-------------|
| `s.session(name, *, database)` | Create agent session with context persistence |
| `sess.add(content)` | Index through session (context accumulated) |
| `sess.ask(question)` | Query with accumulated entity context |
| `sess.run(message)` | Supervisor hand-off (requires `handoff=True`) |
| `sess.ask_stream(question)` | Stream response token by token |
| `sess.close()` | Finalize session and return summary |

### Graph Management

| Method | Description |
|--------|-------------|
| `s.ensure_constraints(*, database)` | Apply ontology-derived UNIQUE/INDEX constraints |
| `s.register_ontology(database, ontology)` | Bind per-database ontology |

### Portable Runtime Sharing

| Method | Description |
|--------|-------------|
| `s.export_runtime_bundle(path?, *, app_name, default_database)` | Serialize a local SDK setup into a portable JSON bundle |
| `Seocho.from_runtime_bundle(path_or_bundle, *, workspace_id)` | Rehydrate a local SDK client from a portable bundle |

CLI equivalents:

| Command | Description |
|--------|-------------|
| `seocho bundle export --output portable.bundle.json ...` | Export a portable runtime bundle from a local SDK-style config |
| `seocho bundle show portable.bundle.json` | Inspect bundle metadata before serving or sharing |
| `seocho serve-http --bundle portable.bundle.json --port 8010` | Expose the bundle through a small HTTP compatibility runtime |

---

## Ontology

### Construction

```python
from seocho import Ontology, NodeDef, RelDef, P

ontology = Ontology(name="domain", nodes={...}, relationships={...})
ontology = Ontology.from_jsonld("schema.jsonld")
ontology = Ontology.from_yaml("schema.yaml")
```

### Serialization

| Method | Description |
|--------|-------------|
| `to_jsonld(path?)` | Export as JSON-LD (canonical) |
| `to_yaml(path)` | Export as YAML |

### Validation & Scoring

| Method | Description |
|--------|-------------|
| `validate()` | Ontology consistency check |
| `validate_with_shacl(data)` | Full SHACL validation (types + cardinality) |
| `score_extraction(data)` | Quality scores (0.0–1.0) per node/rel |
| `to_shacl()` | View derived SHACL shapes |
| `to_cypher_constraints()` | View Cypher constraint statements |

### Merge & Migration

| Method | Description |
|--------|-------------|
| `onto.merge(other, *, strategy)` | Combine two ontologies (union/left_wins/right_wins/strict) |
| `onto.migration_plan(new_onto)` | Compute Cypher migration statements |

### Denormalization

| Method | Description |
|--------|-------------|
| `denormalization_plan()` | What's safe to embed (based on cardinality) |
| `to_denormalized_view(nodes, rels)` | Flatten graph data |
| `normalize_view(denorm_nodes)` | Reverse to normalized form |

---

## PromptTemplate

```python
from seocho.query import PromptTemplate, PRESET_PROMPTS

# Custom
custom = PromptTemplate(
    system="You are a FIBO expert.\n{{entity_types}}\n{{relationship_types}}",
    user="Financial document:\n{{text}}",
)

# Presets: general, finance, legal, medical, research
PRESET_PROMPTS["finance"]
```

Available `{{variables}}`: `ontology_name`, `entity_types`, `relationship_types`, `constraints_summary`, `text`

---

## Experiment Workbench

```python
from seocho.experiment import Workbench

wb = Workbench(input_texts=["text..."])
wb.vary("ontology", ["v1.jsonld", "v2.jsonld"])
wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
wb.vary("prompt_template", [PRESET_PROMPTS["general"], PRESET_PROMPTS["finance"]])
wb.vary("chunk_size", [4000, 8000])

results = wb.run_all()
```

| Method | Description |
|--------|-------------|
| `wb.vary(axis, values)` | Add a parameter axis |
| `wb.run_all()` | Execute all combinations |
| `results.leaderboard()` | Ranked table |
| `results.best_by(metric)` | Top result |
| `results.to_dataframe()` | Pandas DataFrame |
| `results.save(path)` | Save to directory |

---

## Tracing

```python
from seocho.tracing import enable_tracing

enable_tracing(backend="console")                    # stdout
enable_tracing(backend="jsonl", output="trace.jsonl") # raw file
enable_tracing(backend="opik", project_name="proj")   # Opik
enable_tracing(backend=["console", "jsonl"])           # multiple
```

---

## AgentConfig

```python
from seocho import AgentConfig, RoutingPolicy, AGENT_PRESETS

# Presets
config = AGENT_PRESETS["default"]      # pipeline mode
config = AGENT_PRESETS["strict"]       # high quality threshold
config = AGENT_PRESETS["fast"]         # minimal validation
config = AGENT_PRESETS["agent"]        # tool-use mode
config = AGENT_PRESETS["supervisor"]   # hand-off mode

# Custom
config = AgentConfig(
    execution_mode="supervisor",       # pipeline, agent, supervisor
    handoff=True,
    routing_policy=RoutingPolicy(
        latency=0.1,
        token_efficiency=0.3,
        information_quality=0.6,
    ),
    reasoning_mode=True,
    repair_budget=3,
)
```

| Preset | Latency | Tokens | Quality |
|--------|---------|--------|---------|
| `RoutingPolicy.fast()` | 70% | 20% | 10% |
| `RoutingPolicy.balanced()` | 33% | 33% | 34% |
| `RoutingPolicy.thorough()` | 10% | 10% | 80% |

---

## Storage Backends

```python
from seocho.store import Neo4jGraphStore, OpenAIBackend, FAISSVectorStore

store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
llm = OpenAIBackend(model="gpt-4o", timeout=120.0)
vs = FAISSVectorStore(model="text-embedding-3-small")
```
