# GraphRAG with Multi-Agent Tracing

Welcome to the **Seocho GraphRAG** project! This repository provides a powerful, multi-agent Retrieval-Augmented Generation (RAG) system powered by **Neo4j**, **HuggingFace**, and **OpenAI Agents**.

## 🚀 Key Features
- **Evaluation Interface**: A Chainlit-based control plane for rigorous testing and monitoring.
- **Deep Tracing**: Advanced tracing to **Neo4j** (Trace Graph) and **SQLite** (Session Analytics).
- **LEX-Style Schema**: Schema-as-Code with **Auto-Sync** capabilities. Automatically discovers and enforces schema from data.
- **Easy Data Ingestion**: Load your own datasets from HuggingFace with a single configuration change.
- **NeoDash Visualization**: Pre-configured dashboards to visualize Agent Reasoning and Costs.

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
- **Evaluation Interface**: [http://localhost:8501](http://localhost:8501)
- **NeoDash (Dashboard)**: [http://localhost:5005](http://localhost:5005)
- **Neo4j Browser**: [http://localhost:7474](http://localhost:7474)

## 📊 Ingesting Your Own Data & Customizing Agents
We have improved the workflow significantly.
👉 **[Read the TUTORIAL.md](TUTORIAL.md)** for a complete "Zero to GraphRAG" guide.

## 🧩 Architecture
- **Extraction Service**: Auto-discovers schema and extracts entities.
- **Semantic Service**: Manages graph queries and reasoning.
- **Evaluation Interface**: Chainlit-based UI for interacting with and evaluating your agents.
