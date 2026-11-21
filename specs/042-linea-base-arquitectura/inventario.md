# Inventario de la arquitectura actual (fase 0)

**Base**: `origin/dev` @ `8927ee7` (2026-11-20). **Referencia de paridad**: `origin/main` @
`d975594`.
**Propósito**: mapa verificable de superficies públicas y estado compartido antes de
simplificar la arquitectura. Si una ruta, comando o evento no aparece aquí, el inventario
está incompleto.

> Este documento describe lo que **hay**, no lo que debería haber. Las decisiones de
> simplificación viven en el goal (specs 043–047) y en el
> [ADR 0002](../../docs/decisions/0002-arquitectura-objetivo-3-workers.md).

## 1. Cómo se verificó

- Rutas: decoradores `@router`/`@app` en `apps/api/views/*.py` y `apps/api/main.py`, y
  routers incluidos en `create_app`.
- CLI: `build_parser()` y despacho por `args.command` en
  `packages/core/src/carvajal/cli.py`.
- SSE: llamadas `encode(...)`/`ChatEvent(...)` y ramas `case` en
  `apps/api/static/chat-stream.js` y `apps/api/static/faq.js`.
- Modos: `Makefile`, `Dockerfile`, `compose.prod.yml`, `scripts/deploy.sh` y
  `.github/workflows/`.
- Fuentes de verdad: esquema Alembic, adaptadores de almacenamiento, configuración y
  almacenes en memoria.

## 2. Rutas HTTP

La app se monta una sola vez (`create_app`) y sirve HTML, JSON y SSE. No hay Swagger
público (`docs_url=None`, `openapi_url=None`).

### 2.1 Interfaz Jinja2/HTMX (HTML y formularios)

| Método | Ruta | Respuesta | Entrada | Efecto |
|---|---|---|---|---|
| GET | `/` | 303 a `/chat` | — | Ninguno |
| GET | `/chat` | HTML | `?thread=`, `?panel=hilos` | Lee historial; crea cookie de visitante si falta |
| POST | `/chat` | HTML (redirect 303) o página de error | `question`, `csrf_token`, `thread_id` | CSRF → validación → cuota → `ChatService.ask` → persiste turno |
| POST | `/chat/new` | 303 a `/chat?thread=…` | `csrf_token` | Crea conversación del dueño |
| POST | `/chat/feedback` | 204 / 400 / 403 / 503 | `request_id`, `useful`, `csrf_token` | Guarda marca útil/no útil; no consume cuota |
| POST | `/chat/stream` | SSE | `question`, `csrf_token`, `client_turn_id`, `thread_id` | CSRF → validación → cuota → stream; persiste una vez al cerrar |
| GET | `/faq` | HTML | — | Página de FAQ con token CSRF |
| GET | `/configuracion` | HTML | — | Pantalla de carga y captura |
| POST | `/configuracion` | HTML | `files`, `csrf_token` | Carga local Bronze→Silver→Gold→PostgreSQL→Qdrant |
| POST | `/configuracion/captura` | HTML (parcial) | `urls`, `scope`, `csrf_token` | Valida y crea un job de captura en segundo plano |
| GET | `/configuracion/captura/{job_id}` | HTML (parcial) | — | Progreso consultable del job |
| GET | `/configuracion/captura/{job_id}/eventos` | SSE | — | Progreso del job; reemite el final al reconectar |
| GET | `/corpus` | HTML | filtros y paginación | Explorador del corpus |
| GET | `/documents/{document_id}` | HTML | — | Documento completo y fragmento citado |
| GET | `/live` | JSON | — | Vitalidad del proceso; **no** toca la base |

### 2.2 Contrato JSON/SSE para clientes (`/api`)

| Método | Ruta | Respuesta | Notas |
|---|---|---|---|
| GET | `/api/csrf` | JSON `{token}` | Token de doble cookie |
| GET | `/api/health` | JSON `{status, documents, chunks, index}` | Mismo readiness que `/health`, 200/503 |
| GET | `/api/corpus` | JSON | Lista paginada con filtros |
| GET | `/api/documents/by-slug/{slug}` | JSON | Documento por slug |
| GET | `/api/documents/{document_id}` | JSON | Documento por id |
| GET | `/api/chat/history` | JSON | Historial del hilo del dueño |
| POST | `/api/chat/new` | JSON | Crea hilo |
| POST | `/api/chat/stream` | SSE | Mismo contrato que `/chat/stream` |

### 2.3 Readiness y vitalidad

| Método | Ruta | Cuerpo | 200 vs 503 |
|---|---|---|---|
| GET | `/live` | `{"status": "ok"}` | Siempre 200 si el proceso responde |
| GET | `/health` | `{status, documents, chunks, index, monitoring?}` | 503 si PostgreSQL falla o Qdrant no está `ok`/`not_configured` |
| GET | `/api/health` | `{status, documents, chunks, index}` | Igual que `/health`, sin `monitoring` |

## 3. Comandos CLI

