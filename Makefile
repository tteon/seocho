# Makefile for Seocho - Data Lineage & GraphRAG Framework

DOCKER_COMPOSE = docker compose
DOCKER_COMPOSE_LIVE = docker compose -f docker-compose.yml -f docker-compose.dev.yml
DOCKER_COMPOSE_TUTORIALS = docker compose -f docker-compose.tutorials.yml

# Shared stack project name (fixed so per-instance app tiers can target its
# neo4j for ephemeral-database admin — see src/seocho/local.py).
SHARED_PROJECT = seocho
SEOCHO_CLI = python3 -m seocho.cli

.PHONY: up up-live down restart logs clean bootstrap shell test test-integration e2e-smoke lint format help opik-up opik-down opik-logs demo-raw demo-meta demo-neo4j demo-graphrag-opik demo-all setup-env tutorials-up tutorials-down tutorials-logs tutorials-shell tutorials-build tutorials-smoke tutorials-test tutorials-pytest tutorials-gds

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

up: ## Start core local stack; or an isolated app tier with INSTANCE=<id>
ifeq ($(strip $(INSTANCE)),)
	@echo "🐳 Starting Seocho core local stack from an image-backed source snapshot..."
	@COMPOSE_PROJECT_NAME=$(SHARED_PROJECT) docker compose up -d --build
	@echo "✅ Services started!"
	@echo "🖥️  Platform UI: http://localhost:$${CHAT_INTERFACE_PORT:-8501}"
	@echo "🧠 Backend API Docs: http://localhost:$${EXTRACTION_API_PORT:-8001}/docs"
	@echo "🗄️  DozerDB Browser: http://localhost:$${NEO4J_HTTP_PORT:-7474}"
	@echo "ℹ️  For a bind-mounted live edit loop, use: make up-live"
	@echo "ℹ️  For an isolated per-worktree runtime, use: make up INSTANCE=<id>"
else
	@echo "🐳 Booting isolated instance '$(INSTANCE)' (offset ports + ephemeral DB) against the shared neo4j..."
	@echo "ℹ️  Requires the shared stack to be running first: make up"
	@$(SEOCHO_CLI) serve --instance $(INSTANCE) --build
endif

up-live: ## Start core local stack with live bind mounts for extraction/runtime/src/seocho
	@echo "🐳 Starting Seocho core local stack with live source mounts..."
	@$(DOCKER_COMPOSE_LIVE) up -d --build
	@echo "✅ Live-mount services started."

down: ## Stop all services; or tear down one isolated instance with INSTANCE=<id>
ifeq ($(strip $(INSTANCE)),)
	@echo "🛑 Stopping services..."
	@COMPOSE_PROJECT_NAME=$(SHARED_PROJECT) docker compose down
else
	@echo "🛑 Tearing down instance '$(INSTANCE)' and dropping only its ephemeral DB..."
	@$(SEOCHO_CLI) stop --instance $(INSTANCE)
endif

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

bench-finder-synergy: ## FinDER synergy headline: signal-routed cost vs all-frontier (add LIVE=N for MARA support parity)
	@echo "📊 FinDER synergy benchmark (ontology-governed answering + signal-routed model)..."
	@python3 scripts/benchmarks/finder_synergy.py $(if $(LIVE),--live $(LIVE),) $(if $(DATASET),--dataset $(DATASET),)

bench-finder-cache: ## Synergy #1: persistent-cache hit-rate + latency win (needs DozerDB + MARA; PASSWORD=, LIMIT=)
	@echo "📊 FinDER cache synergy (persistent ResponseCache cross-session reuse)..."
	@python3 scripts/benchmarks/finder_cache_synergy.py $(if $(PASSWORD),--neo4j-password $(PASSWORD),) $(if $(LIMIT),--limit $(LIMIT),)

bench-finder-parity: ## Synergy #2 live: routed model tiers vs all-frontier on the wired path (needs DozerDB + MARA)
	@echo "📊 FinDER routing parity (SEOCHO_MODEL_ROUTING wired path, routed vs all-frontier)..."
	@python3 scripts/benchmarks/finder_routing_parity.py

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

