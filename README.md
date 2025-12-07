# SEOCHO - GraphRAG Pipeline

## Overview
**SEOCHO** is a Graph Retrieval-Augmented Generation (GraphRAG) application. It ingests semi-structured financial data, extracts entities and relationships using LLMs, and builds a Knowledge Graph in Neo4j. It also maintains metadata in DataHub and supports vector search via FAISS.

## Features
- **Entity Extraction**: Uses OpenAI (GPT-3.5/4) to extract nodes and relationships from text.
- **Entity Linking**: Resolves duplicates and standardizes entities.
- **Knowledge Graph**: Stores structured data in Neo4j (using `open-gds` for algorithms).
- **Metadata Management**: Integrates with DataHub for lineage and dataset tracking.
- **Vector Search**: Embeds content using OpenAI Embeddings and stores in FAISS.
- **Jupyter Interface**: Built-in notebook environment for debugging and analysis.

## Project Structure
```
seocho/
├── extraction/         # Python Extraction Service
│   ├── conf/           # Hydra Configuration (Prompts/Models)
│   ├── pipeline.py     # Main Logic Class
│   ├── main.py         # Entry Point
│   ├── collector.py    # Data Ingestion (Real + Mock)
│   ├── extractor.py    # OpenAI Logic
│   └── ...
├── notebooks/          # Jupyter Notebooks for Debugging
├── docker-compose.yml  # Infrastructure Definition
└── README.md
```

## Getting Started

### Prerequisites
- **Docker** & **Docker Compose**
- **OpenAI API Key**

### 1. Configuration
Create a `.env` file in the project root:
```bash
OPENAI_API_KEY=sk-...
NEO4J_PASSWORD=password
```

### 2. Run with Docker
Start the entire stack:
```bash
docker-compose up --build
```
This will start:
- **Extraction Service**: Runs the pipeline.
- **Neo4j**: Graph Database (http://localhost:7474).
- **DataHub**: Metadata Platform (http://localhost:9002).
- **Jupyter**: Debugging Interface (http://localhost:8888, token: `graphrag`).

### 3. Modes (Mock vs Real Data)
You can toggle between Mock Data and Real Data (HuggingFace FinDER dataset) in `extraction/conf/config.yaml`:
```yaml
mock_data: true  # Set to false to use real dataset
```

## Development
- **Pipeline Logic**: Modify `extraction/pipeline.py`.
- **Prompts**: Edit `extraction/conf/prompts/*.yaml`.
- **Debugging**: Use `notebooks/debug_agent.ipynb`.
