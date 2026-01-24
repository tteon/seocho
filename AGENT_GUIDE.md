# Agent Integration Guide (AGENT_GUIDE.md)

## Purpose
This document provides a semantic map and interoperability guide for AI agents interacting with the **SEOCHO** codebase. It defines the architectural invariants, configuration surfaces, and extension points.

## System Architecture

### Core Components
- **Extraction Pipeline** (`extraction/pipeline.py`): The central orchestration logic. Agents modifying the flow should inspect `ExtractionPipeline.run()`.
- **Configuration** (`extraction/conf/`): Hydra-based configuration.
    - `config.yaml`: Global settings (API keys, models, toggles).
    - `prompts/*.yaml`: Jinja2 templates for LLM interactions.
- **Data Ingestion** (`extraction/collector.py`):
    - **Mock Mode**: `mock_data: true`. Uses hardcoded list.
    - **Real Mode**: `mock_data: false`. Fetches from HuggingFace (FinDER).
- **Graph Schema**:
    - **Nodes**: Extracted entities.
    - **Relationships**: Extracted predicates.
    - **Linking**: Canonical IDs via `linker.py`.

## Interoperability Interfaces

### 1. Configuration Interface
Agents should prioritize modifying `extraction/conf/config.yaml` to alter system behavior (e.g., switching models, toggling mock data) rather than editing code.
```yaml
model: gpt-3.5-turbo  # Target for agent optimization
mock_data: true       # Toggle for testing
```

### 2. Prompt Engineering Surface
Agents optimizing extraction quality should target `extraction/conf/prompts/`.
- `default.yaml`: Entity/Relationship extraction prompt.
- `linking.yaml`: Entity resolution prompt.
*Constraint*: Maintain the Jinja2 variables `{{ text }}` and `{{ category }}`.

### 3. Output Consumption
The pipeline produces machine-readable artifacts in `extraction/output/`:
- `*_extracted.json`: Raw node/edge lists.
- `vectors.index`: FAISS vector store.
- `vectors_meta.pkl`: Metadata for vectors.

### 4. Docker Environment
- **Service**: `extraction-service`.
- **Environment Variables**: Defined in `.env` and `docker-compose.yml`.
- **Volume Mounts**: `extraction/` is mounted at `/app`. Code changes reflect immediately on restart.

## Extension Points
- **New Data Sources**: subclass `DataCollector` or add logic in `collect_raw_data`.
- **New Graph Backends**: Implement a loader class following `GraphLoader` protocol.
