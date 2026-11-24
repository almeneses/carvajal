---

description: "Lista de tareas de la spec 045 — memoria del agente y 20 herramientas"
---

# Tasks: Memoria del agente y 20 herramientas

**Input**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo de datos](data-model.md), contratos ([herramientas](contracts/agent-tools-045.md),
[memoria](contracts/memory.md)) y [validación](quickstart.md).

**Tests**: incluidos. La spec exige pruebas por herramienta y de memoria (FR-029) y la
constitución exige `make check` en verde. En cada historia las pruebas se escriben primero
y deben fallar antes de implementar. Ninguna prueba llama a un LLM ni a servicios externos;
las únicas corridas con proveedor real están en la fase 8 y se hacen una sola vez (R13).

**Organization**: por historia de usuario. `[P]` = archivos distintos y sin dependencias
pendientes. Las 11 herramientas nuevas y las 3 de memoria viven en módulos propios bajo
`packages/core/src/carvajal/agent/toolagent/tools/`, así que su prueba y su implementación
se pueden hacer en paralelo; el registro (`tools/registry.py`) y el `Literal` `ToolName` se
tocan una vez por grupo (T050, T069, T081).

Abreviaturas de rutas: `core/` = `packages/core/src/carvajal/`; `ta/` =
`packages/core/src/carvajal/agent/toolagent/`.

Revisión del 2026-11-24: las tareas se renumeraron al aplicar los hallazgos de la revisión
de artefactos (research R17).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: ajustes, *golden* de las herramientas actuales, soporte del ejecutor de
evaluación y datos que deben existir antes de implementar (F0 del plan).

- [X] T001 Añadir `agent_summary_mode: Literal["llm", "deterministic", "off"] = "deterministic"` (por defecto determinista hasta medir el prompt del resumidor, T097), `agent_summary_timeout_s: float = 8` (1–30), `agent_summary_max_chars: int = 1500` (300–4000), `agent_memory_days: int = 180` (1–730) y `agent_faq_timeout_s: float = 25` (5–40, plazo de `preguntas_frecuentes`, research R18) en `packages/core/src/carvajal/infrastructure/settings.py`, documentarlos en `.env.example` y probarlos en `tests/test_settings.py`. FR-003, FR-004, FR-019, FR-022a.
- [X] T002 [P] Capturar el *golden* de `convert_to_openai_tool` de las seis herramientas de la 044 **antes de mover código** en `tests/golden/tool_schemas_044.json`, con la prueba de comparación en `tests/test_tool_registry.py`. FR-027, research R9.
- [X] T003 [P] Permitir en el ejecutor de evaluación que un ítem abra otra conversación del mismo dueño (campo `new_thread_before: [índice de turno]` del ítem: mismo `owner_key`, `thread_id` nuevo) y aceptar ese campo en el parser de ítems de `ta/evaluation.py`, con prueba sin red en `tests/test_eval_agent.py`. Data-model *HerramientasEvalItem*.
- [X] T004 Redactar y congelar `data/eval/herramientas_045.json` (formato del v3: una pregunta por cada una de las 20 herramientas con `tools_any`, más 6 ítems `memoria`: Ana en otra conversación con `new_thread_before`, listar, olvidar, olvidar todo, dato sensible, ciudad habitual), con `frozen_at` y `origin` que declare que lo redactó el asistente de desarrollo. Las preguntas de `recordar_preferencia` no llevan marcas del atajo («Soy Ana, de la Panadería La Espiga», «suelo comprar en Cali»); el ítem «olvidar todo» declara `tools_any: []` y `path: "shortcut"`, que cuenta como equivalente (US5-1). Validar su formato en `tests/test_generalization_set.py`. Depende de T003. FR-030.
- [X] T005 [P] Curar `data/institucional/empresas.json` a partir de `data/gold/home.md`, de la lista «Nuestros Sectores» de `data/gold/quienes_somos.md` y de las 7 páginas de unidad de Gold (`empaques`, `pulpa_y_papel`, `educacion`, `tecnologia`, `comunicacion`, `espacios`, `propiedades_e_inversiones`), con marcas (Bioform, Norma, Mepal, LiquidezYa…) y `alias` citados (PROPAL → Carvajal Pulpa y Papel, de `data/gold/pulpa_y_papel.md`); cada fila con `nombre`, `tipo`, `slug`, `url` de `data/gold/index.jsonl`, `cita` literal y `alias`. Prueba de citas y alias contra `data/gold/` en `tests/test_institutional_data.py`. Research R12.
- [X] T006 Empaquetar `data/institucional/` para producción: `!data/institucional` y `!data/institucional/**` en `.dockerignore`, `COPY` a `./data` y a `/app/seed-data` en `Dockerfile`, y `seed_dir` en `compose.prod.yml` (bloque de siembra) y en `scripts/deploy.sh`. Verificar sin red con una prueba que lea `.dockerignore` y `Dockerfile` en `tests/test_deploy_packaging.py`. Depende de T005. Research R12.
- [X] T007 [P] Registrar la spec 045 en el índice `specs/README.md` con estado «Borrador» (hecho al crear la spec).

**Checkpoint**: ajustes listos, *golden* capturado, ejecutor preparado, conjunto congelado,
datos curados y empaquetados.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: paquete `tools/`, registro, `@tool`, identidad de las herramientas derivada del
registro, actualizaciones de estado desde herramientas, reloj de Bogotá y filtro de
sensibles. Sin cambios de comportamiento visibles.

**CRITICAL**: ninguna historia empieza antes de terminar esta fase.

