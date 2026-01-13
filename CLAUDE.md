# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SEOCHO (KG Build) is a knowledge graph extraction pipeline that builds Hybrid Knowledge Graphs (RDF + LPG) from financial documents using OpenAI and the Opik platform for observability.

## Architecture Overview

### Core Components

- **OpenAI Integration**: Uses GPT models for knowledge extraction and entity/relationship identification
- **Opik Platform**: Provides tracing, evaluation, and monitoring of the KG extraction pipeline
- **Neo4j (DozerDB)**: Graph database supporting both RDF (Semantic Web) and LPG (Labeled Property Graph) models
- **Hybrid Knowledge Graph**: Outputs both RDF format (`.ttl` files) and LPG format (`.csv` files)
- **FIBO Ontology**: Financial Industry Business Ontology for semantic grounding of financial concepts

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Knowledge Sources                       │
│           (Financial Documents, FIBO Ontology)              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  OpenAI GPT Extraction                      │
│  (Entity Extraction, Relationship Identification,           │
│   RDF Triple Generation, Property Graph Generation)        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                 Opik Tracing & Monitoring                   │
│           (Trace Generation, Evaluation Metrics)            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Output Generation                         │
│  RDF (.ttl) ─────────┬───────── LPG (.csv)                 │
└──────────────────────┼──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Neo4j Database                           │
│          (DozerDB with n10s RDF & APOC plugins)            │
└─────────────────────────────────────────────────────────────┘
```

## Development Commands

### Environment Setup

```bash
# Initial setup (installs Docker, Docker Compose, and Opik)
chmod +x setup.sh
./setup.sh

# Alternative: Setup with Docker and Opik installation only
chmod +x setup-docker-and-opik.sh
./setup-docker-and-opik.sh
```

### Running the Pipeline

```bash
# Start all services (Jupyter + Opik + Neo4j)
docker-compose up --build -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down

# Restart services
docker-compose restart
```

### Access Points

- **Jupyter Lab**: http://localhost:8888
- **Opik Platform**: http://localhost:5173
- **Neo4j Browser**: http://localhost:7474 (Bolt: localhost:7687)

### Working in Jupyter

The main pipeline logic is in the `workspace/` directory:

```bash
# Navigate to workspace
cd workspace

# Run the main pipeline
python pipeline.py

# Run evaluation scripts
python evaluation.py
python opik_evaluation.py
python experiment_metrics.py
```

## Architecture Details

### Key Modules

1. **Pipeline Module** (`workspace/pipeline.py`)
   - Main knowledge graph extraction logic
   - FIBO ontology integration
   - RDF and LPG generation
   - OpenAI API integration with caching

2. **Agent Module** (`workspace/agent_*.py`)
   - Agent-based KG extraction
   - Routing and workflow management
   - Multi-agent coordination for complex extractions

3. **Core Module** (`src/core/`)
   - Core data structures
   - FIBO ontology utilities
   - KG validation logic

4. **Utils Module** (`src/utils/`)
   - OpenAI cache management
   - File I/O utilities
   - Neo4j connection helpers

### Data Flow

1. **Input Processing**: Financial text documents are processed through OpenAI GPT with FIBO ontology context
2. **Triple Extraction**: RDF triples (`subject-predicate-object`) are extracted with strict schema adherence
3. **LPG Generation**: Labeled property graph nodes and relationships are created for Neo4j
4. **Opik Tracing**: All operations are traced through Opik for monitoring and debugging
5. **Output Generation**:
   - RDF format: Turtle (`.ttl`) files for semantic web applications
   - LPG format: CSV files for Neo4j import

### Caching Strategy

- **Location**: `.openai_cache/` directory
- **Purpose**: Cache OpenAI API responses to reduce costs and speed up development
- **Key Generation**: MD5 hash of model + messages + temperature
- **Benefits**:
  - Faster iteration during development
  - Cost savings on repeated calls
  - Reproducible results

## Configuration

### Environment Variables (.env)

```bash
# Required
OPENAI_API_KEY=sk-proj-....

# Optional (with defaults)
OPIK_URL_OVERRIDE=http://localhost:5173/api
OPIK_WORKSPACE=seocho-kgbuild
OPIK_PROJECT_NAME=kgbuild

# Neo4j Configuration
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687
```

### Opik Configuration

The project uses Opik for comprehensive tracing and evaluation:

```python
# Opik client initialization
try:
    OPIK_CLIENT = Opik()
    OPENAI_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception as e:
    print(f"❌ Init Failed: {e}")
    exit(1)
```

All functions that interact with OpenAI are decorated with `@opik.track` for automatic tracing.

## Neo4j Integration

### Database Setup

- **Image**: graphstack/dozerdb:5.26.3.0 (Neo4j-compatible with enterprise features)
- **Plugins**:
  - **APOC**: Extended procedures and functions
  - **n10s** (Neosemantics): RDF import and handling

### Neo4j Configuration

```yaml
# Environment variables in docker-compose.yml
NEO4J_PLUGINS=["apoc", "n10s"]
NEO4J_apoc_export_file_enabled=true
NEO4J_apoc_import_file_enabled=true
NEO4J_dbms_security_procedures_unrestricted=*
```

### Importing Data

```python
# RDF import (for semantic web data)
CALL n10s.rdf.import.fetch("file:///path/to/graph.ttl", "Turtle")

