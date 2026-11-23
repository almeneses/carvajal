---

description: "Lista de tareas de la spec 044 — agente con herramientas"
---

# Tasks: Agente con herramientas para el chat de Carvajal

**Input**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo de datos](data-model.md), contratos ([herramientas](contracts/agent-tools.md),
[SSE](contracts/sse-events.md), [evaluación](contracts/eval-agent.md)) y
[validación](quickstart.md).

**Tests**: incluidos. La spec exige pruebas independientes por historia, la constitución
exige `make check` en verde y el plan define pruebas compartidas entre modos. Las pruebas de
cada historia se escriben primero y deben fallar antes de implementar.

**Organization**: por historia de usuario. `[P]` = archivos distintos y sin dependencias
pendientes. Las referencias `data-model.md Entidad.campo` remiten a la definición canónica
de cada campo restringido.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: configuración, interruptor de modo y conjunto de generalización (F0 del plan).

- [X] T001 Añadir `agent_mode: Literal["routes", "tools"] = "routes"`, `agent_max_tool_calls: int = 4` (1–8), `agent_turn_timeout_s: float = 45` y `agent_max_quotes: int = 3` en `packages/core/src/carvajal/infrastructure/settings.py`, con su documentación en `.env.example`; prueba en `tests/test_settings.py`. FR-010, FR-015.
- [X] T002 [P] Redactar el borrador de `data/eval/generalization_questions.json` (≥ 50 ítems con los mínimos por grupo de `contracts/eval-agent.md`) **sin abrir** `data/eval/routing_questions.json`, `agent/router.py` ni `agent/commercial_intent.py`. Incluir las cinco frases del hallazgo «tinto» (research R4), «vasos para café, 300, Bogotá» → 1 tarjeta (research R5), «¿En qué año se fundó Carvajal?» → `buscar_corpus` obligatorio (research R3) y casos de cambio de domicilio. FR-016.
- [X] T003 Revisión y congelamiento del owner: ajustar el borrador de T002, fijar `frozen_at` y `origin`, y commitear. **Bloquea Phase 3 en adelante.** Depende de T002. FR-016. Congelado sin cambios el 2026-11-23.
- [X] T004 [P] Añadir validador del formato de `contracts/eval-agent.md` (mínimos por grupo, ids únicos, `frozen_at` presente) en `tests/test_generalization_set.py`. Depende de T002.

- [X] T005 Enmiendas 1 y 2 del plan en `CONSTITUTION.md` y `.specify/memory/constitution.md` (principio III: abstención garantizada por el servidor y declaración obligatoria de consulta; restricciones técnicas: modos `routes` y `tools`), con versión nueva y en un PR propio `docs(constitution): …` abierto con la skill `abrir-pr`. **Bloquea el merge de cualquier PR con código del modo `tools`** (no su desarrollo). FR-004, FR-004a, FR-015. PR #230 abierto el 2026-11-23.

**Checkpoint**: conjunto congelado, configuración lista y enmiendas en revisión del owner.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: dominio estructurado, corrección «tinto», piezas comunes del grafo y dobles de prueba.

**⚠️ CRITICAL**: ninguna historia empieza antes de terminar esta fase.

