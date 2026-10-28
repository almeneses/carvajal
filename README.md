# Carvajal — Corpus Web

[![CI](https://github.com/markodavid/carvajal/actions/workflows/ci.yml/badge.svg)](https://github.com/markodavid/carvajal/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://docs.astral.sh/uv/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Pipeline de captura y curaduría del corpus web de **Carvajal** para RAG.

## Descripción

Convertir las páginas públicas del sitio web de Carvajal en un corpus Markdown
citable, reproducible y listo para un pipeline de RAG, respetando `robots.txt` y
sin depender de una API del sitio.

## Stack

| Categoría | Tecnología |
|-----------|------------|
| **Lenguaje** | Python 3.13+ |
| **Gestor de paquetes y entorno** | [`uv`](https://docs.astral.sh/uv/) |
| **Build backend** | `uv_build` (src-layout) |
| **Paquete Python** | `carvajal` |
| **HTTP** | `requests` (sesión única, `User-Agent` identificable, `timeout`) |
| **Parseo HTML** | `beautifulsoup4` + `lxml` |
| **RAG** | LangChain + FAISS + embeddings |
| **LLM** | Ollama (`llama3.2`) o Anthropic Claude (vía API) |
| **Interfaz de pruebas** | Streamlit |
| **Tipado estático** | [`ty`](https://github.com/astral-sh/ty) |
| **Testing** | [`pytest`](https://docs.pytest.org/) |
| **Linting / Formato** | [Ruff](https://docs.astral.sh/ruff/) |
| **Pre-commit** | [pre-commit](https://pre-commit.com) |
| **CI/CD** | [GitHub Actions](.github/workflows/ci.yml) |
| **Dependencias automáticas** | [Dependabot](.github/dependabot.yml) |

## Instalación

```bash
# Clonar el repositorio
git clone git@github.com:markodavid/carvajal.git
cd carvajal

# Sincronizar el entorno (crea .venv e instala dependencias)
uv sync
```

## Uso

```bash
# Capturar las semillas del corpus
uv run carvajal capture

# Ver el estado del corpus
uv run carvajal status

# Listar semillas
uv run carvajal list

# Preguntar al corpus (RAG)
uv run carvajal ask "¿En qué consiste el fondo de inversión de Carvajal?"

# Interfaz web de pruebas (Streamlit)
make app

# Evaluación: 25 preguntas de prueba -> docs/resultados.md
make eval
```

## Configuración del LLM

El asistente RAG usa un LLM configurable mediante variables de entorno:

| Variable | Descripción | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | Proveedor LLM: `ollama` (local) o `claude` (API). | `ollama` |
| `ANTHROPIC_API_KEY` | Clave API de Anthropic (solo si `LLM_PROVIDER=claude`). | `""` |
| `CLAUDE_MODEL` | Modelo Claude a utilizar (p. ej. `claude-sonnet-4-5`). | `claude-sonnet-4-5` |

Ejemplo con Claude vía API:

```bash
$env:LLM_PROVIDER = "claude"
$env:ANTHROPIC_API_KEY = "<tu-clave-de-anthropic>"
$env:CLAUDE_MODEL = "claude-sonnet-4-5"
uv run carvajal ask "¿Cuál es la misión de Carvajal?"
```

## Calidad

```bash
make install    # uv sync
make dev        # install + pre-commit install
make check      # lint + formato + tipos + pruebas
make lint       # Ruff (lint)
make format     # Ruff (formato)
make typecheck  # ty
make test       # pytest
make build      # uv build
make clean      # limpiar cachés y artefactos
```

## Estructura

```text
carvajal/
├── src/
│   └── carvajal/
├── tests/
├── data/
│   └── gold/       # entregable versionado (capa gold)
├── docs/
├── .github/
├── .opencode/
├── AGENTS.md
├── CONSTITUTION.md
├── README.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── Makefile
├── pyproject.toml
├── uv.lock
├── .python-version
└── .pre-commit-config.yaml
```

## Documentación

- [`docs/informe.md`](docs/informe.md) — informe del proyecto (problema, solución, datos, modelado, resultados).
- [`docs/presentacion.md`](docs/presentacion.md) — guion de la sustentación (15 min, sin PowerPoint).
- [`CONSTITUTION.md`](CONSTITUTION.md) — verdad central del proyecto.

## Contribución

Lee [`CONTRIBUTING.md`](CONTRIBUTING.md) y la [`CONSTITUTION.md`](CONSTITUTION.md).

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).
