# SEOCHO Project (Feature: KG Build)

This repository contains the setup and pipeline for building a Hybrid Knowledge Graph (RDF + LPG) using the Opik platform and OpenAI.

## Prerequisites

- Ubuntu/Linux Instance
- OpenAI API Key

## 1. Setup Environment

We provide a `setup.sh` script to automate the installation of Docker, Docker Compose, and the Opik platform.

```bash
chmod +x setup.sh
./setup.sh
```

> **Note**: This script may require `sudo` permissions and might prompt for a password. After installation, you may need to log out and log back in (or use `newgrp docker`) to apply Docker group permissions.

## 2. Configuration

Create a `.env` file in the root directory with your OpenAI API Key:

```bash
# .env
OPENAI_API_KEY=sk-proj-....
```

## 3. Launching the Services

This project uses Docker Compose to run the Jupyter environment integrated with Opik.

```bash
docker-compose up --build -d
```

- **Jupyter Lab**: [http://localhost:8888](http://localhost:8888)
- **Opik Platform**: [http://localhost:5173](http://localhost:5173)

## 4. Running the Pipeline

1. Access Jupyter Lab at [http://localhost:8888](http://localhost:8888).
2. Open `workspace/pipeline.py` or create a new notebook.
3. Run the pipeline to process data and generate the Knowledge Graph.

The pipeline results (RDF `.ttl` and LPG `.csv`) will be saved in the `output/` directory.

## Directory Structure

- `Dockerfile`: Defines the Python environment for the agent.
- `docker-compose.yml`:Orchestrates the Jupyter agent and opik networking.
- `setup.sh`: Installs Docker and Opik.
- `workspace/`: Contains the source code and pipeline logic.
    - `pipeline.py`: Main logic for FIBO-based KG extraction.
- `opik/`: (Cloned by setup.sh) The generic Opik platform.

