# Apply Your Data to SEOCHO

SEOCHO is easiest to adopt when you treat it as a runtime with a clear order of
operations:

1. put your data into a graph target
2. query through the semantic layer first
3. enable bounded repair for hard questions
4. use advanced debate only when you explicitly need cross-graph comparison

This document is the shortest path from "I have data" to "I can query it
through the SDK and API."

Before you start, know the main local locations:

- ontology file: usually `schema.jsonld`
- local graph data: `data/neo4j/`
- semantic artifacts: `outputs/semantic_artifacts/`
- rule profile registry: `outputs/rule_profiles/rule_profiles.db`

See `FILES_AND_ARTIFACTS.md` if you want the full map and inspection commands.

## 1. Pick an Ingestion Path

Use `add(...)` when you have one small piece of text and want the memory-style
developer path.

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")

memory = client.add(
    "Alex manages the Seoul retail account.",
    metadata={"source": "crm_note"},
)

print(memory.memory_id)
```

Use `raw_ingest(...)` when you already have records, batches, files, or ETL
output and want a repeatable dataset load.

```python
result = client.raw_ingest(
    [
        {"id": "acct-1", "content": "ACME acquired Beta in 2024."},
        {"id": "acct-2", "content": "Beta provides risk analytics to ACME."},
    ],
    target_database="accounts_graph",
)

print(result.status)
print(result.records_processed)
```

## 2. Structure Records for Raw Ingest

The minimum useful record is:

```json
{
  "id": "record-1",
  "content": "ACME acquired Beta in 2024."
}
```

Recommended additions:

- `metadata.source`
- `metadata.document_type`
- `metadata.timestamp`
- stable IDs from your existing system

Example:

```python
records = [
    {
        "id": "ticket-1024",
        "content": "Customer reports recurring API timeout on Seoul retail sync.",
        "metadata": {
            "source": "support",
            "priority": "high",
            "customer": "ACME",
        },
    },
]
```

## 3. Choose a Target Graph

`target_database` is the physical ingest target.

Use a separate target when:

- the dataset has its own schema or ontology assumptions
- you want clean evaluation boundaries
- you expect to compare multiple graphs later

Start simple:

- one dataset -> one `target_database`
- query that same target through `graph_ids` or `databases`

Inspect available graph targets:

```python
for graph in client.graphs():
    print(graph.graph_id, graph.database, graph.ontology_id, graph.vocabulary_profile)
```

## 4. Query in the Right Order

### 4.1 Start with `ask(...)` or `chat(...)`

```python
print(client.ask("What do you know about Alex?"))
```

### 4.2 Use `semantic(...)` for graph-grounded retrieval

```python
semantic = client.semantic(
    "Who manages the Seoul retail account?",
    graph_ids=["kgnormal"],
)

print(semantic.route)
print(semantic.response)
```

### 4.3 Turn on bounded repair for harder questions

```python
semantic = client.semantic(
    "What is Neo4j related to GraphRAG?",
    graph_ids=["kgnormal"],
    reasoning_mode=True,
    repair_budget=2,
)

print(semantic.semantic_context["reasoning"])
```

### 4.4 Use `advanced(...)` only for explicit graph comparison

```python
advanced = client.advanced(
    "Compare what the baseline and finance graphs know about ACME.",
    graph_ids=["kgnormal", "kgfibo"],
)

print(advanced.debate_state)
print(advanced.response)
```

## 5. Use the Builder Surface When You Want One Explicit Plan

```python
result = (
    client.plan("Who manages the Seoul retail account?")
    .on_graph("kgnormal")
    .with_repair_budget(2)
    .run()
)

print(result.route)
print(result.response)
```

## 6. API Example

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "target_database": "accounts_graph",
    "records": [
      {"id": "acct-1", "content": "ACME acquired Beta in 2024."},
      {"id": "acct-2", "content": "Beta provides risk analytics to ACME."}
    ]
  }' | jq .
```

```bash
curl -sS -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "query": "What is ACME related to?",
    "databases": ["accounts_graph"],
    "reasoning_mode": true,
    "repair_budget": 2
  }' | jq '{route, response, reasoning: .semantic_context.reasoning}'
```

## 7. Where Ontology, JSON-LD, and SHACL Fit

You do not need to start by hand-authoring every ontology artifact.

If you already have a local ontology, do not duplicate it manually for runtime
semantic artifacts. Promote the same ontology into the runtime contract:

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("schema.jsonld")
client = Seocho(ontology=ontology)

artifacts = client.approved_artifacts_from_ontology()
draft = client.artifact_draft_from_ontology(name="finance_core_v1")
```

The practical order is:

1. ingest representative data
2. inspect graph targets and semantic behavior
3. add approved semantic artifacts when you need stronger constraints,
   repeatability, or more deterministic retrieval

Those artifacts matter most when:

- text-to-cypher needs tighter relation/property constraints
- you want stable query behavior across runs
- you need governance around approved ontology/shape changes

## 8. Recommended Adoption Pattern

For most teams, this is the right progression:

1. `raw_ingest(...)` a small but representative dataset
2. validate with `ask(...)` and `chat(...)`
3. switch important questions to `semantic(...)`
4. enable `reasoning_mode=True` for difficult graph questions
5. reserve `advanced(...)` for explicit comparison or disagreement analysis

## 9. Read Next

- `PYTHON_INTERFACE_QUICKSTART.md`
- `QUICKSTART.md`
- `GRAPH_MEMORY_API.md`
- `GRAPH_RAG_AGENT_HANDOFF_SPEC.md`
