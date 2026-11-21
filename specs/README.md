# Inventario de especificaciones — Carvajal Corpus

> **Propósito**: ser el índice único de `specs/`. Define qué capacidades tiene el
> proyecto, cuáles ya están documentadas, cuáles se documentarán **conforme a obra**
> (*as-built*) y cuáles son trabajo futuro. No sustituye a
> [`CONSTITUTION.md`](../CONSTITUTION.md): los principios, el alcance y el historial de
> decisiones siguen viviendo allá.

**Propuesto**: 2026-11-15 · **Estado**: pendiente de aprobación del owner · **Ciclo**: Spec Kit

## 1. Por qué existe este archivo

El proyecto adoptó Spec Kit el 2026-11-03, pero el trabajo grande (Módulo 1 y la
migración del Módulo 2) se ejecutó con PRs y decisiones en `CONSTITUTION.md` §9. El
resultado es que la verdad técnica está en el código, en los PRs y en la constitución,
y no en `specs/`. Este inventario cierra esa brecha sin inventar historia:

- Las specs de capacidades **ya entregadas** se escriben como *as-built*: documentan
  los requisitos y el comportamiento que existen hoy, con fecha honesta y evidencia.
- Las specs de capacidades **futuras** se escriben con el flujo normal, **antes** de
  implementar.

La diferencia entre ambas es explícita y está en el campo `Status` de cada spec.

## 2. Cómo se lee el inventario

| Columna | Significado |
|---|---|
| **#** | Número de carpeta en `specs/`. No se renumeran 001 ni 002. |
| **Capacidad** | Unidad que un stakeholder puede aprobar o rechazar. No es un PR ni un archivo. |
| **Prioridad** | **Alta**: hay que escribirla sí o sí. **Media**: útil y acotada. **Baja**: se puede plegar en la capacidad vecina. |
| **Entregada** | Fecha real de entrega según `CONSTITUTION.md` §9 y los PRs. |
| **Evidencia** | Dónde se demuestra: PR, prueba, documento o comando. Sin evidencia no hay spec. |

## 3. Convención *as-built* (obligatoria)

Una spec as-built solo es legítima si se etiqueta. Reglas, en orden de importancia:

1. **Fecha honesta.** `Created` es la fecha en que se escribe la spec; `Implemented`
   es el periodo real de entrega. Nunca se fecha la spec antes de la implementación.
2. **Evidencia por escenario.** Cada `Given/When/Then` apunta a la prueba que lo
   demuestra o al documento que lo registra. Si no hay evidencia, el escenario no se
   escribe o se marca «no verificado».
3. **Requisitos, no código.** La spec dice qué necesita el usuario y qué comportamiento
   es observable. Los archivos y módulos van al plan, no a la spec.
4. **Estado vigente con nota de cambio.** Si un requisito se revocó, la spec describe lo
   que hay hoy y añade una línea de historial. No se reescribe el pasado como si no
   hubiera existido.
5. **Sin plan ni tasks retroactivos.** Para las as-built basta `spec.md`. Un plan
   inventado después sí sería ficción. **Excepción: la 002**, que conserva `plan.md` y
   `tasks.md` as-built porque no documenta una capacidad cerrada: fija las olas entregadas
   y deja el trabajo pendiente (olas 4 y 7) como contrato de lo que falta. No reconstruye
   un plan vivido.

Cabecera normalizada para una as-built:

```markdown
**Feature Branch**: `003-captura-responsable`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-27 → 2026-10-29
**Evidence**: CONSTITUTION.md §9 (2026-10-29); PRs #3, #6; `tests/test_fetching.py`
```

## 4. Specs existentes

