# Quick Start Tutorial

Get the Graph RAG evaluation framework running in 5 minutes.

---

## Prerequisites

- Docker & Docker Compose installed
- OpenAI API key

## Step 1: Environment Setup

```bash
# Clone and navigate
cd /home/ubuntu/lab/seocho

# Copy environment file
cp .env.example .env

# Edit with your API key
nano .env
# Add: OPENAI_API_KEY=sk-your-key
```

## Step 2: Start Services

```bash
docker-compose up -d
```

This starts:
- Neo4j (bolt://localhost:7687)
- Opik (http://localhost:5173)
- Jupyter container

## Step 3: Build Indexes

```bash
# Enter the container
docker exec -it agent-jupyter-container bash

# Build all indexes (LanceDB + Neo4j)
python -m src.cli.index --all
```

Expected output:
```
🚀 Unified Indexing - Starting
📊 Phase 1: Building LanceDB Vector Index
✅ Table 'fibo_context' created with 1000+ rows
📊 Phase 2: Building Neo4j Graph Indexes
✅ Indexing Complete!
```

## Step 4: Run Your First Experiment

```bash
# Run a simple ablation (LPG only)
python -m src.cli.evaluate --modes lpg
```

## Step 5: View Results

Open Opik dashboard: http://localhost:5173

Navigate to **Projects → graph-agent-ablation** to see:
- Traces with tool calls
- Metric scores
- Experiment comparisons

---

## Common Commands Cheatsheet

| Task | Command |
|------|---------|
| Build vector index | `python -m src.cli.index --lancedb` |
| Build graph index | `python -m src.cli.index --neo4j` |
| Run macro experiments | `python -m src.cli.evaluate --macro` |
| Run full ablation | `python -m src.cli.evaluate --ablation` |
| Run all experiments | `python -m src.cli.evaluate --all` |
| Export traces | `python -m src.cli.export --traces` |

---

## Troubleshooting

### "No module named 'src'"
Make sure you're in `/workspace` directory inside the container.

### Neo4j connection failed
Check if Neo4j is running:
```bash
docker ps | grep neo4j
```

### LanceDB table not found
Run indexing first:
```bash
python -m src.cli.index --lancedb
```

---

## Next Steps

1. **Run full experiments**: `python -m src.cli.evaluate --all`
2. **Add custom metrics**: See [CONTRIBUTING.md](CONTRIBUTING.md)
3. **Analyze results**: Export via Opik dashboard
