# Makefile for Seocho - Data Lineage & GraphRAG Framework

DOCKER_COMPOSE = docker compose
DOCKER_COMPOSE_LIVE = docker compose -f docker-compose.yml -f docker-compose.dev.yml
DOCKER_COMPOSE_TUTORIALS = docker compose -f docker-compose.tutorials.yml

.PHONY: up up-live up-legacy-semantic down restart logs clean bootstrap shell test test-integration e2e-smoke lint format help opik-up opik-down opik-logs demo-raw demo-meta demo-neo4j demo-graphrag-opik demo-all setup-env tutorials-up tutorials-down tutorials-logs tutorials-shell tutorials-build tutorials-smoke tutorials-test tutorials-pytest

##@ Development

help: ## Show this help message
	@echo "Seocho - Graph Memory & Knowledge Graph Platform"
	@echo ""
	@echo "Usage:"
	@echo "  make <target>"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Bootstrap the development environment
	@echo "🚀 Bootstrapping Seocho environment..."
	@docker compose build
	@echo "✅ Environment ready!"

setup-env: ## Interactive .env setup (OpenAI key, Opik, ports)
	@bash scripts/setup/init-env.sh

up: ## Start core local stack (DozerDB + extraction API + platform UI)
	@echo "🐳 Starting Seocho core local stack from an image-backed source snapshot..."
	@docker compose up -d --build
	@echo "✅ Services started!"
	@echo "🖥️  Platform UI: http://localhost:$${CHAT_INTERFACE_PORT:-8501}"
	@echo "🧠 Backend API Docs: http://localhost:$${EXTRACTION_API_PORT:-8001}/docs"
	@echo "🗄️  DozerDB Browser: http://localhost:$${NEO4J_HTTP_PORT:-7474}"
	@echo "ℹ️  Legacy semantic-service is opt-in: docker compose --profile legacy-semantic up -d semantic-service"
	@echo "ℹ️  For a bind-mounted live edit loop, use: make up-live"

up-live: ## Start core local stack with live bind mounts for extraction/runtime/seocho
	@echo "🐳 Starting Seocho core local stack with live source mounts..."
	@$(DOCKER_COMPOSE_LIVE) up -d --build
	@echo "✅ Live-mount services started."

up-legacy-semantic: ## Start the legacy semantic-service profile too
	@echo "🐳 Starting Seocho core stack with legacy semantic-service..."
	@docker compose --profile legacy-semantic up -d
	@echo "✅ Legacy semantic-service started."

down: ## Stop all services
	@echo "🛑 Stopping services..."
	@docker compose down

restart: ## Restart all services
	@echo "🔄 Restarting services..."
	@docker compose restart

logs: ## View logs from all services
	@docker compose logs -f --tail=100

shell: ## Open shell in extraction-service container
	@docker compose exec extraction-service bash

##@ Development

test: ## Run tests
	@echo "🧪 Running tests..."
	@docker compose exec extraction-service python -m pytest tests/ -v

test-integration: ## Run integration-focused extraction tests
	@echo "🧪 Running integration tests..."
	@docker compose exec extraction-service python -m pytest tests/test_integration_runtime_flow.py -v

e2e-smoke: ## Run dockerized runtime smoke checks (ingest + semantic + debate)
	@echo "🧪 Running e2e smoke checks..."
	@bash scripts/integration/e2e_runtime_smoke.sh

demo-raw: ## Run beginner raw-data demo pipeline
	@bash scripts/demo/pipeline_raw_data.sh

demo-meta: ## Run beginner meta/artifact demo pipeline
	@bash scripts/demo/pipeline_meta_artifact.sh

demo-neo4j: ## Run beginner neo4j load/query demo pipeline
	@bash scripts/demo/pipeline_neo4j_load.sh

demo-graphrag-opik: ## Run beginner graphrag + opik demo pipeline
	@bash scripts/demo/pipeline_graphrag_opik.sh

demo-all: ## Run all beginner demo pipelines
	@bash scripts/demo/run_beginner_pipelines.sh

lint: ## Run linting
	@echo "🔍 Running linting..."
	@docker compose exec extraction-service python -m flake8 . --max-line-length=88
	@docker compose exec extraction-service python -m black . --check

format: ## Format code
	@echo "✨ Formatting code..."
	@docker compose exec extraction-service python -m black .
	@docker compose exec extraction-service python -m isort .

clean: ## Clean up containers and volumes
	@echo "🧹 Cleaning up..."
	@docker compose down -v --remove-orphans
	@docker system prune -f

