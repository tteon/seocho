# Seocho Project Structure

```
seocho/
├── 📁 src/                          # Core source code
│   └── seocho/
│       ├── __init__.py             # Package initialization
│       ├── 📁 core/                # Core functionality
│       │   └── config.py           # Configuration management
│       ├── 📁 ingestion/           # Data ingestion modules
│       │   ├── ingest_data.py      # Supply chain data ingestion
│       │   └── datahub_integration.py # DataHub glossary integration
│       ├── 📁 validation/          # Data quality validation
│       └── 📁 visualization/       # Visualization utilities
│
├── 📁 examples/                    # Comprehensive examples
│   ├── README.md                   # Examples overview
│   ├── 📁 supply_chain/            # Supply chain analytics
│   │   ├── README.md               # Supply chain guide
│   │   └── sample_supply_chain_data.json
│   ├── 📁 data_quality/            # Data quality examples
│   └── 📁 graphrag/                # GraphRAG implementations
│
├── 📁 docker/                      # Docker configurations
│   ├── datahub/
│   │   └── Dockerfile              # DataHub launcher
│   └── engine/
│       └── Dockerfile              # Python engine
│
├── 📁 config/                      # Configuration files
│   └── recipe_glossary.yml         # DataHub glossary recipe
│
├── 📁 scripts/                     # Utility scripts
│   └── datahub-bootstrap.sh        # Initial setup script
│
├── 📁 tests/                       # Test suite
├── 📁 docs/                        # Documentation
├── 📁 workspace/                   # Development workspace
├── 📁 sharepoint/                  # Shared data volume
├── 📁 neo4j/                       # Neo4j data (gitignored)
│   ├── data/                       # Database files
│   ├── logs/                       # Log files
│   ├── import/                     # Import directory
│   └── plugins/                    # Neo4j plugins
│
├── docker-compose.yml              # Main orchestration
├── Makefile                        # Development commands
├── .gitignore                      # Git ignore rules
├── README.md                       # Project documentation
├── SECURITY.md                     # Security policy
└── PROJECT_STRUCTURE.md            # This file
```

## 📁 Directory Purposes

- **`src/`**: Core Python package with modular architecture
- **`examples/`**: Ready-to-run examples for different use cases
- **`docker/`**: Container configurations for services
- **`config/`**: Configuration files and recipes
- **`scripts/`**: Automation and setup scripts
- **`tests/`**: Unit and integration tests
- **`workspace/`**: Development workspace (mounted volume)
- **`sharepoint/`**: Shared data storage (mounted volume)
- **`neo4j/`**: Neo4j database files (gitignored for security)

## 🚀 Quick Start

```bash
# Clone and setup
make bootstrap

# Start services
make up

# Run examples
make ingest-supply-chain
make ingest-glossary
```