- [X] T008 Convertir `ta/tools.py` en el paquete `ta/tools/` moviendo el código en bloque, sin reescribirlo: tipos y formato a `ta/tools/_base.py`, las seis herramientas con sus esquemas y ayudantes a `ta/tools/builtin.py`, `run_tool`/`_execute`/`_outcome`/`build_tools` a `ta/tools/runner.py`, y `ta/tools/__init__.py` reexportando `ToolDeps, ToolContext, ToolCallResult, run_tool, build_tools, call_fingerprint, format_tool_content, content_data, last_answer, merge_quote, quote_key, TOOL_SCHEMAS, TOOL_DESCRIPTIONS, CotizarArgs, DATA_OPEN, DATA_CLOSE`. Correr `tests/test_tool_agent_tools.py`, `tests/test_tool_agent_graph.py` y `tests/test_routes_material_cards.py` sin cambios. Depende de T002. Research R9.
- [X] T009 Escribir `tests/test_tool_registry.py`: orden de `TOOLS` (data-model *ToolSpec*), vistas de solo lectura, `TOOL_NAMES` derivado del registro, `set(get_args(ToolName)) == set(TOOL_NAMES)` (igualdad estricta en cada commit, porque el `Literal` crece con cada grupo registrado), descripción explícita (no el docstring) en cada `@tool`, invocación suelta de una herramienta y *golden* de T002. Depende de T008.
- [X] T010 Crear `ToolSpec` y `TOOLS` con las seis de la 044 en `ta/tools/registry.py`, y derivar `TOOL_NAMES = tuple(spec.name for spec in TOOLS)` (ya no `get_args(ToolName)`), `TOOL_SCHEMAS`, `TOOL_DESCRIPTIONS`, `_IMPLEMENTATIONS`, `STATUS_TEMPLATES`, `BUDGET_FREE`, `MEMORY_TOOLS` y `CITY_KEYS` como `MappingProxyType`/`frozenset` en `ta/tools/runner.py` y `ta/tools/__init__.py`. Depende de T009. FR-021, research R9.
- [X] T011 Reescribir `build_tools` con `@tool(name, description=TOOL_DESCRIPTIONS[name], args_schema=TOOL_SCHEMAS[name])` de `langchain_core.tools` dentro de la fábrica en `ta/tools/runner.py`; el *golden* debe seguir igual. Depende de T010. FR-021.
- [X] T012 Añadir los canales `conversation_summary: dict` y `memory: dict` a `ToolAgentState` y `conversation_summary: dict` a `AgentState` (`ta/state.py`, `core/agent/state.py`) y la entrada `turn_count: int` (turnos persistidos del hilo antes del turno actual) a ambos; **no** ampliar `ToolName` todavía. Actualizar `tests/test_tool_agent_state.py:118` para comparar con `TOOL_NAMES` del registro y añadir la prueba de ida y vuelta con el serializador del checkpointer. Depende de T010. Data-model *Estado de los grafos*.
- [X] T013 Hacer que los consumidores del grafo lean el registro en lugar de nombres a mano: `_CITY_KEYS`, la regla de `sin_consulta` y `_status_step` en `ta/graph.py`; `_TEMPLATES` y `_clean` en `ta/status.py`; `gate` y `neutral_only` por `evidence_role` en `ta/evidence.py`. `tests/test_tool_agent_status.py` y `tests/test_tool_agent_graph.py` en verde. Depende de T012. Research R9.
- [X] T014 Hacer que los consumidores del servicio lean el registro: `_tool_backing` y `_replacement` en `apps/api/chat/agent.py`; `_monitored_results` y `_has_evidence` en `apps/api/chat/service.py`. `tests/test_chat_service_tools.py` en verde. Depende de T013.
- [X] T015 Hacer que los consumidores de evaluación lean el registro: `_tool_backing` y `ROUTE_TOOLS` en `ta/evaluation.py` y el literal de `ta/simulation.py:1262`. `tests/test_eval_agent.py` en verde. Depende de T013.
- [X] T016 Ampliar `ToolContext` con `owner_key`, `thread_id` y `memory`, y `ToolDeps` con `memory`, `quote_lookup`, `institutional` y `faq` (`FaqSource`, data-model) en `ta/tools/_base.py`; llenarlos desde el estado en `_TurnProgress` de `ta/graph.py` y desde `apps/api/agent_runtime.py`. Limitar `ToolContext.history` en `from_state` a los últimos `agent_history_turns` de la semilla, para que la holgura no cambie `_city_said` de `cotizar` ni `buscar_sedes`. Pruebas en `tests/test_tool_registry.py` (ningún esquema tiene campos de identidad) y `tests/test_tool_agent_tools.py` (una ciudad dicha 9 turnos atrás no cuenta). Depende de T014, T015. FR-014, FR-027.
- [ ] T017 Añadir `state_update: dict` a `ToolCallResult` y `_Draft` (`ta/tools/_base.py`), volcarlo en `_TurnProgress` y `update()` de `ta/graph.py` para los canales `memory`, `preferences` y `conversation_summary`, hacer que el nodo `tools` reemplace el `SystemMessage` (mismo `SYSTEM_MESSAGE_ID`, como el nodo `shortcut`) cuando cambie la memoria, e invalidar la huella de `listar_memoria` en `contents` tras una herramienta de memoria que escribe. Prueba con una herramienta falsa en `tests/test_tool_agent_graph.py`. Depende de T016. Research R7.
- [ ] T018 Crear un único `_now_bogota(deps)` en `ta/tools/_base.py` (`deps.now` convertido con `astimezone(BOGOTA)`, o el reloj del sistema en `America/Bogota`) y hacer que `_today` de `ta/tools/builtin.py` lo use; prueba con `deps.now` a las 04:30 UTC en `tests/test_tool_agent_tools.py`, y `make eval-cotizador` 18/18 antes y después. Depende de T008. Research R11.
- [ ] T019 [P] Escribir `tests/test_memory_sensitive.py`: ≥ 30 frases sensibles (cédulas con y sin puntos, «CC 123456», celulares, fijos, `+57`, correos, tarjetas que pasan Luhn, CVV, cuentas, claves) rechazadas y ≥ 30 válidas aceptadas, entre ellas «2.000 vasos», «10.000 unidades», «1.000.000 de vasos», «entre 2.000 y 10.000», «tamaños 8 10 12 16 oz», «presupuesto de $2.500.000», «1.250.000 pesos», «pedido del 2026-11-24», «8 oz», «caja x 1000», «Cali», «Panadería La Espiga» y los folios `a1b2c3d4e5f6` y `123456789abc`; `redact` sustituye por `[dato omitido]`. SC-004.
- [ ] T020 Implementar `find_sensitive` y `redact` en `core/agent/memory/sensitive.py` (con `core/agent/memory/__init__.py`) con los patrones de research R8 (teléfono y documento por forma, no por conteo de dígitos). Depende de T019. FR-012.
- [ ] T021 [P] Exponer sin duplicar `declared_sizes`, `conversion` y `published_presentations(product, dataset, city=None)` (con `None`, todas las ofertas publicadas) en `core/quotation/engine.py`, conservando los alias privados; `make eval-cotizador` 18/18 y prueba en `tests/test_quotation_request.py`. Contrato `convertir_presentacion`.
- [X] T022 [P] Implementar `core/institutional.py` (carga de `data/institucional/empresas.json` y de los hitos `## AAAA` de `data/gold/historia.md` con su URL de `index.jsonl`, una sola vez); si falta un archivo, devuelve un estado `no_disponible` que las herramientas convierten en `error` declarado, nunca una excepción. Pruebas en `tests/test_institutional_data.py` (incluye archivo ausente). Depende de T005. Research R12.
- [X] T023 Aplicar la enmienda A (capa institucional curada) en `CONSTITUTION.md` y `.specify/memory/constitution.md` (versión 2.8.0, fila en el historial de decisiones), en el mismo commit que T005, T006 y T022. Depende de T022. Plan *Enmienda A*.

