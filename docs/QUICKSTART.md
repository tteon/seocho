# SEOCHO Quick Start Guide

Get SEOCHO running in 5 minutes.

---

## Prerequisites

- Docker & Docker Compose
- OpenAI API Key
- Git

---

## Step 1: Clone & Configure

```bash
git clone https://github.com/tteon/seocho.git
cd seocho

cp .env.example .env
# Edit .env — required: OPENAI_API_KEY=sk-...
```

---

## Step 2: Start Services

```bash
make up

# Check status
docker compose ps
```

**Expected output:**
```
NAME                  STATUS
graphrag-neo4j        running
extraction-service    running
semantic-service      running
evaluation-interface  running
```

---

## Step 3: Access the Platform

| Service | URL |
|---------|-----|
| **Custom Chat Platform** | http://localhost:8501 |
| **API Docs** | http://localhost:8001/docs |
| **Neo4j Browser** | http://localhost:7474 |

**Credentials:** Neo4j: `neo4j` / `password`

---

## Step 4: Your First Query

:::note[Understanding the Execution Modes]
SEOCHO provides multiple intelligent routing options via the UI dropdown or API endpoints.
- **Router (Legacy)**: Fast, single-agent dispatch.
- **Debate (Recommended)**: Fan-out parallel agent execution utilizing consensus-based Supervisor synthesis over the graph.
- **Semantic**: Precision entity resolution and disambiguation before graph traversal.
:::

### Via Custom Platform (Recommended)

1. Open http://localhost:8501
2. Type: `What databases are available?`
3. Watch trace + entity disambiguation controls in real-time

Use **Execution Mode** selector:
- `Router`: legacy single-route flow
- `Debate`: parallel multi-agent flow
- `Semantic`: entity extraction/fulltext resolution -> LPG/RDF specialists
- Semantic mode includes candidate disambiguation UI:
  - filter by confidence + search text
  - pin candidates in right panel
  - click `Apply Pinned Overrides`

### Via API

```bash
# Router mode
curl -X POST http://localhost:8001/run_agent \
  -H "Content-Type: application/json" \
  -d '{"query": "What entities exist in the graph?", "user_id": "quickstart"}'

# Debate mode (Parallel fan-out)
curl -X POST http://localhost:8001/run_debate \
  -H "Content-Type: application/json" \
  -d '{"query": "Compare entities across all databases"}'
```

:::tip[Expected JSON Response]
```json
{
  "answer": "Based on a parallel synthesis from 4 isolated database agents, here is the comparative analysis...\n\n### Graph Database 1 (kgnormal)\n...",
  "status": "success",
  "traced_agents": ["Agent_kgnormal", "Agent_kgfibo"],
  "synthesis_latency_ms": 1450
}
```
:::

# Semantic graph QA mode
curl -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "query":"What is Neo4j connected to?",
    "workspace_id":"default",
    "databases":["kgnormal","kgfibo"]
  }'

# Ensure fulltext index exists
curl -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["kgnormal","kgfibo"],
    "index_name":"entity_fulltext",
    "create_if_missing":true
  }'

# Optional: build ontology hints from OWL (offline)
python scripts/ontology/build_ontology_hints.py \
  --ontology ./path/to/domain.owl \
  --output output/ontology_hints.json
```

---

## Step 5: Load Sample Data

:::note[What does this do?]
This script (`data_mesh_mock.py`) reads a sample dataset matching the FIBO financial domain ontology and automatically invokes the **Data Extraction Pipeline**. 
It uses LLMs to extract entities, relationships, resolves duplicates via semantic embedding deduplication, and strictly provisions them into the `kgfibo` isolated Neo4j database partition.
:::

```bash
docker exec extraction-service python demos/data_mesh_mock.py
```

### Or ingest your own raw text directly from the UI

1. Open `http://localhost:8501`
2. In `Ingest DB`, choose e.g. `kgnormal`
3. Paste raw lines into `Raw Records` (one line = one record)
4. Click `Ingest Raw`
5. Ask questions in chat (`Semantic` or `Debate` mode) using the same DB

Equivalent API:

```bash
curl -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "target_database":"kgnormal",
    "records":[
      {"id":"raw1","content":"ACME acquired Beta in 2024."},
      {"id":"raw2","content":"Beta provides risk analytics to ACME."}
    ]
  }'
```

---

## Optional: Enable Opik Tracing

```bash
make opik-up
# Open http://localhost:5173 for the Opik dashboard
```

All LLM calls and agent executions are auto-traced. See traces, costs, and latency in the Opik UI.

---

## Next Steps

### Define Your Own Ontology

```yaml
# extraction/conf/schemas/my_domain.yaml
graph_type: "MyDomain"
version: "1.0"

nodes:
  Person:
    description: "A human individual"
    properties:
      name:
        type: STRING
        constraint: UNIQUE
  Organization:
    description: "A company or institution"
    properties:
      name:
        type: STRING
        constraint: UNIQUE

relationships:
  WORKS_AT:
    source: Person
    target: Organization
```

### Create a Custom Agent Tool

```python
from agents import Agent, function_tool

@function_tool
def my_custom_tool(query: str) -> str:
    """My custom tool description."""
    return f"Processed: {query}"

my_agent = Agent(
    name="MyAgent",
    instructions="You are a helpful assistant.",
    tools=[my_custom_tool]
)
```

---

## Troubleshooting

### Services Not Starting?

```bash
docker compose logs extraction-service
docker compose restart extraction-service
```

### Neo4j Connection Failed?

```bash
docker exec graphrag-neo4j cypher-shell -u neo4j -p password "RETURN 1"
```

### Port Conflicts?

Edit `.env` to change default ports:
```bash
NEO4J_HTTP_PORT=17474
NEO4J_BOLT_PORT=17687
EXTRACTION_API_PORT=18001
EXTRACTION_NOTEBOOK_PORT=18888
CHAT_INTERFACE_PORT=18501
```
