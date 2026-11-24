---

description: "Lista de tareas de la spec 046 — Router y System One (Jev)"
---

# Tasks: Router y System One (Jev)

**Input**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo de datos](data-model.md), [contrato](contracts/jev-gatekeeper.md) y
[validación](quickstart.md).

> **Se ejecuta sobre la rama de la 045.** La implementación parte del último commit de
> `feat/045-memoria-herramientas` (`88ee0d6` o posterior; hoy `314f9a7`), rebasada antes sobre
> `main`, porque le faltan `dd35bfc` (webhook firmado), `901f59f` y `921460d` (registro `TOOLS` con 20 `ToolSpec`, `evidence_role`,
> `agent/memory/sensitive.py`, `agent/memory/store.py`). Si la 045 sigue abierta, la 046 puede
> entregarse en la misma PR (pedido del owner de juntar PRs); si ya se mergeó, se rebasa
> `feat/046-jev-router` sobre `main`. Ninguna tarea se empieza sobre `main` sin la 045.

**Tests**: incluidos. La spec exige pruebas sin red (FR-029) y la constitución `make check`
en verde. En cada historia las pruebas se escriben primero y deben fallar antes de
implementar. Ninguna prueba llama a Jev ni a un LLM; las únicas llamadas reales están en la
fase 8, una corrida cada una, con ≤ 120 llamadas a Jev en total (research R11).

**Organization**: por historia de usuario. `[P]` = archivos distintos y sin dependencias
pendientes.

Las tareas T062–T065 se añadieron en la revisión de los artefactos (research R15) y se
ubican en su fase. Los números no se reordenaron para no romper las referencias.

Abreviaturas de rutas: `core/` = `packages/core/src/carvajal/`; `ta/` =
`packages/core/src/carvajal/agent/toolagent/`; `gk/` =
`packages/core/src/carvajal/agent/gatekeeper/`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: ajustes, aislamiento de las pruebas, despliegue, conjunto del gatekeeper y la
investigación previa.

- [X] T001 Añadir los ajustes `JEV_*` y `TYPESAFE_API_KEY` (`SecretStr`) del data-model, con `JEV_ENABLED=false` por defecto, `JEV_LEDGER`, rangos, la validación `JEV_ROUTER_DIRECT_MIN_CONFIDENCE ≥ JEV_ROUTER_MIN_CONFIDENCE` y la propiedad `jev_active = JEV_ENABLED and bool(clave)`, en `packages/core/src/carvajal/infrastructure/settings.py`; documentarlos en `.env.example`; probarlos (valores por defecto, `JEV_ENABLED` sin definir con clave → apagado, rangos, clave nunca en `repr`) en `tests/test_settings.py`. FR-022, FR-031.
- [X] T002 [P] Pasar `TYPESAFE_API_KEY` y las variables `JEV_*` al contenedor de la app en `compose.prod.yml` (sin valores por defecto que contradigan `settings.py`) y verificarlo sin red en `tests/test_deploy_packaging.py`. FR-022.
- [X] T003 [P] Añadir en `tests/conftest.py` una fixture *autouse* que fija `JEV_ENABLED=false` y `TYPESAFE_API_KEY=""` con `monkeypatch` y hace fallar cualquier conexión de httpx a `api.typesafe.ai`; probar que la fixture actúa aunque el `.env` traiga la clave en `tests/test_jev_guard.py`. SC-008, research R13.
- [X] T004 [P] Redactar y congelar `data/eval/gatekeeper_046.json` (20 ítems: 7 `herramienta_estructurada` con c-54, 6 `rag`, 2 `mixta` —c-52 y c-53—, 5 `respuesta_directa`; 15 de `data/eval/routing_questions.json` con su `source` y `rules_route`, 5 redactados con `origin` declarado, uno de ellos un saludo coloquial que las reglas no detectan, «quiubo pues, todo bien?»; `context` donde producción lo enviaría; `frozen_at`) y validar su formato, composición y que cada `source` existe en `tests/test_eval_gatekeeper.py`. FR-026, data-model.
- [X] T005 [P] Crear `docs/research/jev.md` a partir del informe de la prueba del 2026-11-24 (resumen, API, diseño, resultados, costos, riesgos), con la decisión vigente remitida a `specs/046-jev-router/` y la trazabilidad declarada como archivos fuera del repositorio; sin clave ni datos personales. FR-028.
- [X] T006 [P] Registrar la spec 046 en `specs/README.md` con estado «Borrador» (hecho al crear la spec).

**Checkpoint**: ajustes listos, CI aislada de Jev, variables de despliegue y conjunto del
gatekeeper congelado.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: puertos, adaptador de Jev, privacidad, grupos de consultas, canal del estado,
monitoreo y prompts. Sin cambios de comportamiento visibles.

**CRITICAL**: ninguna historia empieza antes de terminar esta fase.

