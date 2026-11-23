# Research — Spec 044

Decisiones técnicas de la Fase 0. Cada una resuelve una incógnita del contexto técnico.

## R1. Dónde corre el bucle y cómo se transmite

- **Decision**: el bucle `agent ⇄ tools` corre dentro de un `StateGraph` y el servicio lo
  consume con `graph.stream(..., stream_mode=["messages", "custom"])`. `messages` entrega
  los fragmentos del modelo; `custom` entrega los eventos de estado que cada herramienta
  publica con `get_stream_writer()`.
- **Rationale**: conserva LangGraph, el checkpointer y el Store; un solo iterador produce
  tokens y estados en orden; el servicio sigue siendo dueño de la persistencia y del SSE.
- **Alternatives considered**: (a) generar fuera del grafo como hoy: obliga a duplicar la
  decisión en el servicio y pierde el bucle; (b) `create_react_agent` prebuilt: no permite
  la compuerta de evidencia, el límite por turno ni la deduplicación sin envolverlo igual.

## R2. Distinguir el paso final de un paso con herramientas durante el streaming

- **Decision**: el adaptador del servicio retiene los fragmentos de cada paso del modelo
  hasta ver un `tool_call_chunk` (paso con herramientas: se descarta el texto retenido) o
  hasta acumular 48 caracteres o el fin del paso sin herramientas (paso final: se libera y
  se sigue transmitiendo).
- **Rationale**: DeepSeek emite las llamadas a herramientas en los primeros deltas; el
  retenido es imperceptible y evita mostrar «pensamientos» previos a una llamada.
- **Alternatives considered**: invocar sin streaming hasta el paso final y luego volver a
  pedir el texto en streaming: duplica el costo del paso final (SC-008).

## R3. Compuerta de evidencia (FR-004)

- **Decision**: antes de liberar el paso final, `evidence.gate(state)` comprueba si alguna
  herramienta del turno devolvió resultados con procedencia. Si ninguna lo hizo y hubo al
  menos una `buscar_corpus`, el servicio descarta el paso y emite `NO_CONTEXT_ANSWER`. La
  comprobación de nombres propios (`proper_entities_supported`) se aplica dentro de
  `buscar_corpus`, como hoy en la ruta `rag`.
- **Rationale**: es la garantía equivalente al «no llamar al LLM sin contexto» y es
  determinista, así que es verificable y medible (SC-005).
- **Alternatives considered**: confiar en el prompt: no es verificable; cortar el bucle tras
  la primera búsqueda vacía: impide la reformulación (US1.4).

## R4. Entrada estructurada del motor de cotización

- **Decision**: extraer de `quotation.engine.quote(text, …)` una función
  `quote_request(request: QuoteRequest, *, delivery, today, dataset, catalog)` que recibe
  los campos ya interpretados. `quote()` sigue interpretando texto y llama a
  `quote_request()`; la herramienta `cotizar` llama directo a `quote_request()`.
- **Rationale**: el modelo ya extrae los datos al llamar la herramienta; volver a parsear
  texto con regex reintroduce el problema que la spec resuelve. La paridad se prueba con
  los 12 casos dorados (SC-002).
- **Alternatives considered**: pasar al motor el texto original: el modelo no podría
  corregir cantidades o ciudades de forma estructurada.

## R5. Qué cotizaciones se muestran y reciben folio (FR-012a)

- **Decision**: cada llamada exitosa a `cotizar` con argumentos distintos produce una
  tarjeta y un folio; las repetidas con los mismos argumentos se deduplican; máximo 3 por
  turno. El prompt indica llamar a `cotizar` solo para lo que se va a presentar; para
  explorar se usa `buscar_producto`, que no emite totales. Folio: el primero conserva la
  fórmula actual (`sha256(conversación:client_turn_id)`); los siguientes añaden `:i`.
- **Rationale**: cumple la aclaración sin que el servicio adivine qué mostró el texto; la
  réplica conserva los mismos folios.
- **Alternatives considered**: que el modelo marque qué tarjetas mostrar (frágil); un solo
  folio por turno (descartado por el owner).

