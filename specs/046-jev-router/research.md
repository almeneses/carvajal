# Research — Spec 046

Decisiones técnicas de la Fase 0. Las referencias al código son de la rama
`feat/045-memoria-herramientas` desde su commit `88ee0d6` (hoy en `314f9a7`; el registro de
20 herramientas entró en `d657711`), que es la base de esta spec; se citan por función porque
los números de línea cambiarán al terminar la 045. La 045 no contiene los últimos commits de
`main` que sí tiene la rama de la 046 (`dd35bfc`, webhook firmado `/api/webhooks/whatsapp`,
que reutiliza `WhatsAppBridge` con `client_turn_id`; `901f59f`; `921460d`): debe rebasarse
sobre `main` antes de implementar la 046 (R14). La API de Jev se describe según su `openapi.json` y la prueba
del 2026-11-24 (30 llamadas, informe que pasará a `docs/research/jev.md`). Para esta fase no
se llamó a Jev ni a ningún LLM.

## R1. Dónde vive el gatekeeper

- **Decision**: en el nodo `shortcut` del grafo `tools` (`toolagent/graph.py`), que ya decide
  el atajo con `shortcut_intent` y ya corre antes del primer paso del modelo. El nodo pasa a
  calcular una `GatekeeperDecision` completa (data-model) y la escribe en un canal nuevo
  `gatekeeper` que `recall` reinicia en cada turno. El nombre del nodo no cambia: el streaming
  (`apps/api/chat/agent.py`, `_MODEL_NODES`, `_RECALL_NODE`) y las pruebas lo usan.
  1. `recall` (sin cambios de fondo) arma los mensajes y lee la memoria; reinicia
     `gatekeeper`.
  2. `shortcut` llama a `decide_gate(...)` (módulo nuevo `agent/gatekeeper/decide.py`):
     reglas → Jev → LLM (R2–R4).
  3. `after_shortcut` envía a `direct` si el alcance es `direct`; si no, a `agent`.
  4. `agent` elige el modelo atado al alcance (`all`, `rag` o `structured`, atados una sola
     vez al compilar) y añade al final del sistema los bloques `<alcance_del_turno>` y, con
     cautela, `<cautela>`.
  5. `tools` rechaza una llamada fuera del alcance con un `ToolMessage` de error que gasta
     cupo (como un argumento ilegible).
  6. `rule_decision` (función pura de la pregunta, los turnos y el contexto comercial)
     devuelve `memory_forget` para «olvida todo»; el nodo `shortcut` aplica el olvido con el
     Store y reescribe `rule`/`shortcut_intent` a `memory_unavailable` si falla, como hoy.
- **Dependencias entre módulos**: `GateClass`, `GateScope` y `JevStatus` viven en
  `application/ports.py` (sin dependencias); `agent/gatekeeper/__init__.py` queda vacío;
  `toolagent/tools/registry.py` importa `GateScope` solo de `ports`. Así no hay ciclo
  `registry → gatekeeper → toolagent` aunque `gk/rules.py` importe `names_city` de
  `toolagent/evidence.py` y `ta/graph.py` reexporte `gk/rules`. Una prueba importa cada
  módulo nuevo en un proceso aislado (T062).
- **Rationale**: el punto ya existe, recibe la pregunta, los turnos de la ventana, el contexto
  comercial y la memoria (para enmascarar); no hay que tocar el servicio para decidir.
  Atar los tres conjuntos al compilar evita reconstruir el modelo por turno.
- **Alternatives considered**:
  - (a) Un nodo nuevo `gatekeeper` entre `recall` y `shortcut`: duplica el cálculo de la
    intención del atajo y obliga a tocar el streaming.
  - (b) Decidir en el servicio y pasar el alcance como entrada del grafo: el ejecutor de
    evaluación (`ToolsDriver`) y el puente MCP tendrían que repetir la lógica.
  - (c) Llamar a Jev en paralelo desde `recall`: ahorra milisegundos y complica la
    cancelación; queda como optimización si la latencia lo exige.

## R2. Las clases de la pregunta Choice

- **Decision**: cuatro opciones, en este orden: `herramienta_estructurada`, `rag`, `mixta`,
  `respuesta_directa`. Instrucción y descripciones en español, en el contrato. Estado
  enviado: `{"pregunta": …, "contexto": …}` (`contexto` solo si aplica, R8).
- **Rationale**:
  - El requisito nombra tres decisiones (estructurada, RAG, directa); `mixta` es la
    combinación de las dos primeras y existe porque el v3 tiene preguntas compuestas que la
    necesitan: v3-003 (`buscar_corpus` + `buscar_producto`) y v3-004
    (`indicador_financiero` + `buscar_corpus`). Sin ella, cualquier restricción las rompe.
  - Orden: la documentación de Jev advierte un sesgo posicional hacia la primera opción. La
    clase más frecuente en el tráfico comercial va primero; `respuesta_directa`, cuyo error
    deja a la persona sin datos, va al final.
  - Sin `fuera_de_alcance`: la abstención determinista ya cubre lo que el corpus no tiene
    (v3-036, v3-039 se resuelven buscando y absteniéndose), y una clase de rechazo
    convertiría un error de clasificación en una respuesta perdida. El costo de no tenerla
    es una consulta de más en preguntas imposibles.
