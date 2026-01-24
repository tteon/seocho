# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SEOCHO is an Enterprise GraphRAG & Multi-Agent Orchestration Framework that transforms unstructured data into structured knowledge graphs. It provides scalable pipelines for entity extraction, linking, and multi-agent reasoning with full observability.

## Common Commands

### Development (via Make)
```bash
make up                  # Start all Docker services
make down                # Stop all services
make restart             # Restart services
make logs                # View logs (tail -f style)
make shell               # Open bash in engine container
make clean               # Remove containers and volumes
make bootstrap           # Build containers and prepare environment
```

### Testing & Code Quality
```bash
make test                # Run pytest in Docker
make lint                # Run flake8 + black check
make format              # Auto-format with black + isort

# Inside container:
docker compose exec extraction-service pytest tests/ -v
docker compose exec extraction-service pytest tests/test_api_integration.py -v  # Single test file
```

### Data Ingestion & Demos
```bash
make ingest-glossary           # Ingest glossary terms to DataHub
make ingest-supply-chain       # Ingest supply chain sample data

# Data Mesh demos (run inside extraction-service):
docker exec extraction-service python demos/data_mesh_mock.py      # Seed Neo4j with FIBO domains
docker exec extraction-service python demos/datahub_fibo_ingest.py # Seed DataHub with FIBO metadata
docker exec extraction-service python demos/demo_fibo_metadata.py  # Financial metadata tutorial
```

## Architecture

### Multi-Agent Hierarchy (OpenAI Agents SDK)
```
Router Agent
    ├── VectorAgent → FAISS search → Supervisor
    ├── GraphAgent → GraphDBA (Text2Cypher) → Neo4j → Supervisor
    ├── WebAgent → web search → Supervisor
    └── TableAgent → structured data → Supervisor
```

The agent flow is visualized in real-time via the Streamlit Agent Studio (`evaluation/app.py`).

### Key Modules

**extraction/** - Core ETL and multi-agent system
- `pipeline.py` - Central orchestration: DataCollector → EntityExtractor → EntityLinker → GraphLoader
- `agent_server.py` - FastAPI server with OpenAI Agents SDK implementation (`/run_agent` endpoint)
- `agents/base.py` - BaseAgent abstract class, ToolRegistry, `@register_tool` decorator
- `ontology/base.py` - Ontology classes: NodeDefinition, RelationshipDefinition, PropertyType
- `vector_store.py` - FAISS embedding manager
- `schema_manager.py` - Dynamic schema discovery and application
- `conf/` - Hydra configs (prompts, schemas, ingestion recipes)

**evaluation/** - Streamlit Agent Studio
- `app.py` - Split-screen UI: chat (left) + live agent flow graph (right)
- Uses `streamlit_flow` for real-time trace visualization

**semantic/** - FastAPI semantic analysis with LangChain integration

**demos/** - Data Mesh demonstrations
- `data_mesh_mock.py` - Seeds Neo4j with FIBO-style domains, products, datasets
- `datahub_fibo_ingest.py` - Creates FIBO glossary terms and domains in DataHub

### Databases
- **Neo4j** (DozerDB 5.26): `kgnormal` (general), `kgfibo` (financial/FIBO ontology), `agent_traces`
- **DataHub**: Metadata governance (GMS on 8080, Frontend on 9002)
- **FAISS**: Vector similarity search for semantic retrieval

## Configuration

### Environment Variables (`.env`)
```bash
OPENAI_API_KEY=sk-...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687
```

### Hydra Config Structure (`extraction/conf/`)
- `config.yaml` - Global settings: `model`, `mock_data` toggle, `openai_api_key`
- `prompts/*.yaml` - Jinja2 prompt templates (default, fibo, router, linking)
- `schemas/*.yaml` - Neo4j schema definitions (baseline, fibo, tracing)

## Code Patterns

### Creating Custom Agents
```python
from extraction.agents.base import BaseAgent, register_tool

@register_tool("my_tool")
def my_tool(query: str) -> str:
    return f"Result for {query}"

class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="MyAgent",
            instructions="You are a helpful assistant.",
            tools=[my_tool]
        )

    def validate_input(self, input_data):
        return True
```

### Creating Tools for Agent Server
```python
from agents import function_tool, RunContextWrapper

@function_tool
def execute_cypher_tool(context: RunContextWrapper, query: str, database: str = "neo4j") -> str:
    """Executes Cypher query against specified database."""
    return neo4j_conn.run_cypher(query, database=database)
```

### Ontology Definitions
```python
from extraction.ontology.base import NodeDefinition, PropertyDefinition, PropertyType

node = NodeDefinition(
    label="Company",
    properties={
        "name": PropertyDefinition(name="name", type=PropertyType.STRING, constraint=ConstraintType.UNIQUE)
    }
)
```

## Branch Information

- **main** - Production branch with full Data Mesh integration, documentation, and examples
- **graphrag-dev** - Active development branch for multi-agent orchestration features

## Service Ports
| Service | Port |
|---------|------|
| Streamlit Agent Studio | 8501 |
| FastAPI Agent Server | 8001 |
| Neo4j HTTP | 7474 |
| Neo4j Bolt | 7687 |
| DataHub GMS | 8080 |
| DataHub Frontend | 9002 |

## Development Guidelines

- Follow PEP 8, use type hints for all function signatures
- Agent server uses asyncio; IO-bound tools should be async-compatible
- Use Conventional Commits (`feat:`, `fix:`, `docs:`)
- Use `st.session_state` for Streamlit state management
- GraphDBA must validate database names against allowlist before executing Cypher