- [X] T007 [P] Escribir `tests/test_jev_adapter.py` con `httpx.MockTransport`: cuerpo exacto de Choice (con y sin `contexto`, con y sin Score) y de Noul según el contrato §2–§3; cabecera `Authorization` presente y clave ausente de excepciones, `repr` y logs; respuestas 200 válidas e inválidas (opción desconocida, probabilidades incompletas, sin `confidence` → mayor probabilidad, `noul` fuera de rango); 400, 401, 402, 403, 422, 429, 500, 529 y error de red → `JudgeUnavailable` con su `JevStatus`; demora mayor que `JEV_TIMEOUT_S` → `timeout` en ≤ plazo + 0,1 s; tope diario (`cap`, reinicio a medianoche de Bogotá con reloj inyectado); circuito (3 fallos → 60 s; 401/402/403 → 15 min y un solo log `jev.auth_or_credits`); sin clave → `available() is False` y cero peticiones; `JudgeCall` con el modelo devuelto y los tokens; Score con `clarity` entero en [0, 4] y `clarity_confidence`; con `JEV_LEDGER` definido, cada llamada (también las fallidas) añade una línea sin texto al libro. Contrato §2–§4.
- [X] T008 Añadir `JudgeCall`, `RouteQuery`, `RouteVerdict`, `ClaimsQuery`, `ClaimsVerdict`, `JudgeUnavailable`, `RouteJudge` y `ClaimsJudge` en `packages/core/src/carvajal/application/ports.py`, con los tipos `GateClass`, `GateScope` y `JevStatus` definidos en el puerto (sin dependencias) (la capa de aplicación no importa de `agent/`; `gk/model.py` los reexporta). Data-model *Puerto del juez*.
- [X] T009 Implementar `JevClient` (httpx con conexiones reutilizadas, plazo total con ejecutor pequeño y `future.result(timeout=…)`, sin reintentos, tope diario, circuito, `last_error`, `last_model`, anotación en el libro `JEV_LEDGER`) y `JevJudge` (`classify`, `check`, `available`) en `packages/core/src/carvajal/adapters/judge/jev.py`, y `build_judge(settings)` y `jev_health(settings, judge)` en `packages/core/src/carvajal/adapters/judge/__init__.py`. Depende de T001, T007, T008. FR-003, FR-023, research R9.
- [X] T010 [P] Escribir `tests/test_gatekeeper_privacy.py` (unidad): `outgoing_question` redacta las ≥ 30 frases sensibles de `tests/test_memory_sensitive.py` (045) y su `redact` no toca las ≥ 30 válidas; enmascara nombre y empresa declarados con marcador fuerte («soy Ana, trabajo en Panadería La Espiga…», «llámame Ana», «mi empresa es…») y los valores de `nombre`, `empresa` y notas de la memoria; no enmascara ciudades ni el tipo de negocio sin nombre propio («tengo una cafetería en Cali, ¿qué vasos me sirven?» sale igual, aunque `classify` devuelva `{'empresa': 'cafetería en Cali'}`); recorta a 1 000 y 160 caracteres; `outgoing_answer` conserva los teléfonos del directorio («(601) 555 1234», «+57 1 555 1234») y sustituye solo los tramos sensibles de la pregunta, los valores de la memoria y los folios; `context_line` solo con pregunta elíptica o de ≤ 6 palabras y con turnos previos. FR-017, FR-018, FR-019, research R8.
- [X] T011 Implementar `outgoing_question(text, memory)`, `outgoing_answer(answer, question, memory, payloads)`, `sensitive_spans(text)` (mismos patrones de `sensitive.py`) y `context_line(question, turns, memory)` en `packages/core/src/carvajal/agent/gatekeeper/privacy.py` (con `packages/core/src/carvajal/agent/gatekeeper/__init__.py` vacío), sobre `redact` y `classify` de la 045. Depende de T010.
- [X] T012 [P] Ampliar `tests/test_tool_registry.py`: cada `ToolSpec` tiene `route_group`; solo `sin_consulta` es `any`; `rag ∪ structured ∪ any` = las 20; la asignación de research R3; `TOOLS_BY_SCOPE` en el orden de `TOOLS`, de solo lectura, con `direct` vacío. Data-model *Grupo de cada consulta*.
- [X] T013 Añadir `route_group` a `ToolSpec` y a las 20 entradas de `TOOLS`, y la vista `TOOLS_BY_SCOPE` (tipada con `GateScope` importado solo de `application/ports.py`), en `ta/tools/registry.py` (reexportada en `ta/tools/__init__.py`). Depende de T012. FR-004.
- [X] T014 [P] Escribir `tests/test_gatekeeper_model.py`: `GatekeeperDecision` ida y vuelta como JSON (`to_value`/`from_value`, versión desconocida → `None`), mapa clase → alcance y `map_route` para todas las rutas e intenciones de `core/agent/routers/_payload.py:INTENTS`. Data-model.
- [X] T015 Implementar `GateSource`, `ClaimsStatus`, `GatekeeperDecision` (con `llm_class`) y `map_route` en `gk/model.py`, reexportando `GateClass`, `GateScope` y `JevStatus` de `application/ports.py`. Depende de T014.
- [X] T016 Añadir el canal `gatekeeper: dict` a `ToolAgentState` en `ta/state.py` y reiniciarlo a `{}` en `recall` de `ta/graph.py`; prueba de ida y vuelta con el serializador del checkpointer en `tests/test_tool_agent_state.py`. Depende de T015.
- [X] T017 [P] Añadir las claves nuevas de data-model *Eventos y metadatos* a `SAFE_KEYS` y una función `record_judge_calls(turn, calls)` (un hijo `jev`, `run_type="tool"`, solo metadatos aun con `content=full`) en `packages/core/src/carvajal/infrastructure/monitoring.py`, con pruebas en `tests/test_monitoring_tools.py`. FR-021, FR-025.
- [X] T018 [P] Añadir `JEV_ROUTE_INSTRUCTIONS`, `JEV_ROUTE_CRITERIA`, `JEV_SCORE_CLARITY`, `JEV_CLAIMS_INSTRUCTIONS`, `JEV_CLAIMS_CRITERIA`, `GATEKEEPER_CAUTION` y `DIRECT_INSTRUCTIONS["gatekeeper_direct"]` con los textos del contrato en `packages/core/src/carvajal/prompts.py`; probar en `tests/test_prompts.py` que las cuatro opciones van en el orden del contrato, que `GATEKEEPER_CAUTION` incluye la regla de conversación (`sin_consulta`, motivo `conversacion`) y que los criterios de Noul conservan el texto medido el 2026-11-24, con la referencia a `pregunta` cambiada a `dicho_por_la_persona`, más las reglas marcadas con ⁺ en el contrato. Contrato §2, §3, §7.
- [X] T062 Escribir `tests/test_gatekeeper_imports.py`: importar en un subproceso aislado cada módulo nuevo (`adapters/judge/jev.py`, `adapters/judge/__init__.py`, `gk/model.py`, `gk/privacy.py` y, al crearlos, `gk/rules.py`, `gk/decide.py`, `gk/claims.py`, `gk/evaluation.py`) y `ta/tools/registry.py`, para detectar ciclos (`registry → gatekeeper → toolagent`). Depende de T013, T015. Research R1.