| # | Capacidad | Estado | Acción |
|---|---|---|---|
| [001](001-asistente-rag-carvajal/spec.md) | Asistente Q&A con RAG sobre el corpus | `Implemented (as-built)` | Documentada a posteriori; cabecera normalizada a la convención el 2026-11-15. |
| [002](002-api-backend-frontend/spec.md) | App web con backend y frontend | `Implemented (as-built)` | Reconvertida el 2026-11-15: supuestos corregidos y `plan.md` + `tasks.md` conforme a obra. Los pendientes (olas 4 y 7) están en su `tasks.md`. |
| [037](037-corpus-incremental/spec.md) | Corpus incremental: identidad estable, doble huella, sincronización única y prioridad de capas | `Implemented (as-built)` | Cerrada el 2026-11-20; T056 y su evidencia están en [`adopcion-vps.md`](037-corpus-incremental/adopcion-vps.md). |

## 5. As-built a escribir — Módulo 1 (prototipo)

Periodo real: **2026-10-27 → 2026-10-30**. Todas las specs de este bloque se escriben
con `Status: Implemented (as-built)`.

| # | Capacidad | Prioridad | Entregada | Evidencia |
|---|---|---|---|---|
| 003 | Captura responsable del corpus: semillas explícitas, `robots.txt` + `Crawl-delay`, rate limit, deduplicación SHA-256 y capas Bronze/Silver/Gold | Alta | 2026-10-29 | §9 (2026-10-29); PRs #3, #6; `tests/test_fetching.py`, `tests/test_pipeline.py`, `tests/test_gold.py` |
| 004 | Extractor DOM → Markdown sin *chrome*: conserva encabezados, listas, tablas y enlaces; descarta menús, formularios y pie | Baja (pliegue en 003) | 2026-10-28 | §9 (2026-10-21); commits 2026-10-28; `tests/test_extracting.py` |
| 005 | Tres tareas de LLM sobre el corpus: Resumen, FAQ y Q&A con citas y abstención | Alta | 2026-10-29 | §9 (2026-10-29); PR #8; `docs/prompts.md`; `tests/test_rag.py`, `tests/test_faq.py` |
| 006 | Interfaz de pruebas con pipeline visible (etapas, descartes, métricas) e identidad visual de marca | Media | 2026-10-29 | §9 (2026-10-29); PRs #4, #7; `docs/ui-pipeline.png`; `tests/test_ui.py` |
| 007 | Fuentes de terceros para los indicadores financieros que el sitio oficial no publica en HTML | Media | 2026-10-29 | §9 (2026-10-29); PR #9; `docs/alcance.md`; `data/gold/` con `source_type: tercero` |
| 008 | Capa extendida: importación de corpus externo con manifiesto, deduplicación y procedencia | Media | 2026-10-30 | §9 (2026-10-30); PRs #12, #14; `tests/test_importer.py` |
| 009 | Chunking de documentos e índice FAISS persistido con huella del corpus | Media | 2026-10-28 | §9 (2026-10-28); `tests/test_domain.py`, `tests/test_rag.py` |
| 010 | Evaluación del Q&A y declaración de alcance (30 preguntas, 26/30) | Media | 2026-10-30 | §9 (2026-10-30); PRs #9, #13; `docs/resultados.md`, `docs/alcance.md` |

## 6. As-built a escribir — Módulo 2 (app web)

Periodo real: **2026-11-08 → 2026-11-14** (olas 0–3, 5 y 6 del
[plan de migración](../docs/plans/0001-migracion-app.md); la ola 4 y la 7 siguen pendientes).
Mismo `Status` as-built.

