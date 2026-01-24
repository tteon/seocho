# AGENTS.md

This file provides guidance to coding agents collaborating on this repository.

## Project Overview

SEOCHO (서초) is an Enterprise GraphRAG & Multi-Agent Orchestration Framework that bridges unstructured data and structured knowledge graphs. It provides:

- Multi-agent orchestration with OpenAI Agents SDK
- Knowledge graph construction and Text2Cypher querying
- Real-time agent trace visualization
- Data Mesh integration with DataHub metadata governance
- FIBO (Financial Industry Business Ontology) support

## Project Vision

The de facto enterprise framework for building observable, multi-agent systems that reason over knowledge graphs.

## Project Requirements

- Always use English in code, examples, and comments.
- Features should be implemented concisely, maintainably, and efficiently.
- Code is not just for execution, but also for readability.
- Use type hints for all function signatures (PEP 8 compliance).
- Agent tools must be stateless and return serializable results.
- Never hardcode credentials; use environment variables.

## Architecture

The project is organized as a Docker-based microservices architecture with Python services:

### Core Services

- `extraction/` - Core ETL pipeline and multi-agent system
  - `pipeline.py` - Central orchestration: DataCollector → EntityExtractor → EntityLinker → GraphLoader
  - `agent_server.py` - FastAPI server with OpenAI Agents SDK (`/run_agent` endpoint)
  - `agents/` - Agent base classes and tool registry
    - `base.py` - BaseAgent abstract class, ToolRegistry singleton, `@register_tool` decorator
  - `ontology/` - Ontology management system
    - `base.py` - NodeDefinition, RelationshipDefinition, PropertyType, ConstraintType
  - `vector_store.py` - FAISS embedding manager for semantic retrieval
  - `schema_manager.py` - Dynamic Neo4j schema discovery and application
  - `graph_loader.py` - Neo4j graph data ingestion
  - `prompt_manager.py` - Jinja2 prompt template rendering
  - `conf/` - Hydra configuration (prompts, schemas, ingestion recipes)

- `evaluation/` - Streamlit Agent Studio
  - `app.py` - Split-screen UI: chat (left) + live agent flow graph (right)
  - Uses `streamlit_flow` for real-time trace visualization

- `semantic/` - Semantic analysis service
  - `main.py` - FastAPI server
  - `agent.py` - LangChain-based agent implementation
  - `neo4j_client.py` - Graph database client

- `demos/` - Data Mesh demonstrations
  - `data_mesh_mock.py` - Seeds Neo4j with FIBO-style domains, products, datasets
  - `datahub_fibo_ingest.py` - Creates FIBO glossary terms and domains in DataHub
  - `demo_fibo_metadata.py` - Financial metadata tutorial

- `src/seocho/` - Modular package for DataHub ingestion utilities
  - `ingestion/` - DataHub integration and data ingestion scripts
  - `core/` - Configuration and shared utilities

### Multi-Agent Hierarchy

```
Router Agent (entry point)
    ├── VectorAgent → FAISS search → Supervisor
    ├── GraphAgent → GraphDBA (Text2Cypher) → Neo4j → Supervisor
    ├── WebAgent → web search → Supervisor
    └── TableAgent → structured data → Supervisor
```

### Data Layer

- **Neo4j** (DozerDB 5.26) - Graph databases: `kgnormal`, `kgfibo`, `agent_traces`
- **DataHub** - Metadata governance (GMS + Frontend)
- **FAISS** - Vector similarity search
- **Elasticsearch** - DataHub search backend
- **MySQL** - DataHub persistence
- **Kafka** - Event streaming for DataHub

## Common Development Commands

### Docker Development

```bash
# Start/stop services
make up                    # Start all Docker services
make down                  # Stop all services
make restart               # Restart services
make clean                 # Remove containers and volumes

# Logs and debugging
make logs                  # View logs (tail -f style)
make shell                 # Open bash in engine container

# Bootstrap
make bootstrap             # Build containers and prepare environment
```

### Testing & Code Quality

```bash
# Via Make
make test                  # Run pytest in Docker
make lint                  # Run flake8 + black check
make format                # Auto-format with black + isort

# Direct pytest
docker compose exec extraction-service pytest tests/ -v
docker compose exec extraction-service pytest tests/test_api_integration.py -v
docker compose exec extraction-service pytest tests/test_tools.py::test_get_schema -v
```