**Checkpoint**: registro con 6 herramientas, `@tool`, identidad derivada, contexto
ampliado, `state_update`, reloj de Bogotá, filtro de sensibles y datos listos. La suite
completa sigue verde.

---

## Phase 3: User Story 1 - Recordar lo dicho en una conversación larga (Priority: P1) MVP

**Goal**: resumen acumulado de los turnos fuera de la ventana, calculado en segundo plano,
con respaldo determinista y sin afectar el primer texto.

**Independent Test**: conversación de 15 turnos con doble de modelo: el contexto del turno
15 incluye lo dicho en el turno 2, con el resumen del modelo y con el determinista.

### Tests for User Story 1

- [ ] T024 [P] [US1] Escribir `tests/test_conversation_summary.py`: `pair_turns` (emparejamiento persona/asistente compartido), resumen determinista (una línea por turno, primer turno y recientes ante el tope, `[…]`), `user_quotes` redactadas, un resumen falso del modelo con un teléfono sale con `[dato omitido]`, salida del modelo inválida → determinista, adopción (gana el mayor `covered_turns`, hueco → `mixed`), idempotencia por `covered_turns` y marcador de olvido (`forgotten_through`). Data-model *ConversationSummary*.
- [ ] T025 [P] [US1] Ampliar `tests/test_tool_agent_history.py`: bloque `<resumen_conversacion>` en el sistema, neutralizado ante una instrucción maliciosa, canal no reiniciado por `recall`, conversación de 15 turnos que pregunta por el turno 2 (resumidor falso y modo `deterministic`), hueco de 2 turnos cubierto sin trabajo previo, hueco de 3 o más turnos (usa lo que hay, no avanza `covered_turns` más allá de lo visto y registra `agent.summary_gap`), un Store cuyo `get` lanza en `recall` (turno sin resumen y `agent.summary_store_failed`) y turnos anteriores a `forgotten_through` fuera del resumen y de la ventana. FR-001, FR-002, FR-009, SC-001.
- [ ] T026 [P] [US1] Escribir `tests/test_summary_worker.py`: `submit` no bloquea con un resumidor que duerme 2 s, cola llena descarta y registra `agent.summary_dropped`, plazo vencido → determinista, Store caído → advertencia sin excepción, `submit` repetido no llama al modelo, una lápida de olvido posterior a la lectura descarta el resultado, y `llm` sin DeepSeek configurado se degrada a `deterministic` con `agent.summary_mode_degraded`. FR-003, FR-005, FR-009.
- [ ] T027 [P] [US1] Ampliar `tests/test_chat_service_tools.py`: `summary_worker.submit(owner_key, conversation_id)` se llama tras cada `_persist`/`_persist_owned` (las cinco llamadas de `apps/api/chat/service.py`: 514, 614, 644, 804 y 976) con el dueño correcto y sin esperar, el resumidor nunca corre antes del primer token, la réplica idempotente no encola, el respaldo `routes` también encola, `_seed_turns` pide `2 × (agent_history_turns + 2)` mensajes, la entrada del grafo lleva `turn_count`, y `trace.summary` va sin texto. FR-003, FR-005, FR-008.
- [ ] T028 [P] [US1] Ampliar `tests/test_agent_graph.py`: el atajo y el nodo de respuesta directa de `routes` reciben el resumen y la ventana `agent_history_turns` (no `turns[-4:]`), un turno de respaldo conserva `conversation_summary` y el nodo `rag` no recibe el bloque (excepción declarada en FR-002). FR-002, FR-008.
- [ ] T029 [P] [US1] Ampliar `tests/test_tool_agent_stream.py`: una cifra dicha por la persona en el turno 2 respalda la respuesta del turno 15; una cifra que solo está en el texto del resumen no respalda. FR-006, research R4.
- [ ] T030 [P] [US1] Ampliar `tests/test_eval_agent.py`: el ejecutor de evaluación acota la semilla a `agent_history_turns + 2` turnos y pasa `turn_count`, como producción. Research R13.

### Implementation for User Story 1

- [ ] T031 [US1] Implementar `pair_turns`, `ConversationSummary`, `deterministic_summary` (solo preguntas literales; el contexto comercial ya viaja en su canal), `summarize_llm` (sin herramientas, temperatura 0, `max_tokens=400`, plazo de ajustes, validación de salida, `redact` del texto) y `adopt` en `core/agent/memory/summary.py`, y `SUMMARY_SYSTEM` en `core/prompts.py` (prueba de texto en `tests/test_prompts.py`). Depende de T020, T024. Research R3.
- [ ] T032 [US1] Leer borrador, canal y lápida, adoptar y cubrir el hueco en `recall` de `ta/graph.py` y de `core/agent/graph.py` con `turn_count` (índice del primer turno de la semilla = `turn_count - len(seed)`), sin avanzar `covered_turns` más allá de lo visto; excluir los turnos anteriores a `forgotten_through`; añadir el bloque `<resumen_conversacion>` a `system_prompt` (`ta/graph.py`) y a `build_conversation_prompt` (`core/agent/graph.py`), que pasa a usar `agent_history_turns`. Depende de T012, T031. FR-002, FR-008, FR-009.
- [ ] T033 [US1] Implementar `SummaryWorker` (ejecutor de un hilo, cola de 32, idempotencia, `session_factory` propia para leer el historial completo con `history_all()`, `PostgresStore` propio, relectura de la lápida antes del `put`, degradación de `llm` sin DeepSeek, eventos de log) en `apps/api/chat/summary_worker.py`; añadir `history_all(conversation_id)` a `core/adapters/storage/chat.py`. Depende de T031, T026. Research R1, R2.
- [ ] T034 [US1] Inyectar el `SummaryWorker` en `ChatService` (construido en `default_chat_service`, con cierre en `atexit`) y llamar a `summary_worker.submit(owner_key, conversation.id)` justo después de cada `_persist`/`_persist_owned` en `apps/api/chat/service.py` (sin cambiar la firma de `runner.record`); `_seed_turns` usa `pair_turns`, pide `2 × (agent_history_turns + 2)` mensajes y el servicio añade `turn_count` (un `COUNT` en la misma sesión) a la entrada del grafo. Depende de T033. FR-003, FR-008.
- [ ] T035 [US1] Acotar la semilla del ejecutor de evaluación a `agent_history_turns + 2` y pasar `turn_count` en `ta/evaluation.py` (`_run_item`); sin `SummaryWorker`, el ejecutor solo rellena huecos de forma determinista (limitación declarada en la evidencia). Depende de T032, T030. Research R13.
- [ ] T036 [US1] Añadir las preguntas de `seed_turns` y `conversation_summary.user_quotes` a `_allowed` en `apps/api/chat/agent.py` y la misma regla en `_tool_backing` de `ta/evaluation.py`. Depende de T032. FR-006.
- [ ] T037 [US1] Emitir `trace.summary = {covered_turns, method}` y los eventos `agent.summary_*` sin texto en `apps/api/chat/service.py`. Depende de T034.