| # | Capacidad | Prioridad | Entregada | Evidencia |
|---|---|---|---|---|
| 011 | Arquitectura por capas y contratos: dominio puro, puertos, configuración tipada (ola 0) | Alta | 2026-11-08 | §9 (2026-11-08); PRs #33, #35, #36, #37; `tests/test_ports.py`, `tests/test_domain.py`, `tests/test_settings.py` |
| 012 | Persistencia relacional: Postgres 16, Alembic, repositorios e importador Gold idempotente con poda (ola 1) | Alta | 2026-11-10 | §9 (2026-11-10); PRs #39, #50, #53, #54, #55, #56; `tests/test_storage_import.py`, `tests/test_import_corpus.py` |
| 013 | Índice vectorial pgvector: embedding de 768 dimensiones, HNSW coseno y `PgVectorStore` | Alta | 2026-11-11 | §9 (2026-11-11); PR #64; `tests/test_vector_store.py` |
| 014 | Puerto `LLMProvider` con adaptadores DeepSeek/Ollama y caso de uso `AskQuestion` con citas | Alta | 2026-11-11 | §9 (2026-11-11); PRs #65, #66; `tests/test_llm_adapters.py`, `tests/test_ask.py` |
| 015 | Shell FastAPI: `/live`, `/health`, chat con citas y pantallas de corpus y documento | Alta | 2026-11-13 | §9 (2026-11-11 y 2026-11-13); `tests/test_api.py`, `tests/test_api_corpus.py`, `tests/test_api_documents.py` |
| 016 | Identidad anónima del visitante, chat persistente y cuota diaria (migraciones 0003 y 0004) | Alta | 2026-11-14 | §9 (2026-11-13 y 2026-11-14); `tests/test_chat_identity.py`, `tests/test_chat_store.py`, `tests/test_quota.py` |
| 017 | Streaming SSE del chat con degradación y persistencia exacta una sola vez | Media | 2026-11-13 | §9 (2026-11-13); `tests/test_api_stream.py`, `tests/test_ask_stream.py` |
| 018 | Qdrant como índice vectorial del M2 con IDs UUID5 estables, upsert idempotente y poda | Alta | 2026-11-13 | §9 (2026-11-13); `tests/test_qdrant_store.py`; `make index`; `docs/evidence/m2/health.json` |
| 019 | Agente LangGraph: router `direct`/`tool`/`rag`, checkpointer, Store y herramienta `indicator_lookup` | Alta | 2026-11-13 | §9 (2026-11-13); `tests/test_agent_graph.py`, `tests/test_agent_router.py`; `data/indicators.json` |
| 020 | RAG híbrido ligero: reordenamiento léxico, fichas de recuperación y expansión de consulta | Media | 2026-11-13 | §9 (2026-11-13); `data/retrieval_cards.json`; `tests/test_ask.py` |
| 021 | Frontend propio en `apps/web`: shell SPA, pestañas y contrato JSON de lectura | Media | 2026-11-13 | §9 (2026-11-13); `tests/test_web_app.py`, `tests/test_api_read.py`; `docs/evidence/m2/frontend-desktop.png` |
| 022 | Seguridad web: CSRF por doble cookie, CSP sin `unsafe-inline`, cookies `HttpOnly` y HSTS sobre TLS | Alta | 2026-11-13 | §9 (2026-11-13); `tests/test_security.py` |
| 023 | Evaluación del retrieval separada de la respuesta (31 consultas, Recall@10 = 0.8000) | Media | 2026-11-13 | §9 (2026-11-13); `docs/eval/retrieval-report.md`; `data/eval/retrieval_questions.json` |
| 024 | Chat HTMX con hilos, pestaña Configuración, sistema visual institucional y escala accesible | Alta | 2026-11-14 | §9 (2026-11-14); PRs #114, #117, #118, #120; `docs/evidence/m2/`, `tests/test_api_m2.py` |
| 025 | Captura web y carga de documentos desde la interfaz, con guard SSRF y límites por IP | Media | 2026-11-14 | §9 (2026-11-14); PRs #115, #117; `tests/test_urlguard.py`, `tests/test_capture_jobs.py`, `tests/test_api_capture.py` |
| 026 | FAQ por lotes de 10 preguntas, sin persistencia y con descarga Markdown | Baja (pliegue en 005) | 2026-11-14 | §9 (2026-11-14); PR #116; `tests/test_api_faq.py` |
| 027 | Operación: imagen Docker multi-etapa, compose de producción, despliegue con rollback, logs JSON y Sentry opcional | Media | 2026-11-13 | §9 (2026-11-13); `docs/despliegue-ovh.md`; `compose.prod.yml`; `tests/test_observability.py` |
| 028 | Calidad y gobernanza del repo: CI con ty y pytest, revisión con IA, presupuesto de cambios y etiquetas de PR | Baja (va a `AGENTS.md`) | 2026-11-10 | PRs #22–#32, #59; `.github/workflows/`; `tests/test_ai_review.py`, `tests/test_pr_size.py`, `tests/test_speckit_scripts.py` |

