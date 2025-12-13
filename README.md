# GraphRAG with Multi-Agent Tracing

Welcome to the **Seocho GraphRAG** project! This repository provides a powerful, multi-agent Retrieval-Augmented Generation (RAG) system powered by **Neo4j**, **HuggingFace**, and **Arize Phoenix**.

## 🚀 Key Features
- **Multi-Agent Tracing**: Visualize agent interactions and performance with [Arize Phoenix](https://docs.arize.com/phoenix/).
- **Easy Data Ingestion**: Load your own datasets from HuggingFace with a single configuration change.
- **Multi-Database Support**: Dynamically manage and load data into specific Neo4j databases based on categories.
- **Graph Power**: Leverage Neo4j for deep semantic understanding and entity linking.

## 🛠️ Quick Start

### 1. Setup Environment
We provide an interactive script to help you configure your API keys (`OPENAI_API_KEY`, `HF_TOKEN`, etc.).

```bash
./setup_env.sh
```

### 2. Run the Stack
Build and start all services using Docker:

```bash
docker-compose up -d --build
```

### 3. Explore
- **Chat Interface**: [http://localhost:8501](http://localhost:8501)
- **Phoenix Tracing**: [http://localhost:6006](http://localhost:6006)
- **Neo4j Browser**: [http://localhost:7474](http://localhost:7474)

## 📊 Ingesting Your Own Data
Want to use your own HuggingFace dataset? It's easy!
👉 **[Read the TUTORIAL.md](TUTORIAL.md)** for a step-by-step guide.

## 🧩 Architecture
- **Extraction Service**: Extracts entities and relationships using OpenAI models.
- **Semantic Service**: Manages graph queries and reasoning.
- **Chat Interface**: Streamlit-based UI for interacting with your data.