## R6. Numeración de citas `[n]` entre herramientas

- **Decision**: un registro de fuentes por turno (`SourceRegistry`) asigna números
  consecutivos a cada procedencia nueva; cada herramienta devuelve al modelo sus datos con
  los números ya asignados; el registro final se persiste como `sources` del mensaje.
- **Rationale**: FR-013; el texto y la API usan la misma numeración.
- **Alternatives considered**: numerar por herramienta (colisiones de `[1]`).

## R7. Historial como mensajes (FR-006)

- **Decision**: los turnos de Postgres (`seed_turns`, últimos `agent_history_turns`) se
  convierten en pares `HumanMessage`/`AIMessage` con el texto final. Los mensajes de
  herramientas de turnos anteriores no se reenvían; el contexto comercial vigente viaja
  en el mensaje de sistema como bloque `<contexto_comercial>` de datos.
- **Rationale**: conversación natural sin inflar tokens con resultados viejos; el contexto
  comercial asegura las correcciones («mejor en Cali»).
- **Alternatives considered**: reenviar todos los `ToolMessage` (costo); texto plano como
  hoy (lo que la spec pide abandonar).

## R8. Atajo y respaldo

- **Decision**: el nodo `shortcut` ejecuta `decide_route` y, si la ruta es `direct` con
  intención de saludo, capacidad o preferencia, reutiliza el nodo `direct` actual. El
  respaldo (FR-009) se activa por `LLMError`, `TimeoutError` o decisión inválida antes del
  primer token: el servicio invoca el grafo `routes` y redacta con `fallback.render()` (sin
  LLM): tarjetas, cotización, o hasta 3 fragmentos citados, con el aviso fijo.
- **Rationale**: cero costo para saludos (SC-007) y respuesta útil sin proveedor.
- **Alternatives considered**: Ollama como redactor (descartado por el owner en la
  aclaración).

## R9. Límites del turno

- **Decision**: `agent_max_tool_calls=4` (contador en el estado), `recursion_limit` del grafo
  derivado (2·4+3), plazo de 45 s medido por el servicio. Al llegar al límite de
  herramientas, el nodo `agent` se invoca sin herramientas para redactar con lo obtenido y
  el prompt recibe la lista de lo que faltó. Si vence el plazo antes del paso final, se usa
  el respaldo determinista con lo obtenido.
- **Rationale**: FR-010 y SC-006 con comportamiento predecible.

## R10. Pruebas sin red

- **Decision**: un `ScriptedToolChat` propio (implementa `bind_tools` y `stream`, emite
  `AIMessageChunk` con `tool_call_chunks` guionizados). Las fábricas de LangChain
  (`GenericFakeChatModel`) no soportan `bind_tools`.
- **Rationale**: pruebas deterministas del bucle, la compuerta y el streaming en CI.

## R11. Evaluación comparativa

- **Decision**: `carvajal eval-agent --mode routes --mode tools --questions …` ejecuta
  turnos completos en proceso (sin HTTP, con persistencia en memoria) y puntúa por
  verificaciones deterministas por pregunta: herramientas esperadas/prohibidas, abstención,
  materiales prohibidos en tarjetas, totales pendientes, idioma. Reporta tokens y costo
  desde `usage_metadata`. `--langsmith` registra el experimento (opcional).
- **Rationale**: SC-001…SC-008 medibles con el mismo informe para ambos modos.
- **Alternatives considered**: solo LLM-as-judge: costoso y no determinista; queda como
  complemento opcional en LangSmith.

## R12. DeepSeek y tool calling

- **Decision**: `ChatOpenAI(model="deepseek-chat", base_url=…).bind_tools(tools,
  parallel_tool_calls=False)`; esquemas Pydantic con descripciones en español; temperatura
  0. Llamadas en serie para que los estados y el orden de fuentes sean deterministas.
- **Rationale**: soportado por el adaptador existente sin dependencias nuevas.
- **Riesgo**: calidad del tool calling de DeepSeek en español; se mide en F4 y, si no
  alcanza SC-003, el modo `tools` no pasa a valor por defecto.
