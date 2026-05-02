---
title: Getting Started
description: From zero to a working knowledge graph in 5 minutes
---

# Getting Started

SEOCHO has two valid starting points:

- use `pip install seocho` when you are consuming an existing runtime over HTTP
- use `pip install "seocho[local]"` when you are authoring the ontology and graph locally

If you want the product rationale first, read [/docs/why_seocho/](/docs/why_seocho/).

## 1. Install

```bash
pip install seocho
```

For local engine mode against your own Neo4j/DozerDB, use:

```bash
pip install "seocho[local]"
```

If you are editing the repository itself, prefer:

```bash
pip install -e ".[dev]"
```

## 2. Define your schema

```python
from seocho import Ontology, NodeDef, RelDef, P

ontology = Ontology(
    name="my_domain",
    nodes={
        "Person":  NodeDef(properties={"name": P(str, unique=True)}),
        "Company": NodeDef(properties={"name": P(str, unique=True)}),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company"),
    },
)
```

`P(str, unique=True)` = string property, must be unique. This drives the extraction prompt, query prompt, a Neo4j UNIQUE constraint, and SHACL validation — all from one declaration.

## 3. Connect

```python
from seocho import Seocho
from seocho.store import Neo4jGraphStore, OpenAIBackend

s = Seocho(
    ontology=ontology,
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password"),
    llm=OpenAIBackend(model="gpt-4o"),
)

s.ensure_constraints()
```

## 4. Index data

```python
# Single text
s.add("Marie Curie worked at the University of Paris.")

# From files
s.index_directory("./my_data/")

# Batch
s.add_batch([
    "Apple CEO Tim Cook announced new AI features.",
    "Samsung's Jay Y. Lee met with NVIDIA's Jensen Huang.",
])
```

## 5. Query

```python
print(s.ask("Where did Marie Curie work?"))

# With auto-retry for hard questions
print(s.ask("Which companies are involved in chip supply?",
            reasoning_mode=True, repair_budget=2))
```

## 6. Inspect extraction

```python
# Preview what gets extracted (without writing)
result = s.extract("Elon Musk is the CEO of Tesla and SpaceX.")
print(result)

# Check quality
scores = ontology.score_extraction(result)
print(f"Quality: {scores['overall']:.0%}")
```

## 7. Save your schema

```python
ontology.to_jsonld("schema.jsonld")   # commit this to version control
ontology = Ontology.from_jsonld("schema.jsonld")  # load it back
```

See [`/docs/files_and_artifacts/`](/docs/files_and_artifacts/) for where
`schema.jsonld`, graph data, rule profiles, semantic artifacts, and traces
actually live on disk.

## 8. Use domain-specific prompts

```python
from seocho.query import PRESET_PROMPTS

s = Seocho(
    ontology=ontology,
    graph_store=store,
    llm=llm,
    extraction_prompt=PRESET_PROMPTS["finance"],  # or: legal, medical, research
)
```

## 9. Share the Result Over HTTP

If you want teammates to use the same SDK-authored setup through HTTP client
mode, export a portable runtime bundle and serve it separately:

```python
bundle = s.export_runtime_bundle(
    "portable.bundle.json",
    app_name="team-memory-runtime",
    default_database="neo4j",
)
print(bundle.app_name)
```

```bash
seocho serve-http --bundle portable.bundle.json --port 8010
```

## 10. Agent Sessions

Sessions maintain context across multiple indexing and querying operations:

```python
with s.session("research") as sess:
    sess.add("Samsung CEO Jay Y. Lee reported $234B revenue.")
    sess.add("Apple CEO Tim Cook reported $383B revenue.")
    # QueryAgent sees structured context from both documents
    answer = sess.ask("Compare Samsung and Apple revenue")
```

Three execution modes via `AgentConfig`:

```python
from seocho import AgentConfig, AGENT_PRESETS

# Pipeline (default) — deterministic, no LLM reasoning about flow
s = Seocho(ontology=onto, graph_store=store, llm=llm)

# Agent — LLM decides tool execution order
s = Seocho(..., agent_config=AgentConfig(execution_mode="agent"))

# Supervisor + hand-off — auto-routes indexing vs query
s = Seocho(..., agent_config=AGENT_PRESETS["supervisor"])
with s.session("auto") as sess:
    sess.run("Samsung CEO is Jay Y. Lee")    # → IndexingAgent
    sess.run("Who is Samsung's CEO?")        # → QueryAgent
```

## 11. Ontology Merge and Migration

```python
finance = Ontology.from_jsonld("finance.jsonld")
legal = Ontology.from_jsonld("legal.jsonld")

# Merge: combine nodes/relationships
combined = finance.merge(legal)
combined.to_jsonld("combined.jsonld")

# Migration: schema evolution
plan = old_onto.migration_plan(new_onto)
print(plan["summary"])
for stmt in plan["cypher_statements"]:
    print(stmt["cypher"])  # Ready-to-run Cypher
```

## What's Next

| Goal | Link |
|------|------|
| Design a richer ontology | [Ontology Guide](/sdk/ontology-guide/) |
| Full method reference | [API Reference](/sdk/api-reference/) |
| Real-world patterns | [Examples](/sdk/examples/) |
