# Implementation Plan: Router y System One (Jev)

**Branch**: `feat/046-jev-router` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/046-jev-router/spec.md`

> **Base**: se implementa sobre la rama de la 045 (`feat/045-memoria-herramientas`), contra
> su registro de herramientas, su filtro de datos sensibles y su memoria por categorías
> (research R14). Base citada: `88ee0d6` o posterior (hoy `314f9a7`); la 045 se rebasa sobre
> `main` antes de implementar (le faltan `dd35bfc`, `901f59f` y `921460d`).

## Summary

Dos capas nuevas en el modo `tools`, ambas con Jev (TypeSafe System One) y ambas con
respaldo al comportamiento de hoy:

1. **Gatekeeper** en el nodo `shortcut`: reglas primero (saludo, despedida, «olvida todo»,
   preferencia declarada, seguimiento comercial; sin Jev y sin búsquedas en saludos); si no
   deciden, una pregunta **Choice** de Jev con cuatro clases (`herramienta_estructurada`,
   `rag`, `mixta`, `respuesta_directa`) que restringe las consultas del turno mediante un
   grupo nuevo en el registro de la 045 (`route_group`). Con confianza baja o desacuerdo con
   las reglas, **cautela**: las 20 consultas y una instrucción de consultar antes de afirmar
   y pedir aclaración. Sin Jev, reglas + LLM.
2. **Compuerta de afirmaciones** en el streaming (`_TurnStream`), después de la compuerta
   determinista: una pregunta **Noul** sobre la respuesta y los extractos mínimos de
   evidencia; con `p < 0,5`, el texto se sustituye por el mensaje neutro existente. Con la
   compuerta activa, el camino con evidencia retiene el texto completo hasta el veredicto.

Un adaptador propio (`adapters/judge/jev.py`, httpx, plazo 0,8 s, sin reintentos, tope
diario, circuito y libro de llamadas; apagado por defecto y encendido de forma explícita)
implementa dos puertos (`RouteJudge`, `ClaimsJudge`). A TypeSafe solo viajan la pregunta
redactada y una línea de contexto (gatekeeper), y la respuesta sin los datos de la persona,
la evidencia mínima y los datos estructurados que la persona dio (compuerta). La decisión y el veredicto se ven en la traza del chat, en LangSmith y en `/health`.
Score se mide pero no entra en producción; Box no aplica; el modo `routes` no usa Jev.

## Technical Context

**Language/Version**: Python 3.13 (src-layout, `uv`)

**Primary Dependencies**: LangGraph 1.2.22, langchain-core 1.8.0 (`bind_tools` por alcance),
langchain-openai 1.7.4 (DeepSeek), httpx 0.28 (ya dependencia directa), Pydantic 2,
FastAPI. Sin dependencias nuevas. Servicio externo nuevo: Jev de TypeSafe
(`POST https://api.typesafe.ai/v1/systemone`, modelo `jev-1.14.0`).

**Storage**: sin cambios de esquema ni migraciones. Canal nuevo `gatekeeper` en el estado
del grafo `tools` (se reinicia por turno). Estado del cliente de Jev en memoria del proceso.

**Testing**: pytest (AAA) sin red: dobles de `RouteJudge`/`ClaimsJudge`,
`httpx.MockTransport`, `ScriptedToolChat`, fixture *autouse* que apaga Jev y bloquea
conexiones a TypeSafe. Con Jev real, una vez: `carvajal eval-gatekeeper`,
`carvajal eval-claims` y `carvajal eval-agent --mode tools` sobre el v3 (con y sin Jev).

**Target Platform**: servidor Linux (Docker Compose `prod-up`); web y WhatsApp (MCP).

**Project Type**: aplicación web (FastAPI SSR + SSE) con paquete de dominio `carvajal`.

**Performance Goals**: cada llamada a Jev p95 ≤ 0,5 s y plazo duro 0,8 s; fin del turno p95
con Jev ≤ sin Jev + 1,0 s. Con la compuerta activa, el primer texto coincide con el fin del
turno (decisión R6).

