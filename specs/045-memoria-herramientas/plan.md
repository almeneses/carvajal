# Implementation Plan: Memoria del agente y 20 herramientas

**Branch**: `feat/045-memoria-herramientas` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/045-memoria-herramientas/spec.md`

## Summary

Se añaden tres cosas al modo `tools` de la 044, sin cambiar qué se puede afirmar:

1. **Resumen acumulado** de los turnos que salen de la ventana de 8: lo calcula un
   `SummaryWorker` en segundo plano, encolado por `ChatService` justo después de persistir
   cada turno; lo deja como borrador en el Store y el grafo lo adopta en el `recall`
   siguiente dentro de un canal del estado del hilo (`conversation_summary`), con
   `turn_count` para saber qué turnos de la semilla faltan. Si falla o llega tarde, un
   resumen determinista con las preguntas literales de la persona cubre el hueco. El modo
   por defecto es `deterministic` hasta medir el prompt del resumidor del modelo (una
   corrida). El primer texto no espera nada.
2. **Memoria por categorías** (nombre, empresa, ciudad habitual, formato preferido, notas)
   en un namespace nuevo del Store, con tres herramientas (`recordar_preferencia`,
   `listar_memoria`, `olvidar_preferencia`), filtro determinista de datos sensibles, ciudad
   habitual solo como sugerencia (también en `buscar_sedes`), caducidad de 180 días,
   migración perezosa del formato anterior, el atajo por reglas solo como respaldo para
   recordar y como atajo de «olvidar todo», y un marcador de olvido que impide que lo
   olvidado reaparezca en el resumen.
3. **20 herramientas** declaradas con `@tool(args_schema=...)` desde un registro central:
   `tools.py` pasa a ser el paquete `tools/` (las seis de la 044 se mueven en bloque; las 14
   nuevas, una por módulo, implementables en paralelo). Las 11 nuevas leen solo datos del
   repositorio (catálogo, guía de materiales, directorio, indicadores, historia, núcleo,
   cotizaciones guardadas y calendario). Todas pasan por la compuerta, los límites y la
   traza de la 044.

La medición combina pruebas sin red para todo lo determinista y una sola corrida con
proveedor real de un conjunto nuevo por herramienta, del v3 y de la simulación corta.

## Technical Context

**Language/Version**: Python 3.13 (src-layout, `uv`)

**Primary Dependencies**: LangGraph 1.2.22 (`StateGraph`, `PostgresSaver`,
`PostgresStore`, `get_stream_writer`), langchain-core 1.8.0 (`langchain_core.tools.tool`
con `args_schema`), langchain-openai 1.7.4 (DeepSeek), Pydantic 2.14.1, FastAPI. Sin
dependencias nuevas.

**Storage**: PostgreSQL 16: mensajes y `quotes` (sin cambios de esquema), checkpointer
(canal nuevo `conversation_summary`) y Store de LangGraph (namespaces nuevos
`("carvajal","memory",owner)` y `("carvajal","summary",owner)`). Sin migraciones Alembic.
Archivo curado nuevo: `data/institucional/empresas.json`.

**Testing**: pytest (AAA) sin red: `ScriptedToolChat`, `InMemoryStore`, `quote_lookup` en
memoria, resumidor falso. `make check`, `make eval-cotizador`; con proveedor real, una vez:
`carvajal eval-agent` (conjunto nuevo y v3) y `carvajal simulate-chat` (r3).

**Target Platform**: servidor Linux (Docker Compose `prod-up`); web y WhatsApp (MCP).

**Project Type**: aplicación web (FastAPI SSR + SSE) con paquete de dominio `carvajal`.

**Performance Goals**: primer texto p95 < 5 s y ≤ 0,5 s sobre la ronda 4 (4,21 s); la
memoria añade como mucho dos lecturas del Store en `recall`.

**Constraints**: temperatura 0; ≤ 4 consultas y ≤ 45 s por turno; resumen fuera del camino
crítico; ningún dato sensible guardado; valores de memoria fuera de trazas y estados;
créditos limitados (una corrida por medición); entrega en unos 2 días.

**Scale/Scope**: 20 herramientas; 5 categorías de memoria; resumen ≤ 1 500 caracteres;
catálogo de 200 referencias, 29 precios CO, 29 sedes en 10 ciudades, 10 registros de
indicadores, 68 hitos históricos, unas 11 empresas y marcas curadas.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluado contra `.specify/memory/constitution.md` (versión 2.7.0) y `CONSTITUTION.md`.

| Principio / restricción | Evaluación | Estado |
|---|---|---|
| I. La constitución manda | El plan propone tres enmiendas (abajo). Cada una se aplica en el commit que introduce el cambio (A con los datos institucionales, T023; C con el registro de las herramientas de memoria, T056; B con `fecha_actual`, T083), y la PR se integra con *squash*: en `main` queda un solo commit con el código y las enmiendas. | Enmienda |
| II. El corpus es el contrato | No cambia `data/gold/`, el catálogo ni la capa extendida. `historia_carvajal` lee Gold sin copiarlo. **Nuevo**: `data/institucional/empresas.json`, derivado solo de Gold con cita literal verificada por prueba: es una capa curada que la constitución no nombra. | Enmienda A |
| III. Sin fuentes no hay respuesta | Las 11 herramientas nuevas registran una fuente por dato; la compuerta, la abstención y el filtro de `sin_consulta` se conservan. Memoria y resumen son datos y **no respaldan datos de Carvajal** (FR-006, FR-017). Fuente nueva de tipo `sistema` para el reloj y el calendario: la constitución solo nombra oficial y tercero. | Enmienda B |
| III. Prompts medidos | Las descripciones y el bloque de agrupación de `AGENT_SYSTEM` se miden con el conjunto nuevo y el v3 antes de entregar (FR-030, FR-031). `SUMMARY_SYSTEM` se mide una vez con el escenario de 15 turnos y el caso de inyección (T097); hasta entonces el modo por defecto es `deterministic`. | OK |
| III. Flujo comercial con datos publicados | `ficha_producto`, `comparar_referencias`, `convertir_presentacion` y `ciudades_con_cobertura` leen solo catálogo y contrato publicados; `consultar_cotizacion` solo lee y solo cotizaciones de procedencia `real` (la simulación de la 042 nunca vuelve como fuente). Sin estimar ni convertir entre monedas. | OK |
| IV. Calidad automatizada | Todo lo nuevo con pruebas sin red; `make check` en verde; `uv` sin dependencias nuevas. | OK |
| V. Captura responsable | No aplica. | OK |
| VI. Cambios por PR | Rama `feat/045-memoria-herramientas`; PR a `main` con la skill `abrir-pr`; el agente no mergea. | OK |
| Restricción «Agente LangGraph con `PostgresSaver` y `PostgresStore`» | Se conserva; se amplía lo que guarda el Store (memoria por categorías y borrador del resumen). | Enmienda C |
| Contrato 044 «herramientas sin efectos persistentes» | Excepción explícita para las dos herramientas de memoria que escriben (research R7). | Enmienda C |
| Privacidad del monitoreo (038) | `_outcome` reduce los argumentos de memoria a la categoría en origen: los pasos `tool` de memoria no llevan valores ni con `content=full`. El mensaje literal de la persona sigue en `messages` y en el checkpoint (alcance declarado en FR-012/FR-018). | OK |
| Memoria silenciosa del Cotizador (043) | No cambia; se reutiliza su plazo de 180 días. Sus patrones no cubren «llámame», «suelo comprar en» ni «olvida todo…», así que la memoria del agente tiene patrones propios y `quotation/memory.py` no se toca. | OK |

**Enmienda A (principio II)**: añadir «La capa institucional curada
(`data/institucional/`, spec 045) guarda listas pequeñas derivadas solo del núcleo, cada
fila con su cita literal y su URL de `index.jsonl`, verificadas por prueba contra
`data/gold/`; no añade hechos que el núcleo no diga».

**Enmienda B (principio III)**: añadir «La memoria del dueño y el resumen de la
conversación son datos del usuario: aplican como contexto y estilo, nunca respaldan datos de
Carvajal ni cambian las reglas. Las palabras literales de la persona respaldan solo lo que
ella dijo. Además de oficial y tercero, existe la procedencia `sistema` para datos que
calcula el servidor (fecha y calendario de festivos de Colombia); se cita como tal».

**Enmienda C (restricciones técnicas y estado técnico)**: «Agente LangGraph con
`PostgresSaver` y `PostgresStore`; el estado del hilo guarda un resumen acumulado
(`conversation_summary`) calculado en segundo plano; el Store guarda la memoria del dueño
por categorías, sin datos sensibles y con caducidad de 180 días. El modo `tools` dispone de
20 consultas registradas en `toolagent/tools/registry.py`; solo las de memoria escriben».
Versión de la proyección: 2.8.0 (la fija la primera enmienda aplicada, T023; las demás la
conservan).

**Gate**: pasa con las tres enmiendas justificadas en *Complexity Tracking*. Re-evaluado
tras la Fase 1: sin cambios (sin dependencias, migraciones ni rutas públicas nuevas).

## Project Structure

### Documentation (this feature)

```text
specs/045-memoria-herramientas/
├── plan.md              # este archivo
├── research.md          # Fase 0: R1–R14
├── data-model.md        # Fase 1: resumen, memoria, registro, eventos
├── quickstart.md        # Fase 1: una pregunta por herramienta y escenarios de memoria
├── contracts/
│   ├── agent-tools-045.md   # las 20 herramientas
│   └── memory.md            # memoria de corto y largo plazo
├── evidencia.md         # Fase final (la crea la implementación)
└── tasks.md             # Fase 2
```

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── agent/
│   ├── graph.py                  # recall con resumen y memoria; _preferences/_remember sobre memory/
│   ├── state.py                  # + conversation_summary en AgentState
│   ├── router.py                 # memory_write clasificado; intención memory_forget
│   ├── memory/                   # NUEVO
│   │   ├── __init__.py
│   │   ├── sensitive.py          # find_sensitive, redact (R8)
│   │   ├── store.py              # MemoryProfile, load/remember/forget/lines, migración (R5, R6)
│   │   └── summary.py            # ConversationSummary, deterministic, summarize_llm, adopt (R1–R3)
│   └── toolagent/
│       ├── graph.py              # recall: resumen + memoria; consumidores del registro
│       ├── state.py              # ToolName (20), canales conversation_summary y memory
│       ├── status.py             # plantillas desde el registro
│       ├── evidence.py           # roles desde el registro (memory)
│       ├── evaluation.py         # _tool_backing y ROUTE_TOOLS desde el registro
│       ├── simulation.py         # nombre literal sustituido por el registro
│       └── tools/                # NUEVO paquete (antes tools.py)
│           ├── __init__.py       # reexporta la API pública de tools.py
│           ├── _base.py          # ToolDeps, ToolContext, ToolCallResult, _Draft, _Sources, formato
│           ├── builtin.py        # las 6 de la 044 (movidas en bloque)
│           ├── runner.py         # run_tool, _execute, build_tools con @tool
│           ├── registry.py       # ToolSpec y TOOLS (orden fijo)
│           ├── ficha_producto.py
│           ├── comparar_referencias.py
│           ├── guia_materiales.py
│           ├── convertir_presentacion.py
│           ├── listar_familias.py
│           ├── ciudades_con_cobertura.py
│           ├── consultar_cotizacion.py
│           ├── indicadores_disponibles.py
│           ├── historia_carvajal.py
│           ├── empresas_del_grupo.py
│           ├── fecha_actual.py
│           ├── recordar_preferencia.py
│           ├── listar_memoria.py
│           └── olvidar_preferencia.py
├── quotation/engine.py           # declared_sizes, conversion, published_presentations públicas
├── institutional.py              # NUEVO: carga empresas.json e hitos de historia.md (error declarado si faltan)
├── adapters/storage/chat.py      # history_all() para el SummaryWorker
├── prompts.py                    # AGENT_SYSTEM agrupado; SUMMARY_SYSTEM; DIRECT_INSTRUCTIONS memoria
└── infrastructure/settings.py    # agent_summary_mode/_timeout_s/_max_chars, agent_memory_days

apps/api/
├── agent_runtime.py              # ToolDeps con memory, quote_lookup, institutional; SummaryWorker
├── chat/agent.py                 # record encola; _allowed con user_quotes y memoria
├── chat/service.py               # submit tras cada _persist; _seed_turns con holgura y turn_count; trace
└── chat/summary_worker.py        # NUEVO: ejecutor acotado, idempotente, session_factory y Store propios

data/
├── institucional/empresas.json   # NUEVO (R12), con alias citados
└── eval/herramientas_045.json    # NUEVO, congelado antes de medir (FR-030)

.dockerignore, Dockerfile,        # empaquetado y siembra de data/institucional (T006)
compose.prod.yml, scripts/deploy.sh

tests/
├── golden/tool_schemas_044.json  # golden de las 6 antes del cambio
├── test_tool_registry.py         # registro, Literal, golden, @tool, orden
├── test_memory_sensitive.py
├── test_agent_memory_store.py
├── test_conversation_summary.py
├── test_summary_worker.py
├── test_tool_memory_tools.py
├── test_tool_agent_memory_flow.py
├── test_tool_<nombre>.py         # una por herramienta nueva (11)
├── test_institutional_data.py
├── test_deploy_packaging.py      # data/institucional en la imagen y la siembra
└── (ampliaciones) test_tool_agent_history.py, test_tool_agent_graph.py,
    test_tool_agent_stream.py, test_tool_agent_tools.py, test_chat_service_tools.py,
    test_agent_graph.py, test_agent_router.py, test_tool_agent_status.py,
    test_monitoring_tools.py, test_prompts.py, test_generalization_set.py,
    test_eval_agent.py, test_tool_agent_state.py
```