**Checkpoint**: Jev accesible detrás de un puerto, privacidad y grupos listos, canal en el
estado. La suite completa sigue verde.

---

## Phase 3: User Story 1 - Elegir el camino del turno sin buscar en saludos (Priority: P1) MVP

**Goal**: reglas primero; si no deciden, una pregunta Choice de Jev restringe las consultas
del turno; sin Jev, reglas + LLM.

**Independent Test**: doble de Jev con clase y confianza fijas y `ScriptedToolChat`: cada
clase entrega al modelo el alcance esperado; el saludo no llama a Jev ni consulta nada.

### Tests for User Story 1

- [X] T019 [P] [US1] Escribir `tests/test_gatekeeper_rules.py`: `rule_decision` (pura) devuelve `direct` con `rule` `greeting`, `farewell` o `memory_forget` (los mismos casos que hoy prueba `shortcut_intent`; nunca `memory_unavailable`, que depende del Store), `all` con `memory_write`, `structured` con `commercial_follow_up`, y `None` para «Hola, ¿cuánto vale la caja de vasos de 9 oz?», «¿Qué es Propal?» y «Cotízame 500 vasos en Cali». FR-002, SC-002.
- [X] T020 [P] [US1] Escribir `tests/test_gatekeeper_decide.py` (parte 1) con un `FakeJudge` que registra llamadas: cada clase con confianza 0,9 → su alcance y `source=jev`, `reason=aplicada`; una sola llamada por turno; turnos de regla → cero llamadas; `JEV_ROUTER_MODE=shadow` → `scope=all`, `reason=sombra`, una llamada; `off` → cero llamadas y `source=llm`; `JudgeUnavailable` de cada `JevStatus` → `scope=all`, `source=llm`, `reason=sin_jev:<estado>`; sin juez → `sin_jev:off`; la `RouteQuery` lleva el texto de `outgoing_question`. FR-001, FR-003, FR-006, FR-010.
- [X] T021 [P] [US1] Escribir `tests/test_tool_agent_gatekeeper.py` (parte 1) sobre el grafo con `ScriptedToolChat` y `FakeJudge`: con `rag`, `structured` y `mixta`, los nombres de herramientas atadas en cada paso son exactamente `TOOLS_BY_SCOPE[scope]`; con `respuesta_directa` el turno va al nodo `direct` sin herramientas, `mode_path="shortcut"` y `shortcut_intent="gatekeeper_direct"`; una llamada fuera del alcance devuelve el error `fuera_de_alcance`, gasta cupo y no ejecuta la herramienta; el bloque `<alcance_del_turno>` va al final del sistema y `AGENT_SYSTEM` sigue siendo su prefijo; «Hola» → cero llamadas al juez y cero `ToolMessage`; «olvida todo» con un Store que falla al borrar → `rule="memory_unavailable"` y `shortcut_intent` reescrito por el nodo `shortcut`. FR-002, FR-004, SC-002.

### Implementation for User Story 1