##@ Opik Observability

opik-up: ## Start core + Opik services
	@echo "🔭 Starting Seocho with Opik observability..."
	@docker compose --profile opik up -d
	@echo "✅ Services started with Opik!"
	@echo "🔭 Access Opik UI: http://localhost:5173"

opik-down: ## Stop all services including Opik
	@echo "🛑 Stopping services (including Opik)..."
	@docker compose --profile opik down

opik-logs: ## View Opik service logs
	@docker compose --profile opik logs -f --tail=100 opik-backend opik-python-backend opik-frontend

##@ FinDER Tutorials

tutorials-up: ## Start the tutorial Jupyter container (fully embedded — no graph server)
	@echo "📓 Starting FinDER tutorial environment..."
	@$(DOCKER_COMPOSE_TUTORIALS) up -d --build
	@echo "✅ Tutorial environment started."
	@echo "📓 JupyterLab: http://localhost:$${TUTORIALS_JUPYTER_PORT:-8889}/lab/tree/examples"

tutorials-down: ## Stop the tutorial stack
	@echo "🛑 Stopping FinDER tutorial environment..."
	@$(DOCKER_COMPOSE_TUTORIALS) down

tutorials-logs: ## Tail logs from the tutorial stack
	@$(DOCKER_COMPOSE_TUTORIALS) logs -f --tail=100

tutorials-shell: ## Open a shell inside the tutorial Jupyter container
	@$(DOCKER_COMPOSE_TUTORIALS) exec tutorials-jupyter bash

tutorials-build: ## Build the tutorial Docker image without starting the container
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-build} $(DOCKER_COMPOSE_TUTORIALS) build

tutorials-smoke: ## Fast smoke test — import every module each tutorial uses
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-smoke} $(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter \
		python -c "import sys; \
from seocho.benchmarking import load_finder_cases; \
from seocho.store.vector import create_vector_store; \
from seocho.store.llm import create_llm_backend; \
from examples.lance_graph_store import LanceGraphStore; \
from seocho import Ontology, Seocho; \
from seocho.index.pipeline import IndexingPipeline; \
from seocho.store.graph import LadybugGraphStore; \
from examples.datasets.fibo_modules.compose import compose_modules; \
from examples.fibo_module_metrics import entity_coverage, graph_volume; \
from examples.owlready_graph_store import OwlreadyGraphStore; \
from examples.lpg_metrics import compute_lpg_structure_metrics; \
from examples.rdf_lpg_comparison import golden_standard_overlap, task_track_aggregate; \
from seocho.tracing import enable_tracing, log_span, flush_tracing; \
from seocho.agent_config import AgentConfig, RoutingPolicy; \
from extraction.agent_base.base import BaseAgent, register_tool; \
print('✅ All four tutorial import chains resolve cleanly')"

tutorials-pytest: ## Run the seocho test suite inside the tutorials container
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-test} $(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter \
		python -m pytest seocho/tests/test_ontology_ttl.py -v

tutorials-test: ## Headless nbconvert run of every tutorial notebook (reads OPENAI_API_KEY from .env)
	@$(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter bash -lc '\
		if [ -z "$$OPENAI_API_KEY" ] || [ "$$OPENAI_API_KEY" = "placeholder" ] || [ "$$OPENAI_API_KEY" = "test" ]; then \
			echo "❌ OPENAI_API_KEY not set inside the container. Put a real key in .env."; \
			exit 1; \
		fi; \
		set -e; mkdir -p /workspace/.seocho/test_runs; cd /workspace; \
		for nb in examples/finder_lance_vector_vs_graph_rag.ipynb \
		           examples/finder_fibo_module_impact.ipynb \
		           examples/private_opik_workflow.ipynb; do \
			echo "▶️  Executing $$nb"; \
			jupyter nbconvert --to notebook --execute "$$nb" \
				--ExecutePreprocessor.timeout=900 \
				--output "/workspace/.seocho/test_runs/$$(basename $$nb)"; \
		done; \
		echo "ℹ️   finder_rdf_vs_lpg_evaluation.ipynb skipped — needs JVM for OWL reasoner cell"; \
		echo "✅ Tutorial notebooks executed; outputs under .seocho/test_runs/"'

##@ Production

prod-up: ## Start services in production mode
	@echo "🚀 Starting production services..."
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

dev-up: ## Start services in development mode
	@$(DOCKER_COMPOSE_LIVE) up -d --build
