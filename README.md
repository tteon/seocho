# GraphRAG Application

This repository contains the source code for a GraphRAG (Graph Retrieval-Augmented Generation) application, integrating Neo4j, DataHub, and LLM-powered services.

## Architecture

The application is composed of the following services:

*   **Graph Storage**: Neo4j (GraphStack with various plugins like APOC, GDS).
*   **Metadata Management**: DataHub (GMS, Frontend, MySQL, Elasticsearch, Kafka, Zookeeper).
*   **Core Services**:
    *   `extraction-service`: Python service for extracting knowledge from data and populating the graph.
    *   `semantic-service`: Python service for semantic reasoning and graph querying.
    *   `chat-interface`: Streamlit-based chat UI for interacting with the system.
    *   `app`: Next.js web application for visualizing graph data and managing the system.

## Prerequisites

*   Docker
*   Docker Compose
*   Node.js (for local `app` development)
*   Python 3.10+ (for local service development)

## Getting Started

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/tteon/seocho.git
    cd seocho/graphrag-app
    ```

2.  **Configure Environment**:
    Copy the example environment file and adjust as needed:
    ```bash
    cp .env.example .env
    ```
    *   Update `OPENAI_API_KEY` and other sensitive values in `.env`.

3.  **Run with Docker Compose**:
    ```bash
    docker-compose up --build -d
    ```

4.  **Access the Services**:
    *   **DataHub**: http://localhost:9002 (Default: `datahub` / `datahub`)
    *   **Neo4j Browser**: http://localhost:7474 (Default: `neo4j` / `password` or as configured in `.env`)
    *   **GraphRAG App**: http://localhost:3000
    *   **Chat Interface**: http://localhost:8501

## Folder Structure

*   `app/`: Next.js web application.
*   `chat/`: Streamlit chat interface.
*   `extraction/`: Extraction service code.
*   `semantic/`: Semantic service code.
*   `graph/`: scripts for Neo4j.
*   `data/`: Persistent data volumes (excluded from git).

## License

[MIT](LICENSE)
