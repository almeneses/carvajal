# Tasks: Asistente Q&A con RAG sobre el corpus de Carvajal

**Spec**: [`spec.md`](./spec.md) · **Plan**: [`plan.md`](./plan.md) · **Estado**: completado

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivos distintos)
- **[USn]**: historia de usuario de la spec

## Phase 1: Setup (infraestructura compartida)

- [X] T001 Crear el proyecto con `uv`, src-layout y `pyproject.toml`
- [X] T002 Configurar Ruff, `ty`, pytest y pre-commit
- [X] T003 Configurar CI (GitHub Actions), Dependabot y plantillas de PR/issue
- [X] T004 Escribir `CONSTITUTION.md`, `AGENTS.md` y `README.md`
- [X] T005 Instalar spec-kit y versionar `.specify/` y `specs/`

## Phase 2: Fundacional (prerrequisitos bloqueantes)

- [X] T006 `fetching.py`: sesión HTTP, UA propio, timeout y rate limit
- [X] T007 `fetching.py`: `robots.txt` con `Crawl-delay` y `normalize_robots` (RFC 9309)
- [X] T008 `fetching.py`: errores de red como `FetchError` (descartes, no excepciones)
- [X] T009 `extracting.py`: DOM → Markdown descartando *chrome* y regiones dinámicas
- [X] T010 `gold.py`: front matter con `url`, `title`, `section`, `domain`, `source_type`,
      `captured_at`, `hash` y `chars`
- [X] T011 `gold.py`: capas bronze/silver/gold e `index.jsonl`
- [X] T012 `pipeline.py`: siete etapas con `CaptureReport` y eventos de progreso
- [X] T013 `config.py`: semillas oficiales (18) y de prensa (6)

## Phase 3: User Story 1 — Q&A con fuente (P1) 🎯 MVP

- [X] T014 [US1] `rag.py`: `build_system_prompt` con el corpus consolidado
- [X] T015 [US1] `rag.py`: `ask_with_sources` devolviendo respuesta y documentos
- [X] T016 [US1] `rag.py`: `build_llm` configurable (DeepSeek, Claude, Ollama)
- [X] T017 [US1] Prompt anti-alucinación con citas y distinción oficial/tercero
- [X] T018 [US1] Pruebas del pipeline de respuesta con LLM falso

## Phase 4: User Story 2 — Captura visible (P2)

- [X] T019 [US2] `ui.py`: pestaña Pipeline con formulario (alcance, límite, robots)
- [X] T020 [US2] Diagrama de bloques con estados pendiente/activo/listo/error
- [X] T021 [US2] Métricas de la corrida y tabla de descartes con motivo
- [X] T022 [US2] Pruebas de eventos y del diagrama

## Phase 5: User Story 3 — Resumen y FAQ (P3)

- [X] T023 [US3] `rag.py`: `summarize` con estructura fija y «no consta»
- [X] T024 [US3] `rag.py`: `generate_faq` con temas ausentes
- [X] T025 [US3] CLI `summarize` y `faq` con salida a `docs/`
- [X] T026 [US3] Pestañas Resumen y FAQ con caché en sesión y descarga
- [X] T027 [US3] Documentar la experimentación de prompts (`docs/prompts.md`)

## Phase 6: Pulido y transversal

- [X] T028 Índice FAISS persistido con huella e invalidación por cambio de corpus
- [X] T029 Identidad visual (logo SVG y paleta del sitio) y tema de Streamlit
- [X] T030 `docs/alcance.md` con matriz de cobertura y evidencia de lo no publicado
- [X] T031 Evaluación de 30 preguntas → `docs/resultados.md` (26/30)
- [X] T032 Guion de sustentación (`docs/presentacion.md`) y diagrama de arquitectura
- [X] T033 11 PRs fusionados con revisión y CI en verde

## Pendientes (fuera de esta spec)

- [ ] Mensaje amable y reintento cuando el servicio de embeddings no responde
- [ ] Filtrar bloques de «artículos relacionados» en prensa para estabilizar el índice
- [ ] v2 de la pestaña Pipeline: tabla por URL y panel de directorios
- [ ] Deploy con backend y frontend separados → `specs/002-api-backend-frontend/`
- [ ] Skill reutilizable `rag-corpus-builder`
