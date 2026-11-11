# Constitución de Carvajal Corpus

> Proyección de la constitución del proyecto para el flujo de Spec-Driven Development.
> **La fuente única de verdad es [`CONSTITUTION.md`](../../CONSTITUTION.md) en la raíz del
> repositorio**: este archivo resume sus principios para que los comandos de Spec Kit
> (`/speckit.*`) los verifiquen. Si hay discrepancia, manda la raíz.

## Principios fundamentales

### I. La constitución manda

Todo cambio arquitectónico, tecnológico o de proceso se registra en `CONSTITUTION.md` en el
mismo commit. Las especificaciones y los planes de `specs/` no pueden contradecirla; si lo
hacen, se corrige la spec o se enmienda la constitución, nunca ambas a medias.

### II. El corpus es el contrato

El entregable versionado es `data/gold/` (24 documentos con *front matter*: `url`, `title`,
`section`, `domain`, `source_type`, `captured_at`, `hash`, `chars`) más `index.jsonl`.
Bronze (`data/raw/`) y silver (`data/processed/`) se regeneran y no se versionan.

- Nada del RAG toca la web: se deriva siempre de `data/gold/`.
- Cambiar el corpus invalida el índice vectorial por huella (FAISS hoy, pgvector desde la ola 2
  del plan de migración); reconstruirlo es parte del flujo.
- La capa extendida (`data/external/`) es opcional y solo participa en recuperación.

### III. Sin fuentes no hay respuesta (NO NEGOCIABLE)

Toda afirmación se sustenta en el corpus y se cita. El asistente debe distinguir fuente
oficial (`carvajal.com`) de tercero (prensa) y declarar lo que no sabe.

- Los prompts prohíben explícitamente inventar datos y usar conocimiento externo.
- La temperatura es `0`.
- Cada variante de prompt se mide antes de adoptarla (`docs/prompts.md`,
  `scripts/experimentos_prompts.py`).

### IV. Calidad automatizada y reproducible

`uv` es el único gestor de dependencias (prohibido `pip` y `requirements.txt`). Ruff, `ty`,
pytest y pre-commit se ejecutan antes de cada commit; el CI corre lo mismo que en local.
Nada se integra con `make check` en rojo.

### V. Captura responsable

Se respeta `robots.txt` (incluido `Crawl-delay`), se identifica el cliente con un
`User-Agent` propio, se espera un segundo entre peticiones al mismo host y los errores de
red se registran como descartes, no como excepciones.

### VI. Cambios por Pull Request

Los cambios grandes van en rama (`feat/…`, `fix/…`, `docs/…`, `chore/…`), con Conventional
Commits, y entran por Pull Request con descripción y checks verdes. Las ramas se integran a
`dev`; `main` solo recibe releases al cierre de las olas 1, 3, 5 y 7 del plan de migración.
Los agentes abren el PR y se detienen: no mergean ni aprueban, ni siquiera con `--admin`.

## Estado técnico

- Migración de Streamlit a una app web con backend y frontend separados, decidida en
  [`docs/decisions/0001-stack-app-chat.md`](../../docs/decisions/0001-stack-app-chat.md)
  (FastAPI + Jinja2 + HTMX, Postgres 16 con pgvector, Docker en VPS) y planificada por olas en
  [`docs/plans/0001-migracion-app.md`](../../docs/plans/0001-migracion-app.md).
- Streamlit sigue siendo la interfaz desplegada hasta la ola 7, cuando se retira.

## Restricciones técnicas

- Python 3.13+, monorepo `uv` con el paquete en `packages/core/src/carvajal` (src-layout).
- LLM configurable: DeepSeek (por defecto), Claude u Ollama.
- Embeddings locales con Ollama (`nomic-embed-text`, 768 dimensiones) o por API según el
  entorno de despliegue.
- Persistencia en Postgres 16 con la extensión `pgvector`; migraciones con Alembic.
- Sin secretos en el repositorio: `.env` está en `.gitignore`.

## Flujo de desarrollo

1. `/speckit.specify` → `specs/NNN-*/spec.md` (qué y para quién, sin tecnología).
2. `/speckit.plan` → `plan.md` (cómo, con la constitución como filtro).
3. `/speckit.tasks` → `tasks.md` (tareas accionables por historia de usuario).
4. Implementación en rama, `make check`, commit convencional y PR.

## Gobernanza

- Enmiendas: PR que modifique `CONSTITUTION.md` y, si aplica, esta proyección.
- Cumplimiento: el plan de cada feature incluye una sección *Constitution Check*; toda
  violación debe justificarse o eliminarse antes de implementar.
- Versión: 1.1.0 - 2026-11-08, alineada con `CONSTITUTION.md` del repositorio.
