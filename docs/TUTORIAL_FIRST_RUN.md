# Tutorial: Extended First Run

Use this document after [QUICKSTART.md](QUICKSTART.md) succeeds.

This tutorial is not the minimal onboarding path.
It is the manual verification path for developers who want to understand the runtime surfaces.

## 1. Confirm Baseline Services

```bash
docker compose ps
curl -sS http://localhost:8001/health/runtime | jq .
curl -sS http://localhost:8001/health/batch | jq .
curl -sS http://localhost:8501/health | jq .
```

Expected:

- backend API reachable on `8001`
- platform UI proxy reachable on `8501`
- DozerDB reachable on `7474`

## 2. Verify The Public Graph-Memory API

### 2.1 Store one memory

```bash
curl -sS -X POST http://localhost:8001/api/memories \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "user_id": "tutorial_user",
    "session_id": "tutorial_session",
    "content": "ACME acquired Beta in 2024.",
    "metadata": {
      "source": "tutorial_note",
      "tags": ["mna"]
    }
  }' | jq .
```

### 2.2 Read the memory back

Replace `<MEMORY_ID>` with the returned value.

```bash
curl -sS "http://localhost:8001/api/memories/<MEMORY_ID>?workspace_id=default" | jq .
```

### 2.3 Search memories

```bash
curl -sS -X POST http://localhost:8001/api/memories/search \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "query": "Who was acquired by ACME?",
    "limit": 5
  }' | jq .
```

### 2.4 Ask from memories

```bash
curl -sS -X POST http://localhost:8001/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "message": "What do we know about ACME and Beta?"
  }' | jq .
```

## 3. Inspect The Internal Runtime Path

The public memory facade wraps a deeper runtime path.
Use the internal endpoints below when you need ingestion or semantic behavior diagnostics.

### 3.1 Ingest raw records directly

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "target_database":"kgnormal",
    "records":[
      {"id":"raw_1","content":"ACME acquired Beta in 2024."},
      {"id":"raw_2","content":"Beta provides analytics to ACME."}
    ]
  }' | jq .
```

### 3.2 Ensure fulltext index

```bash
curl -sS -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["kgnormal"],
    "index_name":"entity_fulltext",
    "create_if_missing":true
  }' | jq .
```

### 3.3 Run semantic graph QA

```bash
curl -sS -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Show links between ACME and Beta.",
    "user_id":"tutorial_user",
    "workspace_id":"default",
    "databases":["kgnormal"]
  }' | jq .
```

### 3.4 Run platform chat through the backend endpoint

```bash
curl -sS -X POST http://localhost:8001/platform/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"tutorial_chat_1",
    "message":"What does ACME relate to?",
    "mode":"semantic",
    "workspace_id":"default",
    "databases":["kgnormal"]
  }' | jq .
```

## 4. Verify Rule And Governance APIs

### 4.1 Infer rules

```bash
curl -sS -X POST http://localhost:8001/rules/infer \
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
  }' | jq .
```

### 4.2 Assess practical readiness

```bash
curl -sS -X POST http://localhost:8001/rules/assess \
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
  }' | jq '.practical_readiness'
```

### 4.3 Semantic artifact lifecycle

Create a draft:

```bash
curl -sS -X POST http://localhost:8001/semantic/artifacts/drafts \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "name":"tutorial_draft",
    "ontology_candidate":{"ontology_name":"tutorial","classes":[],"relationships":[]},
    "shacl_candidate":{"shapes":[]}
  }' | jq .
```

Approve it:

```bash
curl -sS -X POST http://localhost:8001/semantic/artifacts/<ARTIFACT_ID>/approve \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "approved_by":"tutorial_user"
  }' | jq .
```

## 5. Optional: Scripted Demos

If you want repeatable staged demos instead of manual calls, continue with
[BEGINNER_PIPELINES_DEMO.md](BEGINNER_PIPELINES_DEMO.md).

## 6. Optional: Opik

```bash
make opik-up
```

Open `http://localhost:5173`.

## 7. Troubleshooting

If a request fails:

```bash
docker compose logs --tail=200 extraction-service
docker compose logs --tail=200 evaluation-interface
docker compose logs --tail=200 graphrag-neo4j
```

Useful checks:

- `OPENAI_API_KEY` missing: runtime may fall back to deterministic extraction
- fulltext missing: run `/indexes/fulltext/ensure`
- wrong ports: confirm `.env` and `docker compose ps`
