---
title: Examples
description: Runtime-first examples for ingesting data and querying SEOCHO.
---

# Examples

## 1. Raw Records to a Graph Target

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")

result = client.raw_ingest(
    [
        {"id": "doc-1", "content": "ACME acquired Beta in 2024."},
        {"id": "doc-2", "content": "Beta provides risk analytics to ACME."},
    ],
    target_database="accounts_graph",
)

print(result.status)
```

## 2. Semantic Query with Bounded Repair

```python
semantic = client.semantic(
    "What is ACME related to?",
    databases=["accounts_graph"],
    reasoning_mode=True,
    repair_budget=2,
)

print(semantic.route)
print(semantic.response)
print(semantic.semantic_context["reasoning"])
```

## 3. Explicit Cross-Graph Comparison

```python
advanced = client.advanced(
    "Compare what the baseline and finance graphs know about ACME.",
    graph_ids=["kgnormal", "kgfibo"],
)

print(advanced.debate_state)
```

## 4. Direct API Usage

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "target_database": "accounts_graph",
    "records": [
      {"id": "doc-1", "content": "ACME acquired Beta in 2024."}
    ]
  }'
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
  }'
```

## Next

- [`/docs/apply_your_data/`](/docs/apply_your_data/)
- [`/docs/python_sdk/`](/docs/python_sdk/)
- [`/sdk/examples/`](/sdk/examples/)