# CSV import (for LPG data)
LOAD CSV WITH HEADERS FROM "file:///path/to/nodes.csv" AS row
CREATE (n:Label {property: row.property})

LOAD CSV WITH HEADERS FROM "file:///path/to/relationships.csv" AS row
MATCH (from {id: row.from_id}), (to {id: row.to_id})
CREATE (from)-[:REL_TYPE {prop: row.prop}]->(to)
```

## Key Files and Their Purposes

| File | Purpose |
|------|---------|
| `pipeline.py` | Main KG extraction pipeline with FIBO and Opik integration |
| `agent_setup.py` | Agent orchestration and workflow management |
| `agent_evaluation.py` | Evaluation metrics for KG quality |
| `retrieval_metrics.py` | Metrics for evaluating KG retrieval performance |
| `experiment_metrics.py` | Metrics for different KG extraction experiments |
| `graphagent_indexing.py` | Agent-based graph indexing and processing |
| `verify_tools.py` | Tools for verifying KG structure and integrity |
| `kgbuild-traces.csv` | Opik trace data for analysis |
| `kgbuild-traces.json` | Opik trace data in JSON format |

## Testing and Evaluation

### Running Tests

```bash
# Run all tests
cd tests
python -m pytest

# Run specific test modules
python test_agent_routing.py
python retrieval_metrics.py
```

### Evaluation Metrics

The project tracks several evaluation metrics:

1. **Extraction Quality**: How accurately entities and relationships are extracted
2. **Completeness**: Coverage of concepts from FIBO ontology
3. **Consistency**: Alignment between RDF and LPG representations
4. **Performance**: OpenAI API usage, caching efficiency
5. **Opik Metrics**: Trace quality, evaluation scores, feedback

## Output Formats

### RDF Output (`output/rdf_n10s/`)

Turtle format (`.ttl`) files that can be:
- Imported into Neo4j using n10s plugin
- Loaded into any RDF-compatible triplestore
- Processed with semantic web tools

### LPG Output (`output/lpg_native/`)

CSV files optimized for Neo4j import:
- `nodes_*.csv`: Node data with labels and properties
- `edges_*.csv`: Relationship data with types and properties

## Troubleshooting

### Common Issues

1. **OpenAI API Errors**
   - Check `.env` file for valid API key
   - Verify API key has sufficient quota
   - Check cache directory permissions

2. **Opik Connection Issues**
   - Ensure Opik is running: `docker ps | grep opik`
   - Check environment variables: `OPIK_URL_OVERRIDE`, `OPIK_WORKSPACE`
   - Verify network connectivity from Jupyter container

3. **Neo4j Connection Issues**
   - Check container status: `docker ps | grep neo4j`
   - Verify ports are not in use: `lsof -i :7474,7687`
   - Check data directory permissions: `./data/neo4j/`

4. **Docker Network Issues**
   - Ensure opik_default network exists: `docker network ls`
   - Create if missing: `cd opik && docker-compose up -d`

### Performance Optimization

1. **Caching**
   - Always use OpenAI cache in development
   - Clear cache when prompts change significantly
   - Monitor cache hit rate in logs

2. **Batch Processing**
   - Process multiple documents in batches
   - Use tqdm for progress tracking in Jupyter
   - Implement parallel processing for large datasets

3. **Opik Optimization**
   - Use selective tracing for production
   - Batch trace submissions
   - Monitor Opik storage usage

## Best Practices for Claude Code

### When Modifying the Pipeline

1. **Always use Opik tracking**: Decorate functions with `@opik.track`
2. **Maintain both RDF and LPG outputs**: Ensure parity between formats
3. **Follow FIBO ontology**: Strictly adhere to Financial Industry Business Ontology
4. **Test with sample data**: Use small datasets before large-scale processing
5. **Check cache impact**: Understand how changes affect OpenAI API calls

### When Adding New Features

1. **Add corresponding metrics**: Update evaluation and experiment metrics
2. **Update documentation**: Reflect changes in README and code comments
3. **Test integration**: Verify Neo4j import works with new output formats
4. **Monitor Opik dashboard**: Check trace quality and evaluation scores

### Code Quality Standards

- Use type hints consistently
- Add docstrings for all functions
- Log important operations
- Handle errors gracefully with Opik error tracking
- Maintain backward compatibility

## Resources and Documentation

- [Opik Documentation](https://www.comet.com/docs/opik/)
- [FIBO Ontology](https://spec.edmcouncil.org/fibo/)
- [Neo4j n10s Plugin](https://neo4j.com/labs/neosemantics/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
- [Project README](README.md)
