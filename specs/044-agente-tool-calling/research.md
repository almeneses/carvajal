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
  Retención por camino (acordado con el owner, 2026-11-23): con evidencia, 48 caracteres;
  con `sin_consulta`, la respuesta completa se retiene, se filtra y se emite de una vez
  (respuestas cortas); sin evidencia tras `buscar_corpus`, no se genera: la compuerta emite
  la abstención antes del paso final. «Detener» antes del primer token no persiste nada;
  durante el texto persiste el turno como `incomplete` y sin cotizaciones (prueba
  explícita).
- **Rationale**: DeepSeek emite las llamadas a herramientas en los primeros deltas; el
  retenido es imperceptible y evita mostrar «pensamientos» previos a una llamada.
- **Alternatives considered**: invocar sin streaming hasta el paso final y luego volver a
  pedir el texto en streaming: duplica el costo del paso final (SC-008); un paso extra solo
  de redacción: añade una llamada por turno (SC-006, SC-008).

**Ajuste de implementación (2026-11-23).** «Sin evidencia… no se genera» no es literal: el
modelo tiene que empezar el paso final antes de poder distinguirlo de una nueva búsqueda. En
el camino de abstención, el adaptador corta el paso y cancela el grafo en cuanto supera los
48 caracteres retenidos sin llamar herramientas; se generan como mucho ~48 caracteres más un
trozo, que nunca se muestran. Las fuentes (`meta`) se emiten justo antes del primer token
(y otra vez al final solo si cambiaron), para que el cliente pueda limpiar `[n]` en vivo.

## R3. Compuerta de evidencia (FR-004)

- **Decision**: antes de liberar el paso final, `evidence.gate(state)` comprueba si alguna
  herramienta del turno devolvió resultados con procedencia. Si ninguna lo hizo y hubo al
  menos una `buscar_corpus`, el servicio descarta el paso y emite `NO_CONTEXT_ANSWER`. La
  comprobación de nombres propios (`proper_entities_supported`) se aplica dentro de
  `buscar_corpus`, como hoy en la ruta `rag`.
  Ampliación acordada con el owner (2026-11-23):
  1. El primer paso del bucle se invoca con `tool_choice="required"` (salvo el atajo) y se
     añade la herramienta `sin_consulta(motivo: "pedir_dato" | "conversacion" |
     "fuera_de_alcance")`: responder de memoria deja de ser invisible en trazas y evaluación.
  2. Si el turno no tiene evidencia y la única herramienta fue `sin_consulta`, el texto
     retenido pasa el filtro de la ruta directa: si contiene cifras, fechas o nombres
     propios de Carvajal, se sustituye por la abstención.
  3. Evidencia parcial: el prompt exige marcar la parte sin respaldo y la evaluación
     verifica que toda cifra, precio o fecha de la respuesta aparezca en un resultado de
     herramienta del turno (SC-001). No se reescribe texto ya transmitido.
- **Rationale**: es la garantía equivalente al «no llamar al LLM sin contexto» y es
  determinista, así que es verificable y medible (SC-005).
- **Alternatives considered**: confiar en el prompt: no es verificable; cortar el bucle tras
  la primera búsqueda vacía: impide la reformulación (US1.4); generar sin streaming y
  revisar antes de mostrar: rompe SC-006.

**Ajustes de implementación (2026-11-23).** (a) Un `needs_input` sin evidencia toma el
camino `no_query` filtrado (no la abstención), para no perder la petición del dato que
falta. (b) El filtro permite los términos propios del asistente (Carva, Carvajal, Colombia,
México, IVA, SKU) y bloquea cifras con dígitos y en palabras desde «once», ordinales,
décadas, siglos, numerales romanos, empresas del grupo y ciudades; los números «dos»…«diez»
se permiten a propósito para que pasen preguntas como «te pido dos datos». (c) Lo permitido
se calcula solo con la pregunta, los datos devueltos por herramientas (sin `message`) y el
contexto comercial con el que empezó el turno: los argumentos que escribe el modelo no
respaldan sus propias cifras. Hueco conocido: el contexto de turnos previos puede traer
argumentos inventados por el modelo en esos turnos.