- **Alternatives considered**: tres opciones exactas (rompe v3-003/004); cinco con
  `fuera_de_alcance` (riesgo arriba); una Noul por clase (cuatro preguntas, más tokens y sin
  probabilidades que sumen 1).

## R3. Cómo restringe la decisión

- **Decision**: la clase aplicada restringe **todo el turno** a un grupo de consultas
  declarado en el registro de la 045: un campo nuevo `route_group` en `ToolSpec`
  (`"rag" | "structured" | "any"`) y las vistas derivadas `TOOLS_BY_SCOPE`.

  | Grupo | Consultas |
  |---|---|
  | `rag` | `buscar_corpus`, `preguntas_frecuentes`, `historia_carvajal`, `empresas_del_grupo` |
  | `structured` | `buscar_producto`, `cotizar`, `buscar_sedes`, `indicador_financiero`, `ficha_producto`, `comparar_referencias`, `guia_materiales`, `convertir_presentacion`, `ciudades_con_cobertura`, `consultar_cotizacion`, `indicadores_disponibles`, `fecha_actual`, `recordar_preferencia`, `listar_memoria`, `olvidar_preferencia` |
  | `any` | `sin_consulta` (en todos los alcances) |

  Alcances: `direct` (sin herramientas, nodo `direct`), `rag` (`rag` + `any`), `structured`
  (`structured` + `any`), `all` (las 20). Salvaguardas: cautela con confianza baja (R4), la
  regla de desacuerdo (R4) y `mixta` → `all`.
- **Rationale**: una restricción de todo el turno es auditable (la traza dice qué podía
  consultar el modelo) y es la que pide el requisito. `historia_carvajal` y
  `empresas_del_grupo` tienen `evidence_role="data"` pero responden preguntas documentales,
  por eso el grupo es un campo propio y no se deriva de `evidence_role`. La memoria va en
  `structured` porque «¿qué sabes de mí?» es una consulta de datos; una preferencia
  declarada nunca llega a Jev (regla R2 del FR-002).
- **Prefijo de caché**: con alcances distintos cambia la lista de herramientas que viaja al
  proveedor, y con ella el prefijo cacheable de DeepSeek. El texto de `AGENT_SYSTEM` no
  cambia; los bloques nuevos van al final del sistema. Los tokens con y sin caché se
  informan en la medición del v3.
- **Alternatives considered**:
  - (a) Restringir solo el primer paso: el modelo puede usar cualquier consulta después y la
    decisión deja de explicar el turno.
  - (b) Reordenar las herramientas o insinuar la clase en el prompt sin restringir: no es
    verificable.
  - (c) Reintentar con todas si el turno restringido se abstiene: dobla latencia y costo.

## R4. Confianza baja: cautela, aclaración y umbrales

- **Decision**:
  - Confianza = `confidence` de la respuesta Choice (si falta, la mayor probabilidad).
  - Aplicar la clase si `confidence ≥ JEV_ROUTER_MIN_CONFIDENCE` (0,6), y para
    `respuesta_directa` si además `confidence ≥ JEV_ROUTER_DIRECT_MIN_CONFIDENCE` (0,8).
  - **Desacuerdo fuerte**: si `decide_route` (reglas, costo cero, ya se calcula en
    `shortcut_intent`) devuelve `commercial`/`tool` o la intención `commercial_clarify` o
    `missing_period`, y Jev elige `rag` o `respuesta_directa`, se aplica cautela con motivo
    `desacuerdo_reglas`. Si las reglas ven `commercial_mixed`, el alcance es `all`.
  - **Cautela** = alcance `all` + bloque `<cautela>` al final del sistema con el texto
    `GATEKEEPER_CAUTION` (prompts.py): consultar antes de afirmar; si la pregunta admite
    varias lecturas, pedir la aclaración con `sin_consulta(motivo="pedir_dato")` en una
    sola pregunta concreta; si lo pedido no está en las consultas, decirlo y ofrecer lo que
    sí puede buscar; si el mensaje es solo conversación (saludo, agradecimiento), responder
    con `sin_consulta(motivo="conversacion")` sin buscar (un saludo coloquial que las reglas
    no detectan puede llegar a cautela). «Derivar» es pedir la aclaración u ofrecer los canales que ya devuelven
    las consultas (sedes del directorio, canales de una necesidad a la medida); nunca un
    canal inventado. La compuerta de afirmaciones sigue aplicando.
  - **Señal comercial con `rag`** (añadida tras la medición, R1 de `evidencia.md` §9): si
    Jev elige `rag`, la confianza llega al umbral y la pregunta nombra un producto del
    catálogo, un precio, una cantidad, una capacidad o dónde comprar (`commercial_signal`,
    determinista, sin red), se trata como `mixta`: alcance `all`, **sin cautela**, motivo
    `producto_reglas`. Ver R16.
  - **Sin Jev no hay cautela** (decisión del owner: sin Jev, «como hoy»). Para que la decisión
    del LLM también sea auditable, al cerrar el turno se deriva `llm_class` del `route_group`
    de las consultas ejecutadas (solo `rag` → `rag`; solo `structured` →
    `herramienta_estructurada`; ambas → `mixta`; solo `sin_consulta` o ninguna →
    `respuesta_directa`), sin costo ni red.
  - **Regla para fijar los umbrales** (declarada antes de medir): sobre
    `gatekeeper_046.json`, se elige `JEV_ROUTER_MIN_CONFIDENCE` en {0,5; 0,6; 0,7; 0,8} con
    la mayor exactitud de las decisiones aplicadas sujeta a cubrir al menos el 70 % de las
    preguntas; `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` es el menor valor de la misma rejilla con
    cero preguntas con datos enviadas a `respuesta_directa`. Con n = 20 el riesgo de
    sobreajuste es alto: si la rejilla no separa, se quedan los valores iniciales y se
    declara.