**Constraints**: temperatura 0; ≤ 2 llamadas a Jev por turno; ≤ 120 llamadas en toda la
medición; nada de identidad, memoria ni historial hacia TypeSafe; nada de texto en logs ni
trazas; funciona igual sin Jev.

**Scale/Scope**: 4 clases, 3 grupos de consultas sobre las 20 de la 045, 2 preguntas de Jev
(Choice y Noul) y una de medición (Score); 20 + 16 ítems de evaluación nuevos; 51 turnos del
v3.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluado contra `.specify/memory/constitution.md` (versión 2.8.0 de la rama de la 045) y
`CONSTITUTION.md`.

| Principio / restricción | Evaluación | Estado |
|---|---|---|
| I. La constitución manda | Dos enmiendas (abajo), aplicadas en el commit que introduce cada cambio: D con el gatekeeper en el grafo (T026), E con la compuerta de afirmaciones (T038). | Enmienda |
| II. El corpus es el contrato | No cambia `data/gold/`, el catálogo ni las capas. Los conjuntos nuevos (`gatekeeper_046.json`, `claims_046.json`) son de evaluación. | OK |
| III. Sin fuentes no hay respuesta | Se refuerza: una compuerta adicional sobre el texto del camino con evidencia, que hoy no tiene filtro. La compuerta determinista y la abstención se conservan y van primero; ante un fallo de Jev, rige solo la determinista. **Cambia** la retención del camino con evidencia cuando la compuerta está activa (hoy: 48 caracteres). | Enmienda E |
| III. Prompts medidos | Las instrucciones de Choice y Noul, `GATEKEEPER_CAUTION` y `DIRECT_INSTRUCTIONS["gatekeeper_direct"]` se miden antes de entregar: Choice con `gatekeeper_046.json`, Noul con `claims_046.json`, cautela e instrucción directa dentro del v3 con Jev y, sin créditos, reproduciendo los veredictos grabados sobre los ítems de `gatekeeper_046.json` con cautela o directa (`ReplayJudge`, T065). Los textos de Noul parten de los medidos el 2026-11-24 (la referencia a `pregunta` pasa a `dicho_por_la_persona`). | OK |
| III. Temperatura 0 | No cambia (Jev no tiene temperatura). | OK |
| IV. Calidad automatizada | Todo con pruebas sin red; `uv` sin dependencias nuevas; `make check` en verde; CI sin llamadas a Jev (fixture). | OK |
| V. Captura responsable | No aplica. | OK |
| VI. Cambios por PR | Sobre la rama de la 045; PR (propia o junto a la 045, R14) con la skill `abrir-pr`; el agente no mergea. | OK |
| Restricción «Agente LangGraph … dos modos» | El modo `tools` gana el gatekeeper; el modo `routes` no cambia de comportamiento (solo su traza). | Enmienda D |
| Observabilidad (038): «opcional, sin PII» | Metadatos nuevos sin texto; claves en `SAFE_KEYS`; ningún texto enviado a Jev viaja, ni con `content=full`. | OK |
| Privacidad de datos de las personas | Tercero nuevo (TypeSafe, EE. UU.): minimización, redacción y enmascarado (R8); a la compuerta no viaja la pregunta, solo los datos estructurados que la persona dio; Sentry sin variables locales. El DPA se revisa antes de activar en producción (T063) o el owner acepta el riesgo en la PR. | Enmienda D |