- [X] T022 [US1] Mover `shortcut_intent`, `is_closing` y sus ayudantes de `ta/graph.py` a `gk/rules.py` (reexportados desde `ta/graph.py` para no romper importaciones ni pruebas) y añadir `rule_decision(question, turns, commercial_context)` (pura: devuelve `memory_forget`; el nodo `shortcut` aplica el olvido y reescribe a `memory_unavailable` si falla, como hoy) con las reglas de preferencia (`decide_route` → `memory_write`) y seguimiento comercial (`_commercial_follow_up`). Depende de T015, T019. FR-002.
- [X] T023 [US1] Implementar `decide_gate(question, turns, commercial_context, memory, judge, settings)` en `gk/decide.py`: reglas → Jev (una llamada, modo `on`/`shadow`/`off`) → LLM, con `rules_route` de `decide_route` y el log `gatekeeper.decided` sin texto; sin umbrales todavía (toda clase de Jev se aplica). Depende de T009, T011, T020, T022. FR-001, FR-003, FR-006, FR-010.
- [X] T024 [US1] Integrar el gatekeeper en `ta/graph.py`: `build_tool_agent(..., judge=None)`; `shortcut` llama a `decide_gate` y escribe `gatekeeper`, `mode_path` y `shortcut_intent`; modelos atados una vez por alcance (`all`, `rag`, `structured`; primer paso con `tool_choice="required"`); `agent` elige el del alcance y añade `<alcance_del_turno>` al final del sistema; `tools_node` rechaza las llamadas fuera del alcance (`tool_agent.out_of_scope_call`); `direct` usa `DIRECT_INSTRUCTIONS["gatekeeper_direct"]` cuando decidió Jev. Depende de T013, T016, T018, T021, T023. FR-004.
- [X] T025 [US1] Construir el juez con `build_judge(settings)` y pasarlo a `build_tool_agent` en `apps/api/agent_runtime.py` (`AgentRuntime.judge`) y en el armado del ejecutor de evaluación de `ta/evaluation.py`, de modo que `eval-agent` use el mismo gatekeeper que producción, pero solo cuando recibe `--jev-ledger` (sin él, sin juez aunque haya clave; FR-031). Depende de T024.
- [X] T026 [US1] Aplicar la enmienda D (gatekeeper del modo `tools` con Jev opcional, minimización y registros sin texto; `routes` sin Jev) en `CONSTITUTION.md` y `.specify/memory/constitution.md` (versión 2.9.0 compartida con la enmienda E: abre un único Sync Impact Report que T038 actualiza; fila en el historial de decisiones), en el mismo commit que T022–T025. Depende de T025. Plan *Enmienda D*.

**Checkpoint**: el gatekeeper decide y restringe; sin Jev, todo como en la 045.

---

## Phase 4: User Story 2 - Confianza baja: responder con cautela o pedir aclaración (Priority: P1)

**Goal**: umbrales, regla de desacuerdo y cautela con las 20 consultas y su instrucción.

**Independent Test**: doble de Jev con confianza 0,4 → 20 consultas, bloque `<cautela>` y
`cautious: true`; `respuesta_directa` con 0,7 → no va al nodo directo.

### Tests for User Story 2

- [X] T027 [P] [US2] Ampliar `tests/test_gatekeeper_decide.py` (parte 2): confianza 0,59 → `cautious`, `scope=all`, `confianza_baja`; `respuesta_directa` con 0,7 → `confianza_baja_directa` y nunca `direct`; reglas `commercial`/`tool`/`commercial_clarify`/`missing_period` con Jev `rag` o `respuesta_directa` a 0,95 → `desacuerdo_reglas`; reglas `commercial_mixed` con cualquier clase → `scope=all`, `mixta_reglas`; sin `confidence` → mayor probabilidad; umbrales leídos de los ajustes. FR-005, FR-007.
- [X] T028 [P] [US2] Ampliar `tests/test_tool_agent_gatekeeper.py` (parte 2): con cautela, el modelo recibe las 20 consultas y el bloque `<cautela>` con `GATEKEEPER_CAUTION` después de `<alcance_del_turno>`; un `ScriptedToolChat` que llama a `sin_consulta(motivo="pedir_dato")` termina en la petición neutra (`ask_for_missing`) y no en la abstención; con un saludo coloquial que las reglas no detectan («quiubo pues, todo bien?») y un `FakeJudge` con `respuesta_directa` a 0,7 → cautela, el bloque `<cautela>` lleva la regla de conversación y un `ScriptedToolChat` que la sigue (`sin_consulta(motivo="conversacion")`) termina sin ninguna consulta con datos. FR-005, SC-002.

### Implementation for User Story 2

- [X] T029 [US2] Añadir a `decide_gate` en `gk/decide.py` los umbrales `JEV_ROUTER_MIN_CONFIDENCE` y `JEV_ROUTER_DIRECT_MIN_CONFIDENCE`, la regla de desacuerdo, `mixta_reglas` y la cautela. Depende de T023, T027. FR-005, research R4.
- [X] T030 [US2] Añadir el bloque `<cautela>` al final del sistema cuando `gatekeeper.cautious` en `ta/graph.py` (`agent` y el reemplazo del `SystemMessage` de `tools_node`, para que no se pierda al cambiar la memoria). Depende de T024, T028, T029.

**Checkpoint**: confianza baja nunca adivina el camino.

---

## Phase 5: User Story 3 - Ninguna afirmación sin fuente llega a la persona (Priority: P1)

**Goal**: compuerta Noul tras la determinista, con retención completa y el mensaje neutro
existente como sustituto.

**Independent Test**: doble de Jev con p = 0,2 → se emite y persiste el texto neutro y
ningún fragmento del modelo; p = 0,8 → el texto del modelo; Jev caído → el texto que pasó la
compuerta determinista.

### Tests for User Story 3

