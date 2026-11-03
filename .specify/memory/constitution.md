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
- Cambiar el corpus invalida el índice FAISS por huella; reconstruirlo es parte del flujo.
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
Commits, y entran a `main` mediante Pull Request con descripción, revisión y checks verdes.

## Restricciones técnicas

- Python 3.13+, src-layout, paquete `carvajal`.
- LLM configurable: DeepSeek (por defecto), Claude u Ollama.
- Embeddings locales con Ollama (`nomic-embed-text`) o por API según el entorno de despliegue.
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
- Versión: 1.0.0 — 2026-11-03, alineada con `CONSTITUTION.md` del repositorio.