**Enmienda D (restricciones técnicas y estado técnico)**: «El modo `tools` tiene un
gatekeeper en el nodo `shortcut`: reglas primero (saludos, despedidas, «olvida todo»,
preferencias y seguimiento comercial, sin búsquedas ni servicios externos) y, si no deciden,
Jev de TypeSafe (System One, `jev-1.14.0` fijado, pregunta Choice) clasifica el turno en
herramienta estructurada, RAG, mixta o respuesta directa y restringe sus consultas; con
confianza baja, cautela con todas las consultas. Jev es opcional en tiempo de ejecución y se
enciende de forma explícita: sin él, reglas + LLM. A TypeSafe solo viajan la pregunta
redactada y una línea de contexto (y, en la compuerta, la respuesta sin datos de la persona y
la evidencia mínima); nunca identidad, memoria ni historial; los
registros guardan solo decisión, probabilidades, latencia, modelo y tokens. El modo `routes`
no usa Jev».

**Enmienda E (principio III)**: «En el modo `tools`, después de la compuerta determinista,
una compuerta de afirmaciones (Jev, pregunta Noul, umbral 0,5) sustituye por el texto neutro
existente toda respuesta del modelo que marca como no respaldada por las consultas del turno.
Una valoración presentada como hecho sin fuente cuenta como no respaldada; un dato
reformateado no. Con esa compuerta activa, el camino con evidencia retiene el texto completo
antes de emitirlo; si Jev no responde, rige la compuerta determinista sola».

Versión de la proyección: 2.9.0 (MINOR) para D y E juntas, con un único Sync Impact Report
que abre T026 y actualiza T038. Si la 045 cambia la versión antes del merge, se recalcula al
rebasar.

**Gate**: pasa con las dos enmiendas justificadas en *Complexity Tracking*. Re-evaluado tras
la Fase 1: sin cambios (sin dependencias, migraciones ni rutas públicas nuevas; `/health`
solo gana un bloque).

## Project Structure

### Documentation (this feature)

```text
specs/046-jev-router/
├── plan.md              # este archivo
├── research.md          # Fase 0: R1–R14
├── data-model.md        # Fase 1: decisión, veredicto, puertos, ajustes, conjuntos, libro
├── quickstart.md        # Fase 1: validación sin red, en vivo, sin Jev y medición
├── contracts/
│   └── jev-gatekeeper.md    # peticiones, respuestas, errores, privacidad, visibilidad
├── evidencia.md         # Fase final (la crea la implementación)
├── evaluacion/          # Fase final: informes y libro de llamadas (sin texto enviado)
└── tasks.md             # Fase 2
```

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── application/ports.py              # + GateClass, GateScope, JevStatus (sin dependencias),
│                                     #   RouteJudge, ClaimsJudge, JudgeUnavailable, JudgeCall,
│                                     #   RouteQuery/RouteVerdict, ClaimsQuery/ClaimsVerdict
├── adapters/judge/                   # NUEVO
│   ├── __init__.py                   # build_judge(settings) -> JevJudge | None; jev_health()
│   └── jev.py                        # JevClient (httpx, plazo, tope, circuito), JevJudge
├── agent/gatekeeper/                 # NUEVO
│   ├── __init__.py                   # vacío (evita ciclos con toolagent)
│   ├── model.py                      # GatekeeperDecision, map_route (reexporta GateClass,
│   │                                 #   GateScope y JevStatus del puerto)
│   ├── rules.py                      # rule_decision(); shortcut_intent e is_closing movidos
│   │                                 #   desde toolagent/graph.py (reexportados allí)
│   ├── decide.py                     # decide_gate(): reglas → Jev → LLM, umbrales, cautela
│   ├── privacy.py                    # outgoing_question(), outgoing_answer(),
│   │                                 #   sensitive_spans(), context_line()
│   ├── claims.py                     # build_claims_query(), judgeable(), only_questions()
│   └── evaluation.py                 # eval-gatekeeper y eval-claims (informes, libro,
│                                     #   ReplayJudge)
├── agent/toolagent/
│   ├── graph.py                      # shortcut → decide_gate; modelos atados por alcance;
│   │                                 #   bloques <alcance_del_turno>/<cautela>; fuera de alcance
│   ├── state.py                      # canal gatekeeper; ModePath sin cambios
│   ├── status.py                     # plantilla de revisión de la compuerta
│   ├── evaluation.py                 # ModeReport con llamadas, latencias y decisiones de Jev
│   └── tools/registry.py             # ToolSpec.route_group; TOOLS_BY_SCOPE
├── prompts.py                        # JEV_ROUTE_*, JEV_CLAIMS_*, GATEKEEPER_CAUTION,
│                                     #   DIRECT_INSTRUCTIONS["gatekeeper_direct"]
├── infrastructure/settings.py        # JEV_* (data-model)
├── infrastructure/monitoring.py      # SAFE_KEYS nuevas; record_judge_calls()
└── cli.py                            # eval-gatekeeper, eval-claims; eval-agent informa Jev