**Checkpoint**: la conversación de 15 turnos recuerda el turno 2 sin proveedor y con el
resumidor falso; el primer texto no cambia.

---

## Phase 4: User Story 2 - Una memoria entre conversaciones que la persona controla (Priority: P1)

**Goal**: memoria por categorías con tres herramientas, sin datos sensibles, ciudad solo
sugerida, aislada por dueño, con atajo de respaldo y migración.

**Independent Test**: escenarios «llámame Ana» en otra conversación, listar, olvidar una
categoría y todo, dato sensible rechazado y ciudad habitual sugerida, para un dueño web y
uno de WhatsApp, con `InMemoryStore` y doble de modelo.

### Tests for User Story 2

- [ ] T038 [P] [US2] Escribir `tests/test_agent_memory_store.py`: `load`/`remember`/`forget`/`lines`, reemplazo por categoría, notas (dedup, tope 5, 120 caracteres), `formato_preferido` como lista cerrada con sinónimos (lo demás va a `nota`), caducidad de 180 días, Store que lanza, aislamiento por dueño, `owner_key` vacío → `sin_dueno` sin tocar el Store, patrones propios con las frases literales de spec y quickstart («llámame Ana, trabajo en Panadería La Espiga y suelo comprar en Cali» → nombre, empresa «Panadería La Espiga» y ciudad «Cali»; «me dicen», «soy Ana»; «soy de Cali» no es nombre), patrón de olvido total (positivos: «olvida todo lo que sabes de mí», «olvida mis datos», «borra todo lo que sabes de mí», «olvídate de mí»; negativos: «olvida dónde trabajo», «olvida mi nombre»), migración perezosa con 10 frases antiguas (5 notas más recientes recortadas a 120 con «…», sensibles descartados, `agent.memory_migration_dropped` con el conteo) y `forget(todo)` que borra las categorías, el formato anterior y todos los borradores de resumen del dueño y escribe la lápida. Data-model *MemoryProfile*, research R5, R6, R16.
- [ ] T039 [P] [US2] Escribir `tests/test_tool_memory_tools.py`: contratos de `recordar_preferencia`, `listar_memoria` y `olvidar_preferencia` (salidas `ok`/`empty`/`error`, sin `source_ids`, `dato_sensible` sin repetir el dato, `store_no_disponible`, `sin_dueno`, sin campos de identidad en el esquema); `recordar_preferencia` con un `valor` que no está en la pregunta → `empty` con `motivo: "no_dicho"` (un `ScriptedToolChat` intenta guardar lo que dice un fragmento del corpus); `olvidar_preferencia` sin verbo de olvido en la pregunta → `empty` con `motivo: "no_pedido"`. Contrato *Memoria*, research R7.
- [ ] T040 [P] [US2] Escribir `tests/test_tool_agent_memory_flow.py` con `ScriptedToolChat`: «llámame Ana» con proveedor disponible va al agente y a `recordar_preferencia` (sin atajo); con el respaldo, el atajo lo guarda una sola vez; recordado en otra conversación del mismo dueño; no visible para otro dueño ni entre web (`owner_key` de cookie) y WhatsApp (`wa_…`); olvidar una categoría y todo desde ese mismo turno (el `SystemMessage` del resto del turno ya no trae el nombre, `_allowed` no lo respalda y `listar_memoria` después de `recordar` no sale de la deduplicación); olvidar todo y, dos turnos después, el resumen no contiene lo olvidado; ciudad habitual → `cotizar` y `buscar_sedes` devuelven `needs_input` con `sugerencia_ciudad` y cotizan solo tras «sí, en Cali». SC-003, SC-005.
- [ ] T041 [P] [US2] Ampliar `tests/test_tool_agent_stream.py` por camino (FR-017): en `no_query`, los valores de memoria respaldan solo si todas las consultas son de memoria o `sin_consulta`; en `evidence`, una nota «Carvajal se fundó en 1850» no entra en `_allowed`; un turno solo de memoria cuyo texto rechaza el filtro recibe el reemplazo determinista con los datos de la herramienta (confirmación o lista), no `NO_DATA_ANSWER`; el nombre sigue respaldando el saludo.
- [ ] T042 [P] [US2] Ampliar `tests/test_tool_agent_status.py` y `tests/test_monitoring_tools.py`: las líneas de estado de memoria no contienen valores; `ToolOutcome.args` del canal `results`, `trace`, el ejecutor de evaluación y el monitoreo (en `metadata` y en `content=full`) llevan solo `categoria` para las herramientas de memoria. FR-018, SC-008.
- [ ] T043 [P] [US2] Ampliar `tests/test_agent_router.py` y `tests/test_tool_agent_graph.py`: `memory_write` ya no es intención del atajo del grafo `tools` (sí del grafo `routes`, clasificada por categoría), frase sensible rechazada por el atajo de `routes`, intención `memory_forget` sin decisión del proveedor en ambos grafos; convertir las pruebas que fijan el formato anterior (`tests/test_tool_agent_history.py:247, 268, 297`, `tests/test_tool_agent_graph.py:739, 1075, 1080, 1087`) en pruebas de migración. FR-015, FR-016, research R15.
- [ ] T044 [P] [US2] Ampliar `tests/test_tool_agent_tools.py`: `buscar_sedes` con una ciudad que la persona no dijo → `needs_input` con `missing: ["ciudad"]`, `ciudades_disponibles` y `sugerencia_ciudad`; revisar los casos de la 044 en ese archivo y los ítems del v3 que llamen a `buscar_sedes` con una ciudad tomada de una respuesta anterior. FR-013.

### Implementation for User Story 2

