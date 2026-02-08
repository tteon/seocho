# CLAUDE.md

Guidance for Claude Code and AI agents working in this repository.

## Project Overview

SEOCHO is an **Agent-Driven Knowledge Graph Platform** that transforms unstructured data into structured knowledge graphs. It provides:

- **Data Pipeline**: DataSource → Ontology-driven extraction → Entity linking → Deduplication → Neo4j loading
- **Multi-Agent Reasoning**: Router mode (single-agent routing) and Parallel Debate mode (all DB agents answer in parallel, Supervisor synthesizes)
- **Dynamic DB Provisioning**: Each dataset gets its own Neo4j database with auto-applied schema
- **Observability**: Opik for production tracing/eval, Agent Studio for PoC demos

## MCP Tools — Serena

**Always use Serena** for semantic code navigation. It provides language-server-level accuracy (symbol references, definitions, call hierarchies) superior to grep.

Use Serena for: finding all callers of a function, renaming/refactoring symbols, tracing class hierarchy, understanding import graphs. When modifying code across multiple files, **start with Serena** to enumerate all affected locations.

## Commands

```bash
# Core services
make up                    # Start Neo4j + app services
make down                  # Stop services
make restart               # Restart services
make logs                  # Tail all logs
make clean                 # Remove containers and volumes

# Opik observability (opt-in)
make opik-up               # Core + Opik tracing stack
make opik-down             # Stop all including Opik
make opik-logs             # Tail Opik service logs

# Dev
make test                  # pytest in Docker
make lint                  # flake8 + black check
make format                # black + isort auto-format
```

## Architecture

### Two Execution Modes

**Router Mode** (`POST /run_agent`) — Sequential single-agent routing:
```
User → Router → {GraphAgent, VectorAgent, WebAgent, TableAgent} → Supervisor → Answer
```

**Parallel Debate Mode** (`POST /run_debate`) — All DB agents answer in parallel:
```
User → DebateOrchestrator → [Agent_db1 || Agent_db2 || ... || Agent_dbN] → SharedMemory → Supervisor → Answer
```

### Data Pipeline
```
DataSource → OntologyPromptBridge → EntityExtractor → EntityLinker → EntityDeduplicator → DatabaseManager → AgentFactory
```

### Module Map

