# Makefile for Seocho - Data Lineage & GraphRAG Framework

DOCKER_COMPOSE = docker compose

.PHONY: up down restart logs clean bootstrap ingest-glossary ingest-supply-chain shell test lint format help

##@ Development

help: ## Show this help message
	@echo "Seocho - Data Lineage & GraphRAG Framework"
	@echo ""
	@echo "Usage:"
	@echo "  make <target>"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Bootstrap the development environment
	@echo "🚀 Bootstrapping Seocho environment..."
	@chmod +x scripts/datahub-bootstrap.sh
	@docker compose build
	@echo "✅ Environment ready!"

up: ## Start all services
	@echo "🐳 Starting Seocho services..."
	@docker compose up -d
	@echo "✅ Services started!"
	@echo "📊 Access NeoDash: http://localhost:5005"
	@echo "🗄️  Access Neo4j Browser: http://localhost:7474"

down: ## Stop all services
	@echo "🛑 Stopping services..."
	@docker compose down

restart: ## Restart all services
	@echo "🔄 Restarting services..."
	@docker compose restart

logs: ## View logs from all services
	@docker compose logs -f --tail=100

shell: ## Open shell in engine container
	@docker compose exec engine bash

##@ Data Ingestion

ingest-glossary: ## Ingest glossary terms into DataHub
	@echo "📚 Ingesting glossary terms..."
	@docker compose exec engine python /app/src/seocho/ingestion/datahub_integration.py

ingest-supply-chain: ## Ingest supply chain sample data
	@echo "📦 Ingesting supply chain data..."
	@docker compose exec engine python /app/src/seocho/ingestion/ingest_data.py

ingest-custom: ## Ingest custom data (specify RECIPE=path/to/recipe.yml)
	@echo "📥 Ingesting custom data..."
	@docker compose exec engine datahub ingest -c $(RECIPE)

##@ Development

test: ## Run tests
	@echo "🧪 Running tests..."
	@docker compose exec engine python -m pytest tests/ -v

lint: ## Run linting
	@echo "🔍 Running linting..."
	@docker compose exec engine python -m flake8 src/ --max-line-length=88
	@docker compose exec engine python -m black src/ --check

format: ## Format code
	@echo "✨ Formatting code..."
	@docker compose exec engine python -m black src/
	@docker compose exec engine python -m isort src/

clean: ## Clean up containers and volumes
	@echo "🧹 Cleaning up..."
	@docker compose down -v --remove-orphans
	@docker system prune -f

##@ Production

prod-up: ## Start services in production mode
	@echo "🚀 Starting production services..."
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

dev-up: ## Start services in development mode
	@docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d