- [X] T031 [P] [US3] Escribir `tests/test_claims_gate.py`: `build_claims_query` toma los datos con el criterio de `_tool_backing` (sin `message`, `aviso`, `busqueda_nombre`), excluye `sin_consulta`, las de memoria y los `error`, del corpus solo los fragmentos citados `[n]` (todos si no cita), reparte `JEV_MAX_EVIDENCE_CHARS` con «…»; la carga no lleva la pregunta y sí el extracto `dicho_por_la_persona` con solo los valores estructurados de la extracción de las reglas (omitido si no hay ninguno); la respuesta pasa por `outgoing_answer`: el teléfono del directorio de la respuesta se conserva («(601) 555 1234» llega igual) y el celular de la persona repetido en la respuesta se redacta; los folios de `consultar_cotizacion`/`cotizar` llegan como `[folio]` en la evidencia y en la respuesta; `judgeable` reproduce la tabla del contrato §3 (atajo, respuesta directa de Jev → `skipped`/`directa`, abstención, reemplazo, memoria, solo preguntas, demasiado larga); `only_questions`. FR-011, FR-018.
- [X] T032 [P] [US3] Ampliar `tests/test_tool_agent_stream.py` con un `FakeClaimsJudge`: camino `evidence` con p = 0,2 → ningún `ToolAgentToken` con texto del modelo, se emite el sustituto (abstención sin fuentes o reemplazo con fuentes, según el turno), `claims.substituted=true`; p = 0,8 → el texto completo en un solo bloque tras el veredicto y la línea de estado de revisión una sola vez; `JudgeUnavailable` → el texto del modelo y `claims.status="unavailable"`; compuerta `off` o juez no disponible al empezar → retención de 48 caracteres como hoy (las pruebas de la 044 siguen verdes); `no_query` rechazado por el filtro determinista → sin llamada; atajo por reglas y turno solo de memoria → sin llamada; turno con `needs_input` rechazado → `ask_for_missing`; respuesta directa de Jev → sin llamada (`skipped`/`directa`); con reloj inyectado, un turno con evidencia cuya redacción termina con menos de `JEV_TIMEOUT_S` + 0,2 s de plazo → sin llamada, `unavailable`/`sin_plazo` y se emite el texto de la compuerta determinista (no `TurnTimeout`), y con plazo suficiente `begin_text()` se marca antes de la llamada; `ToolAgentFinal.gatekeeper.llm_class` derivado de los `route_group` ejecutados cuando `source=llm`. FR-006, FR-012, FR-014, FR-015, FR-016, FR-030.
- [X] T033 [P] [US3] Ampliar `tests/test_chat_service_tools.py`, `tests/test_mcp_whatsapp.py` y `tests/test_eval_agent.py`: con p = 0,2, `ChatService.ask`, el puente MCP y el ejecutor de evaluación reciben solo el texto sustituto (sin concatenar el del modelo), el mensaje persistido es el sustituto y el turno cierra con `done`. FR-012.

### Implementation for User Story 3

- [X] T034 [US3] Implementar `build_claims_query(answer, question, payloads, memory, rules_extraction, settings)` (la pregunta no va en la carga; `outgoing_answer`, `dicho_por_la_persona`, `[folio]`), `judgeable(...)` y `only_questions(text)` en `gk/claims.py`. Depende de T011, T031. FR-011, FR-018.
- [X] T035 [US3] Integrar la compuerta en `_TurnStream` de `apps/api/chat/agent.py`: `ToolAgentRunner(..., claims_judge=None)`; compuerta activa si `JEV_CLAIMS_MODE=on` y `claims_judge.available()` al empezar el turno; en `evidence` con compuerta activa, retener el paso final entero y emitir una vez la línea de estado de `ta/status.py` (plantilla nueva `REVIEW_STATUS`); al cerrar el paso, tras la decisión determinista, juzgar con `build_claims_query` y, si `p < JEV_CLAIMS_THRESHOLD`, sustituir con `_replacement()` o `_abstain()`; `ToolAgentFinal.claims` y `ToolAgentFinal.gatekeeper`; log `claims.judged` sin texto; llamar a Noul solo si queda plazo del turno mayor que `JEV_TIMEOUT_S` + 0,2 s (si no, `unavailable`/`sin_plazo`) y marcar `begin_text()` antes de la llamada cuando ya hay texto final; derivar `llm_class` al cerrar el turno. Depende de T009, T032, T034. FR-006, FR-011–FR-016, FR-030, research R6, R7.
- [X] T036 [US3] Pasar el juez a `ToolAgentRunner` en `default_tool_agent_runner` (`apps/api/chat/agent.py`) desde `AgentRuntime.judge`, y en el ejecutor de evaluación de `ta/evaluation.py`. Depende de T025, T035.
- [X] T037 [US3] Congelar `data/eval/claims_046.json` (16 ítems de research R11: 8 originales de la v3-r4 y 8 perturbadas de la prueba del 2026-11-24; evidencia reconstruida con `run_tool` y armada con `build_claims_query`, que deriva `dicho_por_la_persona` de la pregunta del ítem; etiquetas con nota) y validar formato, composición (6 `supported`, 10 `unsupported`) y ausencia de datos personales en `tests/test_eval_gatekeeper.py`. Depende de T034. FR-026.
- [X] T038 [US3] Aplicar la enmienda E (compuerta de afirmaciones tras la determinista, umbral 0,5, retención completa con la compuerta activa, *fail-open* a la determinista) en `CONSTITUTION.md` y `.specify/memory/constitution.md` (fila en el historial y actualización del mismo Sync Impact Report de la 2.9.0 que abrió T026; si la 045 cambió la versión antes del merge, recalcularla al rebasar), en el mismo commit que T034–T036. Depende de T036. Plan *Enmienda E*.

**Checkpoint**: una valoración sin fuente ya no llega a la persona; sin Jev, todo igual.

---

## Phase 6: User Story 4 - Funciona con o sin Jev y a TypeSafe solo va lo mínimo (Priority: P1)

**Goal**: demostrar de extremo a extremo el respaldo y la minimización.

