# Seocho Examples

This directory contains comprehensive examples demonstrating how to use Seocho for various data lineage and GraphRAG use cases.

## 📁 Example Categories

### Supply Chain Analytics
- **supply_chain/** - End-to-end supply chain data ingestion and analysis
  - `sample_supply_chain_data.json` - Sample data with suppliers, manufacturers, and distributors
  - Shows data lineage from raw data → DataHub → DozerDB → Visualization

### Data Quality Validation
- **data_quality/** - Data quality assessment and monitoring examples
  - Demonstrates automated data validation using Python engine
  - Shows quality scoring and alerting mechanisms

### GraphRAG Implementations
- **graphrag/** - Graph Retrieval-Augmented Generation examples
  - Shows how to build knowledge graphs for AI applications
  - Includes multi-hop query examples and evaluation datasets

## 🚀 Quick Start Examples

### 1. Supply Chain Data Flow
```bash
# Start services
make up

# Ingest supply chain data
make ingest-supply-chain

# View results in NeoDash
open http://localhost:5005
```

### 2. Glossary Integration
```bash
# Ingest ontology terms
make ingest-glossary

# Query glossary terms
docker compose exec engine cypher-shell -u neo4j -p thisisakccdemo
```

### 3. Custom Data Pipeline
```bash
# Create your own recipe
RECIPE=config/my_recipe.yml make ingest-custom
```

## 🎯 Use Case Examples

| Use Case | Files | Description |
|----------|--------|-------------|
| **Supply Chain Visibility** | `supply_chain/` | Track products from suppliers to consumers |
| **Data Quality Monitoring** | `data_quality/` | Validate data completeness and accuracy |
| **GraphRAG Knowledge Base** | `graphrag/` | Build AI-ready knowledge graphs |
| **Enterprise Integration** | `enterprise/` | Connect to existing data catalogs |

## 🔍 Running Examples

Each example directory contains:
- `README.md` - Specific instructions
- `data/` - Sample datasets
- `scripts/` - Automation scripts
- `config/` - Example configurations

For detailed instructions, see each example's README file.