**Structure Decision**: la memoria es lógica de dominio compartida por los dos grafos y por
el atajo, así que vive en `agent/memory/` y no dentro de `toolagent/`. El trabajo en
segundo plano necesita el repositorio de mensajes y el ciclo de vida del proceso, así que
vive en `apps/api/chat/summary_worker.py`, inyectado en `ChatService`, que es quien ya
tiene el dueño y la fábrica de sesiones al persistir. Las herramientas nuevas van una por módulo para
que se implementen en paralelo sin conflictos; las seis de la 044 se mueven en bloque a
`builtin.py` (research R9).

## Fases de entrega (para `/speckit.tasks`)

1. **F0 — Preparación**: ajustes nuevos, *golden* de las seis herramientas, soporte de
   `new_thread_before` en el ejecutor, conjunto `herramientas_045.json` congelado,
   `empresas.json` curado con su prueba y empaquetado para producción.
2. **F1 — Fundamentos**: paquete `tools/` (movimiento sin cambios de comportamiento),
   registro y `@tool`, `TOOL_NAMES` derivado del registro (el `Literal` crece con cada
   grupo), `ToolDeps`/`ToolContext` ampliados, `state_update`, `_now_bogota`, consumidores
   del registro, filtro de sensibles y enmienda A. Bloquea todo lo demás.