**Independent Test**: grafo y streaming con cada estado de fallo; doble que registra las
cargas a lo largo de turnos con memoria y datos sensibles.

### Tests for User Story 4

- [X] T039 [P] [US4] Ampliar `tests/test_tool_agent_gatekeeper.py` y `tests/test_tool_agent_stream.py` con un parámetro por `JevStatus` de fallo (`off`, `cap`, `circuit_open`, `timeout`, `error`, `auth`, `invalid`) y sin juez: herramientas atadas = las 20, sin bloques nuevos en el sistema, retención de 48 caracteres, `claims.status` `off` o `unavailable`, y la misma respuesta que con la 045 para el mismo guion. FR-006, FR-015, SC-005.
- [X] T040 [P] [US4] Ampliar `tests/test_gatekeeper_privacy.py` (extremo a extremo): con un juez que registra cada `RouteQuery` y `ClaimsQuery`, recorrer turnos web y WhatsApp (`owner_key` de cookie y `wa_…`) con memoria guardada (nombre, empresa, notas), resumen de la conversación, contexto comercial las ≥ 30 frases sensibles y una cotización guardada consultada por folio, y comprobar que ninguna carga contiene `owner_key`, `thread_id`, el número, los valores de la memoria, el texto del resumen, respuestas anteriores, la pregunta completa en la compuerta, el folio ni un dato sensible de la persona sin redactar. FR-017–FR-020, SC-007.
- [X] T041 [P] [US4] Ampliar `tests/test_monitoring_tools.py` y escribir `tests/test_jev_no_text.py` (archivo propio, para no compartir `tests/test_gatekeeper_privacy.py` con T040): con `content=full` y `content=metadata`, los hijos `jev`, la raíz del turno, los logs capturados de structlog y el evento `trace` no contienen ningún texto de las cargas (pregunta, contexto, respuesta, evidencia); una excepción forzada en `gk/claims.py` y en el adaptador, capturada con un transporte de Sentry falso, no lleva variables locales ni textos. FR-021, SC-007.
- [X] T042 [P] [US4] Ampliar `tests/test_chat_service_tools.py`: el respaldo `routes` tras un fallo de DeepSeek, el modo `routes` configurado y la réplica de un `client_turn_id` y un reintento del webhook firmado `/api/webhooks/whatsapp` con el mismo `message_id` no llaman al juez. FR-009.

### Implementation for User Story 4

- [X] T043 [US4] Ajustar lo que T039–T042 revelen en `gk/decide.py`, `gk/privacy.py`, `apps/api/chat/agent.py`, `apps/api/chat/service.py` y `apps/api/observability.py` (disponibilidad evaluada una vez por turno, camino `routes` sin juez, réplica sin juez, excepciones sin `exc_info` en el adaptador y en `gk/*`, Sentry con `include_local_variables=False` o un `before_send` que elimina esos frames), sin relajar las pruebas. Depende de T039–T042.

**Checkpoint**: con o sin Jev el asistente responde; a TypeSafe solo va lo mínimo.

---

## Phase 7: User Story 5 - Ver y medir la decisión (Priority: P2)

**Goal**: traza, LangSmith, `/health`, panel de depuración y comandos de medición.

**Independent Test**: un turno con `?debug=1` muestra la decisión; `/health` trae `jev`; los
comandos de evaluación producen informes y un libro sin texto con dobles.

### Tests for User Story 5

- [X] T044 [P] [US5] Ampliar `tests/test_chat_service_tools.py`: el evento `trace` del modo `tools` trae `gatekeeper` y `claims` con la forma del contrato §6 en atajo, Jev, cautela, sin Jev (con `llm_class`) y respaldo; en `routes`, `gatekeeper` con `source="reglas"` y `gate_class` de `map_route`, y `claims.status="off"`. FR-009, FR-025, SC-009.
- [X] T045 [P] [US5] Ampliar `tests/test_monitoring_tools.py`: la raíz del turno lleva `gatekeeper_*` y `claims_*` y un hijo `jev` por llamada con los campos de `JudgeCall`. FR-025, SC-009.
- [X] T046 [P] [US5] Ampliar `tests/test_api_health.py`: el bloque `jev` existe con y sin runtime construido, con clave y sin ella, con circuito abierto y con el tope alcanzado (`available=false`), y `/health` no hace peticiones a TypeSafe. FR-024, SC-009.
- [X] T047 [P] [US5] Ampliar `tests/test_eval_gatekeeper.py` y `tests/test_eval_agent.py` con dobles: `eval-gatekeeper` calcula exactitud por juez (reglas, LLM, Jev), exactitud de las decisiones aplicadas por umbral (rejilla 0,5–0,8), cobertura, preguntas con datos enviadas a `respuesta_directa` y la comparación confianza frente a Score; `eval-claims` calcula detección y falsas alarmas por umbral; ambos escriben el libro sin texto y se detienen en `--max-jev-calls`; `ModeReport` de `eval-agent` informa llamadas, latencias p50/p95, tokens y estados de Jev, decisiones por alcance, cautelas y sustituciones, y distingue el atajo por reglas de la respuesta directa de Jev (por `gatekeeper.source`): la comprobación `shortcut` y las métricas del atajo de la 044 solo cuentan las reglas, y un ítem con datos enviado a `respuesta_directa` por Jev se informa como error del gatekeeper; `eval-agent` sin `--jev-ledger` no construye juez, y con él respeta `--max-jev-router-calls` y `--max-jev-claims-calls` (turnos sin decisión por cuota declarados y fuera del denominador de SC-004); `ReplayJudge` reproduce veredictos del libro por ítem sin red. FR-027, FR-031, FR-032, SC-004, SC-008.

