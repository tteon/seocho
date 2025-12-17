# SEOCHO (서초)
**Scalable Enterprise GraphRAG & Multi-Agent Orchestration Framework**

[![Open Source](https://img.shields.io/badge/Open%20Source-SEOCHO-blue)](https://github.com/your-org/seocho)
[![Feature](https://img.shields.io/badge/New%20Feature-Agent%20Studio-success)](http://localhost:8501)
[![Stack](https://img.shields.io/badge/Stack-Neo4j%20|%20FastAPI%20|%20Streamlit-orange)]()

**SEOCHO** is an open-source framework designed to bridge the gap between **unstructured data** and **structured knowledge graphs** for enterprise AI. It provides a scalable pipeline for Entity Extraction, Linking, and a robust Multi-Agent Studio for executing complex reasoning tasks over your data.

---

## 📢 Feature Update: Seocho Agent Studio
We are excited to introduce **Agent Studio**, a new module integrated directly into SEOCHO.
* **Visual Agent Debugging**: Interact with your agents and see their thought process in a real-time node graph.
* **Hierarchical Logic**: Ready-to-use Router -> Graph Analyst -> DBA -> Supervisor architecture.
* **Multi-Database Support**: Seamlessly switch between different ontologies (e.g., General vs. Financial).
* **Native Tracing**: Built on `openai-agents` with full observability.

---

## 🚀 Core Capabilities

### 1. 🏗️ Knowledge Graph Integration
Transform raw text into a high-fidelity Knowledge Graph.
- **Scalable Ingestion**: Pipeline to process documents and linking them to standard ontologies (FIBO, etc.).
- **Schema Management**: Dynamic schema application using `SchemaManager`.
- **DataHub Integration**: Governance and metadata management for your graph assets.

### 2. 🧠 Multi-Agent Orchestration
Move beyond simple RAG. SEOCHO agents understand structure.
- **Router Agent**: Intelligently routes queries based on complexity (Single-hop vs. Multi-hop).
- **Graph DBA Agent**: A specialized Text2Cypher expert that understands your specific graph schema and executes optimized queries.
- **Supervisor**: Aggregates insights from Vector, Graph, and Web sources.

### 3. 👁️ Observability & Reproducibility
- **Streamlit-Flow**: "White-box" your agents. See exactly why an agent chose a tool.
- **OpenAI Trace**: Send execution traces to your dashboard for long-term analysis.
- **Test-Driven**: Comprehensive `pytest` suite for agent tools and APIs.

---

## ⚡ Quick Start

### Prerequisites
- Docker & Docker Compose
- OpenAI API Key

### Build & Run
```bash
# 1. Clone the repository
git clone https://github.com/your-org/seocho.git
cd seocho

# 2. Configure Environment
cp .env.example .env
# Enter your OPENAI_API_KEY and NEO4J_PASSWORD

# 3. Launch the Stack
docker-compose up -d --build
```

### Access Points
| Service | URL | Description |
|---------|-----|-------------|
| **Agent Studio UI** | `http://localhost:8501` | Chat and visualize agent traces. |
| **API Server** | `http://localhost:8001/docs` | FastAPI backend for agents. |
| **Neo4j Browser** | `http://localhost:7474` | Direct graph database inspections. |
| **DataHub UI** | `http://localhost:9002` | Metadata Catalog (User: `datahub`, Pwd: `datahub`). |

### 🛠️ Data Mesh Demo
SEOCHO includes a fully functioning Data Mesh simulation.
1. **Mock Data Generation** (Seeds Neo4j with FIBO domains):
    ```bash
    docker exec extraction-service python demos/data_mesh_mock.py
    ```
2. **Financial Metadata Tutorial** (Seeds DataHub with Bond Security mappings):
    ```bash
    # Ensure DataHub GMS is running first!
    docker exec extraction-service python demos/datahub_fibo_ingest.py
    docker exec extraction-service python demos/demo_fibo_metadata.py
    ```

---

## 📂 Architecture

```mermaid
graph TD
    User[User] -->|Chat| UI[Streamlit Agent Studio]
    UI -->|API Request| API[Agent Server (FastAPI)]
    
    subgraph "SEOCHO Agent Core"
        Router[Router Agent] --> Graph[Graph Agent]
        Router --> Vector[Vector Agent]
        Graph --> DBA[Graph DBA]
        DBA -->|Text2Cypher| Neo4j[(Neo4j Graph)]
        Vector -->|Search| FAISS[(Vector Store)]
        All --> Supervisor[Supervisor]
    end
    
    API --> Router
    Supervisor -->|Final Answer| API
    API -->|Trace Steps| UI
```


## 🤝 Contributing
SEOCHO is a community-driven project. We welcome contributions for:
- New Ontology mappings.
- Additional Agent Tools.
- UI Enhancements.

Please read our [Contributing Guidelines](CONTRIBUTING.md) before getting started.

## 📜 License
MIT License.