- **Rationale**: el costo del error es asimétrico (una respuesta directa equivocada pierde
  la respuesta; una consulta de más cuesta segundos). La cautela usa lo que ya existe
  (`sin_consulta` con `pedir_dato`, la abstención ajustada) en lugar de un camino nuevo.
- **Alternatives considered**: derivar siempre a un humano (no hay canal de atención
  humana en el chat); responder directo con un aviso (contradice «no afirmar sin
  consultar»); dos instrucciones distintas para derivar y para cautela (dos prompts que
  medir sin datos que lo justifiquen).

## R5. Score y Box

- **Decision**: Score no entra en producción. En la medición, la petición del gatekeeper
  lleva además una pregunta Score («¿qué tan clara es la intención de `pregunta`?», cinco
  niveles) sobre el mismo estado: sin llamadas extra, solo más tokens. Según `openapi.json`,
  el nivel es la posición en `criteria` empezando en cero, así que `clarity` ∈ [0, 4]; se
  guarda también el `confidence` propio de la respuesta Score (`clarity_confidence`). El
  informe compara la confianza de Choice y el Score como predictores de error. Box no
  aplica.
- **Rationale**: Choice ya devuelve `confidence` y `probabilities` (la duda de la decisión) y
  Noul mide el respaldo; un Score en producción añade tokens y un tercer umbral sin
  evidencia de que mejore algo. Box localiza regiones en imágenes y exige la cabecera
  `TypeSafe-Danger-Zone-Images`; el chat y WhatsApp no envían imágenes al asistente.
- **Alternatives considered**: Score para la «confianza de la respuesta» antes de emitirla
  (redundante con Noul); Score como umbral de cautela (sin datos para calibrarlo).

## R6. Compuerta de afirmaciones: dónde y cómo retener

- **Decision**: en `_TurnStream._close_step` (`apps/api/chat/agent.py`), después de la
  decisión determinista y antes de `_emit`. Con la compuerta activa al empezar el turno
  (`JEV_CLAIMS_MODE=on` y el juez disponible), el camino `evidence` deja de liberar el texto
  a los 48 caracteres: lo acumula como `no_query` y lo juzga al cerrar el paso. La primera
  vez que retiene, emite una línea de estado fija («Reviso que lo que te digo tenga
  fuente…», plantilla en `toolagent/status.py`).
- **Rationale**:
  - Noul necesita la respuesta completa.
  - Emitir y sustituir después mostraría unos segundos una afirmación sin fuente (principio
    III) y rompería a los consumidores que concatenan `token` y `fallback`:
    `ChatService.ask`, el ejecutor de evaluación, `apps/mcp/bridge.py:_collect` y el webhook
    firmado `apps/api/views/webhooks.py` (de `main`), que reutiliza `WhatsAppBridge` con
    `client_turn_id`.
  - WhatsApp no transmite a la persona; para él la retención no cuesta nada.
  - Costo en la web: el primer texto llega con el fin del turno (fin p95 de la ronda 4 de la
    044: 4,95 s) más el veredicto (p95 medido 284 ms). Se acepta y se declara;
    `JEV_CLAIMS_MODE=off` devuelve el streaming de hoy.
- **Qué se juzga**: el texto del modelo en `evidence` y `no_query` (tras el filtro). No se
  juzgan: la respuesta directa, sea del atajo por reglas o decidida por Jev (no consulta
  nada, su instrucción prohíbe cifras, fechas y nombres de Carvajal y ya pasa el filtro
  determinista de `no_query`; con evidencia vacía, Jev marcaría como no respaldadas frases
  propias del asistente y la persona recibiría `NO_DATA_ANSWER`), las abstenciones, los reemplazos neutros, los turnos solo de memoria, el
  respaldo determinista (`fallback.render`) y los textos formados solo por preguntas (cada
  oración termina en «?»), que no afirman nada.
- **Fallo**: si Jev falla o vence, se emite el texto que pasó la compuerta determinista
  (*fail-open*, decisión del owner) con `claims.status="unavailable"`.
- **Plazo del turno**: la llamada a Noul ocurre dentro del bucle del runner (`_close_step`),
  bajo el plazo de la 044 (`agent_turn_timeout_s`, `_Deadline`), que vence si no salió el
  primer `ToolAgentToken`. Con la retención completa, un turno largo podría vencer durante la
  llamada y cambiar una respuesta ya redactada por el respaldo. Por eso se llama a Noul solo
  si queda un plazo mayor que `JEV_TIMEOUT_S` + 0,2 s (si no, `unavailable` con motivo
  `sin_plazo` y se emite lo que pasó la determinista), y se marca `begin_text()` antes de la
  llamada cuando ya hay texto final retenido. Se prueba con reloj inyectado (FR-030).
- **Alternatives considered**: (a) emitir y corregir con un evento nuevo `retract`
  (descartado arriba); (b) juzgar por párrafo mientras se transmite (una llamada por
  párrafo: créditos); (c) modo sombra de la compuerta (el informe lo proponía como primera
  fase; el owner decidió aplicarla y `off` cubre la vuelta atrás).