- [ ] T045 [US2] Implementar `MemoryProfile`, `MemoryOperation`, `load` (caducidad y migración perezosa), `remember`, `forget(store, owner, category, value=None, thread_id=None)` (con `todo`: categorías, formato anterior, todos los borradores de `("carvajal","summary",owner)` y lápida `_forgotten`), `lines`, `forget_all_requested` y los patrones propios de nombre, empresa y ciudad habitual en `core/agent/memory/store.py`, sin tocar `core/quotation/memory.py` (del Cotizador). Depende de T020, T038. Research R5, R6, R16.
- [ ] T046 [US2] Reimplementar `_preferences` y `_remember` sobre `core/agent/memory/store.py` (misma firma para el grafo `routes`) en `core/agent/graph.py`, y leer la memoria una vez en `recall` de `ta/graph.py` (canales `memory` y `preferences`). Depende de T045, T032.
- [ ] T047 [P] [US2] Implementar `recordar_preferencia` (esquema, `run` con la regla «dicho en la pregunta», plantilla de estado sin valores, `state_update` de `memory` y `preferences`) en `ta/tools/recordar_preferencia.py`. Depende de T045, T039, T017.
- [ ] T048 [P] [US2] Implementar `listar_memoria` en `ta/tools/listar_memoria.py`. Depende de T045, T039.
- [ ] T049 [P] [US2] Implementar `olvidar_preferencia` (verbo de olvido en la pregunta; categoría, nota o `todo` con `thread_id`; `state_update` que actualiza `memory` y `preferences` y, con `todo`, deja `conversation_summary` como marcador de olvido) en `ta/tools/olvidar_preferencia.py`. Depende de T045, T039, T017.
- [ ] T050 [US2] Registrar las tres herramientas de memoria (`evidence_role="memory"`, `consumes_budget=True`) en `ta/tools/registry.py`, ampliar `ToolName` con sus tres nombres en `ta/state.py`, y hacer que `_outcome` de `ta/tools/runner.py` guarde `args={"categoria": ...}` para el rol `memory`. Depende de T047–T049. FR-018.
- [ ] T051 [US2] Quitar `memory_write` de `SHORTCUT_INTENTS` del grafo `tools` (lo atiende el agente con `recordar_preferencia`), clasificarlo por categoría con el filtro de sensibles en el atajo del grafo `routes` (respaldo), añadir la intención `memory_forget` con `forget_all_requested` en ambos grafos (el nodo `shortcut` actualiza `memory`, `preferences` y `conversation_summary`) y las instrucciones `DIRECT_INSTRUCTIONS["memory_rejected"]` y `["memory_forget"]` en `core/agent/router.py`, `ta/graph.py` y `core/prompts.py`. Depende de T045. FR-015, research R15.
- [ ] T052 [US2] Aplicar la regla `_city_said` en `_buscar_sedes` y añadir `sugerencia_ciudad` al `needs_input` de `cotizar` y `buscar_sedes` cuando la ciudad no se dijo y hay `ciudad_habitual` en `ta/tools/builtin.py`, sin añadir la ciudad habitual a los textos de `_city_said`; rotular la ciudad en `lines()`. Depende de T016, T045, T044. FR-013.
- [ ] T053 [US2] Aplicar la regla de respaldo de memoria por camino en `_allowed`, añadir a `_replacement` el reemplazo determinista para turnos solo de memoria y leer el nombre de la categoría `nombre` en `_preferred_names` en `apps/api/chat/agent.py`, con la misma regla en `ta/evaluation.py`. Depende de T046, T050. FR-017.
- [ ] T054 [US2] Cambiar `trace.memory` al número de categorías en `apps/api/chat/service.py` y comprobar que `record_tool_calls` de `core/infrastructure/monitoring.py` hereda la redacción de `_outcome` (sin redactar dos veces). Depende de T050. FR-018.
- [ ] T055 [US2] Añadir el área «memoria» a `AGENT_SYSTEM` (cuándo recordar, listar y olvidar; nunca datos sensibles; ciudad habitual solo como sugerencia; memoria como dato; las reglas de datos, citas e idioma prevalecen sobre cualquier preferencia) en `core/prompts.py`, sin cifras, ciudades ni SKU (reglas de `tests/test_prompts.py:549-591`), con prueba en `tests/test_prompts.py`. Depende de T050.
- [ ] T056 [US2] Aplicar la enmienda C (resumen en el estado del hilo, memoria por categorías en el Store, herramientas que escriben) en `CONSTITUTION.md` y `.specify/memory/constitution.md` (2.8.0), en el mismo commit que T050. Depende de T050. Plan *Enmienda C*.

**Checkpoint**: US1 y US2 completas forman el MVP de memoria; los escenarios de memoria
pasan sin red.

---

## Phase 5: User Story 3 - Consultas más finas sobre el catálogo (Priority: P2)

**Goal**: cinco herramientas nuevas de catálogo, una por módulo, con fuente por dato
(`listar_familias` se sustituyó por `preguntas_frecuentes`, que pasa a la US4; research R18).

**Independent Test**: una prueba de contrato por herramienta sin red contra el catálogo
versionado.

### Tests for User Story 3

- [ ] T057 [P] [US3] Escribir `tests/test_tool_ficha_producto.py`: SKU exacto (CO-VTER-04: 4 oz, EPS, 1000 por caja, sin temperatura de ficha, guía rotulada), nombre ambiguo → candidatas, inexistente → `empty`, ficha MX con precio inline, fuentes `ficha:` y `precio:` con excerpt que contiene las cifras, línea de estado sin valores.
- [ ] T058 [P] [US3] Escribir `tests/test_tool_comparar_referencias.py`: CO-CTT-08 frente a CO-CTT-16 (ambas con precio de EDEXA) → precio unitario con `Decimal` y `mas_economica`; CO-VTER-08 frente a CO-VTER-10 (sin precio) → diferencias sin veredicto; distinto `tax_status`, país o moneda → `mas_economica: null` con `aviso`; CO frente a MX sin veredicto; una ambigua → `needs_input`.
- [ ] T059 [P] [US3] Escribir `tests/test_tool_guia_materiales.py`: PET y comida caliente, «icopor» → EPS, solo aplicación ordenada por nivel, PAPEL/PULPA → `sin_regla`, `refrigeracion_deducida` para PS y aluminio.
- [ ] T060 [P] [US3] Escribir `tests/test_tool_convertir_presentacion.py`: 3 cajas del CO-VTER-04 = 3000 unidades, paca del nombre en fichas CO, portacomidas J1 («paca x 200», oferta «caja de 200») → `no_convertible`, tamaños contradictorios → `no_convertible`, MX desde el catálogo, paridad con el motor de cotización.
- [ ] T062 [P] [US3] Escribir `tests/test_tool_ciudades_con_cobertura.py`: 10 ciudades y 29 sedes, con referencia separa `con_precio` de `con_sede`, aviso de existencias.

### Implementation for User Story 3