`carvajal` (entrypoint `carvajal.cli:main`). La CLI completa requiere el extra
`[capture,legacy]`; la imagen web instala solo `capture`.

| Comando | Opciones principales | Efecto | Salida distinta de 0 |
|---|---|---|---|
| `capture` | `--seed`, `--scope`, `--list-seeds`, `--delay`, `--ignore-robots`, `--solo-gold` | Captura Bronze/Silver/Gold; por defecto cierra con `sync` | 1 si falla la captura o el cierre |
| `ask` | `question` | RAG sobre FAISS/Gold (solo CLI) | 1 ante error de proveedor/configuración |
| `eval` | `--questions`, `--output` | Preguntas de evaluación y `docs/resultados.md` | 1 si falla |
| `summarize` | `--output`, `--focus` | Resumen del corpus | 1 si falla |
| `faq` | `--output` | FAQ del corpus | 1 si falla |
| `import` | — | Importa corpus externo a `data/external/` | 1 si falla |
| `migrate-gold` | `--gold`, `--layer`, `--allow-empty`, poda | Importa Gold a PostgreSQL (camino histórico) | 1 si Gold inválido o BD caída |
| `index-qdrant` | `--gold`, `--layer`, `--recreate`, etc. | Indexa Gold en Qdrant con IDs estables | 1 si falla |
| `sync` | `--layer`, `--prune`, `--verify`, `--adopt` | Reconcilia Gold→PostgreSQL→Qdrant | 1 si hay diferencias/errores; 2 si opciones incompatibles |
| `eval-router` | pregunta(s), JSON | Evalúa el enrutador del agente | 1 si falla |
| `catalog-build` | `--corpus`, `--source`, país | Construye catálogo curado | 1 si falla |
| `catalog-prices` | `--country`, entradas | Normaliza precios del catálogo | 1 si falla |
| `status` | — | Semillas y estado del corpus | 0 |
| `list` | — | Lista semillas oficiales y de terceros | 0 |

## 4. Eventos SSE

Formato de cable único: `event: <nombre>\ndata: <json>\n\n` (`apps/api/sse.py`).
Encabezados: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

### 4.1 Chat (`POST /chat/stream` y `POST /api/chat/stream`)

| Evento | Carga | Consumidor |
|---|---|---|
| `open` | `{stream: "chat"}` | `chat-stream.js` |
| `trace` | `{thread_id, route, …}` | Actualiza hilo y traza visible |
| `meta` | `{sources: [...]}` | Panel de fuentes |
| `products` | `{html}` | Tarjetas comerciales saneadas |
| `token` | `{text}` | Texto incremental |
| `fallback` | Mensaje de degradación | Aviso al usuario |
| `done` | `{conversation_id, message_id, thread_id, route, elapsed_ms, request_id}` | Cierre; turno persistido |
| `error` | `{message, incomplete}` | Cierre de error |

Reglas: CSRF antes de cuota; 422/429/503 responden un único evento `error` con
`X-Carvajal-Error: 1`; el cierre siempre llega salvo cancelación del cliente. El chat no
implementa reanudación por `Last-Event-ID`; el historial se recarga con `GET /chat`.

### 4.2 FAQ (`POST /api/faq`)

| Evento | Carga | Consumidor |
|---|---|---|
| `open` | `{stream: "faq"}` | `faq.js` |
| `token` | `{text}` | Vista previa |
| `done` | `{entries, missing_topics, truncated, exhausted, warnings}` | Lote renderizado |
| `error` | `{message, incomplete}` | Aviso |

### 4.3 Captura (`GET /configuracion/captura/{job_id}/eventos`)

| Evento | Carga | Consumidor |
|---|---|---|
| `stage` | `{stage, url, detail}` | `captura.js` |
| `done` | `CaptureOutcome` serializado | Fin de captura |
| `error` | `{message}` | Fin con error |

Reconexión: el job vive en memoria del proceso; al reconectar se reemite el evento final
si ya terminó. Si el proceso muere, el job se pierde.

## 5. Modos de ejecución y procesos

| Modo | Comando | Procesos/workers | Estado local |
|---|---|---|---|
| Desarrollo | `make api` → `uvicorn apps.api.main:app --reload --port 8000` | 1 worker + recarga | Caches por proceso |
| Imagen | `CMD ["uvicorn", …]` en `Dockerfile` | **1 worker** (sin `--workers`) | Caches por proceso |
| Compose objetivo | `compose.prod.yml` servicio `app` (CMD de la imagen) | **1 worker hoy**; objetivo 3 (`UVICORN_WORKERS`) | Volumen `appdata`; PostgreSQL/Qdrant externos al proceso |
| CLI/batch | `carvajal …` | 1 proceso por invocación | Lock de archivo `.corpus.lock` compartido por filesystem |
| Captura web | hilo daemon dentro del worker que atiende el POST | 1 hilo por job; un job activo por registro en memoria | Estado en memoria del worker |
| Pruebas unitarias | `pytest` | 1 proceso | SQLite/fakes; sin red |
| Pruebas de integración | `pytest -m integration` | 1 proceso + testcontainers | PostgreSQL efímero |
| Pruebas de cliente | `node --test tests/js/test_chat_voice.cjs` | 1 proceso Node | Sin navegador ni red |
| Despliegue | `scripts/deploy.sh` (GHCR, migraciones, `/live`, `/health`) | El del Compose | Volúmenes persistentes |