## R7. Qué texto sustituye una respuesta rechazada

- **Decision**: el mismo que usa hoy la compuerta determinista cuando rechaza en
  `no_query`: `_replacement()` si devuelve texto (turno solo de memoria, petición de dato,
  sedes sin precio, con las búsquedas vacías delante) y, si no, `_abstain()` (abstención
  ajustada: búsquedas vacías, `NO_CONTEXT_ANSWER` si buscó en el corpus o `NO_DATA_ANSWER`).
  En el camino `evidence` se usa igual. Una abstención vacía las fuentes
  (`abstained=True`); un reemplazo las conserva.
- **Rationale**: el owner pidió «el mensaje neutro existente»; esa familia es la que ya está
  medida y no convierte una petición de dato en «no está en el corpus».
- **Alternatives considered**: el respaldo determinista con los datos de las herramientas
  (`fallback.render`, propuesta del informe): muestra datos sin redacción, pero es otro
  texto y otro camino que medir.

## R8. Minimización y redacción

- **Decision**: un módulo `agent/gatekeeper/privacy.py` con dos funciones distintas, porque
  la pregunta y la respuesta tienen riesgos distintos:
  1. `outgoing_question(text, memory)` (pregunta y línea de contexto del gatekeeper):
     `redact(text)` de la 045 (documentos, teléfonos y correos, tarjetas, cuentas, claves);
     enmascarar con `[dato de la persona]` el nombre y la empresa que `classify(text)` de la
     045 detecta en el propio texto y los valores de `nombre`, `empresa` y las notas de la
     memoria del dueño si aparecen; recortar (pregunta: 1 000 caracteres; contexto: 160).
     `classify` toma como empresa la frase entera que sigue a «tengo una»
     (`classify('tengo una cafetería en Cali, …')` → `{'empresa': 'cafetería en Cali'}`), así
     que la empresa declarada se enmascara solo si viene de un marcador fuerte («trabajo en»,
     «mi empresa es», «mi negocio se llama») o tiene forma de nombre propio, y nunca se
     enmascaran ciudades (`names_city`): el tipo de negocio y la ciudad hacen falta para
     clasificar.
  2. `outgoing_answer(answer, question, memory, payloads)` (respuesta de la compuerta): **no**
     aplica `redact` completo. `redact` borra todo teléfono fijo, celular o internacional
     (`redact('Llama a la sede al (601) 555 1234 o al +57 1 555 1234')` → `Llama a la sede al
     [dato omitido] o al [dato omitido]`), y entonces Jev no vería el teléfono de la
     respuesta: no se podría medir la regla del owner («reformatear a (601) no es inventar»,
     v3-027) ni detectar un teléfono inventado. Solo sustituye, literalmente, los tramos que
     el filtro de la 045 encuentra en la pregunta (`sensitive_spans(question)`, sobre los
     mismos patrones de `sensitive.py`), el nombre, la empresa y las notas de la memoria, y
     los folios de las cotizaciones de la persona (valores `folio`/`id` de
     `consultar_cotizacion` y `cotizar`) por `[folio]`. Recorta a 8 000 caracteres.

  **Gatekeeper**: `{"pregunta": outgoing_question(question)}` y, solo si `looks_elliptical`
  o la pregunta tiene 6 palabras o menos, `"contexto": outgoing_question(pregunta anterior de
  la persona)`. Nunca respuestas anteriores, resumen, memoria ni contexto comercial.
  **Compuerta**: `{"evidencia": [{"consulta", "texto"}], "respuesta": outgoing_answer(…)}`,
  sin la pregunta (decisión del owner: a la compuerta, la respuesta y la evidencia mínima).
  La evidencia sale de los `ToolMessage` del turno con el criterio de `_tool_backing`
  (`backing_data`, sin `message`, `aviso` ni `busqueda_nombre`), excluye `sin_consulta`, las
  de memoria y los `error`; de las de papel `corpus`, solo los fragmentos `[n]` citados en la
  respuesta si cita alguno; los folios van como `[folio]`. Se añade un extracto
  `{"consulta": "dicho_por_la_persona", "texto": …}` con solo los datos estructurados que la
  persona dio, según la extracción de las reglas que el nodo `shortcut` ya calcula
  (`decide_route` / `extract_commercial`: cantidad, capacidad o medida, familia, uso,
  temperatura, ciudad, indicador y período), pasado por `redact`; si no hay ninguno, no se
  envía. Tope total `JEV_MAX_EVIDENCE_CHARS` (12 000) repartido por extracto. Los datos del
  directorio (teléfonos de sedes) no se redactan: son públicos y sin ellos Jev no podría
  verificar un teléfono inventado.
- **Rationale**: cumple la decisión del owner («solo lo estrictamente necesario»: al router,
  la pregunta redactada y una línea de contexto; a la compuerta, la respuesta y la evidencia
  mínima) y deja a Jev lo necesario para su tarea. El extracto `dicho_por_la_persona` evita
  que «500 vasos» parezca inventado sin enviar la pregunta completa: son valores, no texto
  libre.