apps/api/
├── agent_runtime.py                  # build_judge; judge al grafo y al runner; health
├── chat/agent.py                     # _TurnStream: retención y compuerta de afirmaciones;
│                                     #   ToolAgentFinal.gatekeeper/.claims
├── chat/service.py                   # trace gatekeeper/claims (tools y routes); LangSmith
├── main.py                           # /health: bloque jev
├── observability.py                  # Sentry sin variables locales de gk/* y del adaptador
├── static/chat-stream.js             # panel de depuración: decisión
└── templates/_chat_stream.html       # campo «Decisión»

data/eval/gatekeeper_046.json         # NUEVO, congelado antes de medir
data/eval/claims_046.json             # NUEVO, congelado antes de medir
docs/research/jev.md                  # NUEVO, desde el informe de la prueba
.env.example, compose.prod.yml        # variables JEV_* y TYPESAFE_API_KEY
.claude/skills/evaluar-agente/SKILL.md  # Jev apagado por defecto; --jev-ledger con permiso

tests/
├── conftest.py                       # fixture autouse: Jev apagado y sin red a TypeSafe
├── test_jev_guard.py                 # la fixture actúa aunque el .env traiga la clave
├── test_jev_adapter.py               # contrato HTTP, plazo, errores, tope, circuito, sin clave
├── test_gatekeeper_model.py          # decisión como JSON, mapa clase → alcance, map_route
├── test_gatekeeper_rules.py
├── test_gatekeeper_decide.py         # umbrales, cautela, desacuerdo, sombra, sin Jev
├── test_gatekeeper_privacy.py        # ≥ 30 frases sensibles, memoria, cargas registradas
├── test_jev_no_text.py               # sin texto en logs, traza, LangSmith ni Sentry
├── test_gatekeeper_imports.py        # importación aislada de cada módulo nuevo (sin ciclos)
├── test_claims_gate.py               # evidencia mínima, juzgable, sustitución
├── test_tool_agent_gatekeeper.py     # grafo: alcances, directo, fuera de alcance, bloques
├── test_eval_gatekeeper.py           # formato de los conjuntos, informe, libro sin texto
└── (ampliaciones) test_tool_registry.py, test_tool_agent_stream.py,
    test_chat_service_tools.py, test_api_health.py, test_monitoring_tools.py,
    test_settings.py, test_prompts.py, test_eval_agent.py, test_mcp_whatsapp.py,
    test_tool_agent_state.py, test_deploy_packaging.py
