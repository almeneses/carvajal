# Tasks: Asistente comercial de empaques para foodservice

**Spec**: [`spec.md`](./spec.md) · **Plan**: [`plan.md`](./plan.md) · **Estado**: pendiente

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivos distintos)
- **[USn]**: historia de usuario de la spec
- **PR n**: agrupación sugerida en Pull Requests a `dev` (≤300 líneas; los CSV curados se
  presupuestan aparte como datos)

## Phase 1: Setup (infraestructura compartida)

- [ ] T001 Aprobar la spec 036 y aplicar la enmienda de `CONSTITUTION.md` §2, §5 y §9 y de
      `.specify/memory/constitution.md` después de que la 035 entre a `dev`
- [ ] T002 Registrar la 036 en `specs/README.md` §7 (specs futuras)
- [X] T003 [P] Crear `tests/fixtures/catalog/` con 6 referencias, 5 materiales y 3
      distribuidores de prueba

## Phase 2: Fundacional (prerrequisitos bloqueantes)

**PR 1 — Modelo y carga del catálogo**

- [X] T004 `catalog/models.py`: `Reference`, `MaterialRule`, `Distributor` con validación de
      rangos, moneda y fecha
- [X] T005 `catalog/loader.py`: carga de los CSV con cache en memoria y error claro por fila
      inválida
- [X] T006 [P] `tests/test_catalog_loader.py`: filas válidas, fila inválida, archivo ausente

**PR 2 — Datos curados**

- [X] T007 `catalog/build.py` y orden `catalog-build`: fichas de la tienda MX →
      `referencias.csv` con `source_url` y `captured_at`
- [X] T008 [P] Transcribir la guía de materiales a `materiales.csv` (con la fuente y la fecha
      de captura de la guía) y revisarla contra el PDF
- [ ] T009 [P] Transcribir el catálogo PDF a filas de `referencias.csv` (país CO, sin precio)
      — se separa en la PR 2b para no bloquear la PR 3
- [X] T010 [P] Transcribir el directorio a `distribuidores.csv`
- [X] T011 `manifest.json` con fuente, fecha de captura, conteos y filas curadas a mano
- [X] T012 Prueba de integridad: todo material de `referencias.csv` existe en
      `materiales.csv` o queda marcado como excluido de filtros de temperatura

## Phase 3: User Story 1 — Recomendación técnicamente válida (P1) 🎯 MVP

**PR 3 — Herramientas de búsqueda y validación**

- [X] T013 [US1] `agent/commercial.py`: `search_packaging` con filtro exacto (la conversión
      ml → oz la hace el router, T017, según el contrato de `docs/agentes/`)
- [X] T014 [US1] `agent/commercial.py`: `check_material` con motivo de descarte
- [X] T015 [P] [US1] `tests/test_commercial_tools.py`: caliente excluye PS/PET; microondas
      solo PP; sin candidatos válidos devuelve vacío con motivo

**PR 4 — Ruta comercial en el router y el grafo**

- [X] T016 [US1] `agent/state.py`: `RouteName` incluye `commercial`
- [X] T017 [US1] `agent/router.py`: regla comercial y extracción de uso, temperatura,
      capacidad, cantidad, país y ciudad en `RouteDecision.extra` (la extracción vive en
      `agent/commercial_intent.py`)
- [X] T018 [US1] `agent/router.py`: aclaración en `direct` cuando falta uso o temperatura
- [X] T019 [US1] `agent/graph.py`: nodo `commercial` con secuencia fija y prompt que prohíbe
      añadir referencias, cifras o disponibilidad (la secuencia vive en
      `agent/commercial_flow.py`)
- [X] T020 [P] [US1] Pruebas de los escenarios 1–5 de la US1 en
      `tests/test_agent_router_commercial.py` y `tests/test_agent_commercial_flow.py`; las
      de `test_agent_router.py` y `test_agent_graph.py` siguen en verde sin cambios

## Phase 4: User Story 2 — Cantidad y precio de referencia (P1)

**PR 5 — Cálculo y cotización**

- [X] T021 [US2] `boxes_for` con redondeo hacia arriba y excedente
- [X] T022 [US2] `quote` con moneda, impuestos, fecha y aviso; error explícito sin precio
- [X] T023 [US2] Aviso de oferta vencida cuando la vigencia capturada ya pasó
- [X] T024 [P] [US2] Pruebas de los escenarios 1–4 de la US2 y del borde «más de 100 cajas»

