# Tasks: Reglas compartidas del chat, lector SSE único y HTMX solo en Configuración

**Input**: Design documents from `/specs/044-chat-sse-interfaz/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: sí, explícitos en la spec (FR-012): caracterización antes del refactor.

**Organization**: tareas por historia de usuario; la caracterización es fundacional
porque fija el contrato que todas las historias deben conservar.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivos distintos)
- **[Story]**: US1 (reglas compartidas), US2 (lector SSE), US3 (HTMX)

## Phase 1: Setup

- [x] T001 Crear `specs/044-chat-sse-interfaz/` con spec, plan, research, data-model, contratos, quickstart y checklist.
- [x] T002 Verificar la rama `refactor/044-chat-sse-interfaz` desde `origin/dev`, el venv y la línea base (`pytest` y `node --test` en verde).

---

## Phase 2: Foundational (caracterización, bloquea el refactor)

**Purpose**: fijar el contrato observable actual antes de tocar producción.

- [x] T003 [P] [US1] Escribir `tests/test_044_chat_contract.py` con la matriz de las tres entradas: rutas, formatos, códigos 403/404/422/429/503, mensajes, cabeceras, cookies y orden CSRF → validación → cuota (cita `data-model.md` `ChatEntry.question` e `IdentityCookie`).
- [x] T004 [P] [US2] Escribir `tests/js/test_044_chat_stream_reader.cjs` con dobles de DOM y flujos fragmentados para el lector actual de `chat-stream.js` y `faq.js`: cierre, bloques partidos, varios `data:`, `\r\n`, último bloque sin separador y diferencias entre consumidores.
- [x] T005 Ejecutar la caracterización y confirmar que pasa contra el código actual (commit `test(044): ...`).

**Checkpoint**: contrato congelado; el refactor no puede cambiar estas expectativas salvo la diferencia documentada.

---

## Phase 3: User Story 1 - Reglas compartidas (Priority: P1) 🎯 MVP

**Goal**: una sola definición de validación, identidad, cuota, CSRF y traducción SSE,
reutilizada por formulario, stream web y stream de API, con el mismo contrato.

**Independent Test**: `tests/test_044_chat_contract.py` pasa sin cambios tras extraer
el módulo; el orden de controles y las cookies son idénticos.

- [x] T006 [US1] Crear `apps/api/views/chat_rules.py` con constantes (`MAX_QUESTION_CHARS`, mensajes), `validate_question`, `resolve_visitor`, `require_csrf`, `consume_quota`, `set_identity_cookie`, `chat_provider`, `invalid_response` y `stream_events` (traducción de `ChatEvent` a SSE con cierre de seguridad y logging parametrizable).
- [x] T007 [US1] Refactorizar `apps/api/views/chat.py` para usar `chat_rules` y reexportar los nombres públicos que consumen otras vistas y pruebas.
- [x] T008 [P] [US1] Refactorizar `apps/api/views/stream.py` para usar `chat_rules` y eliminar su traductor SSE duplicado.
- [x] T009 [P] [US1] Refactorizar `apps/api/views/api.py` para usar `chat_rules` y eliminar `_api_sse` duplicado.
- [x] T010 [US1] Refactorizar `apps/api/views/faq.py` para importar identidad, cuota y CSRF desde `chat_rules` conservando su orden (preparar antes de cobrar).
- [x] T011 [US1] Ejecutar `tests/test_044_chat_contract.py`, `tests/test_api_chat.py`, `tests/test_api_stream.py`, `tests/test_api_faq.py` y `tests/test_security.py`.

---

## Phase 4: User Story 2 - Lector SSE común (Priority: P1)

**Goal**: un único lector estático usado por chat y FAQ, con las diferencias
conservadas por callbacks.

**Independent Test**: `tests/js/test_044_sse_shared.cjs` y la caracterización de
consumidores pasan; el chat notifica `chat:turn-complete` y la FAQ aplica el lote.

- [x] T012 [US2] Crear `apps/api/static/sse.js` con `window.CarvajalSSE.leerEventos` e `interpretar`, con procesamiento del último bloque sin separador.
- [x] T013 [P] [US2] Refactorizar `apps/api/static/chat-stream.js` para usar `CarvajalSSE` y conservar su formateo, marcas, notificación, degradación y timeout.
- [x] T014 [P] [US2] Refactorizar `apps/api/static/faq.js` para usar `CarvajalSSE` y conservar su acumulación, deduplicación, descarga y CSRF.
- [x] T015 [US2] Cargar `/static/sse.js` antes del consumidor en `apps/api/templates/chat.html` y `apps/api/templates/faq.html`.
- [x] T016 [US2] Escribir `tests/js/test_044_sse_shared.cjs` y actualizar la expectativa de la FAQ en la caracterización (último bloque sin separador ahora se procesa).
- [x] T017 [US2] Ejecutar `node --test tests/js/test_044_sse_shared.cjs`, la caracterización del lector y `node --test tests/js/test_chat_voice.cjs`.

---

## Phase 5: User Story 3 - HTMX solo en Configuración (Priority: P2)

**Goal**: ninguna pantalla distinta de Configuración carga HTMX; la captura sigue
igual.

**Independent Test**: `GET /chat`, `/faq`, `/corpus`, `/documents/{id}` no contienen
`htmx.min.js`; `GET /configuracion` sí, y conserva `hx-*` y `captura.js`.

- [x] T018 [US3] Quitar `htmx.min.js` de `apps/api/templates/base.html` y cargarlo en `apps/api/templates/configuracion.html` antes de `captura.js`.
- [x] T019 [US3] Ajustar `tests/test_api.py` (y añadir una prueba si hace falta) para exigir HTMX solo en Configuración y conservar la captura.
- [x] T020 [US3] Verificar que `chat.html` conserva formulario sin streaming, dictado, lectura, autolectura, manos libres y controles accesibles; que `captura.js` sigue escuchando `htmx:*`; y que la CSP no cambia.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T021 Revisar que no queden importaciones ni funciones muertas y que `apps/api/chat/service.py`/`agent.py` no se hayan tocado (`git diff --stat`).
- [x] T022 Ejecutar la puerta completa: `env -u OLLAMA_MODEL make check` y `node --test tests/js/test_chat_voice.cjs`.
- [x] T023 Actualizar `tasks.md` (marcar tareas), escribir `analysis.md` sin hallazgos CRITICAL y registrar evidencia en el reporte al Lead.
- [x] T024 Commit convencional, push de la rama y PR a `dev` con labels `refactor`, `backend`, `ui` y assignee `@me`.

## Dependencies

- T001–T002 bloquean todo.
- T003–T005 (caracterización) bloquean T006–T020.
- T006 bloquea T007–T010; T007 y T008/T009/T010 pueden ir en paralelo.
- T012 bloquea T013–T015; T016 depende de T012–T015.
- T018–T020 independientes de T006–T017 (archivos distintos), pero se validan juntas al final.

## Parallel execution examples

- T008 y T009 en paralelo tras T006 (archivos distintos).
- T013 y T014 en paralelo tras T012 (archivos distintos).
- T003 y T004 en paralelo (Python y Node).

## Implementation strategy

MVP = US1 + caracterización. Luego US2 (lector) y US3 (HTMX) en el mismo PR porque el
goal exige una sola entrega verificable con la puerta completa.

## Evidencia de la ejecución (2026-11-20)

| Verificación | Comando | Resultado |
|---|---|---|
| Caracterización HTTP/SSE | `env -u OLLAMA_MODEL uv run pytest tests/test_044_chat_contract.py` | 40 passed |
| Suite del shell y seguridad | `env -u OLLAMA_MODEL uv run pytest tests/test_api_chat.py tests/test_api_stream.py tests/test_api_faq.py tests/test_api.py tests/test_security.py tests/test_feedback.py` | 106 passed |
| Lector SSE compartido | `node --test tests/js/test_044_sse_shared.cjs` | 13 passed |
| Lector en consumidores | `node --test tests/js/test_044_chat_stream_reader.cjs` | 9 passed |
| Todo Node (incluye 040) | `node --test tests/js/*.cjs` | 90 passed |
| Puerta completa | `env -u OLLAMA_MODEL make check` | 1683 passed, 46 deselected |
| HTMX solo en Configuración | `env -u OLLAMA_MODEL uv run pytest tests/test_api.py -k htmx` | 1 passed |