**Consecuencia para el goal**: con más de un worker, los caches `@cache` y el registro
`CaptureJobs` dejan de ser únicos globales. El estado que deba compartirse tiene que vivir
en PostgreSQL (fase 047).

## 6. Fuentes de verdad

| Dato | Fuente de verdad | Réplicas/derivados | Nota |
|---|---|---|---|
| Reglas, alcance y decisiones | `CONSTITUTION.md` + `.specify/memory/constitution.md` | `AGENTS.md`, specs, ADRs | La constitución manda |
| Decisiones de arquitectura | `docs/decisions/*.md` | `CONSTITUTION.md` §9 | ADR 0001 histórico, ADR 0002 nuevo |
| Configuración | entorno + `.env` → `Settings` | `compose.prod.yml` inyecta variables | `SESSION_SECRET` obligatorio en Compose |
| Corpus núcleo | `data/gold/*.md` + `data/gold/index.jsonl` | PostgreSQL `documents`/`chunks`/`versions`; Qdrant payload | Gold es el contrato versionado |
| Capa extendida | `data/external/<fuente>/gold/` + `manifest.json` | PostgreSQL y Qdrant con `layer=extended` | Solo recuperación/cita |
| Catálogo | `data/catalog/empaques/*.csv` + `manifest.json` | Carga en memoria | Datos curados, precios fechados |
| Indicadores | `data/indicators.json` | Herramienta `indicator_lookup` | Extracto versionado |
| Evaluación | `data/eval/*.json` | `docs/resultados.md` | Preguntas etiquetadas |
| Conversaciones y mensajes | PostgreSQL (`conversations`, `messages`) | Checkpoint LangGraph (`turns`) | Historial visible debe mandar en PostgreSQL (fase 046) |
| Preferencias del visitante | PostgreSQL `PostgresStore` (LangGraph) | — | Sobrevive a hilo nuevo |
| Estado técnico del grafo | Checkpointer (PostgreSQL o SQLite) | — | Por hilo; no es autoridad del historial |
| Cuota diaria | PostgreSQL `usage_day` | Reserva atómica | Medianoche en `QUOTA_TIMEZONE` |
| Feedback | PostgreSQL (tabla de feedback) | LangSmith (si muestreado) | `request_id` como clave |
| Vectores | Qdrant colección `QDRANT_COLLECTION` | — | Único índice vectorial |
| Sesión/identidad | Cookie firmada `carvajal_visitante` + HMAC | `owner_key` derivado | No es autenticación |
| CSRF | Token de doble cookie + `security.py` | — | Comparación en bytes |
| Jobs de captura | **Memoria del worker** (hoy) | — | A mover a PostgreSQL (fase 047) |
| Límite por IP de captura | **Memoria del worker** (hoy) | — | A compartir (fase 047) |
| Observabilidad | stdout JSON + Sentry + LangSmith opcional | — | Sin DSN no sale nada |

## 7. Duplicaciones y candidatos por fase

| Fase | Candidato | Evidencia |
|---|---|---|
| 043 | `_safe_rollback` sin consumidores; `ChatService._embedder` sin uso; `GraphAgentRunner._settings` sin uso; declaraciones directas `langchain`/`python-dotenv` | grep y revisión de consumidores en la spec 043 |
| 044 | Validación/identidad/cuota repetida en `views/chat.py`, `views/stream.py` y `views/api.py`; lector SSE duplicado en `chat-stream.js` y `faq.js`; `htmx.min.js` en todas las páginas | ver spec 044 |
| 045 | `load_document`/`split_document` viven en `adapters/storage/import_corpus.py` y los usa también `indexing/qdrant.py`; `sync --verify` hace dos recorridos de Qdrant por capa (`document_hashes` + `document_ids`) | ver spec 045 |
| 046 | `record()` copia los turnos al checkpoint además de persistirlos en PostgreSQL | ver spec 046 |
| 047 | Estado de captura y límite por IP en memoria por worker; sin detección de jobs interrumpidos | ver spec 047 |

## 8. Brecha entre `origin/dev` y `origin/main`

| Commit de `main` | Contenido | ¿Está en `dev`? |
|---|---|---|
| `5826114` | `perf(038)`: traza sin unir la respuesta en cada token; texto parcial al cancelar | No |
| `9e5484f` | `fix(040)`: manos libres devuelve siempre la pregunta al borrador | No |
| `9371268` | `test(040)`: ajusta la prueba al contrato | No |

`dev` tiene además el commit documental `8927ee7` (#199) que `main` no tiene. La
sincronización se hace con cherry-pick de los tres commits, en una rama y PR
independientes (`fix/042-sincroniza-arreglos-main`), no con un merge.