- [ ] T063 [P] [US3] Implementar `ficha_producto` en `ta/tools/ficha_producto.py`. Depende de T016, T057.
- [ ] T064 [US3] Implementar `comparar_referencias` en `ta/tools/comparar_referencias.py` (reutiliza la resolución de `ficha_producto`; precio unitario = importe ÷ unidades de la base con `Decimal`: CO desde `Offer.units_in_basis`, MX con `amount / units_per_box`). Depende de T063, T058.
- [ ] T065 [P] [US3] Implementar `guia_materiales` en `ta/tools/guia_materiales.py`. Depende de T016, T059.
- [ ] T066 [P] [US3] Implementar `convertir_presentacion` en `ta/tools/convertir_presentacion.py` con `engine.published_presentations(product, dataset, city=None)`. Depende de T021, T060.
- [ ] T068 [P] [US3] Implementar `ciudades_con_cobertura` en `ta/tools/ciudades_con_cobertura.py`. Depende de T016, T062.
- [ ] T069 [US3] Registrar las cinco herramientas de catálogo en `ta/tools/registry.py` en el orden de data-model *ToolSpec* y ampliar `ToolName` con sus nombres en `ta/state.py`. Depende de T063–T068.
- [ ] T070 [US3] Verificar con `tests/test_tool_agent_graph.py` que una herramienta de catálogo nueva respeta el límite, la deduplicación, la cancelación y la emisión de `status` (una prueba parametrizada por herramienta nueva). Depende de T069. FR-024, FR-025.

**Checkpoint**: 14 herramientas registradas; las de catálogo funcionan solas.

---

## Phase 6: User Story 4 - Consultas institucionales, de cotizaciones guardadas y de fecha (Priority: P2)

**Goal**: seis herramientas nuevas sobre historia, empresas, preguntas frecuentes,
indicadores, cotizaciones y calendario. T061 y T067 conservan su número de la revisión
anterior (eran de `listar_familias` en la US3).

**Independent Test**: una prueba de contrato por herramienta sin red ni base de datos real
(`preguntas_frecuentes` con un corpus en memoria y un LLM falso).

### Tests for User Story 4

- [ ] T061 [P] [US4] Escribir `tests/test_tool_preguntas_frecuentes.py` con un `FaqCorpus` en memoria (documentos núcleo y uno de la capa extendida) y un `LLMProvider` falso que devuelve Markdown fijo: lote verificado con fuentes `faq:{slug}:{huella}` de la capa núcleo cuyo `excerpt` contiene las cifras y nombres propios de cada entrada; entrada con una cifra que su documento no tiene → descartada y contada en `descartadas`; `_Fuente:_` con un slug inexistente o de la capa extendida → descartada; ninguna verificada → `empty` (y el turno se abstiene como con `buscar_corpus`); tema sin fragmentos del núcleo → `empty` sin llamar al LLM; `cantidad` limita el lote; el tema llega neutralizado dentro de `<tema>`; sin `FaqSource` → `error`; LLM que lanza o que tarda más que `timeout_s` → `error` sin caché y `ok` con `desde_cache: true` con un lote anterior en caché; segunda llamada igual dentro de 24 h sale de la caché sin llamar al LLM; línea de estado sin valores; esquema sin campos de identidad; `build_faq_prompt` y `GenerateFaq.prepare` con los valores por defecto producen el mismo prompt que hoy (comparación byte a byte) y `tests/test_faq.py` y `tests/test_api_faq.py` siguen en verde sin cambios. FR-022a, research R18.
- [ ] T071 [P] [US4] Escribir `tests/test_tool_historia_carvajal.py`: año con hito (1960) literal, año sin hito → `anios_cercanos`, rango con más de 10 hitos → `hay_mas`, fuente `historia:{anio}` oficial, `historia.md` ausente → `error` declarado.
- [ ] T072 [P] [US4] Escribir `tests/test_tool_empresas_del_grupo.py`: lista completa con cita (incluye las unidades de «Nuestros Sectores»), nombre listado, alias («Propal» → Carvajal Pulpa y Papel con su cita), nombre que Gold no menciona («Alpina») → `empty` sin «no pertenece», `empresas.json` ausente → `error` declarado.
- [ ] T073 [P] [US4] Escribir `tests/test_tool_indicadores_disponibles.py`: 6 indicadores con períodos y sin valores, excerpt con nombres y períodos.
- [ ] T074 [P] [US4] Escribir `tests/test_tool_fecha_actual.py` con `deps.now` fijo: día hábil, fin de semana, festivo de Colombia, `deps.now` a las 04:30 UTC (fecha del día anterior en Bogotá), `source_type="sistema"`.
- [ ] T075 [P] [US4] Escribir `tests/test_tool_consultar_cotizacion.py` con un `quote_lookup` en memoria: folio propio real, folio ajeno, inexistente, simulado y ambiguo con la misma salida `empty`, sin fuentes ni valores; sin folio lista las reales del hilo (máx. 5, recorte en Python); vigencia frente a `_now_bogota` con `deps.now` a las 04:30 UTC; sin `quote_lookup` → `error`.

### Implementation for User Story 4

- [ ] T067 [P] [US4] Implementar `preguntas_frecuentes` (`evidence_role="corpus"`, plantilla de estado sin valores) en `ta/tools/preguntas_frecuentes.py`, reutilizando `GenerateFaq` con los argumentos opcionales `topic`, `max_questions` y `slugs` que se añaden a `build_faq_prompt` y `GenerateFaq.prepare` en `core/application/faq.py` (sin cambio con los valores por defecto; `FAQ_INSTRUCTIONS` intacto); construir el `FaqSource` en `apps/api/agent_runtime.py` con `SqlFaqCorpus`, `runtime.llm_provider()`, el tope de `faq_service` y `agent_faq_timeout_s` (y en `ta/evaluation.py` si hay base configurada; si no, `error` declarado en la evidencia); hacer que `_tool_backing` de `apps/api/chat/agent.py` y de `ta/evaluation.py` lea solo `fragmento` de sus entradas. No se tocan `apps/api/views/faq.py`, `faq.html`, `faq.js` ni `faq.css`. Depende de T016, T061. FR-022a, research R18.
- [ ] T076 [P] [US4] Implementar `historia_carvajal` en `ta/tools/historia_carvajal.py`. Depende de T022, T071.
- [ ] T077 [P] [US4] Implementar `empresas_del_grupo` en `ta/tools/empresas_del_grupo.py`. Depende de T022, T072.
- [ ] T078 [P] [US4] Implementar `indicadores_disponibles` en `ta/tools/indicadores_disponibles.py`. Depende de T016, T073.
- [ ] T079 [P] [US4] Implementar `fecha_actual` con `_now_bogota` en `ta/tools/fecha_actual.py`. Depende de T018, T074. Research R11.
- [ ] T080 [P] [US4] Implementar `consultar_cotizacion` (solo `provenance == "real"`) en `ta/tools/consultar_cotizacion.py`, y el `QuoteLookup` sobre `SqlQuoteRepository.get`/`for_conversation` (recorte `[-5:]` en Python) con su propia sesión en `apps/api/agent_runtime.py`. Depende de T016, T018, T075. FR-026.
- [ ] T081 [US4] Registrar las seis herramientas en `ta/tools/registry.py` (con `preguntas_frecuentes` en la posición 16 del orden de data-model *ToolSpec*), ampliar `ToolName` con sus nombres en `ta/state.py` (20 en total) y ampliar la prueba parametrizada de T070 a ellas (con un `FaqSource` falso). Depende de T067, T076–T080.
- [ ] T082 [US4] Comprobar que `source_type="sistema"` no rompe la API, el replay ni `_productos.html` (prueba en `tests/test_chat_service_tools.py`). Depende de T079.
- [ ] T083 [US4] Aplicar la enmienda B (memoria y resumen como datos del usuario; procedencia `sistema`) en `CONSTITUTION.md` y `.specify/memory/constitution.md` (2.8.0), en el mismo commit que T079. Depende de T079, T053. Plan *Enmienda B*.

