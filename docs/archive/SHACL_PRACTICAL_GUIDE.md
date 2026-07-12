# SHACL-like Practical Guide

This guide explains how to evaluate whether SEOCHO's SHACL-like rules are actually usable in production workflows.

## Why This Exists

In practice, teams ask two questions:

1. Do current graph payloads pass inferred constraints?
2. How much of those constraints can be enforced directly in DozerDB?

`POST /rules/assess` answers both in one response.

## Minimal Flow

1. Prepare graph payload (`nodes`, `relationships`).
2. Call `/rules/assess`.
3. Check:
- `validation_summary`
- `violation_breakdown`
- `export_preview.unsupported_rules`
- `practical_readiness`

## Example

```bash
curl -s -X POST http://localhost:8001/rules/assess \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "graph":{
      "nodes":[
        {"id":"1","label":"Company","properties":{"name":"Acme","employees":100}},
        {"id":"2","label":"Company","properties":{"name":"","employees":"many"}}
      ],
      "relationships":[]
    }
  }' | jq
```

## Readiness Status

- `ready`
  - high pass ratio and enough enforceable rules
  - apply exported Cypher constraints and keep runtime validation in ingestion path
- `caution`
  - partially usable, but quality gaps remain
  - prioritize top violations and re-assess
- `blocked`
  - payload quality is not yet reliable
  - fix failing nodes before promoting profile to governance baseline

## Local Demo Script

Use a built-in reference/candidate dataset:

```bash
python scripts/rules/shacl_practical_demo.py
```

Use your own files:

```bash
python scripts/rules/shacl_practical_demo.py \
  --reference ./data/reference_graph.json \
  --candidate ./data/candidate_graph.json \
  --workspace-id default \
  --out output/rules_assessment_demo.json
```
