# =============================================================================
# Carvajal — automatización de tareas
# =============================================================================

SHELL := /bin/bash

.DEFAULT_GOAL := help
.PHONY: help install dev pre-commit-install pre-commit lint format format-check typecheck test test-integration check build app api web image prod-up prod-down db db-down qdrant qdrant-down qdrant-stats index index-eval eval import clean

help: ## Muestra esta ayuda
	@echo "Uso: make [objetivo]"
	@echo "Objetivos disponibles:"
	@echo "  help               Muestra esta ayuda"
	@echo "  install            Sincroniza el entorno e instala dependencias"
	@echo "  dev                Prepara el entorno de desarrollo (sync + hooks)"
	@echo "  pre-commit-install Instala los hooks de pre-commit"
	@echo "  pre-commit         Ejecuta los hooks sobre todos los archivos"
	@echo "  lint               Ejecuta Ruff (lint)"
	@echo "  format             Formatea el código con Ruff"
	@echo "  format-check       Verifica el formato sin modificarlo"
	@echo "  typecheck          Verifica los tipos con ty (Astral)"
	@echo "  test               Ejecuta las pruebas (sin integración)"
	@echo "  test-integration   Ejecuta las pruebas de integración (requiere Docker)"
	@echo "  check              Verificación completa (lint + formato + tipos + pruebas)"
	@echo "  build              Construye el paquete (sdist + wheel)"
	@echo "  app                Lanza la interfaz web de prueba (Streamlit)"
	@echo "  api                Lanza la API (FastAPI + uvicorn) en :8000"
	@echo "  web                Lanza el frontend propio en :8001"
	@echo "  qdrant             Levanta Qdrant con volumen persistente"
	@echo "  qdrant-down        Detiene Qdrant (conserva el volumen)"
	@echo "  qdrant-stats       Muestra el estado y las métricas de la colección"
	@echo "  index              Indexa data/gold en Qdrant"
	@echo "  index-eval         Indexa y ejecuta la evaluación de retrieval"
	@echo "  image              Construye la imagen Docker de la app web"
	@echo "  prod-up            Levanta app, Postgres, Qdrant y Ollama en APP_PORT"
	@echo "  prod-down          Detiene los servicios (conserva los volúmenes)"
	@echo "  db                 Levanta Postgres 16 con pgvector (docker compose)"
	@echo "  db-down            Detiene la base de datos local"
	@echo "  eval               Ejecuta las preguntas de prueba (genera docs/resultados.md)"
	@echo "  import             Importa el corpus externo a la capa extendida"
	@echo "  clean              Limpia cachés y artefactos"

install: ## Sincroniza el entorno virtual e instala dependencias (incluidos los extras)
	uv sync --all-extras

dev: install pre-commit-install ## Prepara el entorno de desarrollo (sync + hooks)

pre-commit-install: ## Instala los hooks de pre-commit en el repositorio local
	uv run pre-commit install

pre-commit: ## Ejecuta todos los hooks de pre-commit sobre el repositorio
	uv run pre-commit run --all-files

lint: ## Ejecuta Ruff (lint)
	uv run ruff check .

format: ## Formatea el código con Ruff
	uv run ruff format .

format-check: ## Verifica el formato sin modificarlo
	uv run ruff format --check .

typecheck: ## Verifica los tipos con ty (Astral)
	uv run ty check

test: ## Ejecuta las pruebas (sin integración)
	uv run pytest -m "not integration"

test-integration: ## Ejecuta las pruebas de integración (requiere Docker)
	@if grep -rq "pytest.mark.integration" tests; then \
		uv run pytest -m integration; \
	else \
		echo "Sin pruebas de integración todavía."; \
	fi

check: lint format-check typecheck test ## Verificación completa (lint + formato + tipos + pruebas)

build: ## Construye el paquete (sdist + wheel)
	uv build

app: ## Lanza la interfaz web de prueba (Streamlit)
	uv run streamlit run app.py

api: ## Lanza la API en :8000
	uv run uvicorn apps.api.main:app --reload --port 8000

web: ## Lanza el frontend propio en :8001
	CARVAJAL_API_BASE=$${CARVAJAL_API_BASE:-http://localhost:8000} uv run uvicorn apps.web.main:app --reload --port 8001

qdrant: ## Levanta Qdrant con volumen persistente
	docker compose up -d --wait qdrant

qdrant-down: ## Detiene Qdrant (conserva el volumen)
	docker compose stop qdrant

qdrant-stats: ## Muestra el estado y las métricas de la colección en Qdrant
	uv run carvajal qdrant-stats

index: ## Indexa data/gold en Qdrant
	uv run carvajal index-qdrant

index-eval: ## Indexa y evalúa el retrieval (Recall@10 y nDCG@10)
	uv run carvajal index-qdrant
	uv run python scripts/eval_retrieval.py

image: ## Construye la imagen Docker de la app web
	docker build -t carvajal-api:local .

prod-up: ## Levanta app, Postgres, Qdrant y Ollama en APP_PORT (requiere SESSION_SECRET)
	docker compose -f compose.prod.yml up -d --build

prod-down: ## Detiene app, Postgres, Qdrant y Ollama (conserva los volúmenes)
	docker compose -f compose.prod.yml down

# Compose y Settings leen el mismo `.env` (y el entorno del shell): si cambias
# POSTGRES_PORT, ambos lo ven.
db: ## Levanta Postgres 16 con pgvector y espera al healthcheck
	docker compose up -d --wait --wait-timeout 120 db

db-down: ## Detiene la base de datos local (conserva el volumen)
	docker compose down

eval: ## Ejecuta las preguntas de prueba y genera docs/resultados.md
	uv run carvajal eval

import: ## Importa el corpus externo a la capa extendida
	uv run carvajal import

clean: ## Limpia cachés y artefactos
	uv run python -c "import shutil; from pathlib import Path; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.ruff_cache', '.mypy_cache', 'dist', 'build', 'htmlcov']]; [shutil.rmtree(p, ignore_errors=True) for p in Path('.').rglob('__pycache__')]; Path('.coverage').unlink(missing_ok=True)"