- [X] T006 Corrección «tinto» en commit `fix:` propio: casos dorados nuevos en `data/eval/cotizacion_cases.json` con las cinco frases de research R4 (esperado: temperatura deducida o pregunta, nunca CO-VRP-07 para tinto), prueba que falla en `tests/test_quotation_engine.py`, y deducción de bebida caliente para «tinto», «aromática», «chocolate» y afines en `packages/core/src/carvajal/agent/commercial_intent.py`. Verificar `make eval-cotizador`. Spec: caso límite de temperatura implícita.
- [X] T007 Escribir `tests/test_quotation_request.py`: paridad `quote()` ↔ `quote_request()` en todos los casos de `data/eval/cotizacion_cases.json`, y `needs_input` cuando faltan `data-model.md QuoteRequest.family`/`QuoteRequest.use` (ambos), `QuoteRequest.temperature` (sin uso que la implique), `QuoteRequest.quantity` o `QuoteRequest.city`; total pendiente con `QuoteRequest.delivery = None`. Depende de T006.
- [X] T008 Extraer `QuoteRequest` (dataclass) y `quote_request()` en `packages/core/src/carvajal/quotation/engine.py` con los campos `data-model.md QuoteRequest.family`, `QuoteRequest.use`, `QuoteRequest.capacity_oz`, `QuoteRequest.temperature`, `QuoteRequest.microwave`, `QuoteRequest.prefers_renewable`, `QuoteRequest.quantity`, `QuoteRequest.city` y `QuoteRequest.delivery`; `quote(text, …)` interpreta texto y delega en `quote_request()`. Exportar desde `packages/core/src/carvajal/quotation/__init__.py`. Depende de T007. FR-002, FR-005.
- [X] T009 [P] Implementar `SourceRegistry` (numeración consecutiva por procedencia, deduplicación por `chunk_id`/URL, serialización con `n`) en `packages/core/src/carvajal/agent/toolagent/sources.py` con pruebas en `tests/test_tool_agent_sources.py`. Cubre `data-model.md ToolAgentState.sources` y `Source.n`. FR-013.
- [X] T010 [P] Implementar `ScriptedToolChat` (doble con `bind_tools` y `stream` que emite `AIMessageChunk` con `tool_call_chunks` guionizados, contenido previo opcional y `usage_metadata`) en `tests/fakes/tool_chat.py` (research R10).
- [X] T011 [P] Definir `ToolOutcome` (con `data-model.md ToolOutcome.tool`, `ToolOutcome.args`, `ToolOutcome.status`, `ToolOutcome.source_ids` y `ToolOutcome.elapsed_ms`) y el estado `ToolAgentState` en `packages/core/src/carvajal/agent/toolagent/state.py`, con `data-model.md ToolAgentState.question` (sin vacíos), `ToolAgentState.messages` (reductor `add_messages`), `ToolAgentState.seen_calls`, `ToolAgentState.sources` y `ToolAgentState.results` como tipos JSON-serializables según la nota de serialización de `data-model.md`. Prueba de ida y vuelta con el serializador del checkpointer en `tests/test_tool_agent_state.py`.
- [X] T012 [P] Añadir `AGENT_SYSTEM` en `packages/core/src/carvajal/prompts.py`: identidad y estilo vigentes, contexto y resultados como datos, citar `[n]`, marcar partes sin respaldo, cotizar solo lo pedido y comparar con `buscar_producto` (research R5), pedir datos con `sin_consulta(pedir_dato)`, temperatura 0. Corregir el voseo de `ABSENCE_POLICY["out_of_scope"]`. Prueba en `tests/test_prompts.py`.
- [X] T013 Crear `tests/test_hard_rules_modes.py` parametrizado con `mode ∈ {routes, tools}` y fixture que construye cada grafo con dobles (plan, *Convivencia y retiro*); por ahora solo `routes` en verde y `tools` marcado `xfail(strict=True)`. Depende de T010.

**Checkpoint**: motor estructurado con paridad 12/12 + casos «tinto»; piezas comunes listas.

---

## Phase 3: User Story 1 - Resolver una necesidad compuesta en un solo turno (Priority: P1) 🎯 MVP

**Goal**: el agente elige y encadena consultas y responde todas las partes respaldadas en un turno.

**Independent Test**: cinco preguntas compuestas del conjunto de generalización en hilos nuevos con `AGENT_MODE=tools`: cada parte respaldada aparece con su fuente y cada parte sin respaldo se declara.

### Tests for User Story 1

- [X] T014 [P] [US1] Pruebas de contrato de las seis herramientas (entradas válidas e inválidas, `status`, `n`, `missing`) según `contracts/agent-tools.md` en `tests/test_tool_agent_tools.py`.
- [X] T015 [P] [US1] Pruebas del bucle con `ScriptedToolChat` en `tests/test_tool_agent_graph.py`: llamadas agrupadas en un paso ejecutadas en serie en el orden pedido (research R12), reformulación tras `buscar_corpus` vacío (US1.4), y `tool_choice="required"` en el primer paso (research R3).
- [X] T016 [P] [US1] Pruebas del adaptador de streaming en `tests/test_tool_agent_stream.py`: retención de 48 caracteres, descarte del texto previo a una llamada (research R2), eventos `status` en orden y sin resultados (`contracts/sse-events.md`).

### Implementation for User Story 1