### Implementation for User Story 5

- [X] T048 [US5] Añadir `gatekeeper` y `claims` al evento `trace` (modo `tools`, respaldo y modo `routes` con `map_route`), los metadatos de la raíz y `record_judge_calls` en `apps/api/chat/service.py`. Depende de T017, T036, T044, T045. FR-025.
- [X] T049 [P] [US5] Añadir el bloque `jev` a `/health` (y a su rama `degraded`) con `jev_health` en `apps/api/main.py`. Depende de T009, T046. FR-024.
- [X] T050 [P] [US5] Mostrar «Decisión: <alcance o regla> · <fuente> <confianza>» en el panel de depuración (`apps/api/templates/_chat_stream.html`, campo `data-campo="decision"`, y `pintarTraza` en `apps/api/static/chat-stream.js`), solo con `?debug=1`. Depende de T048. FR-025.
- [X] T051 [US5] Implementar `eval_gatekeeper` y `eval_claims` (informes Markdown y JSON, libro `jev-calls.jsonl`, `--smoke` con `GET /v1/models`, `--max-jev-calls`, Score solo aquí) en `gk/evaluation.py`, con `ReplayJudge` y las opciones `--replay-ledger`, `--agent` y `--only` de `eval-gatekeeper`; los comandos `eval-gatekeeper` y `eval-claims` y las opciones `--jev-ledger`, `--max-jev-router-calls` y `--max-jev-claims-calls` de `eval-agent` en `packages/core/src/carvajal/cli.py`; las métricas de Jev y la distinción reglas/Jev del camino directo en `ModeReport` de `ta/evaluation.py`; y, en el mismo commit, `.claude/skills/evaluar-agente/SKILL.md` (Jev apagado por defecto; `--jev-ledger` solo con permiso del owner y dentro del presupuesto de research R11). Depende de T047. FR-026, FR-027, FR-031, FR-032.

**Checkpoint**: la decisión se ve y se puede medir sin gastar créditos en pruebas.

---

## Phase 8: Medición, evidencia y entrega

**Purpose**: las únicas llamadas reales (una corrida cada una, ≤ 120 a Jev en total, todas en
el libro), la evidencia, las enmiendas finales y la PR.

- [X] T052 Correr los objetivos `check` y `eval-cotizador` del `Makefile` (18/18) sin Jev antes de medir. SC-010.
- [X] T053 Corrida única del humo y del gatekeeper (21 llamadas) y de las reglas sobre las 130 preguntas (0 llamadas), según la sección 4.1 de `specs/046-jev-router/quickstart.md`, con salida en `specs/046-jev-router/evaluacion/`; fijar los umbrales con la regla de research R4 y, si cambian, actualizar `packages/core/src/carvajal/infrastructure/settings.py` y `.env.example` antes de T056. Depende de T051, T052. SC-001. Hecho: la primera corrida dio `invalid` por Score (D1); repetida una vez con 20 llamadas; umbrales sin cambio (0,6 y 0,8), ver `evidencia.md` §2.
- [X] T054 Corrida única de la compuerta sobre `data/eval/claims_046.json` (16 llamadas), sección 4.2 de `specs/046-jev-router/quickstart.md`. Depende de T037, T051. SC-003. Hecho: 10/10 y 0/6, ver `evidencia.md` §3.
- [X] T055 [P] Corrida única del v3 en modo `tools` sin Jev (`JEV_ENABLED=false`, 0 llamadas), sección 4.3 de `specs/046-jev-router/quickstart.md`. Depende de T052. SC-004. Hecho: 29/40, ver `evidencia.md` §4.
- [X] T056 Corrida única del v3 en modo `tools` con Jev (`--jev-ledger`; cuotas de la corrida: 45 del gatekeeper y 30 de la compuerta, ≤ 75 llamadas), sección 4.4 de `specs/046-jev-router/quickstart.md`. Depende de T053, T055. SC-004, SC-006. Hecho: 28/40, 75 llamadas (cuotas agotadas), ver `evidencia.md` §4.
- [X] T065 Corrida única de la reproducción (`ReplayJudge` desde el libro: 0 llamadas a Jev, solo DeepSeek) sobre los ítems de `gatekeeper_046.json` que en T053 terminaron en cautela o en respuesta directa, sección 4.5 de `specs/046-jev-router/quickstart.md`, para medir `GATEKEEPER_CAUTION` y `DIRECT_INSTRUCTIONS["gatekeeper_direct"]`. Depende de T053. FR-032, principio III. Hecho: 5 directas, 0 en cautela, 0 llamadas, ver `evidencia.md` §2.5.
- [X] T057 Auditar cada sustitución de la compuerta y cada turno con cautela del v3 con Jev, y escribir `specs/046-jev-router/evidencia.md` con la tabla SC-001–SC-010, los umbrales elegidos y su justificación, la comparación reglas/LLM/Jev, la decisión sobre Score, el costo el resultado de la reproducción (T065) y el total del libro (≤ 112 tras T053–T056; ≤ 118 con la demostración opcional de T064). Depende de T053–T056 y T065. Hecho: libro en 116 (4 de más por el incidente de Score), ver `evidencia.md`.
- [ ] T058 [P] Registrar en `docs/prompts.md` los textos de Choice, Noul, `GATEKEEPER_CAUTION` y `gatekeeper_direct` con su medición (cautela y directa, con la reproducción de T065; principio III). Depende de T057.
- [ ] T059 Registrar la decisión en el historial de `CONSTITUTION.md` §9 y el estado técnico de la 046 (también en `.specify/memory/constitution.md`), actualizar la fila de la 046 en `specs/README.md` y el `Status` de `specs/046-jev-router/spec.md`. Depende de T057.
- [ ] T063 Revisar el DPA de TypeSafe y su política y plazo de retención, y registrar el resultado en `docs/research/jev.md` antes de activar Jev en producción; si no se puede completar, pedir en la descripción de la PR la aceptación explícita del owner del riesgo declarado (research R8). Depende de T005.
- [ ] T064 (Opcional, solo si el owner la quiere ver) Demostración en vivo de la sección 2 de `specs/046-jev-router/quickstart.md` con `JEV_ENABLED=true` y `JEV_LEDGER` (≤ 6 llamadas presupuestadas, una vez). Depende de T057.
- [ ] T060 Configurar `JEV_ENABLED=true`, `TYPESAFE_API_KEY`, `JEV_MODEL` y los umbrales finales en el `.env` del servidor de producción (fuera del repositorio, con el acceso autorizado por el owner; sin escribir la clave en ningún archivo del repositorio ni en logs). Tras el merge y el despliegue que decide el owner, verificar `/health` (`jev.configured`, `jev.available`, `agent.mode_configured="tools"`) y un turno real con `?debug=1` (2 llamadas, anotadas en el libro), sección 5 de `specs/046-jev-router/quickstart.md`. Depende de T063. SC-008, SC-009.
- [ ] T061 Validar las secciones 1 y 3 de `specs/046-jev-router/quickstart.md`, correr `make check` y abrir la PR con la skill `abrir-pr` (sola o junto con la 045, research R14): etiquetas del catálogo vigente y assignee; el agente se detiene sin mergear. Depende de T057–T059.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (fase 1)**: sin dependencias; requiere la rama de la 045.
- **Foundational (fase 2)**: depende de T001; bloquea todas las historias.
- **US1 (fase 3)**: tras la fase 2. Es el MVP del requisito.
- **US2 (fase 4)**: tras US1 (amplía `decide_gate` y el grafo).
- **US3 (fase 5)**: tras la fase 2; puede avanzar en paralelo con US1 hasta T036, que
  necesita T025.