- **Riesgos declarados**: TypeSafe (EE. UU.) retiene según su DPA; la retención cero solo
  existe para clientes enterprise. La revisión del DPA y de su plazo de retención es una
  tarea previa a activar Jev en producción (T063, resultado en `docs/research/jev.md`); si no
  se hace, la PR pide al owner la aceptación explícita del riesgo. Un nombre propio que no se
  declara con un marcador («Ana necesita vasos») no se detecta; el riesgo se acota porque la
  memoria del dueño nunca viaja. Lo que la persona dijo y la extracción no recoge (p. ej., el
  tipo de negocio) puede producir una falsa alarma si la respuesta lo repite como hecho; las
  instrucciones excluyen lo que la respuesta atribuye a la persona y la medición lo audita.
- **Alternatives considered**: redactar todos los números de la evidencia o aplicar `redact`
  completo a la respuesta (Jev ya no verificaría teléfonos ni precios; rechazado en la
  revisión, R15); enviar la pregunta redactada a la compuerta (propuesta inicial; va más allá
  de la lista literal del owner y solo se propondría en la PR si la medición muestra falsas
  alarmas por la minimización); enviar el historial (más exposición, contra la decisión del
  owner).

## R9. Cliente HTTP, plazo, tope y modelo

- **Decision**: `adapters/judge/jev.py` con un `JevClient` sobre `httpx.Client` (dependencia
  directa que ya usa `adapters/embeddings/ollama.py`; sin dependencias nuevas), con
  conexiones reutilizadas (evita el TLS por llamada que incluyó la latencia de la prueba) y:
  - plazo total por llamada `JEV_TIMEOUT_S` (0,8 s): la petición corre en un ejecutor
    pequeño y se espera con `future.result(timeout=…)`; los plazos por fase de httpx se fijan
    al mismo valor para que el hilo también termine;
  - sin reintentos dentro del turno (el cliente de la prueba reintentaba tras 2 s);
  - tope `JEV_MAX_CALLS_PER_DAY` (1 000) por proceso, que se reinicia a medianoche de Bogotá;
  - circuito: tres fallos seguidos (plazo, red, 429, 5xx, 529, salida inválida) lo abren
    60 s; un 401, 402 o 403 lo abre 15 min y registra una sola advertencia
    `jev.auth_or_credits`;
  - `JEV_MODEL=jev-1.14.0` fijado; el modelo que devuelve cada respuesta se registra.
  - **Encendido explícito**: `JEV_ENABLED` es `false` por defecto aunque haya clave (el `.env`
    local del owner la trae); producción lo enciende en el `.env` del servidor. En local,
    `make api` solo usa Jev con `JEV_ENABLED=true`, y `carvajal eval-agent` solo construye el
    juez con `--jev-ledger`; `eval-router` no usa Jev.
  - **Libro**: si `JEV_LEDGER` está definido, el cliente anota cada llamada (sin texto) en ese
    archivo, venga del comando que venga; así ninguna llamada de desarrollo queda fuera del
    presupuesto.
  La clave es `SecretStr`, solo del entorno, y nunca aparece en logs ni en errores.
- **Rationale**: la prueba midió p95 284–349 ms incluyendo TLS; 0,8 s deja margen sin
  alargar un turno caído más de lo aceptable. Sin endpoint de saldo, el tope y el circuito
  son la única defensa de los créditos.
- **Alternatives considered**: `urllib` como en la prueba (sin conexiones reutilizadas y
  con plazo por operación, no total); reintento con espera (rompe el plazo); alias
  `jev-latest` (cambia de versión sin aviso).

## R10. El modo `routes` y el `DecisionRouter` de la 036

- **Decision**: el modo `routes` no llama a Jev. Su traza gana el bloque `gatekeeper` con la
  decisión de `decide_route` mapeada a la clase (`map_route`, data-model) y fuente `reglas`.
  El `DecisionRouter` de la 036 no se usa para Jev.
- **Rationale**: `routes` es el interruptor de emergencia y el respaldo cuando DeepSeek
  falla: añadirle un servicio externo empeora justo el camino que debe ser más robusto. Su
  router siempre decide (por defecto `rag` con abstención) y las rutas `tool` y
  `commercial` necesitan la extracción que hacen las reglas (indicador, período, uso,
  temperatura, cantidad, ciudad); Jev devuelve probabilidades, no esa extracción, así que
  no cumple el `OUTPUT_SCHEMA` del `DecisionRouter`. El único uso posible sería convertir
  `rag` en `direct` para charla, que el atajo ya cubre.
- **Alternatives considered**: Jev solo cuando `decide_route` cae en `rag/documental`
  (beneficio marginal, dependencia nueva en el respaldo); un `DecisionModel` que rellene la
  extracción con reglas (sería el router de reglas con otra etiqueta).

## R11. Medición con ≤ 120 llamadas

