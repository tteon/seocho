# SEOCHO Quick Start

This guide is optimized for one goal:
raw data in -> graph build -> semantic/debate answer out.

## 0. Prerequisites

- Docker + Docker Compose
- OpenAI API key (`OPENAI_API_KEY`)
- `jq` (for API response checks)
- Git

## 1. Clone and configure

```bash
git clone https://github.com/tteon/seocho.git
cd seocho

cp .env.example .env
# edit .env
# required: OPENAI_API_KEY=sk-...
```

Optional custom ports (if defaults collide):

```bash
NEO4J_HTTP_PORT=7475
NEO4J_BOLT_PORT=7688
EXTRACTION_API_PORT=8002
EXTRACTION_NOTEBOOK_PORT=8890
CHAT_INTERFACE_PORT=8502
```

## 2. Start services

```bash
make up
docker compose ps
```

Expected services:

- `neo4j`
- `extraction-service`
- `semantic-service`
- `evaluation-interface`

## 3. Verify base endpoints

If you changed ports in `.env`, replace `8001`/`8501` below with your configured ports.

```bash
curl -sS http://localhost:8001/databases | jq .
curl -sS http://localhost:8501/api/config | jq .
curl -sS http://localhost:8001/health/runtime | jq .
curl -sS http://localhost:8001/health/batch | jq .
```

Default URLs:

- Platform UI: `http://localhost:8501`
- API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

## 4. Ingest your raw data

Use runtime ingest API to load raw materials directly into a target graph database.
Supported per-record `source_type` is `text`, `csv`, `pdf`.

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "target_database":"kgruntime",
    "records":[
      {"id":"raw_1","source_type":"text","content":"ACME acquired Beta in 2024."},
      {"id":"raw_2","source_type":"csv","content":"company,partner\nBeta,ACME"},
      {"id":"raw_3","source_type":"pdf","content_encoding":"base64","content":"<base64_pdf_payload>"}
    ]
  }' | jq .
```

Success criteria:

- `status` is one of `success`, `success_with_fallback`, `partial_success`
- `records_processed >= 1`
- `semantic_artifacts` includes merged ontology/SHACL candidates and relatedness summary

## 5. Ensure fulltext index for semantic mode

```bash
curl -sS -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["kgruntime"],
    "index_name":"entity_fulltext",
    "create_if_missing":true
  }' | jq .
```

## 6. Ask semantic and debate questions

### 6.1 Semantic mode (API)

```bash
curl -sS -X POST http://localhost:8501/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"qs_semantic_1",
    "message":"Show entities in kgruntime",
    "mode":"semantic",
    "workspace_id":"default",
    "databases":["kgruntime"]
  }' | jq '{assistant_message, route: .runtime_payload.route}'
```

Success criteria:

- `assistant_message` is non-empty
- `runtime_payload.route` is `lpg`, `rdf`, or `hybrid`

### 6.2 Debate mode (API)

```bash
curl -sS -X POST http://localhost:8501/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"qs_debate_1",
    "message":"Compare known entities across databases",
    "mode":"debate",
    "workspace_id":"default"
  }' | jq '{assistant_message, debate_results: .runtime_payload.debate_results}'
```

Success criteria:

- `assistant_message` is non-empty
- `runtime_payload.debate_results` exists

## 7. Run strict integration smoke test

```bash
make e2e-smoke
```

What it checks end-to-end:

- `/platform/ingest/raw`
- `/indexes/fulltext/ensure`
- semantic chat (`/api/chat/send`, mode `semantic`)
- debate chat (`/api/chat/send`, mode `debate`)

If `OPENAI_API_KEY` is real, debate is checked in strict pass mode.

If you are running custom ports, execute the script with explicit overrides:

```bash
EXTRACTION_API_PORT=8002 CHAT_INTERFACE_PORT=8502 bash scripts/integration/e2e_runtime_smoke.sh
```

## 8. Validate through UI

1. Open `http://localhost:8501`
2. Set `Ingest DB` to `kgruntime`
3. Paste raw lines and click `Ingest Raw`
4. Ask a question in `Semantic` mode
5. Switch to `Debate` mode and ask the same question
6. Compare `Trace` and result payloads

## 9. Next practical steps

- Run SHACL-like readiness: `POST /rules/assess`
- Export governance artifacts:
  - Cypher constraints: `POST /rules/export/cypher`
  - SHACL-compatible Turtle: `POST /rules/export/shacl`
- Build ontology hints offline: `python scripts/ontology/build_ontology_hints.py ...`
- Read extension guide: `docs/OPEN_SOURCE_PLAYBOOK.md`
- Read first-run walkthrough: `docs/TUTORIAL_FIRST_RUN.md`

## Troubleshooting

Extraction service logs:

```bash
docker compose logs --tail=200 extraction-service
```

Chat interface logs:

```bash
docker compose logs --tail=200 evaluation-interface
```

If ports conflict, set the port env vars in `.env` and rerun `make up`.
