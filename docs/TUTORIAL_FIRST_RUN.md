# Tutorial: First End-to-End Run

This tutorial walks through a practical first run:

1. start services,
2. verify core APIs,
3. test rule inference/validation/profile/export,
4. optionally run agent chat and tracing.

## 0. Prerequisites

- Docker + Docker Compose
- OpenAI API key

## 1. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```bash
OPENAI_API_KEY=sk-...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

Notes:

- Runtime uses DozerDB image (`graphstack/dozerdb`) via Neo4j protocol.
- Keep default ports unless they conflict on your machine.

## 2. Start Core Services

```bash
make up
```

Check containers:

```bash
docker compose ps
```

Expected key endpoints:

- Agent API: `http://localhost:8001/docs`
- Custom Platform: `http://localhost:8501`
- DozerDB Browser: `http://localhost:7474`

## 3. Smoke Test API

### 3.1 Router mode

```bash
curl -s -X POST http://localhost:8001/run_agent \
  -H "Content-Type: application/json" \
  -d '{
    "query":"What entities exist in the graph?",
    "user_id":"tutorial_user",
    "workspace_id":"default"
  }' | jq
```

### 3.2 Debate mode

```bash
curl -s -X POST http://localhost:8001/run_debate \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Summarize known entities by database",
    "user_id":"tutorial_user",
    "workspace_id":"default"
  }' | jq
```

If you get "No database agents available", ingest sample data first (Step 5).

### 3.3 Semantic graph QA mode

Optionally ensure fulltext index first:

```bash
curl -s -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["kgnormal","kgfibo"],
    "index_name":"entity_fulltext",
    "create_if_missing":true
  }' | jq
```

Then run semantic QA:

```bash
curl -s -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Neo4j 에서 GraphRAG 관련 entity 연결을 보여줘",
    "user_id":"tutorial_user",
    "workspace_id":"default",
    "databases":["kgnormal","kgfibo"]
  }' | jq
```

This route runs:

- question entity extraction
- fulltext candidate lookup
- semantic dedup/disambiguation
- router -> LPG/RDF specialist -> answer generation

Optional (offline ontology hint artifact):

```bash
python scripts/ontology/build_ontology_hints.py \
  --ontology ./path/to/domain.owl \
  --output output/ontology_hints.json
```

Once generated, semantic reranking auto-loads hints from `output/ontology_hints.json`
or `ONTOLOGY_HINTS_PATH`.

## 4. Rule Lifecycle Tutorial (Core New Feature)

## 4.1 Infer rule profile from graph payload

```bash
curl -s -X POST http://localhost:8001/rules/infer \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "graph":{
      "nodes":[
        {"id":"1","label":"Company","properties":{"name":"Acme","employees":100}},
        {"id":"2","label":"Company","properties":{"name":"Beta","employees":80}}
      ],
      "relationships":[]
    }
  }' | jq
```

## 4.2 Validate graph with inferred rules

```bash
curl -s -X POST http://localhost:8001/rules/validate \
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

## 4.3 Save rule profile

First, infer and copy `.rule_profile` from the response. Then:

```bash
curl -s -X POST http://localhost:8001/rules/profiles \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "name":"company_rules_v1",
    "rule_profile":{
      "schema_version":"rules.v1",
      "rules":[
        {"label":"Company","property_name":"name","kind":"required","params":{"minCount":1}}
      ]
    }
  }' | jq
```

## 4.4 List/read profiles

```bash
curl -s "http://localhost:8001/rules/profiles?workspace_id=default" | jq
curl -s "http://localhost:8001/rules/profiles/<PROFILE_ID>?workspace_id=default" | jq
```

## 4.5 Export to DozerDB Cypher constraint plan

```bash
curl -s -X POST http://localhost:8001/rules/export/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "profile_id":"<PROFILE_ID>"
  }' | jq
```

## 5. Optional: Ingest Sample Data

```bash
docker compose exec extraction-service python demos/data_mesh_mock.py
```

Then retry `/run_debate` and graph queries.

## 6. Optional: Enable Opik Tracing

```bash
make opik-up
```

Open:

- Opik UI: `http://localhost:5173`

Run `/run_agent` or `/run_debate` again and inspect spans.

## 7. Validate Docs/Process Baseline

```bash
scripts/pm/lint-agent-docs.sh
```

## 8. Stop Services

```bash
make down
```

## Troubleshooting

- `Missing OPENAI_API_KEY` on startup:
  - set `OPENAI_API_KEY` in `.env`, then `make down && make up`
- API not reachable on `8001`:
  - `docker compose logs extraction-service --tail=200`
- Rule profile not found:
  - verify `workspace_id` and `profile_id`
- Debate returns no agents:
  - ingest data (Step 5) and retry