3. **F2 — US1 resumen** (P1).
4. **F3 — US2 memoria** (P1). F2 y F3 pueden avanzar en paralelo tras F1 (tocan
   `recall` en el mismo archivo: una persona a la vez en `graph.py`).
5. **F4 — US3 catálogo** y **F5 — US4 institucional**: 11 herramientas en paralelo, un
   módulo cada una; luego un solo cambio para registrarlas.
6. **F6 — US5 selección y medición**: compuerta de tokens, descripciones, agrupación del
   prompt, mapa de equivalencias, quickstart, corridas únicas con proveedor real (incluida
   la medición de `SUMMARY_SYSTEM`), evidencia, revisión de enmiendas y PR con *squash*.

## Riesgos y mitigaciones

| Riesgo | Mitigación | Medición |
|---|---|---|
| DeepSeek elige peor con 20 herramientas | Descripciones contrastivas, agrupación en el prompt, orden fijo, límite sin cambios (R10) | `herramientas_045.json` ≥ 85 % (SC-006); v3 sin regresión (SC-007) |
| Más tokens por turno | Prefijo estable para la caché de DeepSeek; descripciones nuevas ≤ 300 caracteres; compuerta de estimación antes de redactar (T085) | tokens medios por turno ≤ 1,5 × 6 900 (SC-009) |
| Lo olvidado reaparece en el resumen | Marcador `forgotten_through`, lápida por dueño, borrado de todos los borradores (R16) | prueba de olvidar todo y dos turnos después |
| Memoria envenenada por inyección indirecta | Regla «dicho en la pregunta» y verbo de olvido (R7) | `tests/test_tool_memory_tools.py` |
| `empresas.json` ausente en producción | Empaquetado y siembra (T006); `error` declarado si falta | `tests/test_deploy_packaging.py` |
| Mover `tools.py` rompe importaciones | Movimiento en bloque, reexportación completa, *golden* y las 117 pruebas existentes | `make check` |
| Resumen perdido por escrituras cruzadas | El grafo escribe su canal; el trabajo solo escribe un borrador (R2) | prueba de respaldo `routes` |
| Resumen o memoria como respaldo falso | Solo palabras literales de la persona; memoria solo en turnos de memoria (R4, FR-017) | prueba de nota falsa sobre Carvajal |
| Datos sensibles guardados | Filtro determinista por forma en los cuatro caminos (R8); redacción en origen de los argumentos | SC-004 |
| Ciudad habitual usada sin confirmar | No entra en `_city_said`; `buscar_sedes` adopta la regla; `needs_input` con sugerencia | SC-005 |
| Créditos | Una corrida por medición, sin `routes` ni simulación larga (R13) | — |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Enmienda A: capa institucional curada (`empresas.json`) | `empresas_del_grupo` necesita una lista estructurada que Gold no tiene; cada fila cita Gold literalmente. | Leer `home.md` con expresiones es frágil ante cambios de formato; responder con `buscar_corpus` ya existe y no da una lista completa ni determinista. |
| Enmienda B: memoria y resumen como datos; procedencia `sistema` | La memoria es lo pedido en la spec; sin declarar su papel, la compuerta la trataría como evidencia o la bloquearía. La fecha no es oficial ni de tercero. | No registrar fuente para la fecha hace que la compuerta la abstenga; marcarla como oficial es falso. |
| Enmienda C: herramientas que escriben | Recordar y olvidar a pedido son el núcleo de la US2; el atajo ya escribe hoy. | Diferir la escritura al cierre del turno obliga a superponer operaciones pendientes sin beneficio para la entrega (R7). |
