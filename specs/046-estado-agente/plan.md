# Implementation Plan: Separar historial visible, preferencias y estado técnico del agente

**Branch**: `refactor/046-estado-agente` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/046-estado-agente/spec.md`

## Summary

Separar por escrito y en el código las tres capas que hoy se llaman «memoria»: historial
visible en PostgreSQL, preferencias del visitante en el Store y estado técnico de la
ejecución del grafo en el checkpointer. Primero se caracteriza el comportamiento actual
con PostgreSQL real (testcontainers); después se retira la copia de turnos que el
adaptador escribe en el checkpoint, **solo si** las pruebas demuestran que nada depende de
ella. No se toca el esquema, no hay migraciones destructivas y se conservan LangGraph, las
rutas del router, los seguimientos, las preferencias y los eventos SSE.

## Technical Context

**Language/Version**: Python 3.13+
**Primary Dependencies**: FastAPI, Jinja2/HTMX, LangGraph + LangChain, SQLAlchemy 2.0,
PostgreSQL 16, Qdrant, pytest, testcontainers
**Storage**: PostgreSQL (conversaciones, mensajes, checkpointer y Store), Qdrant (índice
vectorial, fuera del alcance de esta fase)
**Testing**: pytest; `@pytest.mark.integration` con testcontainers (`pgvector/pgvector:pg16`)
**Target Platform**: Linux, servicio web de un solo proceso (hoy; el goal pasa a 3 workers
en otra fase)
**Project Type**: aplicación web con paquete de dominio/adaptadores
**Performance Goals**: sin cambio en latencia del chat; la semilla de hasta 16 mensajes ya
existe y no se amplía
**Constraints**: no borrar datos históricos, no migraciones destructivas, no tocar
`apps/api/views/`, `static/`, `templates/` ni `application/sync.py` (otros PR); cambios
quirúrgicos en `apps/api/chat/service.py` (otro PR quita el embedder del constructor)
**Scale/Scope**: 1 adaptador del agente, 1 servicio de chat, 1 grafo, ~5 módulos de prueba

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cumple | Evidencia en este plan |
|---|---|---|
| I. La constitución manda | ✅ | Se añade la fila de decisión en `CONSTITUTION.md` §9 en el mismo PR; la spec no contradice el stack |
| II. El corpus es el contrato | ✅ | No se toca `data/gold/`, ni la ingesta; solo estado del chat |
| III. Sin fuentes no hay respuesta | ✅ | La ruta `rag`, las citas y la abstención no cambian (FR-012) |
| IV. Calidad automatizada | ✅ | `make check` + `make test-integration` + `node --test tests/js/test_chat_voice.cjs` |
| V. Captura responsable | ✅ | Fuera de alcance; no se toca captura |
| VI. Cambios por Pull Request | ✅ | Rama `refactor/046-estado-agente`, PR a `dev`, sin merge ni aprobación |

Sin violaciones; no hace falta `Complexity Tracking`.

## Las tres capas (documento de decisión)

| Capa | Qué guarda | Autoridad | Dónde vive | Quién escribe | Quién lee | Qué NO es |
|---|---|---|---|---|---|---|
| **Historial visible** | Turnos usuario→asistente que el visitante ve: pregunta, respuesta, fuentes, `status`, `client_turn_id`, dueño y fechas | **PostgreSQL** (absoluta) | `conversations` y `messages` | `ChatService` al cerrar el turno (`append_turn`) y al cortarlo (`status=incomplete`) | `ChatService.history`, panel de hilos, semilla del grafo (`_seed_turns`) y replay idempotente | No es preferencia ni estado técnico; no se escribe desde el grafo |
| **Preferencias del visitante** | Declaraciones del tipo «prefiero respuestas breves», máximo 10, sin duplicados | **Store del agente** (namespace por dueño) | `PostgresStore` en producción; `InMemoryStore` en pruebas | Nodo `route` del grafo (`_remember`) al detectar la preferencia | Nodo `recall` (`_preferences`), que las inyecta en el prompt | No es historial: sobrevive al hilo nuevo y no cruza de dueño |
| **Estado técnico del grafo** | Decisión de ruta, intención, consulta, prompt, fuentes, herramienta y contexto sembrado del hilo | **Ninguno para el producto**; traza de ejecución | Checkpointer de LangGraph (`PostgresSaver`/`SqliteSaver`) | La propia invocación del grafo | Solo inspección/diagnóstico; ninguna decisión del turno siguiente lo lee | No es historial ni preferencias; si desaparece, el chat sigue con PostgreSQL + Store |

**Regla de autoridad**: ante discrepancia, manda PostgreSQL para lo visible y el Store para
las preferencias. El checkpointer no puede ser fuente de ninguna de las dos (FR-003).

### La copia que se evalúa

Hoy `GraphAgentRunner.record()` escribe en el checkpoint `turns`, `answer` e `incomplete`
después de cada turno, y `recall` cae a `state.get("turns")` solo cuando `seed_turns` viene
vacío. Esa copia es la que se retira si la caracterización demuestra que su único lector es
ese respaldo:

1. En producción `ChatService` siempre pasa `seed_turns` (lista, aunque sea vacía) y solo
   cae al checkpoint cuando la semilla está vacía → primer turno de un hilo (checkpoint
   también vacío) o turno posterior a una persistencia fallida.
2. No existe otro lector: ningún nodo lee `answer` ni `incomplete`, y `turns` persistido se
   sobrescribe en cada invocación.
3. El respaldo no cubre un caso que PostgreSQL no cubra ya: si la persistencia falló, la
   lectura del historial también falla, y el turno no puede continuar.

**Criterio de decisión**: si las pruebas de caracterización (incluida la de checkpointer
vacío y la de copia envenenada) pasan sin la copia, se retira; si alguna conducta depende
de ella, se conserva y este plan se actualiza con la razón.

## Phase 0: Investigación y línea base

- **R1 — Caracterización antes de tocar código.** El comportamiento actual se fija con
  pruebas de integración con PostgreSQL real (testcontainers) y el grafo real
  (`GraphAgentRunner` + `PostgresSaver` + `PostgresStore`), con embedder, vector store y
  LLM falsos para que la prueba sea determinista y rápida.
- **R2 — Alternativas para la copia del checkpoint**:
  - (a) conservar la copia y solo documentar las capas: mínima, pero deja dos escrituras y
    una ambigüedad que el goal pide resolver.
  - (b) retirar `record()` y el respaldo de `recall`: una sola escritura del turno; el
    checkpointer se queda como traza técnica. **Elegida si la evidencia acompaña.**
  - (c) retirar el checkpointer entero: descartada; el goal pide conservar LangGraph y el
    estado técnico, y otra fase puede necesitarlo.
- **R3 — Sin migraciones.** El esquema de mensajes y las tablas del checkpointer no
  cambian; no se borra ninguna fila histórica (FR-011). `PostgresSaver.setup()` sigue
  creando sus tablas si no existen.
- **R4 — Reintentos.** La idempotencia sigue en PostgreSQL (`client_turn_id` + índice
  parcial); el replay no toca el grafo ni el checkpointer.
- **R5 — Reinicios.** La recuperación tras reinicio se prueba con un checkpointer nuevo y
  la misma base: la semilla viene de PostgreSQL. Se añade además una prueba que demuestra
  que la copia no se lee aunque exista.

## Presupuesto de conexiones por worker (ensayo de la fase 047b)

El ensayo de 3 workers puede correr con el `agent_runtime` cacheado por proceso
(`@cache` en `apps/api/agent_runtime.py`): cada worker construye su propio grafo en la
primera pregunta y comparte con los demás el checkpointer y el Store, que viven en
PostgreSQL. Presupuesto por worker:

| Recurso | Conexiones | Notas |
|---|---|---|
| `PostgresSaver` (checkpointer) | 1 conexión larga | Se abre una vez por proceso; `setup()` corre una vez por worker |
| `PostgresStore` (preferencias) | 1 conexión larga | Se abre una vez por proceso; `setup()` corre una vez por worker |
| Motor SQLAlchemy del chat | pool 5 + 10 de desborde | Sesiones cortas; se cierran antes de llamar al LLM |
| **Total por worker (pico)** | **≤ 17** | 3 workers ≈ **≤ 51 conexiones**, dentro de `max_connections` 100 |

Riesgos para el ensayo 047b: (a) los tres workers pueden ejecutar `PostgresSaver.setup()`
y `PostgresStore.setup()` a la vez en su primera pregunta —son idempotentes y usan
`IF NOT EXISTS`, pero conviene calentar el runtime de forma secuencial o en el arranque de
cada worker para no competir por los índices—; (b) ningún estado del turno se comparte por
memoria entre workers, porque la autoridad es PostgreSQL y el Store, así que una pregunta
puede caer en cualquier worker; (c) el presupuesto asume `agent_runtime` cacheado, no
reconstruido por petición.

## Phase 1: Diseño

### Matriz de caracterización (antes y después)

| # | Escenario | Requisito | Prueba propuesta |
|---|---|---|---|
| C1 | Hilo nuevo: primera pregunta crea hilo y un turno; sin memoria heredada | FR-005 | `test_hilo_nuevo_empieza_sin_memoria` |
| C2 | Seguimiento elíptico tras un turno respondido | FR-004, FR-012 | `test_seguimiento_eliptico_usa_el_turno_anterior` |
| C3 | Preferencia declarada en el hilo A se aplica en el hilo B del mismo dueño | FR-002 | `test_preferencia_sobrevive_al_hilo_nuevo` |
| C4 | Otro visitante no hereda la preferencia ni ve el hilo ajeno | FR-002, FR-009 | `test_visitantes_aislados_en_preferencias_e_hilos` |
| C5 | Reinicio del runtime: checkpointer nuevo, historial y seguimiento desde PostgreSQL | FR-001, FR-006 | `test_reinicio_recupera_historial_y_seguimiento` |
| C6 | Reintento con `client_turn_id`: replay sin agente ni LLM, sin duplicar | FR-007 | `test_reintento_idempotente_no_duplica_ni_llama_al_llm` |
| C7 | Turno cortado tras el primer token: `incomplete` persistido; replay lo declara | FR-008 | `test_turno_incompleto_se_persiste_y_el_replay_lo_declara` |
| C8 | Turno siguiente a uno incompleto lo trata como contexto, no como respuesta | FR-008 | `test_el_turno_incompleto_no_se_confunde_con_respuesta` |
| C9 | La copia del checkpoint no se lee: checkpointer vacío + semilla de PostgreSQL | FR-003, FR-010 | `test_el_grafo_no_depende_de_la_copia_del_checkpointer` |
| C10 | Checkpoint con copia envenenada + semilla: el resultado sale de la semilla | FR-003, FR-010 | `test_una_copia_envenenada_no_altera_el_turno` |
| C11 | Checkpoint con copia de turnos y semilla vacía: el grafo no reutiliza la copia como historial (**prueba de la retirada**: falla antes, pasa después) | FR-003, FR-010, SC-002 | `test_el_checkpoint_no_es_historial_sin_semilla` |
| C12 | Tras un turno, el estado técnico ya no guarda `answer` ni `incomplete` ni una copia acumulada de turnos | FR-003, SC-003 | `test_el_estado_tecnico_no_guarda_la_respuesta` |

**Evidencia de la retirada**: C9 (`test_el_historial_de_postgres_basta_con_checkpoint_vacio`)
prueba que el historial de PostgreSQL basta con un checkpointer distinto y vacío; C10
(`test_la_semilla_manda_sobre_la_copia_del_checkpoint`) prueba que la semilla manda sobre
una copia envenenada; C11 es la prueba roja→verde de que el grafo no lee la copia; C12
comprueba que el estado técnico deja de guardar la respuesta.

**Nota de integración con `main`**: esta rama parte de `origin/dev`, que aún no tiene los
arreglos de trazas y manos libres de `main` (los sincroniza el PR #201). El diff de esta
fase en `apps/api/chat/service.py` se limita a quitar las dos llamadas a `runner.record` y
el párrafo del docstring; no toca el bloque de streaming, la acumulación de la traza ni la
degradación a `fallback`. Si #200 o #201 entran antes, el rebase solo repone esas líneas.

### Cambios de código (posteriores a la caracterización)

| Archivo | Cambio | Riesgo |
|---|---|---|
| `packages/core/src/carvajal/agent/graph.py` | `recall` usa solo `seed_turns`; el estado técnico sigue en el checkpoint | bajo |
| `packages/core/src/carvajal/agent/state.py` | retirar `answer`/`incomplete` del contrato de estado, que solo escribía `record` | bajo |
| `apps/api/chat/agent.py` | retirar `record` del protocolo y `turns` de `AgentTurn`; `GraphAgentRunner` deja de escribir la copia | medio (contrato interno) |
| `apps/api/chat/service.py` | quitar las dos llamadas a `runner.record` (cambio quirúrgico, sin reformatear) | bajo |
| pruebas del runner y fakes | adaptar a un protocolo sin `record`; añadir C9 y C10 | bajo |
| `CONSTITUTION.md` §9 | fila de decisión: tres capas y retirada de la copia | ninguno |

### Contratos

- `AgentRunner.prepare(owner_key, thread_id, question, seed_turns) -> AgentTurn` se
  conserva tal cual (FR-004).
- `AgentRunner.record(...)` desaparece del contrato; el servicio deja de necesitarlo
  (FR-003, FR-010).
- `ChatService.stream` mantiene los eventos `open/trace/meta/token/fallback/products/done/
  error` y los datos de cada uno (FR-012).
- El estado persistido por el checkpointer deja de tener `answer` e `incomplete`; `turns`
  será el contexto sembrado de la invocación, no una copia acumulada (SC-003).

### Data model (capas)

- `messages`: `id`, `conversation_id`, `role`, `content`, `sources`, `created_at`,
  `status`, `client_turn_id`. Sin cambios de esquema.
- Store: `namespace=("carvajal","preferences",owner_key)`, `key="profile"`,
  `value={"items": [...]}`. Sin cambios.
- Checkpoint: canales técnicos del grafo. Sin cambios de esquema; se dejan de escribir
  `turns` acumulados, `answer` e `incomplete`.

## Project Structure

### Documentation (this feature)

```text
specs/046-estado-agente/
├── spec.md
├── plan.md
├── tasks.md
├── analysis.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
apps/api/
├── agent_runtime.py          # sin cambios (checkpointer y Store se conservan)
└── chat/
    ├── agent.py              # protocolo sin record; AgentTurn sin turns
    └── service.py            # sin llamadas a record (quirúrgico)