- [X] T017 [P] [US1] Implementar `buscar_corpus` sobre `LayerPriorityStore`, `rag_min_score`, `proper_entities_supported` y neutralización de delimitadores en `packages/core/src/carvajal/agent/toolagent/tools.py`. FR-002, FR-003.
- [X] T018 [P] [US1] Implementar `buscar_producto` (`search_packaging` + `check_material`, descartadas con motivo, `needs_input` por temperatura) en `packages/core/src/carvajal/agent/toolagent/tools.py`. FR-002, FR-005.
- [X] T019 [US1] Implementar `cotizar` sobre `quote_request()` con relleno desde `data-model.md ToolAgentState.commercial_context` y lista `reutilizado` en `packages/core/src/carvajal/agent/toolagent/tools.py`. Depende de T008. FR-002, FR-003.
- [X] T020 [P] [US1] Implementar `buscar_sedes` (contrato comercial, disponibilidad solo con `valid_until` vigente) e `indicador_financiero` (envuelve `indicator_lookup`) en `packages/core/src/carvajal/agent/toolagent/tools.py`. FR-002.
- [X] T021 [P] [US1] Implementar `sin_consulta(motivo, dato)` sin evidencia y sin línea de estado en `packages/core/src/carvajal/agent/toolagent/tools.py` (`contracts/agent-tools.md`).
- [X] T022 [P] [US1] Implementar plantillas amigables por herramienta (sin nombres internos, resultados ni precios; dato principal escapado) y su publicación con `get_stream_writer()` en `packages/core/src/carvajal/agent/toolagent/status.py`; revisar textos con el owner. FR-018.
- [X] T023 [US1] Construir `build_tool_agent()` en `packages/core/src/carvajal/agent/toolagent/graph.py`: `recall → agent ⇄ tools → END`, `bind_tools` con llamadas múltiples permitidas y ejecución en serie, primer paso con `tool_choice="required"`, registro en `data-model.md ToolAgentState.results`, `ToolAgentState.sources` y `ToolAgentState.commercial_context`. Depende de T011, T017–T022. FR-001.
- [X] T024 [US1] Construir el grafo según `AGENT_MODE` en `apps/api/agent_runtime.py` (el de `routes` se construye siempre para atajo y respaldo). Depende de T023. FR-015.
- [X] T025 [US1] Implementar `ToolAgentRunner.stream()` en `apps/api/chat/agent.py`: consume `graph.stream(stream_mode=["messages", "custom"])`, aplica la retención de research R2 y produce eventos `status`, `token` y el resultado final (fuentes, tarjetas, cotizaciones). Depende de T023.
- [X] T026 [US1] Ramificar `ChatService.stream()` por modo en `apps/api/chat/service.py`: emitir `status`, tokens y `trace` con `mode`/`path`, conservar persistencia en una transacción y monitoreo existente. Depende de T025. FR-011, FR-012.
- [X] T027 [P] [US1] Manejar el evento `status` en `apps/api/static/chat-stream.js` (línea de estado `role=status`, reemplazo de «Pensando…», borrado con `token`/`fallback`/`error`) y prueba en `tests/browser/` o `tests/test_chat_stream_js.py`. FR-018, SC-006a.

**Checkpoint**: con `AGENT_MODE=tools` una necesidad compuesta se resuelve en un turno con estados y fuentes.

---

## Phase 4: User Story 2 - Conservar las garantías de lo que se afirma (Priority: P1)

**Goal**: cero materiales prohibidos, datos sin fuente o afirmaciones sin evidencia en ambos modos.

**Independent Test**: casos dorados del cotizador, preguntas de material de la 036 y conjunto adversarial con cero violaciones; `tests/test_hard_rules_modes.py` verde en `routes` y `tools`.

### Tests for User Story 2

- [X] T028 [P] [US2] Completar `tests/test_hard_rules_modes.py` (quitar `xfail` de `tools`): material prohibido pedido por nombre, precio ausente sin estimar, total pendiente, abstención sin evidencia, fuente de tercero etiquetada e instrucciones dentro de fragmentos. FR-002–FR-004, FR-007.
- [X] T029 [P] [US2] Pruebas de la compuerta en `tests/test_tool_agent_evidence.py`: sin evidencia + `buscar_corpus` → abstención sin generar; solo `sin_consulta` con cifras, fechas o nombres propios → abstención; solo `sin_consulta` limpio → texto completo emitido de una vez (research R2, R3).

