# Implementation Plan: Agente con herramientas para el chat de Carvajal

**Branch**: `feat/agente-tool-calling` | **Date**: 2026-11-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/044-agente-tool-calling/spec.md`

## Summary

El chat pasa de «un enrutador por reglas elige una ruta y el LLM redacta» a «DeepSeek
decide y encadena consultas tipadas, y redacta solo con sus resultados». Se construye un
segundo grafo LangGraph (`agent` ⇄ `tools`) junto al actual, seleccionable con
`AGENT_MODE=routes|tools` (por defecto `routes` hasta aprobar el informe). Las cinco
herramientas envuelven funciones deterministas que ya existen; la única refactorización de
dominio es exponer una entrada estructurada del motor de cotización, que hoy interpreta
texto libre. El streaming se hace con `graph.stream(stream_mode=["messages", "custom"])`:
los tokens del paso final van al SSE `token` y las herramientas emiten un evento nuevo
`status` con mensajes amigables. Saludos y preferencias siguen por el atajo de reglas; si
DeepSeek falla, el turno cae al grafo actual sin redacción libre (respuesta determinista).
Persistencia, idempotencia y replay se conservan; una cotización mostrada = un folio.

## Technical Context

**Language/Version**: Python 3.13 (src-layout, `uv`)

**Primary Dependencies**: LangGraph 1.2.22 (`StateGraph`, `ToolNode`, `get_stream_writer`),
langchain-core 1.8, langchain-openai 1.7.4 (`ChatOpenAI.bind_tools` contra DeepSeek),
FastAPI + Jinja2 + HTMX, LangSmith 0.14 (opcional). Sin dependencias nuevas.

**Storage**: PostgreSQL 16 (mensajes, `quotes`, checkpointer y Store de LangGraph), Qdrant
(768 d, coseno). Sin migraciones: `quotes` admite varias filas por `message_id` y el
resultado comercial ya vive en `messages.sources` (JSONB).

**Testing**: pytest (AAA) con un chat falso que emite `tool_calls` guionizados; pruebas de
contrato de herramientas; `make check`; skill `evaluar-agente` + `carvajal eval-agent` nuevo.

**Target Platform**: servidor Linux (Docker Compose `prod-up`), navegadores Chrome/Firefox.

**Project Type**: aplicación web (FastAPI SSR + SSE) con paquete de dominio `carvajal`.

**Performance Goals**: SC-006 (primer texto < 5 s y fin < 20 s en p95), SC-006a (estado < 1 s).

**Constraints**: temperatura 0; ≤ 4 llamadas a herramientas y ≤ 45 s por turno; costo
≤ 3× el actual (SC-008); sin PII en trazas de producción; CSP sin `unsafe-inline`.

**Scale/Scope**: piloto de un solo nodo; corpus núcleo de 24 documentos en `main` (con una ampliación en curso) + capa extendida de 479;
catálogo CO (29 precios, 29 sedes) y MX (98 referencias); ≥ 50 preguntas de generalización.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio / restricción | Evaluación | Estado |
|---|---|---|
| I. La constitución manda | El plan propone dos enmiendas (abajo) en el mismo PR de implementación. | ⚠️ Enmienda |
| II. El corpus es el contrato | No cambia el corpus, el catálogo ni sus capas; `buscar_corpus` respeta la prioridad núcleo → extendida. | ✅ |
| III. Sin fuentes no hay respuesta | Las herramientas devuelven solo datos con procedencia; las citas `[n]` se numeran por turno (FR-013); temperatura 0; abstención determinista sin redacción cuando no hay evidencia (FR-004, «compuerta de evidencia»). **Choca con la letra** de «en la ruta documental sin contexto no se llama al LLM»: el modelo decide antes de buscar. | ⚠️ Enmienda 1 |
| III. Prompts medidos antes de adoptarse | El prompt del agente se mide con `eval-agent` y el conjunto de generalización antes de cambiar el valor por defecto (FR-015, SC-003). | ✅ |
| III. Flujo comercial con datos publicados | Las herramientas comerciales leen solo el catálogo y el contrato comercial vigentes. | ✅ |
| IV. Calidad automatizada | `make check`, `ty`, pytest sin red con chat falso; las evaluaciones con red son opcionales y explícitas. | ✅ |
| V. Captura responsable | No aplica (no hay captura). | ✅ |
| VI. Cambios por PR | Rama `feat/agente-tool-calling`, PRs pequeños por fase, el agente no mergea. | ✅ |
| Restricción «router `direct`/`tool`/`rag`» | Se añade el modo `tools`; el router por reglas queda como atajo y respaldo. | ⚠️ Enmienda 2 |
| Restricción «sin framework JS» | El evento `status` se maneja en `chat-stream.js` existente. | ✅ |
| Privacidad en monitoreo (038) | Las trazas de herramientas registran nombre, orden, éxito y latencia; argumentos y resultados solo con `content=full` (desarrollo). | ✅ |

**Enmienda 1 (principio III)**: sustituir «En la ruta documental (`rag`) sin contexto
recuperado no se llama al LLM» por «Sin evidencia recuperada, la respuesta documental es la
abstención declarada y el modelo no redacta afirmaciones del corpus; en el modo `tools` el
modelo puede decidir qué consultar, pero una compuerta determinista sustituye su redacción
por la abstención cuando ninguna consulta del turno devolvió evidencia».

**Enmienda 2 (restricciones técnicas)**: «Agente LangGraph con `PostgresSaver` y
`PostgresStore`; modo `routes` (router `direct`/`tool`/`rag`/`commercial`) y modo `tools`
(spec 044: el modelo encadena consultas tipadas; el router por reglas es atajo y respaldo)».

**Gate**: pasa con las dos enmiendas justificadas en *Complexity Tracking*. Re-evaluado
tras la Fase 1: sin cambios (el diseño no añade almacenamiento, dependencias ni rutas
públicas nuevas).

## Project Structure

### Documentation (this feature)

```text
specs/044-agente-tool-calling/
├── plan.md              # este archivo
├── research.md          # Fase 0: decisiones técnicas
├── data-model.md        # Fase 1: estado del agente, resultados y cotizaciones
├── quickstart.md        # Fase 1: validación de punta a punta
├── contracts/
│   ├── agent-tools.md   # esquema y semántica de las 5 herramientas
│   ├── sse-events.md    # evento `status` nuevo y cambios a `done`/`products`
│   └── eval-agent.md    # formato del conjunto de generalización y del informe
└── tasks.md             # Fase 2 (/speckit.tasks)
```

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── agent/
│   ├── graph.py                 # modo routes (sin cambios de comportamiento)
│   └── toolagent/               # NUEVO: modo tools
│       ├── __init__.py
│       ├── graph.py             # build_tool_agent: recall → shortcut → agent ⇄ tools
│       ├── tools.py             # buscar_corpus, buscar_producto, cotizar, buscar_sedes, indicador_financiero
│       ├── sources.py           # registro de fuentes del turno y numeración [n]
│       ├── status.py            # mensajes amigables por herramienta (FR-018)
│       ├── evidence.py          # compuerta de evidencia (FR-004)
│       └── fallback.py          # respuesta determinista sin redacción (FR-009)
├── quotation/engine.py          # + quote_request(): entrada estructurada; quote() la reutiliza
├── prompts.py                   # + AGENT_SYSTEM
├── infrastructure/settings.py   # + agent_mode, agent_max_tool_calls, agent_turn_timeout_s
└── cli.py                       # + eval-agent

apps/api/
├── agent_runtime.py             # construye el grafo del modo configurado
├── chat/agent.py                # + ToolAgentRunner.stream() → eventos de agente
├── chat/service.py              # rama por modo, `status`, varias cotizaciones, respaldo
├── templates/_productos.html    # itera `quotes` (compatibilidad con `quote`)
└── static/chat-stream.js        # maneja `status`

data/eval/generalization_questions.json   # NUEVO, congelado antes de implementar
tests/
├── test_tool_agent_graph.py     # bucle, límites, deduplicación, compuerta, atajo
├── test_tool_agent_tools.py     # contrato de herramientas y reglas duras
├── test_tool_agent_stream.py    # SSE: status, tokens, respaldo, replay, folios
├── test_quotation_request.py    # paridad quote() ↔ quote_request()
├── test_eval_agent.py           # runner de evaluación sin red
└── test_hard_rules_modes.py     # reglas duras parametrizadas en routes y tools
```

