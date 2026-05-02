---
title: Examples
description: Copy-pasteable patterns for building knowledge graphs with SEOCHO
---

# Examples

## Index Files from a Directory

The easiest way to bring your own data:

```python
from seocho import Seocho, Ontology, NodeDef, RelDef, P
from seocho.store import Neo4jGraphStore, OpenAIBackend
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

s = Seocho(
    ontology=ontology,
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass"),
    llm=OpenAIBackend(model="gpt-4o"),
)

# Index a whole directory
results = s.index_directory("./my_data/", database="mydb")
print(f"Indexed {results['files_indexed']} files")

# Re-run — only changed files are processed
results = s.index_directory("./my_data/", database="mydb")
# files_unchanged: 5, files_indexed: 0
```

### Supported formats

| Format | How it's processed |
|--------|--------------------|
| `.txt`, `.md` | Entire file → one document |
| `.csv` | Each row → one document (auto-detects `content` column) |
| `.json` | Array of objects → each is a document |
| `.jsonl` | One JSON object per line |

## News Article Pipeline

```python
ontology = Ontology(
    name="news",
    nodes={
        "Person":       NodeDef(properties={"name": P(str, unique=True), "title": P(str)}),
        "Organization": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
        "Event":        NodeDef(properties={"name": P(str, unique=True), "date": P(str)}),
    },
    relationships={
        "WORKS_AT":    RelDef(source="Person", target="Organization", cardinality="MANY_TO_ONE"),
        "INVOLVED_IN": RelDef(source="Person", target="Event"),
    },
)

s = Seocho(ontology=ontology, graph_store=store, llm=llm)

for article in articles:
    s.add(article, database="news_kg")

print(s.ask("What happened at WWDC?", database="news_kg"))
```

## Extract and Validate Before Writing

```python
extracted = s.extract("Tesla reported Q4 revenue of $25.2B.")
scores = ontology.score_extraction(extracted)
print(f"Quality: {scores['overall']}")

errors = ontology.validate_with_shacl(extracted)
if errors:
    print("Issues:", errors)
```

## Reasoning Mode (Auto-Retry)

```python
answer = s.ask(
    "Which companies are in chip supply?",
    database="news_kg",
    reasoning_mode=True,
    repair_budget=2,
)
```

## Multi-Ontology per Database

```python
s.register_ontology("finance_db", Ontology.from_jsonld("finance.jsonld"))
s.register_ontology("hr_db", Ontology.from_jsonld("hr.jsonld"))

s.add("Q4 revenue was $10B", database="finance_db")
s.add("Alice joined engineering", database="hr_db")
```

## Incremental Re-indexing

```python
mem = s.add("Initial version.", database="mydb")
s.reindex(mem.memory_id, "Updated version.", database="mydb")
s.delete_source(mem.memory_id, database="mydb")
```

## Denormalized Export

```python
flat = ontology.to_denormalized_view(nodes, relationships)
# Person now has company_name, company_ticker embedded
```

## Experiment Workbench

Find the best combination of ontology, model, chunk size, and other settings:

```python
from seocho.experiment import Workbench

wb = Workbench(input_texts=["Samsung CEO Jay Y. Lee met NVIDIA's Jensen Huang in Seoul."])
wb.vary("ontology", ["schema_v1.jsonld", "schema_v2.jsonld"])
wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
wb.vary("chunk_size", [4000, 8000])
wb.vary("temperature", [0.0, 0.2])

results = wb.run_all()  # 2*2*2*2 = 16 runs
print(results.leaderboard())
```

Output:

```
  #     Score   Nodes   Rels  Errors    Time  Config
──────────────────────────────────────────────────────────────────────
  1     95.0%       4      2       0    1.2s  ontology=v2 | model=gpt-4o | chunk_size=8000 | temperature=0.0
  2     92.0%       4      2       0    0.8s  ontology=v2 | model=gpt-4o-mini | chunk_size=8000 | temperature=0.0
  3     88.0%       3      1       0    1.1s  ontology=v1 | model=gpt-4o | chunk_size=4000 | temperature=0.0
  ...
```

### Analysis

```python
# Best result
best = results.best_by("extraction_score")
print(f"Best: {best.params}")

# Pandas DataFrame for deeper analysis
df = results.to_dataframe()
print(df.groupby("model")["score"].mean())

# Save for later comparison
results.save("./experiments/run_001")
```

### CLI

```bash
seocho experiment \
  --input ./test_articles/ \
  --ontology v1.jsonld --ontology v2.jsonld \
  --model gpt-4o --model gpt-4o-mini \
  --chunk-size 4000 --chunk-size 8000 \
  --output ./experiments/run_001
```

## HTTP Mode

```python
s = Seocho(base_url="http://localhost:8001")
s.add("Some content")
print(s.ask("What do you know?"))
```

## Portable Runtime Bundle

Use this when one developer authors a local ontology-first SDK setup and wants
other developers to consume it over HTTP without copying the whole Python app.

```python
from seocho import Seocho

client = Seocho(
    ontology=ontology,
    graph_store=store,
    llm=llm,
    agent_config=agent_config,
)

client.export_runtime_bundle(
    "portable.bundle.json",
    app_name="team-memory-runtime",
    default_database="neo4j",
)
```

```bash
seocho serve-http --bundle portable.bundle.json --host 0.0.0.0 --port 8010
```

```python
from seocho import Seocho

remote = Seocho(base_url="http://localhost:8010", workspace_id="default")
print(remote.ask("What do you know about Alex?"))
```