## 7. Specs futuras (no son as-built)

Ninguna se escribe hasta que este inventario esté aprobado. Se numeran a partir de 029,
en `Status: Draft`, **antes** de implementar.

| # | Capacidad | Origen |
|---|---|---|
| 029 | Autenticación y cuotas por usuario (ola 4) | `docs/plans/0001-migracion-app.md`; §9 (2026-11-13) deja `user_id` preparado |
| 030 | Retiro de Streamlit y FAISS del repositorio (ola 7) | `CONSTITUTION.md` §3.1; `pyproject.toml` conserva ambos extras |
| 031 | Captura de PDFs e informes anuales | `CONSTITUTION.md` §10 |
| 032 | Transcripciones de vídeo | `CONSTITUTION.md` §10 |
| 033 | Entidades y especialidades detectadas en el front matter | `CONSTITUTION.md` §10 |
| 034 | Contrato de chunking y deduplicación de *boilerplate* | `CONSTITUTION.md` §10 |
| [035](035-ingesta-multiformato/spec.md) | Ingesta multiformato de archivos locales (PDF, XML, JSON, JSONL, CSV y TSV) en web y consola, con índice concurrente | `CONSTITUTION.md` §2; flujo normal de Spec Kit (`/speckit.specify`) |
| [036](036-asistente-empaques/spec.md) | Asistente comercial de empaques foodservice sobre un catálogo curado, con ruta `commercial` y comparación de enrutadores | `CONSTITUTION.md` §2 y §5; flujo normal de Spec Kit (`/speckit.specify`). Entregada el 2026-11-18; SC-006 transferido a la 041 |
| [038](038-monitoreo-langsmith/spec.md) | Monitoreo del agente con LangSmith: trazas por turno, opcional y sin PII, y experimentos de evaluación | `CONSTITUTION.md` §9 (2026-11-13 y 2026-11-17); flujo normal de Spec Kit (`/speckit.specify`) |
| [039](039-catalogo-colombia/spec.md) | Catálogo de empaques de Colombia con precios de referencia en COP, fechados y con fuente; absorbe la T009 de la 036. **Implementada** el 2026-11-18 (`evidencia.md`) | `CONSTITUTION.md` §9 (2026-11-17); Spec 036 (T009, absorbida); flujo normal de Spec Kit (`/speckit.specify`) |
| [040](040-chat-por-voz/spec.md) | Chat por voz: dictado de preguntas y respuestas habladas, sin guardar audio | `CONSTITUTION.md` §9 (2026-11-17) y el diseño «Asistente Carvajal» del chat; flujo normal de Spec Kit (`/speckit.specify`) |
| [044](044-chat-sse-interfaz/spec.md) | Reglas compartidas de validación, identidad y cuota del chat; lector SSE único entre chat y FAQ; HTMX solo en Configuración | `CONSTITUTION.md` §9 (2026-11-20); goal de simplificación sin regresiones; flujo normal de Spec Kit |

La 035 es la primera spec que se abre con el flujo normal **antes** de implementar. Se
escribe en `Status: Draft` con `spec.md`, `plan.md` y `tasks.md`; los tres pasan a
`Implemented (as-built)` cuando la capacidad se entregue y tenga evidencia. La ampliación
de alcance que exige —PDF y datos estructurados— se enmienda en `CONSTITUTION.md` §2, §9
y §10 en la misma PR, como manda el Principio I.

