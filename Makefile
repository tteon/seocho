# Makefile for Seocho - Data Lineage & GraphRAG Framework

DOCKER_COMPOSE = docker compose
DOCKER_COMPOSE_LIVE = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: up up-live up-legacy-semantic down restart logs clean bootstrap shell test test-integration e2e-smoke lint format help opik-up opik-down opik-logs demo-raw demo-meta demo-neo4j demo-graphrag-opik demo-all setup-env

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

##@ Production

prod-up: ## Start services in production mode
	@echo "🚀 Starting production services..."
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

dev-up: ## Start services in development mode
	@$(DOCKER_COMPOSE_LIVE) up -d --build
