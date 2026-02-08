# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SEOCHO is an Agent-Driven Development Platform that transforms unstructured data into structured knowledge graphs with dynamic, per-database agent pools and Parallel Debate orchestration. It provides scalable pipelines for entity extraction, linking, deduplication, and multi-agent reasoning with full observability.

## MCP Tools — Serena (Semantic Code Analysis)

**Always use the Serena MCP tool** when navigating, refactoring, or analyzing this codebase. Serena provides language-server-level semantic understanding (symbol references, definitions, call hierarchies) that is far more accurate than grep-based search.

Recommended Serena usage patterns:
- **Find all callers of a function**: Use Serena instead of grep to find all call sites
- **Rename/refactor a symbol**: Use Serena to discover all references before editing
- **Understand class hierarchy**: Use Serena to trace inheritance and interface implementations
- **Navigate imports and dependencies**: Use Serena to understand module dependency graphs

When performing code modifications that affect multiple files (e.g., renaming a class, changing a function signature), **always start with Serena** to enumerate all affected locations.

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

### Opik Observability (opt-in)
```bash
make opik-up                   # Start core + Opik services
make opik-down                 # Stop all services including Opik
make opik-logs                 # View Opik service logs
# Or directly: docker compose --profile opik up -d
```

### Data Mesh Demos
```bash
# Run inside extraction-service:
docker exec extraction-service python demos/data_mesh_mock.py      # Seed Neo4j with FIBO domains
docker exec extraction-service python demos/demo_fibo_metadata.py  # Financial metadata tutorial
```

## Architecture

> Full architecture documentation: `docs/ARCHITECTURE.md`

### Two Execution Modes

**Mode 1: Legacy Router** (`POST /run_agent`)
```
User → Router → {GraphAgent, VectorAgent, WebAgent, TableAgent} → Supervisor → Answer
```

**Mode 2: Parallel Debate** (`POST /run_debate`)
```
User → DebateOrchestrator → [Agent_db1 ∥ Agent_db2 ∥ ... ∥ Agent_dbN] → SharedMemory → Supervisor → Answer
```

### Data Pipeline Flow
```
DataSource → OntologyPromptBridge → EntityExtractor → EntityLinker → EntityDeduplicator → DatabaseManager → AgentFactory
```

### Agent Definitions (agent_server.py)

**Static agents** (legacy Router mode):
- **Router** → {VectorAgent, GraphAgent, WebAgent, TableAgent}
- **GraphAgent** ↔ **GraphDBA** (bidirectional handoff, forward-declare pattern)
- **VectorAgent** → Supervisor
- **WebAgent** → Supervisor
- **TableAgent** → Supervisor
- **Supervisor** — synthesizes results

**Dynamic agents** (Debate mode):
- **AgentFactory** creates `Agent_{db_name}` for each registered Neo4j database
- Each agent has closure-bound tools scoped to its database only
- SharedMemory provides cross-agent query caching

### Key Modules