### Implementation for User Story 2

- [X] T030 [US2] Implementar `evidence.gate()` y el filtro de `sin_consulta` (reutilizando el criterio de la ruta directa) en `packages/core/src/carvajal/agent/toolagent/evidence.py` sobre `data-model.md ToolOutcome.status` y `ToolOutcome.source_ids`. FR-004.
- [X] T031 [US2] Integrar la compuerta y la retención por camino (48 caracteres / completa / sin generar) en `apps/api/chat/agent.py`. Depende de T030. FR-004.
- [X] T032 [US2] Endurecer el formato que cada herramienta devuelve al modelo (bloques de datos delimitados y neutralizados, nunca órdenes) en `packages/core/src/carvajal/agent/toolagent/tools.py`. FR-007.

**Checkpoint**: MVP (US1 + US2) — el modo `tools` es seguro de probar con personas internas.

---

## Phase 5: User Story 3 - Conversar con naturalidad en varios turnos (Priority: P1)

**Goal**: correcciones y elipsis usan el contexto correcto del hilo.

**Independent Test**: diez hilos de 3–5 turnos con cambios de ciudad, cantidad, producto y referencias elípticas; cada turno usa el contexto correcto.

### Tests for User Story 3

- [X] T033 [P] [US3] Pruebas en `tests/test_tool_agent_history.py`: historial como `HumanMessage`/`AIMessage` limitado por `agent_history_turns`, sin `ToolMessage` de turnos previos, bloque `<contexto_comercial>` como datos, preferencias aplicadas como estilo (research R7).

### Implementation for User Story 3

- [X] T034 [US3] Construir los mensajes del turno desde `data-model.md ToolAgentState.seed_turns`, `ToolAgentState.preferences` y `ToolAgentState.commercial_context` en el nodo `recall` de `packages/core/src/carvajal/agent/toolagent/graph.py`. FR-006, FR-007.
- [X] T035 [US3] Persistir y recuperar `commercial_context` entre turnos (checkpointer) y actualizarlo con los argumentos de `buscar_producto`/`cotizar` en `packages/core/src/carvajal/agent/toolagent/graph.py`; la herramienta informa qué campos reutilizó. FR-006.

**Checkpoint**: «mejor en Cali», «¿y si son 500?» y «¿eso cuándo fue?» funcionan en el modo `tools`.

---

## Phase 6: User Story 4 - Operar sin regresiones ni costos innecesarios (Priority: P2)

**Goal**: atajo sin costo, respaldo sin redacción, límites, folios múltiples, replay y monitoreo.

**Independent Test**: diez saludos sin decisión del proveedor; caída simulada del proveedor → respaldo; reenvío con el mismo `client_turn_id` → misma respuesta y folios.

### Tests for User Story 4

- [X] T036 [P] [US4] Pruebas en `tests/test_tool_agent_graph.py`: atajo para saludo/capacidad/preferencia sin llamar al chat; límite de `data-model.md ToolAgentState.tool_calls_used` con redacción final sin herramientas y `ToolAgentState.missing` en el prompt; deduplicación por `ToolAgentState.seen_calls`. FR-008, FR-010.
- [X] T037 [P] [US4] Pruebas en `tests/test_tool_agent_stream.py`: respaldo determinista ante `LLMError`, plazo vencido y decisión inválida; «Detener» antes del primer token no persiste nada; durante el texto persiste `incomplete` sin cotizaciones (research R2, R8, R9). FR-009, FR-011.
- [X] T038 [P] [US4] Pruebas de folios en `tests/test_tool_agent_stream.py` y `tests/test_quotation_memory.py`: dos cotizaciones → dos tarjetas y dos filas `quotes` en una transacción; fórmula de `data-model.md Quote` (índice 0 igual que hoy, `:i` después); reemplazo cuando solo cambia `QuoteRequest.delivery`; máximo `agent_max_quotes`; reenvío → mismos folios y orden. FR-012a.

### Implementation for User Story 4