tutorials-up: ## Start the tutorial Jupyter + Neo4j browser stack
	@echo "📓 Starting FinDER tutorial environment..."
	@$(DOCKER_COMPOSE_TUTORIALS) up -d --build
	@echo "✅ Tutorial environment started."
	@echo "📓 JupyterLab:    http://localhost:$${TUTORIALS_JUPYTER_PORT:-8888}/lab/tree/examples/finder"
	@echo "🌐 Neo4j Browser: http://localhost:$${TUTORIALS_NEO4J_HTTP_PORT:-7474}  (neo4j / $${TUTORIALS_NEO4J_PASSWORD:-tutorialspw})"

tutorials-down: ## Stop the tutorial stack
	@echo "🛑 Stopping FinDER tutorial environment..."
	@$(DOCKER_COMPOSE_TUTORIALS) down

tutorials-logs: ## Tail logs from the tutorial stack
	@$(DOCKER_COMPOSE_TUTORIALS) logs -f --tail=100

tutorials-shell: ## Open a shell inside the tutorial Jupyter container
	@$(DOCKER_COMPOSE_TUTORIALS) exec tutorials-jupyter bash

tutorials-gds: ## Install OpenGDS (DozerDB-compatible Graph Data Science) into the tutorial Neo4j
	@bash scripts/setup/install-opengds.sh
	@$(DOCKER_COMPOSE_TUTORIALS) restart tutorials-neo4j
	@echo "✅ OpenGDS installed and Neo4j restarted."
	@echo "ℹ️   Verify in cypher-shell: RETURN gds.version() AS version"

tutorials-build: ## Build the tutorial Docker image without starting the container
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-build} $(DOCKER_COMPOSE_TUTORIALS) build

tutorials-smoke: ## Fast smoke test — import every module each tutorial uses
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-smoke} $(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter \
		python -c "import sys; \
from seocho.benchmarking import load_finder_cases; \
from seocho.store.vector import create_vector_store; \
from seocho.store.llm import create_llm_backend; \
from seocho import Ontology, Seocho; \
from seocho.index.pipeline import IndexingPipeline; \
from seocho.store.graph import Neo4jGraphStore; \
from seocho.query.strategy import ExtractionStrategy; \
from examples.finder.lib.lance_graph_store import LanceGraphStore; \
from examples.finder.lib.graph_viz import draw_lpg, fetch_lpg_subgraph; \
from examples.finder.lib.ontology_io import ontology_plus, ontology_minus; \
from seocho.tracing import enable_tracing, log_span, flush_tracing; \
from seocho.agent_config import AgentConfig, RoutingPolicy; \
from extraction.agent_base.base import BaseAgent, register_tool; \
import networkx as nx; \
print('✅ All four tutorial import chains resolve cleanly')"

tutorials-pytest: ## Run the seocho test suite inside the tutorials container
	@OPENAI_API_KEY=$${OPENAI_API_KEY:-test} $(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter \
		python -m pytest tests/seocho/test_ontology_ttl.py -v

tutorials-test: ## Headless nbconvert run of every tutorial notebook (reads OPENAI_API_KEY from .env)
	@$(DOCKER_COMPOSE_TUTORIALS) run --rm --no-deps tutorials-jupyter bash -lc '\
		if [ -z "$$OPENAI_API_KEY" ] || [ "$$OPENAI_API_KEY" = "placeholder" ] || [ "$$OPENAI_API_KEY" = "test" ]; then \
			echo "❌ OPENAI_API_KEY not set inside the container. Put a real key in .env."; \
			exit 1; \
		fi; \
		set -e; mkdir -p /workspace/.seocho/test_runs; cd /workspace; \
		for nb in examples/finder/01_vector_vs_graph_rag.ipynb \
		           examples/finder/02_fibo_module_impact.ipynb \
		           examples/finder/03_network_analytics.ipynb \
		           examples/finder/04_private_opik.ipynb; do \
			echo "▶️  Executing $$nb"; \
			jupyter nbconvert --to notebook --execute "$$nb" \
				--ExecutePreprocessor.timeout=900 \
				--output "/workspace/.seocho/test_runs/$$(basename $$nb)"; \
		done; \
		echo "ℹ️   T3 needs T1 to run first to populate the workspace it reads."; \
		echo "✅ Tutorial notebooks executed; outputs under .seocho/test_runs/"'

dev-up: up-live ## Alias for up-live