**Checkpoint**: las 20 herramientas están registradas, probadas y visibles.

---

## Phase 7: User Story 5 - Elegir bien entre 20 herramientas y demostrarlo (Priority: P2)

**Goal**: descripciones y prompt que ayudan a elegir, visibilidad completa, equivalencias
de evaluación y quickstart.

**Independent Test**: pruebas sin red del texto que ve el modelo y del orden; luego la
medición de la fase 8.

- [ ] T084 [P] [US5] Ampliar `tests/test_prompts.py`: cada descripción de las 14 nuevas sigue el patrón «Úsala para… No la uses para…» y mide ≤ 300 caracteres; `buscar_corpus`, `buscar_producto`, `cotizar`, `buscar_sedes` e `indicador_financiero` terminan en una frase «No la uses para…» y no crecen más de 160 caracteres sobre el *golden*; `sin_consulta` no cambia; ninguna descripción ni regla nueva lleva cifras, ciudades ni SKU (regla existente de la línea 575); `AGENT_SYSTEM` nombra las 20 herramientas en sus seis áreas. FR-028, research R10.
- [ ] T085 [US5] Compuerta de tokens antes de redactar: contar los caracteres de los 20 esquemas serializados (descripciones provisionales ≤ 300) y del `AGENT_SYSTEM` previsto, estimar el prefijo del primer paso y los tokens de entrada por turno frente a la línea base de 6 900 (044, §1), y registrar la estimación en `specs/045-memoria-herramientas/evidencia.md`. Si supera 1,5×, acortar descripciones de campo antes de T086; si aun así no cabe, ajustar el umbral de SC-009 con su justificación en `specs/045-memoria-herramientas/spec.md` (*Clarifications*). Depende de T081. SC-009, research R14.
- [ ] T086 [US5] Redactar las descripciones de las 14 herramientas nuevas y la frase «No la uses para…» de las cinco de la 044 que la necesitan, actualizando el *golden* en el mismo commit, en `ta/tools/*.py` y `tests/golden/tool_schemas_044.json`. Depende de T084, T085. Research R10.
- [ ] T087 [US5] Reorganizar `AGENT_SYSTEM` por áreas (documental, catálogo, comercial, finanzas, memoria, utilidades) conservando las reglas actuales en `core/prompts.py`. Depende de T086.
- [ ] T088 [P] [US5] Ampliar `tests/test_tool_agent_graph.py`: `bind_tools` recibe las 20 en el orden del registro y el primer paso sigue con `tool_choice="required"`. Depende de T081.
- [ ] T089 [P] [US5] Ampliar `tests/test_monitoring_tools.py`: cada una de las 20 (salvo `sin_consulta`) aparece como hijo `tool` con nombre, estado y latencia. FR-025.
- [ ] T090 [US5] Añadir a `ta/evaluation.py` un mapa congelado de equivalencias aplicado a `tools_any` (`buscar_sedes` ↔ `ciudades_con_cobertura`; `buscar_producto` ↔ `ficha_producto`/`comparar_referencias`; `buscar_corpus` ↔ `historia_carvajal`/`empresas_del_grupo`) y la aceptación de `path: "shortcut"` declarada en el ítem; el informe da el crudo estricto y el crudo con equivalencias. Prueba sin red en `tests/test_eval_agent.py`. Depende de T081. US5-1.
- [ ] T091 [US5] Ejecutar en local la sección 1 de `specs/045-memoria-herramientas/quickstart.md` y ajustar las preguntas de la sección 2 a los datos reales si alguna no aplica. Depende de T087.

**Checkpoint**: todo lo verificable sin red está en verde.

---

## Phase 8: Medición, documentación y entrega

**Purpose**: comprobar que no hay regresiones (una sola corrida con proveedor real por
medición, R13) y cerrar la documentación.

