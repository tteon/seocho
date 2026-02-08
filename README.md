# SEOCHO (서초)

**Agent-Driven Knowledge Graph Platform**

[![Open Source](https://img.shields.io/badge/Open%20Source-SEOCHO-blue)](https://github.com/tteon/seocho)
[![Stack](https://img.shields.io/badge/Stack-Neo4j%20|%20FastAPI%20|%20Streamlit-orange)]()

SEOCHO transforms unstructured data into structured knowledge graphs and provides dynamic, per-database agent pools with **Parallel Debate** orchestration for multi-perspective reasoning.

---

## Architecture

```mermaid
graph TD
    User[User] -->|Chat| UI[Streamlit Agent Studio]
    UI -->|Toggle| Mode{Mode}

    Mode -->|Router| Router[Router Agent]
    Mode -->|Debate| Debate[DebateOrchestrator]

    subgraph Legacy_Router[Router Mode]
        Router --> Graph[GraphAgent]
        Router --> Vector[VectorAgent]
        Router --> Web[WebAgent]
        Graph --> DBA[GraphDBA]
        DBA -->|Text2Cypher| Neo4j[(Neo4j)]
        Vector -->|Search| FAISS[(FAISS)]
        DBA --> Supervisor
        Vector --> Supervisor
        Web --> Supervisor[Supervisor]
    end

    subgraph Parallel_Debate[Debate Mode]
        Debate -->|Fan-out| A1[Agent_kgnormal]
        Debate -->|Fan-out| A2[Agent_kgfibo]
        Debate -->|Fan-out| AN[Agent_...]
        A1 --> Collect[Collect]
        A2 --> Collect
        AN --> Collect
        Collect --> Synth[Supervisor Synthesis]
    end

    subgraph Pipeline[Data Pipeline]
        DS[DataSource] --> Bridge[OntologyPromptBridge]
        Bridge --> Extract[EntityExtractor]
        Extract --> Link[EntityLinker]
        Link --> Dedup[EntityDeduplicator]
        Dedup --> DBM[DatabaseManager]
        DBM -->|CREATE DB| Neo4j
        DBM --> AF[AgentFactory]
    end

    Supervisor -->|Answer| UI
    Synth -->|Answer| UI
```

## Core Capabilities

### Data Pipeline
- **DataSource**: Universal ingestion from CSV, JSON, Parquet, and REST APIs
- **Ontology-Driven Extraction**: LLM prompts are generated from ontology definitions, not hard-coded
- **Semantic Deduplication**: Embedding cosine similarity (threshold 0.92) merges "SpaceX" and "Space Exploration Technologies Corp"
- **Dynamic DB Provisioning**: Each dataset gets its own Neo4j database with schema auto-applied

### Multi-Agent Reasoning
- **Router Mode**: Classic single-agent routing (Graph, Vector, Web, Table specialists)
- **Parallel Debate Mode**: All DB agents answer independently via `asyncio.gather()`, then Supervisor synthesizes
- **SharedMemory**: Request-scoped query caching prevents duplicate Cypher execution across agents
- **AgentFactory**: Per-DB agents with closure-bound tools — each agent only queries its own database

### Observability
- **Agent Studio**: Split-screen Streamlit UI with live trace visualization
- **Click-to-Detail**: Click any node in the flow graph to see full tool calls, Cypher queries, and reasoning
- **Trace Topology**: Fan-out / internal steps / collect / synthesis — not just linear chains

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- OpenAI API Key

### Setup
```bash
git clone https://github.com/tteon/seocho.git
cd seocho

cp .env.example .env
# Fill in OPENAI_API_KEY

make up
```

> Detailed setup: [docs/QUICKSTART.md](docs/QUICKSTART.md)

### Access Points
| Service | URL |
|---------|-----|
| Agent Studio | http://localhost:8501 |
| API Server | http://localhost:8001/docs |
| Neo4j Browser | http://localhost:7474 |
| DataHub UI | http://localhost:9002 |

### API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run_agent` | POST | Router mode (legacy) |
| `/run_debate` | POST | Parallel Debate mode |
| `/databases` | GET | List registered databases |
| `/agents` | GET | List active DB-bound agents |

---

## Project Structure

```
seocho/
├── extraction/              # Core ETL + agent system
│   ├── agent_server.py      # FastAPI endpoints
│   ├── pipeline.py          # DataSource → Extract → Link → Dedup → Load
│   ├── data_source.py       # DataSource ABC (File, API)
│   ├── ontology_prompt_bridge.py
│   ├── deduplicator.py      # Embedding similarity dedup
│   ├── database_manager.py  # Dynamic Neo4j DB provisioning
│   ├── agent_factory.py     # Per-DB agent creation
│   ├── shared_memory.py     # Request-scoped cache
│   ├── debate.py            # DebateOrchestrator
│   ├── config.py            # Centralized config + DatabaseRegistry
│   └── conf/                # Hydra configs (prompts, schemas)
├── evaluation/              # Streamlit Agent Studio
├── semantic/                # Semantic analysis service
├── demos/                   # Data Mesh demos
├── docs/
│   ├── ARCHITECTURE.md      # Full architecture reference
│   ├── QUICKSTART.md        # 5-minute setup guide
│   └── ROADMAP.md           # Development roadmap
├── CLAUDE.md                # Agent developer guide (10 rules + code flow)
├── AGENTS.md                # Agent collaboration guidelines
├── CONTRIBUTING.md          # Contribution guidelines
└── SECURITY.md              # Security policy
```

---

## Documentation

| Document | Audience | Content |
|----------|----------|---------|
| [CLAUDE.md](CLAUDE.md) | AI Agents / Developers | Code flow, 10 rules, MCP, patterns |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Developers | Module map, data flow, trace topology |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Users | 5-minute setup with troubleshooting |
| [AGENTS.md](AGENTS.md) | Contributors | Review guidelines, session workflow |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributors | PR process, coding standards |

---

## Contributing

We welcome contributions for new ontology mappings, agent tools, and UI enhancements.
Please read [CONTRIBUTING.md](CONTRIBUTING.md) before getting started.

## License

MIT License.