- **Decision**: cuatro mediciones con Jev real, una demostración en vivo opcional y una
  verificación en producción, una corrida de cada una, en este orden, con un libro de
  llamadas (`specs/046-jev-router/evaluacion/jev-calls.jsonl`, sin texto). Cada corrida usa
  su propio contador (`--max-jev-calls`) y no el tope diario del proceso, que es compartido
  entre gatekeeper y compuerta y se reinicia a medianoche de Bogotá:

  | Medición | Llamadas a Jev |
  |---|---|
  | Humo (`GET /v1/models`: clave y modelo) | 1 |
  | Gatekeeper: `gatekeeper_046.json`, Choice + Score en la misma petición | 20 |
  | Compuerta: `claims_046.json` | 16 |
  | v3 en modo `tools` con Jev (cuotas de la corrida: gatekeeper ≤ 45, compuerta ≤ 30) | ≤ 75 |
  | Demostración en vivo opcional (quickstart §2, `JEV_ENABLED=true` con `JEV_LEDGER`) | ≤ 6 |
  | Verificación en producción tras el despliegue (un turno real con `?debug=1`) | 2 |
  | **Total** | **≤ 120** |

  No hay otras llamadas: Jev está apagado por defecto en todo comando local (R9), el MVP se
  valida sin red y la skill `evaluar-agente` no activa Jev sin `--jev-ledger`. Más, sin
  llamadas a Jev: el v3 con Jev apagado (línea base del mismo día), las reglas sobre las 130
  preguntas de `routing_questions.json` mapeadas a clases, el enrutador LLM (`LLMRouter`,
  DeepSeek) sobre las 20 del gatekeeper y la reproducción de los veredictos grabados
  (`ReplayJudge` desde el libro) con el agente real sobre los ítems de `gatekeeper_046.json`
  que terminaron en cautela o en respuesta directa, para medir `GATEKEEPER_CAUTION` y
  `DIRECT_INSTRUCTIONS["gatekeeper_direct"]` aunque el v3 no los ejercite (principio III).
- **Conjuntos** (se congelan antes de medir, FR-026):
  - `gatekeeper_046.json`: 7 estructuradas, 6 rag, 2 mixtas y 5 directas. 15 salen de
    `routing_questions.json` (con su `id` de origen). Allí solo hay 3 `commercial_mixed` y
    una no es mixta: c-54 («vasos de café de 12 oz y los ingresos de Carvajal en 2024») son
    dos datos estructurados y se etiqueta `herramienta_estructurada`; las mixtas son c-52 y
    c-53. Como allí la respuesta directa son saludos que deciden las reglas, las 5 directas se
    redactan («¿eres una persona o un bot?», «no entendí tu última respuesta», «¿me puedes
    responder más corto?», «¿en qué idiomas me puedes atender?» y un saludo coloquial que las
    reglas no detectan, «quiubo pues, todo bien?») y se marcan con su origen. Las etiquetas
    de `routing_questions.json` vienen del router de reglas: las reglas parecerán perfectas
    sobre ellas y así se declara.
  - `claims_046.json`: 8 pares (original de la v3-r4 y perturbada) de la prueba del
    2026-11-24: v3-001, v3-002, v3-005, v3-008, v3-010, v3-018, v3-027 y v3-034, con la
    evidencia reconstruida con `run_tool` y armada como en producción (`build_claims_query`:
    la pregunta del ítem solo sirve para derivar `dicho_por_la_persona` y no viaja).
    Etiquetas: no respaldadas las 8 perturbadas, v3-001 («la de 16 oz es la más usada»,
    confirmada por el owner) y v3-005 («la tapa se pide aparte», misma regla: ninguna
    consulta lo devolvió); buenas las otras 6 originales, incluida v3-027 (teléfono «(601)»,
    que el owner decidió que no cuenta como inventado y que ahora llega visible a Jev, R8).
- **Estimación del v3**: 51 turnos; los que deciden las reglas no llaman al gatekeeper, y los
  abstenidos, los de reemplazo y las respuestas directas no llaman a la compuerta, así que se
  esperan unas 45 + 35 llamadas. Prioridad del gatekeeper: su cuota (45) cubre los turnos que
  no decide una regla; si se agota la cuota de la compuerta (30), los turnos restantes se
  emiten con la compuerta determinista sola (`claims.status=unavailable`, motivo `cuota`) y se
  declaran. Los turnos sin decisión del gatekeeper por cuota se excluyen del denominador de
  SC-004 y se declaran.
- **Rationale**: lo pidió el owner; las pruebas sin red cubren la lógica y la medición real
  solo responde lo que no se puede simular (exactitud de Jev y efecto en el v3).

## R12. Visibilidad

- **Decision**:
  - Evento `trace` del chat: objetos `gatekeeper` y `claims` (contrato §6); el panel de
    depuración (`_chat_stream.html`, `chat-stream.js`) muestra «Decisión: rag · jev 0,82».
  - LangSmith: metadatos en la raíz (`gatekeeper_route`, `gatekeeper_scope`,
    `gatekeeper_source`, `gatekeeper_llm_class`, `gatekeeper_confidence`, `gatekeeper_cautious`, `claims_status`,
    `claims_p`) y un hijo `jev` por llamada con `kind`, `status`, `latency_ms`, `model`,
    `input_tokens`, `output_tokens` y la decisión o el veredicto. Las claves nuevas entran en
    `SAFE_KEYS` de `infrastructure/monitoring.py`; ningún texto enviado viaja, ni con
    `content=full`.
  - `/health`: bloque `jev` (FR-024) calculado sin red: `available` = configurado, encendido,
    circuito cerrado y bajo el tope (si el runtime aún no se construyó, solo la
    configuración).
  - Logs: `gatekeeper.decided`, `jev.call`, `jev.circuit_open`, `jev.auth_or_credits`,
    `claims.judged`, sin texto ni `exc_info`.
  - Sentry: está activo con DSN y hoy usa `include_local_variables` por defecto (verdadero),
    así que una excepción en `gk/claims.py` o en el adaptador enviaría las variables locales
    (pregunta, respuesta, evidencia) a otro tercero. Se configura
    `include_local_variables=False` o un `before_send` que elimina los frames de
    `adapters/judge/` y `agent/gatekeeper/`.
  - Evaluación: el ejecutor distingue el atajo por reglas de la respuesta directa de Jev
    (con `gatekeeper.source`); `mode_path` sigue valiendo `"shortcut"` en ambos, pero la
    comprobación `shortcut` y las métricas del atajo de la 044 solo cuentan las reglas, y un
    ítem con datos que Jev manda a `respuesta_directa` se informa como error del gatekeeper.