- [X] T039 [US4] Nodo `shortcut` con `decide_route` que reutiliza el nodo `direct` actual para saludo, capacidad y preferencia en `packages/core/src/carvajal/agent/toolagent/graph.py`; `ToolAgentState.mode_path = "shortcut"`. FR-008.
- [X] T040 [US4] Límites del turno: contador `ToolAgentState.tool_calls_used` (sin contar `sin_consulta`), registro de `data-model.md ToolAgentState.missing` para el paso final, `recursion_limit`, deduplicación `ToolAgentState.seen_calls` y paso final sin herramientas en `packages/core/src/carvajal/agent/toolagent/graph.py`; plazo de `agent_turn_timeout_s` en `apps/api/chat/agent.py`. FR-010.
- [X] T041 [US4] Respaldo determinista `fallback.render()` (tarjetas, cotización o hasta 3 fragmentos citados + aviso fijo; sin resultados → aviso e invitación a reintentar) en `packages/core/src/carvajal/agent/toolagent/fallback.py` e integración vía evento `fallback` en `apps/api/chat/service.py`; el servicio registra `path: "fallback"` en `trace` y en el monitoreo (no en el estado del grafo, ver `data-model.md ToolAgentState.mode_path`). FR-009.
- [X] T042 [US4] Varias cotizaciones: `ToolAgentState.quotes`, reemplazo por domicilio y tope en `packages/core/src/carvajal/agent/toolagent/tools.py`; `_issue_quote` → lista con folios de `data-model.md Quote` y `_persist` con varias filas en `apps/api/chat/service.py`; `done.quote_ids` y `quote_id` compatible (`contracts/sse-events.md`). FR-012a.
- [X] T043 [US4] Resultado comercial con `quotes` y `quote` compatible en `messages.sources`; iterar `quotes` (con caída a `[quote]`) en `apps/api/templates/_productos.html`, `apps/api/chat/products.py` y `_replay` de `apps/api/chat/service.py`. Depende de T042. FR-012.
- [X] T044 [US4] Monitoreo por turno: hijo por herramienta con nombre, orden, `status`, latencia; `mode`, `path`, tokens; argumentos y resultados solo con contenido completo, en `apps/api/chat/service.py` y `packages/core/src/carvajal/infrastructure/monitoring.py`; prueba en `tests/test_monitoring.py`. FR-014.

**Checkpoint**: el modo `tools` no rompe nada de lo entregado en las specs 017, 038 y 043.

---

## Phase 7: User Story 5 - Medir si el cambio vale la pena (Priority: P2)

**Goal**: informe comparativo `routes` vs `tools` con SC-001…SC-008 y costo.

**Independent Test**: `carvajal eval-agent --mode routes --mode tools` produce el informe de `contracts/eval-agent.md` con ambos modos sobre el mismo corpus y catálogo.

### Tests for User Story 5

- [X] T045 [P] [US5] Pruebas sin red del runner en `tests/test_eval_agent.py`: verificaciones por ítem (`tools_any`, `tools_none`, `abstain`, `asks_for`, `forbidden_materials`, `total_pending`, `language`, `no_tool_allowed`), cifras de la respuesta presentes en resultados de herramientas, mapeo ruta→herramientas en modo `routes`, agregados de tokens y costo.

### Implementation for User Story 5

- [X] T046 [US5] Implementar el runner en proceso (persistencia en memoria, ambos grafos construidos) en `packages/core/src/carvajal/agent/toolagent/evaluation.py`. Depende de T045.
- [X] T047 [US5] Añadir `carvajal eval-agent --mode … --questions … --output … [--langsmith]` en `packages/core/src/carvajal/cli.py` y objetivo `make eval-agent` en `Makefile`. Depende de T046. FR-017.
- [X] T048 [US5] Actualizar `.claude/skills/evaluar-agente/SKILL.md` para incluir `eval-agent` y los criterios SC-001…SC-008. Depende de T047.
- [ ] T049 [US5] Correr la evaluación completa (con permiso del owner por costo de DeepSeek) y guardar el informe en `specs/044-agente-tool-calling/evidencia.md`, con la limitación de origen del conjunto (FR-016) y la medición de SC-006/SC-006a en el chat real. Depende de T003 y de las fases 3–6.