```

**Structure Decision**: el gatekeeper es lógica de dominio sin red (reglas, umbrales,
privacidad) y vive en `agent/gatekeeper/`, separado del grafo para probarlo sin LangGraph. El
acceso a Jev es un adaptador detrás de dos puertos en `application/ports.py`, como
`LLMProvider`: el grafo y el streaming dependen del puerto, las pruebas de dobles. La
compuerta de afirmaciones vive en el streaming (`apps/api/chat/agent.py`) porque ahí ya se
decide qué texto sale y ahí la usan la web, la API síncrona, el ejecutor de evaluación y el
puente de WhatsApp.

## Fases de entrega (para `/speckit.tasks`)

1. **F0 — Preparación**: ajustes `JEV_*`, fixture de pruebas, variables de despliegue,
   conjuntos congelados, `docs/research/jev.md`.
2. **F1 — Fundamentos**: puertos, adaptador de Jev con su cliente, privacidad,
   `route_group` en el registro, canal `gatekeeper`, claves de monitoreo. Bloquea lo demás.
3. **F2 — US1 gatekeeper** (P1) y **F3 — US2 confianza baja** (P1): misma función
   `decide_gate`; US2 se apoya en US1.
4. **F4 — US3 compuerta de afirmaciones** (P1): puede avanzar en paralelo con F2 tras F1
   (archivos distintos: `apps/api/chat/agent.py` frente a `toolagent/graph.py`).
5. **F5 — US4 respaldo y privacidad** (P1): pruebas de extremo a extremo sin Jev y de cargas.
6. **F6 — US5 visibilidad y medición** (P2): traza, LangSmith, `/health`, panel, comandos
   de evaluación, corridas únicas con ≤ 120 llamadas, evidencia, enmiendas, despliegue y PR.

## Riesgos y mitigaciones

| Riesgo | Mitigación | Medición |
|---|---|---|
| Jev clasifica mal y la restricción deja sin la consulta correcta | `mixta`; cautela con confianza baja; desacuerdo con señales fuertes de las reglas; umbral propio para `respuesta_directa` | SC-001; v3 con y sin Jev (SC-004) |
| Falsas alarmas de la compuerta (respuesta buena sustituida) | Criterios medidos (0/7 con etiquetas corregidas); pregunta incluida; formatos equivalentes aceptados | SC-003; sustituciones del v3 auditadas |
| Latencia: el primer texto espera a la compuerta | Plazo 0,8 s; conexiones reutilizadas; línea de estado; `JEV_CLAIMS_MODE=off` | SC-006 |
| El plazo del turno vence durante la llamada a Noul y descarta una respuesta ya redactada | Noul solo si queda plazo > `JEV_TIMEOUT_S` + 0,2 s; `begin_text()` antes de la llamada | T032 con reloj inyectado |
| TypeSafe caído, lento o sin créditos | Plazo, sin reintentos, circuito, tope diario; respaldo al camino de hoy | SC-005; pruebas con `MockTransport` |
| Fuga de datos personales a un tercero | Minimización, `redact`, enmascarado de nombre, empresa y notas; nunca memoria ni historial | SC-007 |
| Gasto de créditos en CI o en evaluaciones locales | Fixture *autouse*; `JEV_ENABLED=false` por defecto y opt-in explícito (`--jev-ledger`); `JEV_LEDGER` anota toda llamada; cuotas por corrida | SC-008 |
| Peor caché del proveedor por listas de herramientas distintas | Bloques nuevos al final del sistema; se informan tokens con y sin caché | v3 con Jev |
| El alias del modelo cambia | `jev-1.14.0` fijado; repetir mediciones al cambiar | — |
| Varios procesos multiplican el tope | Documentado; el tope es por proceso | — |
| La 045 cambia su API antes de mergearse | La 046 se implementa sobre su último commit y se rebasa | `make check` tras el rebase |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Enmienda D: servicio externo en el camino del turno | El requisito de la entrega exige el gatekeeper System One (Jev) y el owner lo hizo obligatorio. | Usar solo reglas + LLM no cumple el requisito; usar Jev solo en evaluación no lo pone «en el router». |
| Enmienda E: retener el texto completo con la compuerta activa | Noul necesita la respuesta entera; emitir y corregir mostraría afirmaciones sin fuente y rompería a tres consumidores del stream. | Juzgar por párrafo multiplica las llamadas; el modo sombra no protege a nadie. |
| Tres conjuntos de herramientas atados al compilar | La restricción tiene que ser real (el modelo no ve lo que no puede usar). | Filtrar después de la llamada desperdicia pasos y cupo; reconstruir el modelo por turno cuesta en cada turno. |