- **Rationale**: la entrega se demuestra con la traza y la salud; el monitoreo de la 038 ya
  define qué viaja sin PII.

## R13. Pruebas sin red

- **Decision**: puertos `RouteJudge` y `ClaimsJudge` (`application/ports.py`) con dobles
  (`FakeJudge`: respuesta fija, lanza, tarda, registra cargas). El adaptador se prueba con
  `httpx.MockTransport` (200, 400, 401, 402, 422, 429, 5xx, 529, cuerpo inválido, demora).
  Una fixture *autouse* en `tests/conftest.py` fija `JEV_ENABLED=false` y
  `TYPESAFE_API_KEY=""` (el `.env` local del owner trae la clave y `Settings` lo lee) y hace
  fallar cualquier conexión a `api.typesafe.ai`.
  `ReplayJudge` reproduce sin red los veredictos grabados en el libro (por `item`). Una
  prueba importa cada módulo nuevo (`adapters/judge/*`, `agent/gatekeeper/*`,
  `toolagent/tools/registry.py`) en un subproceso aislado para detectar ciclos.
- **Rationale**: principio IV y SC-008 (cero llamadas en CI).

## R14. Base y entrega

- **Decision**: la 046 se implementa sobre la rama de la 045. Si la 045 sigue abierta, la
  implementación parte de `feat/045-memoria-herramientas` y, por pedido del owner de juntar
  PRs, puede entregarse en la misma PR; si la 045 ya se mergeó, la rama
  `feat/046-jev-router` se rebasa sobre `main` y va en su propia PR. En ambos casos las
  enmiendas de la constitución de la 046 van en el commit que introduce cada cambio. Antes de
  implementar, la 045 se rebasa sobre `main` (le faltan `dd35bfc`, `901f59f` y `921460d`).
- **Versión de la constitución**: las enmiendas D y E se publican juntas como 2.9.0 (MINOR)
  con un único Sync Impact Report: T026 lo abre con D y T038 lo actualiza con E. Si la 045
  cambia la versión antes del merge, se recalcula al rebasar.
- **Rationale**: la 046 depende del registro (`route_group` amplía `ToolSpec`), de
  `sensitive.py` y de `classify`; implementarla sobre `main` obligaría a duplicarlos.

## R15. Revisión de los artefactos (2026-11-24)

Hallazgos de la revisión aplicados a spec, plan, research, data-model, contrato, quickstart y
tasks. Se aceptaron todos los P1 y P2. Solo se rechazaron partes opcionales o una de las dos
alternativas que proponía el hallazgo, con su motivo:

| Hallazgo | Decisión | Rechazado y por qué |
|---|---|---|
| P1: `redact` borra los teléfonos de la respuesta que ve Jev | `outgoing_question` / `outgoing_answer` (R8, FR-018, FR-019, T010, T011, T031, T034) | — |
| P1: presupuesto sin holgura y llamadas fuera del libro | `JEV_ENABLED=false` por defecto, opt-in local, `JEV_LEDGER`, cuotas por corrida, v3 ≤ 75, demostración ≤ 6 (R9, R11, FR-022, FR-027, FR-031; skill `evaluar-agente` en T051) | `JEV_ENABLED=None` encendido solo en producción: exige un indicador de entorno que `Settings` no tiene (solo `sentry_environment`, con otro fin). Un `false` explícito es más simple y se enciende en el `.env` del servidor (T060). |
| P1: respuesta directa de Jev juzgada con evidencia vacía | Opción 1: no se juzga (FR-011, R6, contrato §3, T031, T032) | Opción 2 (evidencia fija «capacidades» y 2 ítems más en `claims_046`): gasta 2 llamadas de un presupuesto sin holgura y crea otro texto de evidencia que también habría que medir; el filtro de `no_query` ya impide cifras, fechas y nombres. |
| P1: la pregunta va a la compuerta | Opción (b): extracto `dicho_por_la_persona` con valores estructurados (FR-018, R8, contrato §3 y §5, T031, T034) | Opción (a) (confirmación del owner): el owner está ocupado y pidió enviar lo estrictamente necesario; (b) cumple su lista literal. Si la medición muestra falsas alarmas por la minimización, la PR propone (a). |
| P2: plazo del turno con retención completa | FR-030, R6, contrato §3, T032, T035 | Parte opcional (no llamar a la compuerta si falló el gatekeeper del mismo turno): un fallo aislado quitaría la protección a ese turno; el circuito ya acota el costo de un Jev caído tras 3 fallos seguidos. |
| P2: composición de `gatekeeper_046` imposible | 7 / 6 / 2 / 5, con c-54 estructurada y un saludo coloquial (R11, data-model, T004) | — |
| P2: saludos coloquiales en cautela | regla de conversación en `GATEKEEPER_CAUTION`, ítem en el conjunto, T018, T028, SC-002 | — |
| P2: rama LLM sin decisión auditable | `llm_class` (FR-001, FR-006, R4, data-model, contrato §1 y §6) | — |
| P2: `classify` enmascara el tipo de negocio y la ciudad | marcador fuerte o nombre propio; ciudades nunca (R8, FR-019, T010) | — |
| P2: escala de Score | `clarity` ∈ [0, 4] y `clarity_confidence` (R5, data-model, contrato §2) | — |
| P2: `memory_unavailable` en una función pura | `rule_decision` → `memory_forget`; el nodo reescribe (R1, data-model, T019, T021, T022) | — |
| P2: riesgo de importación circular | tipos en `ports.py`, `gk/__init__.py` vacío, prueba de importación (R1, T013, T015, T062) | — |
| P2: `[P]` en T040 y T041 sobre el mismo archivo | T041 pasa a `tests/test_jev_no_text.py` | — |
| P2: base de la 045 desactualizada y webhook | `88ee0d6` o posterior (hoy `314f9a7`), rebase sobre `main`, webhook en R6 y T042 | — |
| P2: prompts de cautela y directa sin medición asegurada | `ReplayJudge` (FR-032, R11, T065) | — |
| P2: tope diario compartido | cuotas por corrida; SC-004 excluye los turnos sin decisión por cuota | Contador único con prioridad dinámica del gatekeeper: más complejo de probar y de explicar en la evidencia que dos cuotas fijas. |
| P2: redacción de FR-012 y FR-014 | «o fue de memoria» quitado; retención evaluada al empezar el turno | — |
| P2: folios de cotizaciones | `[folio]` en evidencia y respuesta (FR-018, R8, T031, T040) | Sustituir por expresión regular: el folio no tiene un formato fijo en el código; se sustituyen los valores literales del payload. |
| P2: DPA de TypeSafe | T063 antes de T060 | — |
| P2: Sentry con variables locales | FR-021, R12, contrato §5, T041, T043 | — |
| P2: `mode_path="shortcut"` ambiguo en la evaluación | distinción por `gatekeeper.source` (data-model, R12, T047, T051) | Un `mode_path` nuevo: cambiaría `ModePath` y las pruebas de la 044 y la 045 sin aportar información que `source` no dé. |
| P2: versión de la constitución | D y E juntas como 2.9.0 con un único Sync Impact Report (R14, plan, T026, T038) | — |

## R16. `rag` de Jev con señal comercial (R1 de la evidencia, 2026-11-24)

- **Problema**: en v3-003 («¿qué es Propal y qué tiene que ver el bagazo de caña con los
  portacomidas Bioform que ustedes venden?») las reglas ven `rag/documental` (sin señal de
  compra no es comercial, FR-009 de la 036) y Jev eligió `rag` con 0,83. El alcance `rag`
  quitó `buscar_producto` y la compuesta perdió la parte del catálogo. Sin Jev la consulta
  pasaba. La regla de desacuerdo fuerte (R4) no la cubre porque las reglas no ven
  `commercial` ni `tool`.
- **Decision**: tras los umbrales y la cautela, si Jev elige `rag` y `commercial_signal`
  (`agent/commercial_intent.py`) detecta un producto del catálogo (las familias de
  `extract_commercial` o un desechable para llevar), un precio o dónde comprar, una
  cantidad o una capacidad, el turno se trata como **`mixta`**: alcance `all`, sin el
  bloque `<cautela>`, `reason="producto_reglas"`. `gate_class` sigue siendo `rag` (lo que
  eligió Jev) para que la decisión se pueda auditar. Con confianza baja sigue la cautela de
  R4, que ya usa todas las consultas.
- **Rationale**: `mixta` y no cautela. La cautela es para cuando la clase es dudosa y añade
  instrucciones de aclaración; aquí la pregunta es clara y Jev acierta en que pide
  documentos, solo que también nombra un producto que `buscar_producto` resuelve. Es el
  mismo trato que `commercial_mixed` (`mixta_reglas`), que tampoco lleva cautela. La señal
  es más amplia que `extract_commercial` (no exige señal de compra junto al producto) pero
  más estrecha que `_INTENT` («busco», «quiero» aparecen en preguntas institucionales).
  Solo se relaja una restricción: en el peor caso el turno vuelve al comportamiento sin
  Jev (las 20 consultas).
- **Efecto medido sin red**: de las 6 preguntas `rag` de `gatekeeper_046.json`, 0 tienen la
  señal (la exactitud y las decisiones de T053 no cambian). De las 33 `rag` de
  `routing_questions.json`, 3 la tienen (n-12, n-13 y n-14: «¿Qué vasos fabrica Carvajal
  Empaques?» y similares), donde `buscar_producto` también aporta. No se volvió a llamar a
  Jev: la prueba usa un juez falso con `rag` a 0,95 sobre la pregunta de v3-003.
- **Alternatives considered**: cautela con `desacuerdo_reglas` (añade el bloque de
  aclaración a una pregunta clara); ampliar `extract_commercial` para que v3-003 sea
  `commercial_mixed` (cambiaría el enrutador de la 036 y el modo `routes`, con sus
  conjuntos de evaluación); un umbral más alto para `rag` (no separa: 0,83 está por encima
  de cualquier valor razonable de la rejilla).