**extraction/** - Core ETL and multi-agent system
- `pipeline.py` — Central orchestration: DataSource → Extract → Link → Dedup → Schema → Load
- `agent_server.py` — FastAPI server (`/run_agent`, `/run_debate`, `/databases`, `/agents`)
- `data_source.py` — DataSource ABC + FileDataSource (CSV/JSON/Parquet) + APIDataSource
- `ontology_prompt_bridge.py` — Ontology → LLM prompt variable bridge
- `deduplicator.py` — Embedding cosine-similarity based semantic entity dedup
- `database_manager.py` — Neo4j DB provisioning + schema + data loading
- `agent_factory.py` — Dynamic per-DB Agent creation with closure-bound tools
- `shared_memory.py` — Request-scoped agent shared memory + query cache
- `debate.py` — DebateOrchestrator: parallel fan-out → collect → synthesize
- `config.py` — Centralized config + DatabaseRegistry singleton (`db_registry`) + Opik settings
- `tracing.py` — Opik integration: `configure_opik()`, `wrap_openai_client()`, `@track()`, `update_current_span()`, `update_current_trace()`
- `ontology/base.py` — Ontology, NodeDefinition, RelationshipDefinition, PropertyType
- `vector_store.py` — FAISS embedding manager
- `schema_manager.py` — Dynamic schema discovery and application
- `graph_loader.py` — Neo4j MERGE operations with regex-validated labels
- `conf/` — Hydra configs (prompts, schemas, ingestion recipes)

**evaluation/** - Streamlit Agent Studio (PoC demo)
- `app.py` — Split-screen UI: chat + live agent flow graph (supports both linear and fan-out topology)
- Toggle: "Parallel Debate Mode" switches between `/run_agent` and `/run_debate`
- Role: PoC presentation & demo tool — NOT production observability

**semantic/** - FastAPI semantic analysis service

**demos/** - Data Mesh demonstrations

### Database Architecture
- **Neo4j** (DozerDB 5.26): **single instance**, multi-database
  - Static: `kgnormal`, `kgfibo`, `agenttraces`
  - Dynamic: databases created via `DatabaseManager.provision_database()`
  - DB name validation: `^[A-Za-z][A-Za-z0-9]*$` (alphanumeric, starts with letter)
  - Runtime registry: `db_registry` (singleton in `config.py`)
  - DB selection: `driver.session(database="kgfibo")`
- **FAISS**: Vector similarity search for semantic retrieval
- **Opik** (opt-in profile): Production LLM evaluation, tracing & agent visualization
  - MySQL 8.4 + Redis + ClickHouse + MinIO + Backend + Frontend
  - Enabled via `docker compose --profile opik up -d`

### Observability: Agent Studio vs Opik

| Concern | Agent Studio (Streamlit) | Opik |
|---------|--------------------------|------|
| **Role** | PoC demo & presentation | Production eval & trace |
| **Agent trace** | Custom flow graph (FANOUT/DEBATE/COLLECT) | Native span tree with parent-child |
| **LLM call tracing** | Manual trace_steps construction | Auto-traced via `wrap_openai_client` |
| **Cost / latency** | Not tracked | Built-in per-span metrics |
| **Evaluation** | None | Datasets, scoring, experiments |
| **When to use** | Stakeholder demos, PoC walkthroughs | Development, debugging, production monitoring |

Agent Studio trace visualization (`_build_debate_trace`) remains for demo purposes.
For development and production monitoring, use Opik at `http://localhost:5173`.

## Configuration

### Environment Variables (`.env`)
```bash
OPENAI_API_KEY=sk-...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687

# Opik (opt-in)
OPIK_VERSION=latest
OPIK_URL=http://opik-backend:8080/api
OPIK_PROJECT_NAME=seocho
```

### Hydra Config Structure (`extraction/conf/`)
- `config.yaml` - Global settings: `model`, `mock_data` toggle, `openai_api_key`
- `prompts/*.yaml` - Jinja2 prompt templates (support `{{ entity_types }}`, `{{ relationship_types }}`, `{{ ontology_name }}` variables)
- `schemas/*.yaml` - Neo4j schema definitions (baseline, fibo, tracing)

## Code Flow & Rules

### Rule 1: Database Names Must Be Validated
All database names must match `^[A-Za-z][A-Za-z0-9]*$`. Use `db_registry.is_valid()` to check, `db_registry.register()` to add.
```python
from config import db_registry, _VALID_DB_NAME_RE
```

### Rule 2: Neo4j Labels Must Be Regex-Validated
Before interpolating any label into Cypher, validate with `^[A-Za-z_][A-Za-z0-9_]*$`. The `graph_loader._validate_label()` function does this.

### Rule 3: Centralized Config Only
Import Neo4j credentials from `config.py`. Never duplicate `os.getenv()` calls.
```python
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
```

### Rule 4: Logging, Not Print
Every module: `logger = logging.getLogger(__name__)`. No `print()` in production code.

### Rule 5: DataSource Standard Format
All data sources must return: `[{"id": str, "content": str, "category": str, "source": str, "metadata": dict}]`

### Rule 6: Ontology Drives Extraction
When an ontology YAML is provided, `OntologyPromptBridge.render_extraction_context()` injects entity types and relationship types into the extraction prompt via Jinja2 variables. The `default.yaml` prompt uses `{% if ontology_name %}` to branch.

### Rule 7: Dedup Before Loading
Pipeline order: Extract → Link → **Deduplicate** → Schema → Load. The `EntityDeduplicator` uses embedding cosine similarity (threshold=0.92) to merge semantic duplicates.

### Rule 8: Agent Tools Use Closures
`AgentFactory.create_db_agent()` creates tools as closures that capture `db_name`. Each agent's `query_db` tool only queries its bound database. SharedMemory caching is automatic if `context.context.shared_memory` exists.

### Rule 9: SharedMemory is Request-Scoped
Create a new `SharedMemory()` per API request. Never share across requests.

### Rule 10: Debate Trace Structure
DebateOrchestrator produces trace steps with types: `FANOUT`, `DEBATE`, `COLLECT`, `SYNTHESIS`. The Streamlit UI uses `metadata.parent` for fan-out edges and `metadata.sources` for collect edges.

## Code Patterns

### Creating a DB-Bound Agent
```python
from agent_factory import AgentFactory
from database_manager import DatabaseManager

db_manager = DatabaseManager()
db_manager.provision_database("mydb01", ontology=my_ontology)

factory = AgentFactory(neo4j_connector)
schema = db_manager.get_schema_info("mydb01")
agent = factory.create_db_agent("mydb01", schema)
```

### Using DataSource in Pipeline
```python
from data_source import FileDataSource
from pipeline import ExtractionPipeline

source = FileDataSource("data/companies.csv", content_column="description")
pipeline = ExtractionPipeline(
    cfg=hydra_cfg,
    data_source=source,
    ontology_path="conf/schemas/baseline.yaml",
    target_database="kgnormal",
)
pipeline.run()
```

### Creating Tools for Agent Server
```python
from agents import function_tool, RunContextWrapper

@function_tool
def execute_cypher_tool(context: RunContextWrapper, query: str, database: str = "neo4j") -> str:
    """Executes Cypher query against specified database."""
    return neo4j_conn.run_cypher(query, database=database)
```

### Ontology-Driven Extraction
```python
from ontology.base import Ontology
from ontology_prompt_bridge import OntologyPromptBridge

ontology = Ontology.from_yaml("conf/schemas/fibo.yaml")
bridge = OntologyPromptBridge(ontology)
context = bridge.render_extraction_context()
# Inject into PromptManager template rendering
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
| Opik Frontend (opt-in) | 5173 |
| Opik Backend API (opt-in) | 8080 |
| Opik ClickHouse (opt-in) | 8123 |

## Development Guidelines

- Follow PEP 8, use type hints for all function signatures
- Agent server uses asyncio; IO-bound tools should be async-compatible
- Use Conventional Commits (`feat:`, `fix:`, `docs:`)
- Use `st.session_state` for Streamlit state management
- Use `logging` module instead of `print()` — each module uses `logger = logging.getLogger(__name__)`
- Avoid broad `except Exception` — use specific exception types where possible
- Dynamic Neo4j labels in Cypher must be validated with regex before interpolation
- Neo4j connection config is centralized in `extraction/config.py`
- API endpoints should validate input length and avoid exposing internal tracebacks in error responses
- DB names validated via `DatabaseRegistry` (singleton `db_registry` in config.py)
- Use Serena MCP for all semantic code navigation and refactoring tasks
