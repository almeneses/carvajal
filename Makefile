# =============================================================================
# Carvajal — automatización de tareas
# =============================================================================

SHELL := /bin/bash

# GPU NVIDIA opcional para Ollama en Docker. `auto` la activa solo si el host
# tiene `nvidia-smi` y Docker expone el runtime `nvidia`; sin eso usa CPU.
# Fuerza un modo con `GPU=1 make up` o `GPU=0 make up`.
GPU ?= auto
GPU_SUPPORT = $(shell nvidia-smi -L >/dev/null 2>&1 && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q 'nvidia' && echo 1 || echo 0)
ifeq ($(GPU),1)
GPU_OVERRIDE = -f compose.gpu.yml
else ifeq ($(GPU),0)
GPU_OVERRIDE =
else
GPU_OVERRIDE = $(if $(filter 1,$(GPU_SUPPORT)),-f compose.gpu.yml,)
endif
COMPOSE = docker compose -f compose.yml $(GPU_OVERRIDE)
PROD_COMPOSE = docker compose -f compose.prod.yml $(GPU_OVERRIDE)

.DEFAULT_GOAL := help
.PHONY: help gpu-check install dev pre-commit-install pre-commit lint format format-check typecheck test test-integration check build api image up down prod-up prod-down prod-bootstrap db db-down qdrant qdrant-down index index-eval sync sync-verify ingest eval import clean

help: ## Muestra esta ayuda
	@echo "Uso: make [objetivo]"
	@echo "Objetivos disponibles:"
	@echo "  help               Muestra esta ayuda"
	@echo "  install            Sincroniza el entorno e instala dependencias"
	@echo "  gpu-check          Detecta GPU NVIDIA y el override de Ollama"
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
	@echo "  api                Lanza la interfaz y la API (FastAPI + uvicorn) en :8000"
	@echo "  qdrant             Levanta Qdrant con volumen persistente"
	@echo "  qdrant-down        Detiene Qdrant (conserva el volumen)"
	@echo "  index              Indexa data/gold en Qdrant"
	@echo "  sync               Publica el Gold revisado en PostgreSQL y Qdrant"
	@echo "  sync-verify        Compara Gold con los almacenes sin escribir"
	@echo "  ingest             Carga una carpeta en Gold para revisión (ARGS=...)"
	@echo "  index-eval         Indexa y ejecuta la evaluación de retrieval"
	@echo "  image              Construye la imagen Docker de la app web"
	@echo "  up                 Stack completo en Docker (app, base, Qdrant, Ollama y corpus)"
	@echo "  down               Detiene el stack completo (conserva los volúmenes)"
	@echo "  prod-up            Levanta app, Postgres, Qdrant y Ollama en APP_PORT"
	@echo "  prod-down          Detiene los servicios (conserva los volúmenes)"
	@echo "  prod-bootstrap     Prepara un servidor limpio: modelos, migraciones y corpus"
	@echo "  db                 Levanta Postgres 16 con pgvector (docker compose)"
	@echo "  db-down            Detiene la base de datos local"
	@echo "  eval               Ejecuta las preguntas de prueba (genera docs/resultados.md)"
	@echo "  import             Importa el corpus externo a la capa extendida"
	@echo "  clean              Limpia cachés y artefactos"

gpu-check: ## Detecta GPU NVIDIA y muestra cómo se invocará Docker Compose
	@echo "GPU mode: $(GPU)"
	@echo "nvidia-smi + runtime nvidia: $(if $(filter 1,$(GPU_SUPPORT)),sí,no)"
	@echo "Override: $(if $(GPU_OVERRIDE),$(GPU_OVERRIDE),ninguno (CPU))"

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

test: ## Ejecuta las pruebas (sin integración, e2e ni smoke)
	uv run pytest -m "not integration and not e2e and not smoke"

eval-cotizador: ## Casos dorados del Cotizador con totales esperados
	uv run python scripts/evaluar_cotizador.py

smoke: ## Smoke tests del monitoreo (sin credenciales)
	uv run pytest -m smoke

e2e: ## End-to-end del monitoreo con servicios reales y LangSmith
	uv run pytest -m e2e

test-integration: ## Ejecuta las pruebas de integración (requiere Docker)
	@if grep -rq "pytest.mark.integration" tests; then \
		uv run pytest -m integration; \
	else \
		echo "Sin pruebas de integración todavía."; \
	fi

check: lint format-check typecheck test ## Verificación completa (lint + formato + tipos + pruebas)

build: ## Construye el paquete (sdist + wheel)
	uv build

api: ## Lanza la interfaz y la API (FastAPI + uvicorn) en :8000
	uv run uvicorn apps.api.main:app --reload --port 8000

qdrant: ## Levanta Qdrant con volumen persistente
	docker compose up -d --wait qdrant

qdrant-down: ## Detiene Qdrant (conserva el volumen)
	docker compose stop qdrant

index: ## Indexa el núcleo y las capas extendidas en Qdrant
	uv run carvajal index-qdrant --layer all

sync: ## Publica el Gold revisado en PostgreSQL y Qdrant (operador)
	uv run carvajal sync --layer core

sync-verify: ## Compara Gold con PostgreSQL y Qdrant sin escribir (operador)
	uv run carvajal sync --layer core --verify

ingest: ## Carga una carpeta en Gold para revisión: make ingest ARGS="/ruta --solo-extraer"
	uv run python -m apps.api.ingest_cli $(ARGS)

index-eval: ## Indexa todas las capas y evalúa el retrieval (Recall@10 y nDCG@10)
	uv run carvajal index-qdrant --layer all
	uv run python scripts/eval_retrieval.py

image: ## Construye la imagen Docker de la app web
	docker build -t carvajal-api:local .

up: ## Levanta el stack completo en Docker (app, base, Qdrant, Ollama y corpus)
	# LOCAL_UID/LOCAL_GID hacen que la app escriba en ./data con el usuario del
	# host, no como root. El perfil `full` añade app, Ollama y la preparación.
	@echo "==> Levantando el stack; la primera vez descarga ~2,4 GB de modelos"
	@echo "==> Ollama en $(if $(GPU_OVERRIDE),GPU,CPU)"
	LOCAL_UID=$$(id -u) LOCAL_GID=$$(id -g) \
		$(COMPOSE) --profile full up -d --build --wait --wait-timeout 1800
	@echo "==> Chat en http://localhost:8000"

down: ## Detiene el stack completo (conserva los volúmenes)
	$(COMPOSE) --profile full down

prod-up: ## Levanta app, Postgres, Qdrant y Ollama en APP_PORT (requiere SESSION_SECRET)
	@echo "==> Ollama en $(if $(GPU_OVERRIDE),GPU,CPU)"
	$(PROD_COMPOSE) up -d --build

prod-down: ## Detiene app, Postgres, Qdrant y Ollama (conserva los volúmenes)
	$(PROD_COMPOSE) down

prod-bootstrap: ## Prepara un servidor limpio: modelos, migraciones, Postgres y Qdrant
	# Descarga nomic-embed-text, copia ./data al volumen si está vacío, aplica
	# migraciones, importa Gold y reindexa. Idempotente; el runbook lo detalla.
	$(PROD_COMPOSE) --profile bootstrap run --rm bootstrap

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