**Checkpoint**: el owner decide con el informe si `AGENT_MODE` pasa a `tools`.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T050 [P] Verificar que `CONSTITUTION.md` refleja lo entregado (estado técnico y spec 044, §3 stack si aplica); las enmiendas de principio ya entraron con T005.
- [ ] T051 [P] Documentar `AGENT_MODE`, límites y respaldo en `docs/despliegue-ovh.md`, `README.md` y `AGENTS.md` (sección de comandos).
- [ ] T052 [P] Actualizar `specs/README.md` y el estado de `specs/044-agente-tool-calling/spec.md` con la evidencia.
- [ ] T053 Ejecutar `specs/044-agente-tool-calling/quickstart.md` completo y `make check`; registrar resultados en `specs/044-agente-tool-calling/evidencia.md`.
- [ ] T054 Abrir las PRs por fase con la skill `abrir-pr` (sin merge).
- [ ] T055 F5 — Retiro (diferido, PR propio tras el cambio de valor por defecto y dos semanas de observación según el plan): retirar redacción por rutas, `commercial_mixed`, `LLMRouter`, `DecisionRouter` y regex sin consumidores en `packages/core/src/carvajal/agent/`; reducir `decide_route` a atajo y respaldo; actualizar `CONSTITUTION.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)**: T001, T002 sin dependencias; **T003 (congelamiento) bloquea las fases 3–7**.
  **T005 (enmiendas a la constitución) bloquea el merge** de todo PR con código del modo `tools`; el desarrollo puede avanzar en paralelo.
- **Foundational (2)**: depende de T001; T006 → T007 → T008 en serie (mismo motor).
- **US1 (3)**: depende de la fase 2. Es la base técnica de US2–US5.
- **US2 (4)**: depende de T023 y T025 (grafo y adaptador). Con US1 forma el MVP.
- **US3 (5)**: depende de T023; independiente de US2 y US4.
- **US4 (6)**: depende de T026; T042 → T043.
- **US5 (7)**: T045–T048 pueden empezar tras T023; T049 necesita las fases 3–6 y T003.
- **Polish (8)**: tras US5; T055 queda fuera de esta entrega.

### Within Each User Story

- Pruebas primero (deben fallar), luego herramientas/nodos, luego servicio y UI.
- `graph.py`, `tools.py`, `agent.py` y `service.py` los toca una persona a la vez.

### Parallel Opportunities

- Fase 1: T002 y T004 en paralelo con T001.
- Fase 2: T009, T010, T011 y T012 en paralelo, a la vez que la cadena T006 → T008.
- US1: T014, T015, T016 en paralelo; luego T017, T018, T020, T021, T022 en paralelo (T019 espera T008); T027 en paralelo con T025–T026.
- US2 y US3 pueden avanzar en paralelo tras T025.
- US4: T036, T037, T038 en paralelo.

## Parallel Example: User Story 1

```text
# Pruebas de US1 en paralelo:
T014 tests/test_tool_agent_tools.py
T015 tests/test_tool_agent_graph.py
T016 tests/test_tool_agent_stream.py

# Herramientas en paralelo (mismo archivo tools.py: dividir en funciones y unir al final,
# o asignar una persona por herramienta en ramas cortas):
T017 buscar_corpus · T018 buscar_producto · T020 buscar_sedes + indicador_financiero · T021 sin_consulta
T022 status.py
```

## Implementation Strategy

### MVP First (US1 + US2)

1. Fase 1 con el conjunto congelado (T003).
2. Fase 2 (incluye el `fix:` de «tinto», que puede salir como PR propio a `main` de inmediato).
3. US1 + US2 → validar con `AGENT_MODE=tools` en local y con `tests/test_hard_rules_modes.py` verde en ambos modos.
4. **Parar y validar**: quickstart pasos 1–5.

### Incremental Delivery

1. Fase 2 → PR `fix:` «tinto» + PR del motor estructurado.
2. US1 + US2 → PR del agente (modo `tools` apagado por defecto).
3. US3 → PR de conversación multi-turno.
4. US4 → PR de operación (atajo, respaldo, folios, monitoreo).
5. US5 + Polish → PR de evaluación, informe y enmiendas.
6. Decisión del owner sobre el valor por defecto; F5 (T055) después de la observación.

## Notes

- `[P]` = archivos distintos y sin dependencias pendientes.
- Ningún PR cambia el valor por defecto de `AGENT_MODE`; eso lo decide el owner con el informe de T049.
- Commit por tarea o grupo lógico; `make check` en verde antes de cada PR.
