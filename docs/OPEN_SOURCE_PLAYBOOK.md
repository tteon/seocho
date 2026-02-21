# SEOCHO Open Source Playbook

This guide is for contributors who want to adapt SEOCHO to real domain data.

## 1. What to extend first

Recommended order for external teams:

1. load your raw data through runtime ingest
2. define ontology/schema artifacts
3. validate semantic routing on your graph
4. tighten rule governance with `/rules/assess`
5. add integration tests before feature expansion

## 2. Minimum reproducible workflow

### 2.1 Ingest your own records

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "target_database":"mydomain",
    "records":[
      {"id":"r1","content":"Entity A acquired Entity B."},
      {"id":"r2","content":"Entity B supplies analytics to Entity C."}
    ]
  }' | jq .
```

### 2.2 Ensure semantic index

```bash
curl -sS -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["mydomain"],
    "create_if_missing":true
  }' | jq .
```

### 2.3 Query with semantic mode

```bash
curl -sS -X POST http://localhost:8501/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"oss_semantic_1",
    "message":"What entities are linked in mydomain?",
    "mode":"semantic",
    "workspace_id":"default",
    "databases":["mydomain"]
  }' | jq '{assistant_message, route: .runtime_payload.route}'
```

## 3. Ontology and hint extension

### 3.1 Add or modify ontology schema

- baseline examples:
  - `extraction/conf/schemas/baseline.yaml`
  - `extraction/conf/schemas/fibo.yaml`

### 3.2 Build ontology hints (offline)

```bash
python scripts/ontology/build_ontology_hints.py \
  --ontology ./path/to/domain.owl \
  --output output/ontology_hints.json
```

Use this artifact to improve semantic candidate reranking without heavy runtime ontology reasoning.

## 4. Agent and routing extension points

Core extension points:

- `extraction/semantic_query_flow.py`: semantic extraction/resolution and route choice
- `extraction/agent_factory.py`: per-database agent creation
- `extraction/debate.py`: fan-out/collect/synthesis orchestration
- `extraction/platform_agents.py`: backend/frontend runtime payload shaping

When adding a new route policy, keep:

- `workspace_id` propagation
- runtime permission checks (`extraction/policy.py`)
- trace metadata contract for UI DAG rendering (`node_id`, `parent_id`, `parent_ids`)

## 5. Required quality gates for contributors

Run these before opening a PR:

```bash
make test
make test-integration
make e2e-smoke
scripts/pm/lint-agent-docs.sh
```

If you changed runtime API or orchestration behavior, include at least one integration test in `extraction/tests/`.

## 6. Contribution patterns that scale

- prefer small, contract-first changes over broad refactors
- add ADR + decision log entry for architecture-significant changes
- document user-visible behavior in `README.md` and `docs/QUICKSTART.md`
- avoid adding runtime-only dependency on heavy ontology reasoning tools

## 7. Docs sync for seocho.blog

Source-of-truth docs live in this repository.

For stable website publishing, update these together:

- `docs/README.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`

Then push to `main` after running local quality gates.