- **US4 (fase 6)**: tras US1, US2 y US3 (prueba el conjunto).
- **US5 (fase 7)**: tras US3 (T048 necesita `ToolAgentFinal.gatekeeper/claims`).
- **Fase 8**: tras todo lo anterior; T053 → T056 en ese orden (los umbrales se fijan antes
  del v3 con Jev); T065 tras T053; T063 antes de T060; T064 es opcional.

### Parallel Opportunities

```text
# Fase 2, en paralelo:
T007 tests/test_jev_adapter.py        T010 tests/test_gatekeeper_privacy.py
T012 tests/test_tool_registry.py      T014 tests/test_gatekeeper_model.py
T017 infrastructure/monitoring.py     T018 prompts.py

# US1 y US3, pruebas en paralelo:
T019 tests/test_gatekeeper_rules.py   T031 tests/test_claims_gate.py
T020 tests/test_gatekeeper_decide.py  T032 tests/test_tool_agent_stream.py
T021 tests/test_tool_agent_gatekeeper.py

# Implementación en paralelo (archivos distintos):
T024 ta/graph.py                      T035 apps/api/chat/agent.py

# US5:
T049 apps/api/main.py                 T050 _chat_stream.html + chat-stream.js
```

## Implementation Strategy

### MVP First (US1)

1. Fases 1 y 2.
2. US1 → validar la sección 1 del quickstart (sin red). Sin preguntas en vivo con Jev: no
   están en el presupuesto de research R11.
3. **Parar y validar**: `make check` en verde; sin Jev, comportamiento idéntico al de la 045.

### Incremental Delivery

1. Fases 1–2 → commit `feat(agente):` del adaptador de Jev, la privacidad y los grupos de
   consultas (sin cambio visible).
2. US1 + US2 → commit `feat(agente):` del gatekeeper con la enmienda D (T026).
3. US3 → commit `feat(agente):` de la compuerta de afirmaciones con la enmienda E (T038).
4. US4 + US5 → commits `test(agente):` y `feat(chat):` de respaldo, privacidad y
   visibilidad.
5. Fase 8 → commit `docs(spec-046):` de evidencia, prompts e historial; PR a `main` (sola o
   junto con la 045). El owner la revisa y la mergea.

## Notes

- `[P]` = archivos distintos y sin dependencias pendientes.
- Ninguna tarea cambia el corpus, el catálogo ni los precios.
- Las corridas reales (T053–T056, la demostración opcional T064 y la verificación de T060)
  se hacen una vez cada una; el
  libro `specs/046-jev-router/evaluacion/jev-calls.jsonl` es la cuenta del presupuesto y no
  guarda texto.
- La clave de TypeSafe nunca se escribe en el repositorio, en la evidencia ni en los logs.
- Commit por tarea o grupo lógico; `make check` en verde antes de la PR.