- [ ] T092 Ejecutar `make check` (objetivo del `Makefile`: Ruff, formato, `ty`, pytest) en verde y registrar el resultado en `specs/045-memoria-herramientas/evidencia.md`.
- [ ] T093 Ejecutar `make eval-cotizador` (objetivo del `Makefile`), confirmar 18/18 y registrarlo en `specs/045-memoria-herramientas/evidencia.md`.
- [ ] T094 Correr una vez `uv run carvajal eval-agent --mode tools --questions data/eval/herramientas_045.json` y registrar la elección por herramienta, estricta y con equivalencias (SC-006), en `specs/045-memoria-herramientas/evidencia.md`, declarando que el ejecutor no tiene `SummaryWorker` (solo relleno determinista). Depende de T004, T087, T090.
- [ ] T095 Correr una vez `uv run carvajal eval-agent --mode tools --questions data/eval/generalization_v3.json`, auditar solo los fallos con el verificador escéptico y comparar con la ronda 4 de la 044 (auditado 34/40; crudo estricto y con equivalencias, informativo) y los tokens de entrada medios por turno frente a 6 900 (SC-007, SC-009) en `specs/045-memoria-herramientas/evidencia.md`; declarar que el respaldo `routes` ahora ve `agent_history_turns` turnos (sin corrida extra). Depende de T090.
- [ ] T096 Correr una vez `uv run carvajal simulate-chat --scenarios data/eval/simulacion_escenarios_r3.json` (12 escenarios), con juez y verificador una vez, y registrar violaciones, datos sin fuente, respaldos y primer texto p95 (SC-002, SC-007) en `specs/045-memoria-herramientas/evidencia.md`.
- [ ] T097 Medir `SUMMARY_SYSTEM` una sola vez con el escenario 1 de la sección 3 de `specs/045-memoria-herramientas/quickstart.md` (15 turnos, `AGENT_SUMMARY_MODE=llm`, más el caso de inyección) frente al determinista sin red; registrar el resultado en `specs/045-memoria-herramientas/evidencia.md` y en `docs/prompts.md`, y cambiar el valor por defecto a `llm` en `packages/core/src/carvajal/infrastructure/settings.py` solo si cumple SC-001 sin efecto de la inyección. Los demás escenarios de la sección 3 se validan sin red (T040). SC-001, research R3.
- [ ] T098 Si alguna medición no cumple, registrar la causa y el arreglo propuesto en `specs/045-memoria-herramientas/evidencia.md` sin repetir corridas salvo que la causa sea ajena al cambio (red o cuota); la decisión de entregar con riesgos es del owner.
- [ ] T099 Revisar que las enmiendas A, B y C (T023, T056, T083) sean coherentes entre `CONSTITUTION.md` y `.specify/memory/constitution.md` (versión 2.8.0, estructura del repositorio con `agent/memory/`, `toolagent/tools/` y `data/institucional/`, y una fila en el historial de decisiones).
- [ ] T100 [P] Actualizar `specs/README.md` (estado de la 045) y `.env.example` con los ajustes nuevos si faltara alguno.
- [ ] T101 Abrir la PR a `main` con la skill `abrir-pr` (etiquetas del catálogo vigente, assignee, sin merge e integración por *squash*) y enlazarla en `specs/045-memoria-herramientas/evidencia.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)**: T001, T002, T003 y T005 sin dependencias entre sí; T004 espera T003 (el
  parser acepta `new_thread_before`) y T006 espera T005. **T002 debe terminar antes de
  T008** (el *golden* se captura antes de mover código).
- **Foundational (2)**: T008 → T009 → T010 → T011/T012 → T013 → T014/T015 → T016 → T017 en
  serie (mismos archivos); T018 tras T008; T019 → T020, T021 y T022 → T023 en paralelo con
  esa cadena. Bloquea las fases 3–7.
- **US1 (3)**: depende de la fase 2. Es independiente de US3 y US4.
- **US2 (4)**: depende de la fase 2; T046 espera T032 (ambas tocan `recall`).
- **US3 (5)** y **US4 (6)**: dependen solo de la fase 2 (y de T021/T022); son
  independientes de US1 y US2 y entre sí. T083 espera T053 (la regla de memoria de la
  enmienda B).
- **US5 (7)**: depende de que las 20 estén registradas (T050, T069, T081).
- **Fase 8**: depende de las fases 3–7. T094–T097 son las únicas tareas que gastan
  créditos.

### Within Each User Story

- Pruebas primero (deben fallar), luego módulo de la herramienta o del dominio, luego el
  registro y el `Literal` en el mismo commit y, al final, servicio, prompt y monitoreo.
- `ta/graph.py`, `ta/tools/registry.py`, `ta/state.py`, `apps/api/chat/agent.py`,
  `apps/api/chat/service.py` y `core/prompts.py` los toca una persona a la vez.

### Parallel Opportunities

- Fase 1: T002, T003, T005 y T007 en paralelo con T001.
- Fase 2: T018–T023 en paralelo con la cadena T008–T017 (T018 tras T008).
- US1: T024–T030 en paralelo.
- US2: T038–T044 en paralelo; T047, T048 y T049 en paralelo.
- US3: T057–T060 y T062 en paralelo; T063, T065, T066 y T068 en paralelo (T064 espera
  T063).
- US4: T061 y T071–T075 en paralelo; T067 y T076–T080 en paralelo.
- US3 y US4 pueden avanzar a la vez entre sí y con US1/US2 (módulos distintos); solo el
  registro y el `Literal` se serializan (T050, T069, T081).

## Parallel Example: herramientas nuevas

```text
# Pruebas en paralelo (un archivo cada una):
T057 tests/test_tool_ficha_producto.py      T071 tests/test_tool_historia_carvajal.py
T059 tests/test_tool_guia_materiales.py     T073 tests/test_tool_indicadores_disponibles.py
T061 tests/test_tool_preguntas_frecuentes.py T074 tests/test_tool_fecha_actual.py

# Implementación en paralelo (un módulo cada una):
T063 ta/tools/ficha_producto.py             T076 ta/tools/historia_carvajal.py
T065 ta/tools/guia_materiales.py            T078 ta/tools/indicadores_disponibles.py
T067 ta/tools/preguntas_frecuentes.py       T079 ta/tools/fecha_actual.py

# Luego, una sola persona: T069 y T081 (ta/tools/registry.py y ta/state.py)
```

## Implementation Strategy

### MVP First (US1 + US2)

1. Fase 1 y fase 2 (registro con las seis de la 044, sin cambio visible).
2. US1 (resumen) y US2 (memoria) → validar con la sección 3 del quickstart en modo
   `deterministic`, sin gastar créditos.
3. **Parar y validar**: `make check` en verde.

### Incremental Delivery

1. Fases 1–2 → commit `refactor:` del paquete `tools/` y el registro (sin cambio de
   comportamiento; el *golden* lo demuestra), con la enmienda A en el commit de los datos
   institucionales (T023).
2. US1 → commit `feat(agente):` del resumen acumulado.
3. US2 → commit `feat(agente):` de la memoria por categorías, con la enmienda C (T056).
4. US3 y US4 → commits `feat(agente):` por grupo de herramientas, con la enmienda B en el
   de `fecha_actual` (T083).
5. US5 + fase 8 → commit de descripciones, prompt y evidencia; una sola PR a `main` (la
   entrega del curso no da tiempo para varias revisiones), integrada con *squash*: en `main`
   queda un solo commit con el código y las tres enmiendas (principio I). El owner la
   revisa y la mergea.

## Notes

- `[P]` = archivos distintos y sin dependencias pendientes.
- Ninguna tarea cambia el catálogo, el corpus ni los precios; la única curaduría nueva es
  T005.
- `listar_familias` se sustituyó por `preguntas_frecuentes` por pedido del owner
  (2026-11-24, research R18); T061 y T067 conservan su número y pasan a la US4.
- Las corridas con proveedor real (T094–T097) se hacen una vez cada una por pedido del
  owner; los resultados con su limitación (sin repeticiones ni intervalos) van a la
  evidencia.
- Commit por tarea o grupo lógico; `make check` en verde antes de la PR.
