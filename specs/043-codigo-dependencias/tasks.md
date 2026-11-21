# Tareas: retiro de código y dependencias sin consumidores (043)

**Fuentes**: [spec](spec.md) y [plan](plan.md).
`[P]` indica trabajo que puede ejecutarse en paralelo tras cumplir sus prerequisitos.

## Fase 1 — Preparación

- [X] T001 Escribir `specs/043-codigo-dependencias/spec.md`, `plan.md`, `tasks.md`,
  `analysis.md` y `checklists/requirements.md` con el flujo specify → plan → tasks →
  analyze, en español y con el estilo de la spec 040.
- [X] T002 Verificar en el árbol de `origin/dev` que `_safe_rollback`, el embedder de
  `ChatService` y la configuración de `GraphAgentRunner` no tienen consumidores, con
  búsquedas en `apps/`, `packages/`, `tests/` y `scripts/` y revisión de reexports en
  `__all__`; registrar la evidencia en `analysis.md`.

## Fase 2 — Caracterización (antes de tocar código)

- [X] T003 [P] [US1] Añadir en `tests/test_characterization_043.py` las pruebas del
  contrato de `ChatService`: eventos `open`/`trace`/`meta`/`token`/`done` con SQLite en
  memoria y dobles, `ask` recomponiendo texto y citas, persistencia del historial y
  `default_chat_service()` cacheado y perezoso (sin depender de la firma del constructor ni
  de `ask_components`).
- [X] T004 [P] [US2] Añadir en `tests/test_characterization_043.py` las pruebas del runner:
  campos de `AgentTurn` que devuelve `prepare`, adición única del turno en `record`,
  ausencia de excepción cuando el checkpointer falla y construcción de
  `default_agent_runner()` sobre `apps.api.agent_runtime.agent_runtime`.
- [X] T005 [P] [US3] Añadir en `tests/test_characterization_043.py` las pruebas de la CLI:
  `prog`, subcomandos `ask` y `faq`, pregunta posicional, salida por defecto y ayuda de
  cada subcomando con código 0.
- [X] T006 Ejecutar `env -u OLLAMA_MODEL uv run pytest tests/test_characterization_043.py`
  en el árbol sin cambios y dejar las pruebas en verde; sin este punto de control no se
  implementa nada.

## Fase 3 — US1: chat sin plomería muerta (P1)

**Prueba independiente:** la caracterización del chat, `test_chat_service`, `test_api_chat`,
`test_api_stream` y la prueba de `default_chat_service` pasan antes y después.

- [X] T007 [US1] Retirar `_safe_rollback` de `apps/api/chat/service.py` (sin llamadas) y
  simplificar el import de `Session` solo si deja de usarse.
- [X] T008 [US1] Retirar el parámetro `embedder` y el atributo `_embedder` de
  `ChatService`, la clase `_DeferredEmbedder` y el argumento de `default_chat_service()`;
  conservar `_DeferredLLM` y `runtime.ask_components()` intactos.
- [X] T009 [P] [US1] Ajustar los dobles y constructores de prueba en
  `tests/test_chat_service.py`, `tests/test_chat_tracing.py`,
  `tests/test_monitoring_smoke.py` y `tests/test_monitoring_e2e.py`; retirar los embedders
  falsos que solo servían a `ChatService`.
- [X] T010 [US1] Ejecutar `env -u OLLAMA_MODEL uv run pytest tests/test_chat_service.py
  tests/test_chat_tracing.py tests/test_api.py tests/test_api_chat.py
  tests/test_api_stream.py tests/test_characterization_043.py` y confirmar el mismo
  comportamiento.

## Fase 4 — US2: runner sin configuración no usada (P1)

**Prueba independiente:** `test_agent_graph`, `test_productos` y la caracterización del
runner pasan antes y después.

- [X] T011 [US2] Retirar el parámetro `settings`, el atributo `_settings` y el import de
  `Settings` de `apps/api/chat/agent.py`; `default_agent_runner()` pasa a construir
  `GraphAgentRunner(runtime.graph)`.
- [X] T012 [P] [US2] Ajustar las construcciones de `GraphAgentRunner` en
  `tests/test_agent_graph.py` y `tests/test_productos.py`.
- [X] T013 [US2] Ejecutar `env -u OLLAMA_MODEL uv run pytest tests/test_agent_graph.py
  tests/test_productos.py tests/test_agent_routers.py
  tests/test_agent_commercial_flow.py tests/test_characterization_043.py`.

## Fase 5 — US3: dependencias directas sin uso (P1)

**Prueba independiente:** instalación completa, instalación base, import de la CLI
histórica y la imagen funcionan; si no, la dependencia se conserva y se documenta.

- [X] T014 [US3] Retirar `langchain` del extra `legacy` en `pyproject.toml`, regenerar el
  lock con `uv lock` y verificar `uv sync --all-extras`, `uv run python -c "import
  carvajal.rag"`, `uv run carvajal --help`, `uv run carvajal ask --help` y
  `uv run carvajal faq --help`; si falla, restaurar y documentar el error.
- [X] T015 [US3] Retirar `python-dotenv` de las dependencias base en `pyproject.toml`,
  regenerar el lock y verificar `uv sync` base en un venv desechable y que `Settings` lee
  `.env`; si falla, restaurar y documentar el error.
- [X] T016 [US3] Ejecutar `env -u OLLAMA_MODEL make check` completo, `uv run pytest
  tests/test_rag.py tests/test_cli.py` y `node --test tests/js/test_chat_voice.cjs`.

## Fase 6 — US4 y cierre

- [X] T017 [US4] Comprobar que sobreviven FAISS y el extra `legacy`, el checkpointer SQLite,
  Sentry y las referencias a pgvector de las migraciones; ejecutar sus pruebas
  (`tests/test_rag.py`, `tests/test_agent_graph.py`, `tests/test_observability.py`,
  `tests/test_storage_migrations.py`).
- [X] T018 [US4] Construir `docker build -t carvajal-api:043 .`; si termina, arrancar el
  contenedor y comprobar `GET /live`; si no, registrar el motivo exacto en `analysis.md` y
  en el cuerpo del PR sin tocar el Dockerfile para sortearlo.
- [X] T019 Repetir la caracterización sobre el árbol final y registrar en `analysis.md` el
  resumen de FR → tarea → evidencia y el resultado exacto de cada comando.
- [ ] T020 Hacer commits convencionales (`test(043): ...` para la caracterización y
  `refactor(043): ...` para cada retirada), `git push -u origin
  refactor/043-codigo-dependencias`, abrir el PR contra `dev` con el cuerpo en español,
  labels `refactor`, `backend` y `chore`, y assignee `@me`; no fusionar ni aprobar.

## Dependencias, paralelismo y entrega

- T001 y T002 preparan el terreno; T003–T005 se escriben en paralelo y T006 los valida en
  `origin/dev` antes de cambiar código.
- T007–T010 (US1) y T011–T013 (US2) son independientes entre sí: pueden ir en commits
  separados y en cualquier orden, pero la caracterización T003–T006 es prerrequisito de
  ambos.
- T014 y T015 son independientes entre sí y de las retiradas de código, pero deben ir
  después de T006 y antes de la puerta T016.
- T017 y T018 son paralelos; T019 y T020 cierran la fase.
- **MVP**: US1 + US2, que son el centro del goal. US3 se entrega en el mismo PR solo si la
  evidencia de instalación e import sale limpia; si no, la spec deja constancia y la
  dependencia se conserva.