### Data Ingestion

```bash
# DataHub ingestion
make ingest-glossary              # Ingest glossary terms
make ingest-supply-chain          # Ingest supply chain data
make ingest-custom RECIPE=path    # Custom ingestion recipe

# Data Mesh demos
docker exec extraction-service python demos/data_mesh_mock.py
docker exec extraction-service python demos/datahub_fibo_ingest.py
docker exec extraction-service python demos/demo_fibo_metadata.py
```

### Local Development (without Docker)

```bash
# Install dependencies
pip install -r extraction/requirements.txt
pip install -r evaluation/requirements.txt

# Run services
cd extraction && uvicorn agent_server:app --host 0.0.0.0 --port 8001
cd evaluation && streamlit run app.py --server.port 8501
```

## Key Technical Details

1. **Multi-Agent Architecture**: Hierarchical agent system using OpenAI Agents SDK with Router → Specialists → Supervisor pattern
2. **Tool Decoration**: Use `@function_tool` decorator for agent tools; tools receive `RunContextWrapper` for context access
3. **Async-first**: Agent server uses asyncio; IO-bound operations should be async-compatible
4. **Database Allowlist**: GraphDBA validates database names (`kgnormal`, `kgfibo`, `neo4j`, `agent_traces`) before Cypher execution
5. **Schema Discovery**: SchemaManager dynamically reads and applies Neo4j schemas from YAML definitions
6. **Trace Observability**: All agent executions are traced via `trace()` context manager and visualized in Streamlit
7. **Configuration**: Hydra + OmegaConf for hierarchical YAML configuration with environment variable interpolation

## Development Notes

- All agent tools should have comprehensive docstrings (used by LLM for tool selection)
- Agent handoffs are explicit; define `handoffs` list when creating agents
- Use `st.session_state` for Streamlit state management
- Neo4j queries should use parameterized values to prevent Cypher injection
- Always rebuild Docker images after changing `requirements.txt`
- Integration tests require all Docker services running

## Development Tips

Code standards:
- Use `@dataclass` for configuration and context objects
- Prefer composition over inheritance for agent specialization
- Return JSON-serializable results from tools for trace logging
- Use type hints consistently; the codebase uses Python 3.11+ features

Agents:
- Inherit from `BaseAgent` for custom agents; implement `validate_input()` method
- Register tools globally via `@register_tool("name")` decorator or locally via agent's `tools` list
- Keep agent instructions focused and specific; avoid generic prompts
- GraphDBA should always check schema before generating Cypher

Tests:
- Place tests in `extraction/tests/` or `semantic/tests/`
- Use `pytest` fixtures for Neo4j and API client setup
- Mock external services (OpenAI, DataHub) in unit tests
- Include regression tests for bug fixes

Configuration:
- Add new prompts to `extraction/conf/prompts/` as Jinja2 YAML files
- Define schemas in `extraction/conf/schemas/` with node labels, relationships, and properties
- Use environment variables for secrets: `${oc.env:OPENAI_API_KEY}`

## Review Guidelines

Please note that the attention of contributors and maintainers is the MOST valuable resource.
Less is more: focus on the most important aspects.

- Your review output SHOULD be concise and clear.
- You SHOULD only highlight P0 and P1 level issues, such as severe bugs, performance degradation, or security concerns.
- You MUST not reiterate detailed changes in your review.
- You MUST not repeat aspects of the PR that are already well done.

Please consider the following when reviewing code contributions.

### Agent Design
- Ensure agent instructions are clear and unambiguous
- Verify handoff chains are properly defined and don't create cycles
- Check that tools return appropriate error messages for debugging
- Validate that database queries use the allowlist for database names

### API Design
- Use Pydantic models for request/response validation
- Return structured `AgentResponse` with both `response` and `trace_steps`
- Include proper HTTP error codes and error messages

### Testing
- Ensure all new agent tools have corresponding tests
- Ensure that all bugfixes and features have corresponding tests
- Test agent handoff scenarios end-to-end

### Documentation
- New agents must include docstrings explaining their role and capabilities
- Update `extraction/conf/prompts/` with any new prompt templates
- Link to relevant modules and classes in documentation