## Phase 5: User Story 3 — Dónde comprar (P2)

- [X] T025 [US3] `find_distributors` por ciudad, con lista de ciudades disponibles si no hay
- [X] T026 [US3] Canal MX: enlace de la ficha; país ausente → aclaración (el enlace es
      `Reference.source_url`; el país ausente lo pide el nodo `commercial` junto con la
      recomendación, T019)
- [X] T027 [P] [US3] Pruebas de los escenarios 1–4 de la US3

**PR 6 — Tarjetas en la interfaz**

- [X] T028 [US1] [US2] [US3] `apps/api/templates/_productos.html`: nombre, SKU, material,
      unidades por caja, precio de referencia con fecha y fuente
- [X] T029 [US1] Integrar las tarjetas en el chat cuando la ruta es `commercial` (evento SSE
      `products` con el parcial renderizado en el servidor; `chat-stream.js` lo inserta)
- [X] T030 [P] Prueba de renderizado del parcial con y sin precio

## Phase 6: User Story 4 — Enrutar sin romper lo existente (P2)

- [X] T031 [US4] Regla: pregunta institucional sobre empaques sin intención de compra → `rag`
- [X] T032 [US4] Regla: necesidad a la medida (industrial, agro) → derivación comercial
- [X] T033 [US4] Pregunta mixta: atender la parte comercial y avisar la otra
- [X] T034 [US4] `data/eval/routing_questions.json` con preguntas existentes y comerciales
      etiquetadas (110: 49 existentes con la ruta del router de `dev`, 61 comerciales)
- [X] T035 [US4] Verificar SC-005: pruebas actuales del router y del grafo y evaluación de
      recuperación sin regresión (`tests/test_routing_eval_set.py`; las 31 consultas de
      recuperación conservan ruta, intención y consulta expandida, así que el retrieval no
      cambia)

## Phase 7: User Story 5 — Comparar enrutadores (P3)

**PR 7 — Router intercambiable y evaluación comparativa**

- [X] T036 [US5] `build_agent(router=...)` con `decide_route` por defecto
- [X] T037 [P] [US5] `agent/routers/llm.py`: salida estructurada con el mismo contrato
- [X] T038 [P] [US5] `agent/routers/decision.py`: adaptador del modelo de decisión cargado
      desde `DECISION_MODEL=paquete.modulo:fabrica` (sin extra hasta elegir el paquete); no
      disponible → error controlado
- [X] T039 [US5] Orden `eval-router --router rules|llm|decision` con precisión de ruta,
      precisión por dato, violaciones de material, latencia y costo
- [X] T040 [US5] Registrar en los logs la ruta, los datos extraídos y el enrutador (FR-012)
- [X] T041 [US5] `docs/resultados-router.md` con la tabla comparativa

## Phase 8: Pulido y transversal

- [X] T042 Actualizar `README.md` y `docs/arquitectura.md` con la ruta `commercial`
- [ ] T043 Ejecutar `make check` y el E2E del chat con tres conversaciones de ejemplo
      (`make check` en verde; guion en `docs/demo-036.md`, pendiente la corrida con la app)
- [X] T044 Revisar cada SC de la spec con evidencia en la PR de cierre
      (`specs/036-asistente-empaques/evidencia.md`)

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → US2 → US3; US4 puede empezar cuando exista el router de la US1;
  US5 necesita US1–US4.
- PR 1 y PR 2 pueden avanzar en paralelo (código del catálogo y transcripción de datos).
- PR 3 depende de PR 1 y de los fixtures; PR 4 de PR 3.

## Parallel Team Strategy

Reparto por capa, como en el plan 0002, para que dos personas no toquen los mismos archivos:

| Pista | Tareas | Archivos que posee |
|---|---|---|
| Datos y herramientas | T003–T015, T021–T027 | `data/catalog/**`, `catalog/**`, `agent/commercial.py` |
| Router, grafo e interfaz | T016–T020, T028–T041 | `agent/router.py`, `state.py`, `graph.py`, `routers/**`, `templates/**` |

El punto de encuentro es la firma de las herramientas de `agent/commercial.py`, que se fija
en PR 3 antes de que la otra pista la use.
