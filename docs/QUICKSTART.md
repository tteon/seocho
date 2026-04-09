# SEOCHO Quick Start

Goal: one successful run in under 5 minutes.

This is the canonical onboarding document.

If you only read one runtime document, read this one first.

If you want the mem0-style developer path instead of the UI-first path, jump to [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md).

## 1. Prerequisites

- Docker and Docker Compose
- `curl` and `jq` for optional API checks
- `OPENAI_API_KEY` recommended for full extraction quality

Without `OPENAI_API_KEY`, SEOCHO can still run in local fallback mode for basic verification.

## 2. Setup Environment

```bash
git clone https://github.com/tteon/seocho.git
cd seocho
make setup-env
```

`make setup-env` creates `.env` from `.env.example` and lets you:

- set `OPENAI_API_KEY`
- optionally enable Opik
- optionally change ports

## 3. Start Services

```bash
make up
docker compose ps
```

If you installed the local CLI and want one command instead of manual Compose:

```bash
pip install -e .
seocho serve
```

`seocho serve` runs `docker compose up -d`, waits for `/health/runtime` and `/graphs`, and injects a fallback local `OPENAI_API_KEY` when your environment still has the example placeholder.

Expected local access points:

| Surface | URL |
|---|---|
| Platform UI | `http://localhost:8501` |
| Backend API docs | `http://localhost:8001/docs` |
| DozerDB browser | `http://localhost:7474` |

## 4. Recommended First Success: UI Path

1. Open `http://localhost:8501`
2. In the ingest panel, leave the default database
3. Click `Load Sample & Ask`

This runs the shortest end-to-end path:

- sample raw ingest
- fulltext ensure
- semantic question
- trace rendering in the UI

Success signals:

- an assistant answer is rendered
- the right-side trace/workflow panel is populated

## 5. Optional First Success: Official Client / CLI Path

If you want a simple local client workflow from the repository root:

```bash
pip install -e .
seocho serve
seocho doctor
seocho add "Alice manages the Seoul retail account."
seocho search "Who manages the Seoul retail account?"
seocho chat "What do we know about Alice?"
```

## 6. Optional First Success: Direct Backend API Path

If you want to verify the memory-first backend surface directly:

Create one memory:

```bash
curl -sS -X POST http://localhost:8001/api/memories \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "content": "Alice manages the Seoul retail account.",
    "metadata": {
      "source": "quickstart_note",
      "tags": ["account", "org"]
    }
  }' | jq .
```

Ask from memories:

```bash
curl -sS -X POST http://localhost:8001/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "message": "Who manages the Seoul retail account?"
  }' | jq .
```

Search memories:

```bash
curl -sS -X POST http://localhost:8001/api/memories/search \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "query": "Seoul retail account",
    "limit": 5
  }' | jq .
```

List graph targets:

```bash
curl -sS http://localhost:8001/graphs | jq .
```

Run a graph-scoped debate:

```bash
curl -sS -X POST http://localhost:8001/run_debate \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "user_id": "alex",
    "query": "Compare what the baseline and finance graphs know about Alex.",
    "graph_ids": ["kgnormal", "kgfibo"]
  }' | jq .
```

## 7. Multi-Instance Graph Configuration

By default, SEOCHO loads graph targets from:

```bash
extraction/conf/graphs/default.yaml
```

Override it with:

```bash
export SEOCHO_GRAPH_REGISTRY_FILE=extraction/conf/graphs/default.yaml
```

Each graph target can point at a different Neo4j or DozerDB instance. That is the control plane contract used by debate-mode graph agents.

## 8. If It Fails

Check container and app logs:

```bash
docker compose ps
docker compose logs --tail=200 extraction-service
docker compose logs --tail=200 evaluation-interface
docker compose logs --tail=200 graphrag-neo4j
```

Common issues:

- missing `OPENAI_API_KEY`: extraction falls back to deterministic mode
- port collision on `8001`, `8501`, `7474`, or `7687`
- Docker services not fully started yet

Useful CLI helpers:

- `seocho serve --dry-run`: print the compose command without running it
- `seocho stop`: stop the local stack
- `seocho stop --volumes`: stop and remove compose volumes

## 9. What To Read Next

After Quick Start succeeds, choose one path:

- [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md): mem0-style Python interface walkthrough
- [TUTORIAL_FIRST_RUN.md](TUTORIAL_FIRST_RUN.md): deeper manual API verification
- [BEGINNER_PIPELINES_DEMO.md](BEGINNER_PIPELINES_DEMO.md): scripted demo pipelines
- [ARCHITECTURE.md](ARCHITECTURE.md): system architecture
- [OPEN_SOURCE_PLAYBOOK.md](OPEN_SOURCE_PLAYBOOK.md): contributor path

## 10. Optional Opik

Only after the base flow works:

```bash
make opik-up
```

Open `http://localhost:5173`.