La 038, 039 y 040 se abren con `spec.md` y `checklists/requirements.md`. Sus `plan.md` y
`tasks.md` llegan en una PR de seguimiento inmediata (`/speckit.plan` y `/speckit.tasks`),
antes de implementar: tres planes y tres listas de tareas no caben en la misma revisión sin
agotar el presupuesto de cambios. La ampliación de alcance de §2, la decisión de §9 y la
proyección de la constitución para Spec Kit se enmiendan en esta misma PR.

## 8. Orden de escritura y estimación

Se escribe **de lo más antiguo a lo más nuevo**, para que la numeración cuente la
historia real del proyecto. Cada tanda es una PR independiente.

| Tanda | Specs | Contenido |
|---|---|---|
| 1 | 003–010 | Prototipo del Módulo 1 (8 specs) |
| 2 | 011–014 | Arquitectura y persistencia del M2 |
| 3 | 015–020 | Núcleo de la app web: shell, chat, Qdrant, agente, RAG híbrido |
| 4 | 021–028 | Interfaz, seguridad, evaluación y operación |
| 5 | 002 | Reconversión de la spec existente: supuestos, `plan.md` y `tasks.md` as-built (excepción de §3.5) |

**Estado (2026-11-15)**: las tandas 1–5 están escritas. Las capacidades 004, 026 y 028 quedaron
plegadas en su vecina. Las specs futuras (029–034) no se escriben hasta que se prioricen con el
flujo normal.

**Estado (2026-11-16)**: la 035 se escribió como `Draft` con `spec.md`, `plan.md` y
`tasks.md`, antes de implementar. La implementación (extracción multiformato, PDF por
página y consola de carpetas) quedó entregada el mismo día con `make check` en verde. La
036 sigue el mismo flujo y enmienda §2, §5 y §9 en su PR. Las 029–034 siguen sin
escribir.

- **Excepción de orden**: si el objetivo es sanear lo antes posible, la tanda 5 (002) se
  adelanta a la primera posición. Es la única spec que hoy induce a error, y arreglarla
  cuesta menos que cualquiera de las demás.
- **Estimación**: 25–35 minutos por spec; 26 specs ≈ 11–15 h en serie.
  Con 4 agentes en paralelo (archivos disjuntos) ≈ 3–4 h más revisión.
- **Cupo**: 23 specs de prioridad alta y media cubren de sobra el mínimo de 20. Las 3
  de prioridad baja se pliegan en su vecina si se quiere reducir volumen.
- **Presupuesto de cambios**: el CI advierte al superar 3.000 líneas por PR y no bloquea;
  por eso el trabajo va en tandas y no en un solo diff.

## 9. Cómo añadir o actualizar una entrada

1. Abrir la spec con el flujo normal (`/speckit.specify`) o, si es as-built, con la
   cabecera de §3.
2. Añadir la fila a la tabla del bloque que corresponda, con `#` siguiente libre.
3. Enlazar la evidencia: PR, prueba o documento. Una fila sin evidencia no entra.
4. Actualizar `CONSTITUTION.md` si la capacidad cambia el alcance, el stack o una regla.

Los comandos de Spec Kit viven en [`.opencode/commands/`](../.opencode/commands/) y los
scripts en [`.specify/scripts/`](../.specify/scripts/). La proyección de la constitución
para esos comandos es [`.specify/memory/constitution.md`](../.specify/memory/constitution.md).

## 10. Lo que este archivo no es

- No es un historial de intenciones: no afirma que estas specs guiaron el trabajo.
- No reemplaza `CONSTITUTION.md` §9, que es el registro real de decisiones.
- No promete una spec por cada ola planificada. Una ola puede tener varias capacidades
  y una capacidad puede cruzar olas.
- No sustituye los ADR de [`docs/decisions/`](../docs/decisions/). Las specs dicen qué
  se necesita; los ADR dicen por qué se eligió así.