**Structure Decision**: el modo `tools` vive en un subpaquete nuevo `agent/toolagent/` para
no tocar el grafo actual, que sigue siendo el valor por defecto, el atajo y el respaldo.
`apps/api/chat/` solo gana la rama por modo; persistencia y replay no cambian de forma.

## Fases de entrega (para `/speckit.tasks`)

1. **F0 — Conjunto de generalización** (bloqueante): borrador ≥ 50 preguntas sin mirar las
   reglas → revisión y congelamiento del owner (FR-016).
2. **F1 — Dominio**: `quote_request()` con paridad 12/12; herramientas con pruebas de
   contrato y reglas duras (FR-002, FR-003). Incluye la **corrección del caso «tinto»**
   (hallado el 2026-11-23 durante el plan): «2.000 vasos de 7 onzas para tinto, Bogotá»
   cotiza hoy CO-VRP-07, un vaso de PP para bebida fría, porque el regex no deduce que el
   tinto es caliente y el motor no pregunta la temperatura. Se corrige en ambos modos:
   (a) `quote_request()` devuelve `needs_input` si falta la temperatura y el uso no la
   implica; (b) «tinto», «aromática», «chocolate» y afines implican bebida caliente en
   `commercial_intent.py`; (c) las cinco frases del hallazgo entran como casos dorados en
   `data/eval/cotizacion_cases.json`. Va primero dentro de F1 y en su propio commit `fix:`.
