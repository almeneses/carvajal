# Tasks: Aplicación web del asistente *(as-built)*

**Spec**: [`spec.md`](./spec.md) · **Plan**: [`plan.md`](./plan.md) ·
**Estado**: olas 0–6 entregadas el 2026-11-14; los pendientes van marcados `[ ]`

## Formato: `[ID] [P?] [USn] Descripción`

- **[P]**: podía ejecutarse en paralelo (archivos distintos)
- **[USn]**: historia de usuario de la spec
- **[X]**: tarea entregada · **[ ]**: pendiente

## Phase 0: Contratos y arquitectura (ola 0)

- [X] T001 Mover el paquete a `packages/core` con src-layout (PR #33)
- [X] T002 Extraer el dominio puro: entidades, reglas, front matter y chunking (PR #35)
- [X] T003 Configuración tipada con pydantic-settings y `.env` (PR #36)
- [X] T004 Definir los puertos de persistencia, RAG, captura y LLM (PR #37)
- [X] T005 Cerrar la ola 0 y documentar el estado en el plan de migración (PR #38)

## Phase 1: Persistencia (ola 1)

- [X] T006 [P] Postgres 16 con pgvector en compose de desarrollo (PR #39)
- [X] T007 [P] Esquema relacional y migraciones Alembic 0001–0002 (PR #50)
- [X] T008 [US4] Versiones de documento en el dominio y en el puerto (PR #53)
- [X] T009 [US4] Repositorios SQL de documentos, fragmentos y versiones (PR #54)
- [X] T010 [US4] Carga de Gold: front matter, chunking y parser (PR #55)
- [X] T011 [US4] Comando `carvajal migrate-gold` con poda autoritativa (PR #56)
- [X] T012 [US4] Fijar el contrato de límites del listado (PR #58)
- [X] T013 Cerrar la ola 1 sin presupuestar releases (PR #60)

## Phase 2: RAG y shell (ola 2)

- [X] T014 [P] [US1] `PgVectorStore` con embedding de 768 dimensiones e índice HNSW (PR #64)
- [X] T015 [P] [US1] Puerto `LLMProvider` con adaptadores DeepSeek y Ollama (PR #65)
- [X] T016 [P] [US1] Caso de uso `AskQuestion` con contexto numerado y citas (PR #66)
- [X] T017 [US1] Shell FastAPI con handlers diferidos, `/health` y `/ask`
- [X] T018 [US1] Pantalla de portada con HTMX y fuentes citadas

## Phase 3: Pantallas, identidad y chat (ola 3)

- [X] T019 [US2] Router de corpus y documento con filtros, paginación y Markdown saneado
- [X] T020 [US2] Sanear HTML con `nh3` y validar la URL de origen contra `javascript:`
- [X] T021 [US1] Identidad de visitante: hash en el navegador, cookie firmada y `owner_key` por HMAC
- [X] T022 [US1] Chat persistente: tablas `conversations` y `messages` (migración 0003)
- [X] T023 [US1] Cuota diaria por visitante con reserva atómica y 429 con `Retry-After` (0004)
- [X] T024 [US1] Stream SSE con `open/meta/token/done/fallback/error` y persistencia exacta
- [X] T025 [US1] Estado del mensaje e idempotencia por `client_turn_id` (0005)
- [X] T026 [US1] Logs JSON con `request_id`, consumo por visitante y Sentry opcional
- [X] T027 [P] [US1] Qdrant como índice del M2 con UUID5 estable, upsert y poda
- [X] T028 [P] [US1] Agente LangGraph con router `direct`/`tool`/`rag`, checkpointer y Store
- [X] T029 [P] [US1] Herramienta `indicator_lookup` con `args_schema` Pydantic
- [X] T030 [P] [US1] RAG híbrido: reordenamiento léxico, fichas de recuperación y expansión
- [X] T031 [P] [US2] SPA `apps/web` con pestañas y contrato JSON de lectura
- [X] T032 [P] [US1] CSRF por doble cookie, CSP sin `unsafe-inline`, cookies `HttpOnly`
- [X] T033 [P] [US1] Evaluación del retrieval con 31 consultas y reporte reproducible

## Phase 4: Interfaz institucional y captura (ola 3b)

- [X] T034 [US1] Chat HTMX con compositor inferior, hilos navegables y pestaña Configuración (PR #114)
- [X] T035 [US2] Sistema visual institucional, fuentes plegables y panel de lectura (PR #114)
- [X] T036 [US3] Escala accesible: 18px base, controles de 54px y navegación móvil 2×2 (PR #118)
- [X] T037 [US4] Captura web pública desde Configuración con guard SSRF y 5 capturas/hora (PR #115)
- [X] T038 [US4] Carga de documentos pública con límites de 100 archivos y 5 MB (PR #117)
- [X] T039 [US1] FAQ por lotes de 10 con descarga Markdown (PR #116)
- [X] T040 [US1] Ocultar la pestaña Asistente y redirigir la portada al chat (PR #120)

## Phase 5: Despliegue y operación (ola 6)

- [X] T041 [US3] Imagen Docker multi-etapa; `capture` y `legacy` pasan a extras
- [X] T042 [US3] Separar `/live` de `/health` y arrancar sin credenciales del LLM
- [X] T043 [US3] Compose de producción con Postgres, Qdrant y Ollama; runbook de despliegue

## Pendientes (fuera de esta spec)

- [ ] T044 Autenticación OIDC y sesiones de servidor (ola 4) → spec futura 029
- [ ] T045 Cuotas por usuario con `user_id` aditivo, sin cambiar el contrato de las páginas
- [ ] T046 Retirar Streamlit y FAISS del repositorio (ola 7) → spec futura 030
- [ ] T047 Captura programada por cron con estado visible en la web (US4)
- [ ] T048 Endurecimiento del despliegue: dominio, TLS HSTS, respaldos y rotación de secretos
