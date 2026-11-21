# Tareas: separar historial visible, preferencias y estado técnico (046)

**Fuentes**: [spec](spec.md), [plan](plan.md), [analysis](analysis.md).
`[P]` indica trabajo paralelizable una vez cumplidos sus prerequisitos. Cada tarea cita el
requisito o el escenario de la matriz de caracterización del plan (C1–C11).

## Fase 1 — Especificación y caracterización

- [ ] T001 Escribir `spec.md`, `checklists/requirements.md`, `plan.md` y `tasks.md` con las
  tres capas, la matriz C1–C11 y el criterio de retirada (FR-001, FR-002, FR-003, FR-010).
- [ ] T002 [P] Crear `tests/test_chat_state_layers.py` con PostgreSQL real (testcontainers):
  grafo real `GraphAgentRunner` + `PostgresSaver` + `PostgresStore`, embedder, vector
  store y LLM falsos; fixtures de base limpia y semilla de turnos.
- [ ] T003 [P] Caracterizar hilo nuevo (C1) y seguimiento elíptico (C2): rutas, intención,
  consulta de recuperación y secuencia de eventos SSE (FR-004, FR-005, FR-012).
- [ ] T004 [P] Caracterizar preferencia entre hilos (C3) y aislamiento entre visitantes en
  preferencias, hilos y error de acceso (C4), leyendo el Store directamente (FR-002,
  FR-009).
- [ ] T005 [P] Caracterizar reinicio del runtime (C5): checkpointer nuevo sobre la misma
  base, historial y seguimiento desde la semilla de PostgreSQL, con la misma secuencia de
  eventos (FR-001, FR-006, FR-012).
- [ ] T006 [P] Caracterizar reintento idempotente (C6) y turno incompleto + replay (C7,
  C8) atravesando `ChatService.stream` (FR-007, FR-008).
- [ ] T007 Ejecutar la caracterización en el código actual y registrar la evidencia
  (verde antes del refactor) en la PR.
- [ ] T008 Commit `test(046): caracteriza las tres capas del estado del agente`.
- [ ] T009 Escribir `analysis.md` (specify→plan→tasks→analyze) y corregir los hallazgos
  CRITICAL antes de tocar el código de producción.

## Fase 2 — Evidencia y retirada condicionada de la copia

- [ ] T010 Añadir C9 (checkpointer vacío + semilla de PostgreSQL) y C10 (copia envenenada
  + semilla) en `tests/test_chat_state_layers.py`: el comportamiento no cambia (FR-003,
  FR-010).
- [ ] T011 Añadir C11 (checkpoint con copia y semilla vacía): el grafo no reutiliza la
  copia como historial; debe fallar antes del cambio (FR-003, SC-002).
- [ ] T012 Retirar el respaldo de la copia en `packages/core/src/carvajal/agent/graph.py`:
  `recall` usa solo `seed_turns` (FR-001, FR-004).
- [ ] T013 Retirar `record` del protocolo `AgentRunner` y `turns` de `AgentTurn` en
  `apps/api/chat/agent.py`; borrar la escritura de `turns`/`answer`/`incomplete` en el
  checkpoint (FR-003, FR-010).
- [ ] T014 Retirar `answer` e `incomplete` de `AgentState` en
  `packages/core/src/carvajal/agent/state.py` (solo los escribía `record`).
- [ ] T015 Quitar las dos llamadas a `runner.record` de `apps/api/chat/service.py`, sin
  reformatear el archivo ni tocar el constructor (FR-003, FR-010).
- [ ] T016 Adaptar `tests/test_agent_graph.py`, `tests/test_chat_service.py`,
  `tests/test_chat_tracing.py`, `tests/test_monitoring_smoke.py` y
  `tests/test_monitoring_e2e.py`; el script `scripts/evidencia_monitoreo_038.py` deja de
  pasar `turns=()`.
- [ ] T017 Si C11 o cualquier escenario falla con la retirada: revertir T012–T015,
  conservar la copia y documentar en `plan.md` la función que cumple (FR-010). Repetir la
  caracterización.
- [ ] T018 Añadir la fila de decisión de las tres capas en `CONSTITUTION.md` §9 (Principio
  I: mismo commit) y hacer el commit `refactor(046): retira la copia de turnos del
  checkpoint` con el código, las pruebas y la fila juntos.

## Fase 3 — Puerta y publicación

- [ ] T019 Ejecutar `env -u OLLAMA_MODEL make check` y corregir lo que marque lint, formato,
  tipos o pruebas unitarias (SC-005).
- [ ] T020 Ejecutar `env -u OLLAMA_MODEL make test-integration` con Docker disponible
  (SC-001, SC-002, SC-005).
- [ ] T021 Ejecutar `node --test tests/js/test_chat_voice.cjs` (SC-005).
- [ ] T022 Verificar que no se borró ni migró ningún dato: `git diff --stat` sin migraciones;
  las tablas de mensajes y del checkpointer siguen en pie (SC-004).
- [ ] T023 `git push -u origin refactor/046-estado-agente`.
- [ ] T024 Abrir PR contra `dev` con cuerpo en español (qué/por qué/tres capas/pruebas/cómo
  probar/evidencia/reversión/riesgos), labels `refactor` y `backend`, assignee `@me`
  (FR-012, SC-006).
- [ ] T025 Registrar evidencia C1–C11 en la PR y cerrar la tarea compartida `task-6` solo
  con la puerta en verde.

## Dependencias

- T002 depende de T001.
- T003–T006 dependen de T002.
- T007 depende de T003–T006.
- T009 depende de T008 (analysis corre después de tasks).
- T010–T011 dependen de T008.
- T012–T015 dependen de T011 (la prueba de la retirada se escribe antes).
- T016 depende de T012–T015.
- T017 se ejecuta solo si T011/T016 fallan.
- T018 depende de T016.
- T019–T024 dependen de T018.
- T025 depende de T019–T024.

## Estrategia

La caracterización (T002–T008) es el MVP verificable: demuestra que las tres capas ya
funcionan y que PostgreSQL basta como autoridad. Solo después, T010–T011 fijan la evidencia
de que la copia no cumple otra función y T012–T016 la retiran. Si la evidencia no
acompaña, T017 deja el PR como separación documentada de capas.