## R4. Entrada estructurada del motor de cotización

- **Decision**: extraer de `quotation.engine.quote(text, …)` una función
  `quote_request(request: QuoteRequest, *, delivery, today, dataset, catalog)` que recibe
  los campos ya interpretados. `quote()` sigue interpretando texto y llama a
  `quote_request()`; la herramienta `cotizar` llama directo a `quote_request()`.
- **Rationale**: el modelo ya extrae los datos al llamar la herramienta; volver a parsear
  texto con regex reintroduce el problema que la spec resuelve. La paridad se prueba con
  los 12 casos dorados (SC-002). `quote_request()` exige la temperatura cuando el uso no la
  implica (`needs_input`): la regla vive en el motor y protege a ambos modos y al respaldo.
  Motivo: con el texto «2.000 vasos de 7 onzas para tinto, Bogotá», `quote()` cotiza hoy un
  vaso de PP para bebida fría (CO-VRP-07, $106.120) porque la temperatura queda vacía.
- **Alternatives considered**: pasar al motor el texto original: el modelo no podría
  corregir cantidades o ciudades de forma estructurada.

## R5. Qué cotizaciones se muestran y reciben folio (FR-012a)

- **Decision**: cada llamada exitosa a `cotizar` con argumentos distintos produce una
  tarjeta y un folio; las repetidas con los mismos argumentos se deduplican; máximo 3 por
  turno. El prompt indica llamar a `cotizar` solo para lo que se va a presentar; para
  explorar se usa `buscar_producto`, que no emite totales. Folio: el primero conserva la
  fórmula actual (`sha256(conversación:client_turn_id)`); los siguientes añaden `:i`.
  Ajustes acordados con el owner (2026-11-23): (1) el prompt indica cotizar solo las
  combinaciones de producto y cantidad que la persona pidió y usar `buscar_producto` para
  comparar tamaños; (2) si dos cotizaciones del turno comparten producto, ciudad y cantidad
  y difieren solo en el domicilio, se conserva la última; (3) los folios siguen el orden de
  las llamadas y una prueba de reenvío verifica mismos folios y mismo orden.
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
- **Ajuste de implementación (2026-11-23)**: `recursion_limit = 2·límite + 5` porque un paso
  de `sin_consulta` es gratuito y no cuenta para el límite; el corte se aplica con
  `usados ≥ límite`, `pasos reales ≥ límite` o `pasos > límite` (antibucle). «Detener»
  cancela el grafo con un callback (`TurnCancel`) y no espera a que termine el paso.

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

- **Decision**: `ChatOpenAI(model="deepseek-chat", base_url=…).bind_tools(tools)` con
  llamadas múltiples permitidas en un mismo paso; el nodo `tools` las **ejecuta en serie en
  el orden pedido**, cada una con su línea de estado, de modo que la numeración `[n]` y los
  folios siguen siendo deterministas. Esquemas Pydantic con descripciones en español;
  temperatura 0. Cada llamada cuenta para el límite de 4.
- **Rationale**: soportado por el adaptador existente sin dependencias nuevas. Con
  llamadas de a una, una necesidad compuesta cuesta ~4 decisiones (~6–7 s hasta el primer
  texto con la latencia medida en `docs/resultados-router.md`: 1,3 s media, 1,6 s p95) e
  incumple SC-006; agrupadas, ~2 decisiones (~3,5 s).
- **Hallazgo en vivo (2026-11-23)**: el `.env` usa `DEEPSEEK_MODEL=deepseek-flash`, que
  razona por defecto; en ese modo la API rechaza `tool_choice="required"` («Thinking mode
  does not support this tool_choice»). `build_deepseek_chat(..., for_tools=True)` envía
  `thinking: {"type": "disabled"}`; así además aplica la temperatura 0. `deepseek-chat` es
  un alias del mismo modelo sin razonamiento. El proveedor de texto del modo `routes` no
  cambia.
- **Riesgo**: calidad del tool calling de DeepSeek en español; se mide en F4 y, si no
  alcanza SC-003, el modo `tools` no pasa a valor por defecto.
