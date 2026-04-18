# SEOCHO Quick Start

Goal: one successful local run in under 5 minutes.

If you only read one runtime document first, read this one.

This page is the onboarding path. Use it for first success and local product
verification, not for benchmark claims.

If you want the Python SDK path immediately, continue with
`docs/PYTHON_INTERFACE_QUICKSTART.md`.
If you want the bring-your-own-data path immediately, continue with
`docs/APPLY_YOUR_DATA.md`.
If you are measuring quality or latency, continue with `docs/BENCHMARKS.md`.

## 1. Prerequisites

- Docker and Docker Compose
- `OPENAI_API_KEY` recommended
- `curl` and `jq` for optional API checks

Without `OPENAI_API_KEY`, SEOCHO can still boot in local fallback mode for
basic verification.

Important:

- `pip install seocho` alone does not provision DozerDB/Neo4j for you.
- local runtime success still depends on the graph backend being reachable.
- `make up` starts the core local stack, not every legacy service in the repo.
- `make up` rebuilds an image-backed `extraction-service`, so `localhost:8001`
  reflects a known source snapshot instead of a dirty bind-mounted checkout.

## 2. Setup

```bash
git clone https://github.com/tteon/seocho.git
cd seocho
make setup-env
```

## 3. Start the Runtime

```bash
make up
docker compose ps
```

If you explicitly want a live bind-mounted edit loop instead:

```bash
make up-live
```

Or through the local CLI:

```bash
pip install -e ".[dev]"
seocho serve
```

Published-package local engine path:

```bash
pip install "seocho[local]"
```

The default core stack is:

- `neo4j`
- `extraction-service`
- `evaluation-interface`

Expected local surfaces:

- Platform UI: `http://localhost:8501`
- Backend API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

If you need the old standalone `semantic-service`, start it explicitly:

```bash
docker compose --profile legacy-semantic up -d semantic-service
```

## 4. First Success: UI Path

1. Open `http://localhost:8501`
2. Use the ingest panel or click the sample flow
3. Ask a semantic question

The default product path is:

- ingest data
- run semantic retrieval
- use bounded repair only when needed
- reserve debate for explicit advanced use

## 5. First Success: Direct API Path

Ingest two records:

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "target_database": "kgruntime",
    "records": [
      {"id": "r1", "content": "ACME acquired Beta in 2024."},
      {"id": "r2", "content": "Beta provides risk analytics to ACME."}
    ]
  }' | jq .
```

Ask through the semantic endpoint:

```bash
curl -sS -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "query": "What is ACME related to?",
    "databases": ["kgruntime"],
    "reasoning_mode": true,
    "repair_budget": 2
  }' | jq '{route, response, reasoning: .semantic_context.reasoning}'
```

## 6. First Success: Python SDK Path

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")

client.raw_ingest(
    [
        {"id": "r1", "content": "ACME acquired Beta in 2024."},
        {"id": "r2", "content": "Beta provides risk analytics to ACME."},
    ],
    target_database="kgruntime",
)

semantic = client.semantic(
    "What is ACME related to?",
    databases=["kgruntime"],
    reasoning_mode=True,
    repair_budget=2,
)

print(semantic.response)
print(semantic.semantic_context["reasoning"])
print(semantic.support.status)
print(semantic.strategy.next_mode_hint)

recent = client.semantic_runs(limit=5, route="lpg")
print(recent[0].run_id)
```

## 7. Use Debate Only as an Advanced Mode

If you explicitly want cross-graph comparison:

```python
advanced = client.advanced(
    "Compare what each graph knows about ACME.",
    graph_ids=["kgnormal", "kgfibo"],
)

print(advanced.debate_state)
```

Stay on the semantic path first. Inspect `semantic.support`, `semantic.strategy`,
and `semantic.evidence` before reaching for debate.

## 8. Inspect Runtime Semantic History

```bash
curl -sS "http://localhost:8001/semantic/runs?workspace_id=default&limit=5&route=lpg" | jq .
```

## 9. Validate the Runtime

```bash
make e2e-smoke
```

## 10. Keep One Ontology Contract

If you author locally with `schema.jsonld` but ingest/query through the runtime,
do not maintain a second hand-written runtime payload. Use the same ontology to
build runtime-safe artifacts:

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("schema.jsonld")
client = Seocho(ontology=ontology)

artifacts = client.approved_artifacts_from_ontology()
prompt_context = client.prompt_context_from_ontology()
```

## 11. Troubleshooting

Check service state:

```bash
docker compose ps
docker compose logs --tail=200 extraction-service
docker compose logs --tail=200 evaluation-interface
```

Common issues:

- `OPENAI_API_KEY` missing or placeholder only
- port collision on `8001`, `8501`, `7474`, or `7687`
- graph database not ready yet

## 12. Know Where Your Files Go

The main local locations are:

- ontology file: usually `schema.jsonld`
- local graph data: `data/neo4j/`
- semantic artifacts: `outputs/semantic_artifacts/`
- rule profile registry: `outputs/rule_profiles/rule_profiles.db`
- semantic run metadata: `outputs/semantic_metadata/`
- JSONL tracing: path from `SEOCHO_TRACE_JSONL_PATH`

See [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md) for the full map and
inspection commands.

## 13. Read Next

- `docs/PYTHON_INTERFACE_QUICKSTART.md`
- `docs/APPLY_YOUR_DATA.md`
- `docs/TUTORIAL_FIRST_RUN.md`
- `docs/BEGINNER_PIPELINES_DEMO.md`
- `docs/ARCHITECTURE.md`