packages/core/src/carvajal/agent/
├── graph.py                  # recall solo con seed_turns
└── state.py                  # sin answer/incomplete

tests/
├── test_chat_state_layers.py # caracterización y evidencia (Postgres real)
├── test_agent_graph.py       # adaptado al protocolo sin record
├── test_chat_service.py      # fakes sin record; aserción de persistencia
├── test_chat_tracing.py      # fakes sin turns/record
├── test_monitoring_smoke.py  # fake sin turns/record
└── test_monitoring_e2e.py    # fake sin turns/record
```

**Structure Decision**: se conserva la estructura actual (apps/api + packages/core +
tests); el refactor es de contrato interno, no de arquitectura de carpetas.

## Reversión

Revertir el commit `refactor(046):` devuelve `record()` y el respaldo de `recall`; la spec
y las pruebas de caracterización se quedan y siguen verdes. No hay migraciones, ni borrado
de filas, ni cambios de esquema que deshacer. Si la retirada no se autoriza, el PR se
reduce a la caracterización y a la documentación de las tres capas.

## Riesgos y mitigación

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Algún comportamiento dependa de la copia | baja | C9 y C10 lo demuestran; si falla, no se retira (FR-010) |
| Un worker lea un checkpointer de otro punto de la ejecución | baja | El checkpointer no es autoridad; todo lo visible va a PostgreSQL |
| Un fake de prueba o script use `AgentTurn.turns` | media | Se actualizan los 5 usos detectados; `make typecheck` lo verifica |
| Conflictos con otro PR en `service.py` | media | Cambio quirúrgico de dos líneas, sin reformatear; el constructor del embedder no se toca |
| Pérdida del contexto de seguimiento en hilos largos | baja | La semilla conserva el recorte `agent_history_turns` y `_seed_turns` (16 mensajes) |
