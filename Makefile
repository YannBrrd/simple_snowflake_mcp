.PHONY: help install dev test lint format clean docker-build docker-up docker-down

help:
	@echo "Commandes disponibles avec uv :"
	@echo "  install        - Installer les dépendances de production"
	@echo "  dev            - Installer toutes les dépendances (dev inclus)"
	@echo "  test           - Lancer les tests"
	@echo "  lint           - Vérifier le code avec ruff"
	@echo "  format         - Formater le code avec ruff"
	@echo "  run            - Lancer le serveur MCP"
	@echo "  clean          - Nettoyer les fichiers générés"
	@echo "  docker-build   - Build l'image Docker"
	@echo "  docker-up      - Démarrer avec Docker Compose"
	@echo "  docker-down    - Arrêter Docker Compose"

# Installation avec uv
install:
	uv sync --frozen

dev:
	uv sync --all-extras

# Tests
test:
	uv run pytest tests/ -v --cov=src

# Linting et formatting avec ruff
lint:
	uv run ruff check src/ tests/
	uv run mypy src/

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# Lancer le serveur
run:
	uv run simple-snowflake-mcp

# Build
build:
	uv build

# Clean
clean:
	rm -rf dist/ .pytest_cache/ .coverage .mypy_cache/ .ruff_cache/
	find . -type d -name "__pycache__" -exec rm -rf {} +

# Docker
docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

# Quick restart
restart: docker-down docker-up