3. **F2 — Grafo `tools`**: atajo, bucle, límites, deduplicación, compuerta de evidencia,
   numeración de fuentes, historial como mensajes (FR-001, 004–008, 010, 013).
4. **F3 — Servicio y UI**: SSE `status`, varias cotizaciones con folio, respaldo
   determinista, replay, monitoreo (FR-009, 011, 012, 012a, 014, 018).
5. **F4 — Evaluación**: `eval-agent` con ambos modos, informe comparativo con costo
   (FR-015, 017; SC-001…SC-008) y enmiendas a la constitución.
6. **F5 — Retiro** (después de la entrega, con su propio PR): ver *Convivencia y retiro*.

## Convivencia y retiro del modo `routes`

**Qué convive.** `AGENT_MODE` (global, se lee al arrancar) elige qué grafo atiende los
turnos. Con `tools` activo, el grafo `routes` sigue trabajando en dos casos: el atajo
(saludos, capacidad, preferencias) y el respaldo cuando DeepSeek falla. `eval-agent`
construye ambos grafos en el mismo proceso, sin importar `AGENT_MODE`.

**Pruebas compartidas.** Toda prueba de reglas duras (materiales prohibidos, precio ausente
sin estimar, total pendiente, abstención, fuente oficial/tercero, idempotencia y folios) se
escribe una sola vez, parametrizada con `mode ∈ {routes, tools}`, en
`tests/test_hard_rules_modes.py`. Una regla que pase en un modo y falle en el otro bloquea
el PR. Mientras convivan, todo cambio en prompts, tarjetas o cotización corre ambos modos.

**Criterio para cambiar el valor por defecto.** El informe de `eval-agent` cumple SC-001…
SC-008 y el owner lo aprueba; entonces `AGENT_MODE` pasa a `tools` en `.env.example`,
`compose.prod.yml` y `Settings`.

**Observación.** Dos semanas con `tools` por defecto sin regresiones: cero violaciones de
material o datos sin fuente en trazas revisadas, tasa de respaldo < 2 % de los turnos y sin
aumento de «No útil» frente a las dos semanas previas.

**Qué se retira (F5).**

- Redacción por rutas: nodos `rag`, `tool` y `commercial` como caminos de respuesta del
  grafo `routes`, el caso `commercial_mixed`, `mixed_notice` y `build_conversation_prompt`
  como formato de historial.
- `LLMRouter`, `DecisionRouter` y `agent_router`/`decision_model` en `Settings` (servían para
  comparar enrutadores en la 036); `eval-router` pasa a evaluar solo el atajo.
- La interpretación por regex que solo usaba la ruta comercial y no necesita el atajo ni el
  respaldo, previa verificación con `tgrep` de que no tiene otros consumidores.

**Qué se conserva.**

- `decide_route`, reducido a atajo y respaldo.
- El flujo comercial determinista (`run_commercial`, `quote()` sobre texto) que usa el
  respaldo, y `quote_request()` que usan las herramientas.
- `AGENT_MODE` como interruptor de emergencia, documentado en `docs/despliegue-ovh.md`.

**Documentación del retiro.** El PR de F5 actualiza `CONSTITUTION.md` (estado técnico y
restricciones), `specs/README.md` y deja la 044 como *Implemented (as-built)*.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Enmienda 1: el modelo se llama antes de saber si hay evidencia | Es la esencia de la spec: el modelo decide qué consultar y puede reformular (US1.4). | Mantener «no llamar al LLM sin contexto» obliga a decidir la búsqueda con reglas, que es justo lo que falla con formulaciones no previstas. La garantía real (no afirmar sin evidencia) se conserva con la compuerta determinista. |
| Enmienda 2: dos modos de agente conviven | FR-015 exige comparar ambos sobre el mismo corpus antes de cambiar el valor por defecto, y el modo `routes` es el respaldo (FR-009). | Reemplazar el grafo actual impide medir la línea base y deja sin respaldo una caída de DeepSeek. |
