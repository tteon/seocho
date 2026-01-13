# Graph RAG Evaluation Framework

A modular framework for evaluating hybrid retrieval agents using LPG (Labeled Property Graph), RDF, and Vector search.

## 🚀 Quick Start

```bash
# 1. Build indexes
docker exec agent-jupyter-container python -m src.cli.index --all

# 2. Run experiments
docker exec agent-jupyter-container python -m src.cli.evaluate --macro
```

## 📁 Project Structure

```
src/
├── config/          # Configuration & schemas
├── retrieval/       # Database tools (LPG, RDF, LanceDB)
├── indexing/        # Data ingestion pipelines
├── evaluation/      # Experiment framework & metrics
├── agents/          # Agent definitions
├── data/            # Opik dataset utilities
└── cli/             # Command-line entry points
```

## 🔧 CLI Commands

### Indexing
```bash
python -m src.cli.index --lancedb     # Vector index only
python -m src.cli.index --neo4j       # Graph index only
python -m src.cli.index --all         # Both indexes
```

### Evaluation
```bash
python -m src.cli.evaluate --modes lpg,hybrid   # Specific modes
python -m src.cli.evaluate --ablation           # All ablation combinations
python -m src.cli.evaluate --macro              # Macro experiments
python -m src.cli.evaluate --all                # Everything
```

### Data Export
```bash
python -m src.cli.export --traces       # Export Opik traces
python -m src.cli.export --datasets     # Export all datasets
```

## 🧪 Experiment Types

### Macro Experiments
System-level comparisons:
- **M1**: Full System (LPG+RDF+HYBRID) with Manager
- **M2**: Full System with Single Agent
- **M3**: LPG+HYBRID (no ontology)
- **M4**: RDF+HYBRID (no structured facts)

### Ablation Study
Component-level analysis:
- **A1-A3**: Single retrieval methods (LPG, RDF, HYBRID)
- **A4-A6**: Pair combinations

## 📊 Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `AnswerRelevance` | LLM | Output addresses query |
| `Hallucination` | LLM | Fabrication detection |
| `RoutingAccuracy` | Custom | Correct tool selection |
| `ContextPrecision` | Custom | Retrieved context quality |
| `ConflictResolutionScore` | Custom | Hierarchy of Truth compliance |

## 🔌 Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...

# Database (defaults work in Docker)
NEO4J_URI=bolt://graphrag-neo4j:7687
LANCEDB_PATH=/workspace/data/lancedb
OPIK_URL_OVERRIDE=http://localhost:5173/api
```

## 📚 Adding New Components

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Adding new retrieval tools
- Creating custom metrics
- Defining new experiments

## 🐳 Docker Setup

```bash
docker-compose up -d
docker exec -it agent-jupyter-container bash
```

## License

MIT
