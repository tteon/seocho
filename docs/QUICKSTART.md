# SEOCHO Quick Start Guide

Get SEOCHO running in 5 minutes! 🚀

---

## Prerequisites

- Docker & Docker Compose
- OpenAI API Key
- Git

---

## Step 1: Clone & Configure

```bash
# Clone the repository
git clone https://github.com/tteon/seocho.git
cd seocho

# Copy environment template
cp .env.example .env

# Edit .env and add your keys
# Required: OPENAI_API_KEY=sk-...
# Optional: NEO4J_PASSWORD=your-password
```

---

## Step 2: Start Services

```bash
# Launch all services
docker-compose up -d --build

# Check status
docker-compose ps
```

**Expected Output:**
```
NAME                  STATUS
graphrag-neo4j        running (healthy)
extraction-service    running
semantic-service      running
evaluation-interface  running
datahub-gms          running
datahub-frontend     running
```

---

## Step 3: Access the Platform

| Service | URL | Purpose |
|---------|-----|---------|
| **Agent Studio** | http://localhost:8501 | Chat with agents, view traces |
| **API Docs** | http://localhost:8001/docs | REST API documentation |
| **Neo4j Browser** | http://localhost:7474 | Graph database UI |
| **DataHub** | http://localhost:9002 | Metadata catalog |

**Default Credentials:**
- Neo4j: `neo4j` / `password`
- DataHub: `datahub` / `datahub`

---

## Step 4: Your First Query

### Via Agent Studio (Recommended)

1. Open http://localhost:8501
2. Type: `What databases are available?`
3. Watch the agent trace flow in real-time!

### Via API

```bash
curl -X POST http://localhost:8001/run_agent \
  -H "Content-Type: application/json" \
  -d '{"query": "What entities exist in the graph?", "user_id": "quickstart"}'
```

---

## Step 5: Load Sample Data

```bash
# Generate mock financial data
docker exec extraction-service python demos/data_mesh_mock.py

# Load FIBO ontology metadata
docker exec extraction-service python demos/datahub_fibo_ingest.py
```

---

## Next Steps

### Build Your First Ontology

```yaml
# extraction/conf/ontologies/custom/my_domain.yaml
graph_type: "MyDomain"
version: "1.0"

nodes:
  Person:
    description: "A human individual"
    properties:
      name:
        type: STRING
        constraint: UNIQUE
      email:
        type: STRING
        index: TRUE

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

### Create a Custom Agent

See `docs/AGENT_DEVELOPMENT.md` for detailed guide.

```python
# extraction/agents/my_agent.py
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
# Check logs
docker-compose logs extraction-service

# Restart specific service
docker-compose restart extraction-service
```

### Neo4j Connection Failed?

```bash
# Verify Neo4j is healthy
docker exec graphrag-neo4j cypher-shell -u neo4j -p password "RETURN 1"
```

### Port Conflicts?

Edit `.env` to change default ports:
```bash
NEO4J_HTTP_PORT=17474
NEO4J_BOLT_PORT=17687
CHAT_INTERFACE_PORT=18501
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     YOUR APPLICATION                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────────┐       ┌─────────────┐                     │
│   │   Agent     │──────▶│   FastAPI   │                     │
│   │   Studio    │       │   Server    │                     │
│   │  (8501)     │       │   (8001)    │                     │
│   └─────────────┘       └──────┬──────┘                     │
│                                │                             │
│                    ┌───────────┼───────────┐                │
│                    ▼           ▼           ▼                │
│              ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│              │ Router  │ │ Vector  │ │  Graph  │           │
│              │ Agent   │ │ Agent   │ │  Agent  │           │
│              └─────────┘ └─────────┘ └────┬────┘           │
│                                           │                 │
│                                           ▼                 │
│                                    ┌────────────┐          │
│                                    │  GraphDBA  │          │
│                                    │   Agent    │          │
│                                    └─────┬──────┘          │
│                                          │                  │
│  ┌───────────────────────────────────────┼─────────────┐   │
│  │                DATA LAYER             │             │   │
│  │   ┌─────────┐  ┌─────────┐  ┌────────┴───┐        │   │
│  │   │  FAISS  │  │ DataHub │  │   Neo4j    │        │   │
│  │   │ Vectors │  │Metadata │  │   Graph    │        │   │
│  │   └─────────┘  └─────────┘  └────────────┘        │   │
│  └───────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Community

- 📖 [Documentation](./docs/)
- 🐛 [Issue Tracker](https://github.com/tteon/seocho/issues)
- 💬 [Discord](https://discord.gg/RcR5e5VSJW)
- 🤝 [Contributing Guide](../CONTRIBUTING.md)

---

**Happy Building!** 🎉