**extraction/** — Core ETL + multi-agent system

| Module | Purpose |
|--------|---------|
| `agent_server.py` | FastAPI server: `/run_agent`, `/run_debate`, `/databases`, `/agents` |
| `pipeline.py` | Central orchestration: DataSource → Extract → Link → Dedup → Schema → Load |
| `debate.py` | DebateOrchestrator: parallel fan-out → collect → supervisor synthesis |
| `agent_factory.py` | Per-DB Agent creation with closure-bound tools |
| `shared_memory.py` | Request-scoped agent shared memory + query cache |
| `data_source.py` | DataSource ABC + FileDataSource (CSV/JSON/Parquet) + APIDataSource |
| `ontology_prompt_bridge.py` | Ontology YAML → LLM prompt variable injection |
| `extractor.py` | OpenAI-based entity/relationship extraction |
| `linker.py` | OpenAI-based entity resolution |
| `deduplicator.py` | Embedding cosine-similarity dedup (threshold=0.92) |
| `database_manager.py` | Neo4j DB provisioning + schema + data loading |
| `graph_loader.py` | Neo4j MERGE operations with regex-validated labels |
| `schema_manager.py` | Dynamic schema discovery and constraint application |
| `vector_store.py` | FAISS embedding manager |
| `config.py` | Centralized config: Neo4j credentials, DatabaseRegistry, Opik settings |
| `tracing.py` | Opik integration: `configure_opik()`, `wrap_openai_client()`, `@track`, `update_current_span/trace` |
| `ontology/base.py` | Ontology, NodeDefinition, RelationshipDefinition, PropertyType |
| `collector.py` | Legacy HuggingFace data collector |
| `prompt_manager.py` | Jinja2 prompt templating + history logging |
| `main.py` | Hydra CLI entry point for batch pipeline execution |
| `ingest_finder.py` | Hydra-driven batch ingestion with structured output |
| `multi_db_loader.py` | Multi-database entity loading |
| `manage_databases.py` | Database provisioning CLI |
| `conf/` | Hydra configs (prompts, schemas, ingestion recipes) |

**evaluation/** — Streamlit Agent Studio (PoC demo)
- `app.py` — Split-screen UI: chat + live agent flow graph

**semantic/** — FastAPI semantic analysis service

**demos/** — Agent and tracing demos

### Database Architecture

- **Neo4j** (DozerDB 5.26): single instance, multi-database
  - Static: `kgnormal`, `kgfibo`, `agenttraces`
  - Dynamic: created via `DatabaseManager.provision_database()`
  - DB name validation: `^[A-Za-z][A-Za-z0-9]*$`
  - Registry: `db_registry` singleton in `config.py`
- **FAISS**: Vector similarity search for semantic retrieval
- **Opik** (opt-in profile): LLM evaluation, tracing & agent visualization

### Observability: Agent Studio vs Opik

| Concern | Agent Studio (Streamlit) | Opik |
|---------|--------------------------|------|
| **Role** | PoC demo & presentation | Production eval & trace |
| **Agent trace** | Custom flow graph (FANOUT/DEBATE/COLLECT) | Native span tree with parent-child |
| **LLM call tracing** | Manual trace_steps construction | Auto-traced via `wrap_openai_client` |
| **Cost / latency** | Not tracked | Built-in per-span metrics |
| **Evaluation** | None | Datasets, scoring, experiments |
| **When to use** | Stakeholder demos, PoC walkthroughs | Development, debugging, production monitoring |

Opik UI: `http://localhost:5173` (when `--profile opik` is active)

### Opik Span Tree (Debate Pattern)

```
agent_server.run_debate                          [tags: debate-mode]
  └─ debate.run_debate                           [phase: orchestration, agent_count: N]
       ├─ debate.run_single_agent                [phase: fan-out, db: kgnormal]
       │    └─ (OpenAI chat.completions.create)  [auto-traced]
       ├─ debate.run_single_agent                [phase: fan-out, db: kgfibo]
       │    └─ (OpenAI chat.completions.create)  [auto-traced]
       └─ debate.supervisor_synthesis            [phase: synthesis]
            └─ (OpenAI chat.completions.create)  [auto-traced]
```

## Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

# Optional — Neo4j port overrides
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687

# Opik (opt-in, for --profile opik)
OPIK_VERSION=latest
OPIK_URL=http://opik-backend:8080/api
OPIK_PROJECT_NAME=seocho
```

### Hydra Config (`extraction/conf/`)
```
conf/
├── config.yaml              # Global: model, mock_data, openai_api_key
├── prompts/
│   ├── default.yaml          # Extraction prompt (ontology-aware via Jinja2)
│   ├── linking.yaml          # Entity linking prompt
│   └── router.yaml           # Router agent prompt
├── schemas/
│   ├── baseline.yaml         # kgnormal schema
│   ├── fibo.yaml             # kgfibo financial ontology schema
│   └── tracing.yaml          # agent_traces schema
└── ingestion/
    ├── config.yaml           # Batch ingestion config
    └── schema/               # Per-schema ingestion configs
```

## Service Ports

| Service | Port | Profile |
|---------|------|---------|
| Streamlit Agent Studio | 8501 | core |
| FastAPI Agent Server | 8001 | core |
| Neo4j HTTP | 7474 | core |
| Neo4j Bolt | 7687 | core |
| Opik Frontend | 5173 | opik |
| Opik Backend API | 8080 | opik |
| Opik ClickHouse | 8123 | opik |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run_agent` | POST | Router mode (legacy single-agent routing) |
| `/run_debate` | POST | Parallel Debate mode (all DB agents in parallel) |
| `/databases` | GET | List registered Neo4j databases |
| `/agents` | GET | List active DB-bound agents |

Request body: `{"query": "...", "user_id": "user_default"}`
- `query` max length: 2000 chars
- CORS: `localhost:8501`, `localhost:3000`

## Code Rules

### Rule 1: Database Names Must Be Validated
All database names must match `^[A-Za-z][A-Za-z0-9]*$`.
```python
from config import db_registry, _VALID_DB_NAME_RE
db_registry.register("mydb01")
db_registry.is_valid("mydb01")  # True
```

### Rule 2: Neo4j Labels Must Be Regex-Validated
Before interpolating any label into Cypher, validate with `^[A-Za-z_][A-Za-z0-9_]*$`. Use `graph_loader._validate_label()`.

### Rule 3: Centralized Config Only
Import Neo4j/Opik credentials from `config.py`. Never duplicate `os.getenv()` calls.
```python
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, OPIK_ENABLED
```

### Rule 4: Logging, Not Print
Every module: `logger = logging.getLogger(__name__)`. No `print()` in production code.

### Rule 5: DataSource Standard Format
All data sources must return: `[{"id": str, "content": str, "category": str, "source": str, "metadata": dict}]`

### Rule 6: Ontology Drives Extraction
`OntologyPromptBridge.render_extraction_context()` injects entity/relationship types into extraction prompts via Jinja2. The `default.yaml` prompt uses `{% if ontology_name %}` to branch.

### Rule 7: Dedup Before Loading
Pipeline order: Extract → Link → **Deduplicate** → Schema → Load. `EntityDeduplicator` uses embedding cosine similarity (threshold=0.92).

### Rule 8: Agent Tools Use Closures
`AgentFactory.create_db_agent()` creates tools as closures that capture `db_name`. Each agent's `query_db` tool only queries its bound database.

### Rule 9: SharedMemory is Request-Scoped
Create a new `SharedMemory()` per API request. Never share across requests.

### Rule 10: Debate Trace Structure
DebateOrchestrator produces trace steps: `FANOUT`, `DEBATE`, `COLLECT`, `SYNTHESIS`. The Streamlit UI uses `metadata.parent` for fan-out edges and `metadata.sources` for collect edges.

### Rule 11: Opik Tracing is Opt-In
All tracing is gated behind `OPIK_ENABLED` (True when `OPIK_URL_OVERRIDE` env var is set). Use helpers from `tracing.py`:
```python
from tracing import track, wrap_openai_client, update_current_span
```

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

### Ontology-Driven Extraction
```python
from ontology.base import Ontology
from ontology_prompt_bridge import OntologyPromptBridge

ontology = Ontology.from_yaml("conf/schemas/fibo.yaml")
bridge = OntologyPromptBridge(ontology)
context = bridge.render_extraction_context()
# → {"entity_types": "...", "relationship_types": "...", "ontology_name": "FIBO"}
```

### Creating Agent Tools
```python
from agents import function_tool, RunContextWrapper

@function_tool
def execute_cypher_tool(context: RunContextWrapper, query: str, database: str = "neo4j") -> str:
    """Executes Cypher query against specified database."""
    return neo4j_conn.run_cypher(query, database=database)
```

### Enriching Opik Spans
```python
from tracing import track, update_current_span, update_current_trace

@track("my_module.my_function")
async def my_function(query: str):
    update_current_span(metadata={"key": "value"}, tags=["my-tag"])
    update_current_trace(metadata={"query": query[:200]})
    # ... function body
```

## Development Guidelines

- Follow PEP 8, use type hints for all function signatures
- Agent server uses asyncio; IO-bound tools should be async-compatible
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`)
- Avoid broad `except Exception` — use specific exception types
- Dynamic Neo4j labels must be regex-validated before Cypher interpolation
- Use Serena MCP for all semantic code navigation and refactoring tasks
