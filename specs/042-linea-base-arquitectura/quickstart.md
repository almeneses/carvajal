# Quickstart: repetir la línea base

Todos los comandos se ejecutan desde la raíz del repositorio, en el entorno local con
PostgreSQL y Qdrant levantados.

## Requisitos

- `uv`, Docker, Node 24.16.0.
- PostgreSQL (`carvajal-db-1`) accesible con el `.env` del repo, Qdrant en
  `http://127.0.0.1:6333`, Ollama en `http://127.0.0.1:11434`.

## 1. Preparar el entorno

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"   # el cache de HOME es read-only en el sandbox
uv sync --all-extras
```

## 2. Verificación completa

```bash
env -u OLLAMA_MODEL make check
# Esperado: Ruff y ty OK; pytest 1643 passed, 46 deselected; exit 0.
```

Si el shell no exporta `OLLAMA_MODEL`, `make check` también pasa; se usa `env -u` para que
el resultado no dependa de variables heredadas (ver [baseline §1](baseline.md#1-aviso-de-entorno-necesario-para-reproducir)).

```bash
node --test tests/js/test_chat_voice.cjs
# Esperado: 68 tests, 68 pass, 0 fail.
```

## 3. Conteos del corpus

```bash
docker exec carvajal-db-1 psql -U carvajal -d carvajal -tAc \
  "select count(*) from documents; select count(*) from chunks;"
curl -s http://127.0.0.1:6333/collections/carvajal_chunks | head -c 300
ls data/gold/*.md | wc -l
find data/external -name '*.md' | wc -l
```

Esperado en este entorno: 24 documentos, 125 fragmentos, 125 puntos en Qdrant, 24 Markdown
Gold y 479 Markdown extendidos.

## 4. `sync --verify` (solo lectura)

```bash
env -u OLLAMA_MODEL .venv/bin/carvajal sync --verify --layer all
# Esperado: exit 1 con el diagnóstico de diferencias del entorno local.
# No escribe: si el comando fallara por servicios caídos, anotarlo y no inventar tiempos.
```

## 5. Paridad `dev`/`main` (prueba de la corrección)

```bash
git worktree add .worktrees/042fix -b fix/042-sincroniza-arreglos-main origin/dev
cd .worktrees/042fix
export UV_CACHE_DIR="$(git rev-parse --show-toplevel)/.uv-cache"
uv sync --all-extras
git cherry-pick 5826114 9e5484f 9371268
env -u OLLAMA_MODEL .venv/bin/pytest -q tests/test_chat_tracing.py tests/test_api_chat.py
node --test "$(git rev-parse --show-toplevel)/tests/js/test_chat_voice.cjs"
```

Solo para verificación local: la rama real del PR no crea el worktree desde el quickstart.
