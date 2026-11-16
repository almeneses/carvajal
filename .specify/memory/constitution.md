# Constitución de Carvajal Corpus

> Proyección de la constitución del proyecto para el flujo de Spec-Driven Development.
> **La fuente única de verdad es [`CONSTITUTION.md`](../../CONSTITUTION.md) en la raíz del
> repositorio**: este archivo resume sus principios y su estado técnico para que los comandos
> de Spec Kit (`/speckit.*`) los verifiquen. Si hay discrepancia, manda la raíz.
>
> **Sincronizada**: 2026-11-16, con las decisiones de `CONSTITUTION.md` §9 hasta esa fecha.

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
- Cambiar el corpus invalida el índice y obliga a reconstruirlo: en el M2 se ejecuta
  `carvajal index-qdrant`; FAISS y pgvector quedan como legado del prototipo.
- La capa extendida (`data/external/`) es opcional y solo participa en recuperación y citación.

### III. Sin fuentes no hay respuesta (NO NEGOCIABLE)

Toda afirmación se sustenta en el corpus y se cita. El asistente debe distinguir fuente
oficial (`carvajal.com`) de tercero (prensa) y declarar lo que no sabe.

- Los prompts prohíben explícitamente inventar datos y usar conocimiento externo.
- La temperatura es `0`.
- Cada variante de prompt se mide antes de adoptarla (`docs/prompts.md`,
  `scripts/experimentos_prompts.py`).
- En la ruta documental (`rag`) sin contexto recuperado no se llama al LLM: la respuesta es
  una abstención declarada. Las rutas `direct` (saludos) y `tool` (indicadores estructurados)
  responden sin recuperación y no afirman hechos del corpus sin evidencia.

### IV. Calidad automatizada y reproducible

`uv` es el único gestor de dependencias (prohibido `pip` y `requirements.txt`). Ruff, `ty`,
pytest y pre-commit se ejecutan antes de cada commit; el CI corre lo mismo que en local
(pre-commit, `ty` y pytest) y nada se integra con `make check` en rojo.

### V. Captura responsable

Se respeta `robots.txt` (incluido `Crawl-delay`), se identifica el cliente con un
`User-Agent` propio, se espera un segundo entre peticiones al mismo host y los errores de
red se registran como descartes, no como excepciones.

- La captura desde la interfaz está acotada: máximo de 24 URLs por ejecución y límite por IP.
- Las URLs de usuario pasan por un guard SSRF: solo HTTP/HTTPS en los puertos 80 y 443, sin
  IPs privadas, con redirecciones revalidadas y DNS fijado a la IP validada.

### VI. Cambios por Pull Request

Los cambios grandes van en rama (`feat/…`, `fix/…`, `docs/…`, `chore/…`), con Conventional
Commits, y entran por Pull Request con descripción y checks verdes. El trabajo de producto se
integra a `dev`; la documentación y las decisiones pueden ir directo a `main`, que solo recibe
releases aprobados. Los agentes abren el PR y se detienen: no mergean ni aprueban, ni siquiera
con `--admin`.

## Estado técnico

- La migración del prototipo a la app web entregó las olas 0–3, 5 y 6 el 2026-11-14; la ola 4
  (autenticación y cuotas por usuario) y la 7 (retiro del prototipo) siguen pendientes. El
  Módulo 2 está aceptado, con evidencia en `docs/aceptacion-m2.md`.
- Dos frontends y un solo RAG: shell renderizado en servidor con HTMX (puerto 8000) y
  frontend propio con pestañas (puerto 8001) que consume un contrato JSON de lectura.
- Postgres 16 es la fuente de verdad del corpus, del chat y de los checkpoints; Qdrant es el
  índice vectorial del M2.
- Streamlit y FAISS siguen disponibles como extra `legacy` y se retiran en la ola 7.
- Pendientes vivos: autenticación y cuotas por usuario (ola 4), captura programada por cron y
  endurecimiento del despliegue (TLS, dominio, respaldos).
- La spec 035 define la carga local multiformato —PDF con texto por página y datos XML, JSON,
  JSONL, CSV y TSV— para la web y la consola; el OCR de PDF escaneados sigue fuera de alcance.
  El código se implementa después de aprobar el contrato.
- Las capacidades ya entregadas están documentadas como *as-built* en `specs/`; el índice y la
  convención viven en `specs/README.md`. Lo no entregado se especifica antes de implementar.

## Restricciones técnicas

- Python 3.13+, monorepo `uv` con el paquete en `packages/core/src/carvajal` (src-layout).
- API FastAPI + Jinja2 + HTMX; frontend propio sin framework de JavaScript.
- Persistencia Postgres 16 con SQLAlchemy Core y migraciones Alembic (0001–0005).
- Vectores en Qdrant: 768 dimensiones, métrica coseno e IDs UUID5 estables.
- Agente LangGraph con `PostgresSaver` y `PostgresStore`; router `direct`/`tool`/`rag`.
- LLM configurable: DeepSeek (por defecto) u Ollama; embeddings locales con
  `nomic-embed-text` (768 dimensiones).
- Streaming por SSE; CSRF por doble cookie, CSP sin `unsafe-inline` y cookies `HttpOnly`.
- Sin secretos en el repositorio: `.env` está en `.gitignore`.

## Flujo de desarrollo

1. `/speckit.specify` → `specs/NNN-*/spec.md` (qué y para quién, sin tecnología).
2. `/speckit.clarify` → resuelve las ambigüedades antes de planear.
3. `/speckit.plan` → `plan.md` (cómo, con esta constitución como filtro).
4. `/speckit.tasks` → `tasks.md` (tareas accionables por historia de usuario).
5. `/speckit.analyze` → coherencia entre spec, plan y tasks antes de implementar.
6. Implementación en rama, `make check`, commit convencional y PR.

- Una capacidad ya entregada sin spec se documenta como *as-built*: fecha honesta, requisito
  observable y evidencia por escenario, sin `plan` ni `tasks` retroactivos.
- Toda spec nueva se numera en `specs/` y se registra en el índice `specs/README.md`.

## Gobernanza

- Enmiendas: PR que modifique `CONSTITUTION.md` y, si aplica, esta proyección **en el mismo
  commit**.
- Cumplimiento: el plan de cada feature incluye una sección *Constitution Check*; toda
  violación debe justificarse o eliminarse antes de implementar.
- Versión: 2.1.0 - 2026-11-16, sincronizada con `CONSTITUTION.md` del repositorio.
