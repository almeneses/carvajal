# Feature Specification: Router y System One (Jev)

**Feature Branch**: `feat/046-jev-router`

**Created**: 2026-11-24

**Status**: Implemented (2026-11-24). Medida una vez con `jev-1.14.0` ([evidencia](./evidencia.md)); D1, D2 y R1 corregidos después sin llamar a Jev. Jev sigue apagado por defecto (`JEV_ENABLED=false`): activarlo en producción (T060) es decisión del owner tras el DPA de TypeSafe (T063). Decisiones autónomas en *Clarifications*, pendientes de revisión del owner

**Input**: Requisito obligatorio de la entrega, texto literal: «Router y System One (Jev):
Decisión entre herramienta estructurada, RAG o respuesta directa; no buscar en saludos;
routing por reglas, LLM o gatekeeper System One (Jev: Choice, Noul, Score, Box) si está
disponible; confianza baja → derivar o responder con cautela.»

Decisiones del owner (2026-11-24): Jev es obligatorio; compuerta de afirmaciones (Noul)
después de la compuerta determinista, umbral 0,5, con abstención por el mensaje neutro; a
TypeSafe solo va lo estrictamente necesario; sin Jev, el comportamiento actual; la medición
real es pequeña (≤ 120 llamadas a Jev en total).

## Contexto

El modo `tools` (specs 044 y 045) ya decide qué consultar: el atajo por reglas atiende
saludos, despedidas y «olvida todo», y en el resto de los turnos el modelo (DeepSeek) elige
entre 20 consultas. Esa decisión la toma un LLM grande, en el mismo paso en que redacta, y no
deja una decisión explícita y auditable sobre **qué tipo de turno es**. Además, en el camino
con evidencia el texto del modelo sale casi sin control: la compuerta determinista filtra
cifras y nombres solo cuando el turno no trajo evidencia, así que una valoración sin fuente
(«la de 16 oz es la más usada para sopas») puede llegar a la persona.

Esta spec añade un **gatekeeper** explícito antes del primer paso del modelo y una
**compuerta de afirmaciones** después de la redacción, ambos con Jev (TypeSafe System One):

1. **Gatekeeper**: reglas primero (no se busca en saludos ni se llama a Jev); si las reglas
   no deciden, una pregunta **Choice** de Jev clasifica el turno en respuesta directa, RAG,
   herramienta estructurada o mixta, y esa clase limita las consultas del turno. Con
   confianza baja, el asistente responde con cautela o pide una aclaración.
2. **Compuerta de afirmaciones**: después de la compuerta determinista, una pregunta
   **Noul** de Jev comprueba que las afirmaciones de la respuesta están respaldadas por lo
   que devolvieron las consultas; si no, la respuesta se sustituye por el mensaje neutro
   que ya existe.

Todo funciona igual sin Jev: si no está configurado, falla o tarda más de su plazo, el
turno sigue el camino de hoy (reglas + LLM y compuerta determinista).

### Relación con especificaciones existentes

- **045** (memoria y 20 herramientas, rama `feat/045-memoria-herramientas`, aún no
  mergeada): base de esta spec. Se diseña contra su registro de herramientas
  (`ToolSpec` con `evidence_role`), su filtro de datos sensibles y su memoria por
  categorías. La 046 se implementa encima de la 045, desde su commit `88ee0d6` o posterior
  (hoy `314f9a7`). La 045 no contiene los últimos commits de `main` que sí tiene la rama de
  la 046 (`dd35bfc`, webhook firmado `/api/webhooks/whatsapp`; `901f59f`; `921460d`): debe
  rebasarse sobre `main` antes de implementar la 046 (research R14).
- **044** (agente con herramientas): se conservan el atajo, el respaldo `routes`, la
  compuerta determinista, la abstención y los límites. Cambia la retención del camino con
  evidencia cuando la compuerta de afirmaciones está activa (FR-014).
- **036** (asistente comercial): su `DecisionRouter` («Jev u otro System One», T038) no se
  reutiliza: exige que el modelo extraiga la solicitud comercial completa y Jev devuelve
  probabilidades, no extracción (research R10). Esta spec cubre la comparación de
  enrutadores con Jev que la 036 transfirió al número reservado 041; la 041 no se escribe.
- **038** (monitoreo): la decisión del gatekeeper y el veredicto de la compuerta viajan como
  metadatos sin texto.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Elegir el camino del turno sin buscar en saludos (Priority: P1)

Una persona escribe «Hola», luego «¿Qué es Propal?», luego «Cotízame 500 vasos de 9 oz en
Cali» y por último «¿Cuánto vendió Carvajal en 2024 y desde cuándo hace empaques?». El
saludo se responde sin consultar nada ni llamar a Jev. Las otras tres preguntas reciben una
decisión de Jev: la segunda va al corpus, la tercera a las herramientas estructuradas y la
cuarta, que mezcla una cifra con historia, puede usar ambas. Cada decisión se ve en la traza
con su fuente y su confianza.

**Why this priority**: es el núcleo literal del requisito de la entrega (decisión entre
herramienta estructurada, RAG o respuesta directa; no buscar en saludos; gatekeeper Jev).

**Independent Test**: con un doble de Jev que devuelve una clase y una confianza fijas y
un doble de modelo, comprobar para cada clase qué consultas recibe el modelo, que el saludo
no llama a Jev ni consulta nada y que la traza lleva la decisión.

**Acceptance Scenarios**:

1. **Given** un saludo, un agradecimiento, una despedida o «olvida todo lo que sabes de
   mí», **When** llega el turno, **Then** lo decide una regla, no se llama a Jev y no se
   consulta nada (ni corpus ni herramientas).
2. **Given** una pregunta que ninguna regla decide y Jev disponible, **When** llega el
   turno, **Then** se hace **una** pregunta Choice a Jev con cuatro opciones
   (`herramienta_estructurada`, `rag`, `mixta`, `respuesta_directa`) y la traza registra la
   clase, la confianza y la fuente `jev`.
3. **Given** la clase `rag` con confianza suficiente, **When** el modelo decide, **Then**
   solo dispone de las consultas documentales (corpus, preguntas frecuentes, historia y
   empresas del grupo) y de `sin_consulta`.
4. **Given** la clase `herramienta_estructurada` con confianza suficiente, **When** el
   modelo decide, **Then** solo dispone de las consultas estructuradas (catálogo, cotización,
   sedes, indicadores, fecha, cotizaciones guardadas y memoria) y de `sin_consulta`.
5. **Given** la clase `respuesta_directa` con confianza suficiente, **When** se responde,
   **Then** el turno va al nodo de respuesta directa, sin herramientas.
6. **Given** la clase `mixta`, **When** el modelo decide, **Then** dispone de las 20
   consultas.
7. **Given** una preferencia declarada («llámame Ana…») o una corrección de cantidad o
   entrega sobre una cotización en curso, **When** llega el turno, **Then** lo decide una
   regla (sin Jev): la preferencia va al agente con todas las consultas y la corrección a
   las estructuradas.

---

### User Story 2 - Confianza baja: responder con cautela o pedir aclaración (Priority: P1)

Una persona escribe «¿y eso cuánto?» al empezar una conversación, o una pregunta que Jev no
sabe clasificar con seguridad. El asistente no adivina el camino: consulta antes de afirmar
y, si la pregunta admite varias lecturas, pide una aclaración concreta u ofrece lo que sí
puede buscar.

**Why this priority**: es la segunda mitad literal del requisito («confianza baja →
derivar o responder con cautela») y evita que una clasificación dudosa deje a la persona sin
respuesta.

**Independent Test**: con un doble de Jev que devuelve confianza 0,4, comprobar que el
modelo recibe las 20 consultas y la instrucción de cautela, y que la traza marca
`cautious: true`; con confianza 0,7 para `respuesta_directa`, que el turno no va a la
respuesta directa.

**Acceptance Scenarios**:

1. **Given** una decisión de Jev con confianza menor que el umbral (0,6 inicial), **When**
   el modelo decide, **Then** dispone de las 20 consultas y de la instrucción de cautela, y
   la traza registra `cautious: true` con el motivo `confianza_baja`.
2. **Given** `respuesta_directa` con confianza menor que su umbral propio (0,8 inicial),
   **When** se decide, **Then** el turno no va a la respuesta directa: va a cautela.
3. **Given** una señal fuerte de las reglas (producto del catálogo con dato de compra,
   indicador con período) y una clase de Jev que excluye las estructuradas, **When** se
   decide, **Then** gana la cautela con todas las consultas (motivo `desacuerdo_reglas`).
4. **Given** cautela y una pregunta ambigua, **When** el modelo responde, **Then** pide la
   aclaración con `sin_consulta` (petición del dato) o declara que no lo encontró y ofrece
   lo que sí puede buscar; nunca afirma datos sin consultarlos.
5. **Given** un saludo coloquial que las reglas no detectan («quiubo pues, todo bien?») y una
   decisión de Jev que termina en cautela, **When** el modelo responde, **Then** contesta con
   `sin_consulta(motivo="conversacion")` y no ejecuta ninguna consulta con datos.

---

### User Story 3 - Ninguna afirmación sin fuente llega a la persona (Priority: P1)

El dueño de un restaurante pide un recipiente para sopa caliente. El modelo consulta el
catálogo y redacta: «La cazuela Wau Maestro viene en 12, 16 o 26 oz. La de 16 oz es la más
usada para sopas.» La segunda frase no está en ningún resultado. Jev la marca como no
respaldada y la persona recibe el mensaje neutro, no la frase inventada. En cambio, una
respuesta que escribe un teléfono del directorio con el indicativo «(601)» en lugar de
«+57 (1)» se emite: reformatear un dato no es inventarlo.

**Why this priority**: es la decisión del owner sobre Noul y cierra el hueco del camino con
evidencia, que hoy no filtra el texto (principio III).

**Independent Test**: con un doble de Jev que devuelve p = 0,2, comprobar que la respuesta
emitida y persistida es el texto neutro y que no salió ningún fragmento del texto del
modelo; con p = 0,8, que sale el texto del modelo; con Jev caído, que sale el texto que pasó
la compuerta determinista.

**Acceptance Scenarios**:

1. **Given** una respuesta que pasó la compuerta determinista y Jev disponible, **When** Jev
   devuelve una probabilidad de respaldo menor que 0,5, **Then** la respuesta se sustituye
   por el texto neutro que la compuerta determinista usa al rechazar (reemplazo neutro si el
   turno pidió un dato o trajo sedes; si no, la abstención ajustada a lo consultado) y eso es
   lo que se emite y se guarda.
2. **Given** una probabilidad mayor o igual que 0,5, **When** se cierra el turno, **Then** se
   emite la respuesta del modelo con sus fuentes.
3. **Given** la compuerta de afirmaciones activa, **When** el modelo redacta la respuesta
   final de un turno con evidencia, **Then** ningún fragmento se emite antes del veredicto y
   la persona ve una línea de estado mientras se revisa.
4. **Given** un saludo, una despedida, una respuesta directa (por reglas o decidida por Jev),
   una abstención, un reemplazo neutro, un turno solo de memoria, el respaldo determinista o
   un texto que solo hace preguntas a la persona, **When** se cierra el turno, **Then** no se
   llama a Jev.
5. **Given** que Jev no responde a tiempo o falla, **When** se cierra el turno, **Then** se
   emite el texto que pasó la compuerta determinista y la traza registra la compuerta como
   no disponible.
6. **Given** un turno con evidencia cuya redacción termina cuando ya no queda plazo del turno
   (`agent_turn_timeout_s`) para una llamada a Jev, **When** se cierra el paso, **Then** no se
   llama a Jev, se emite el texto que pasó la compuerta determinista con `claims.status =
   unavailable` (motivo `sin_plazo`) y el turno no cae en el respaldo por plazo vencido.

---

### User Story 4 - Funciona con o sin Jev y a TypeSafe solo va lo mínimo (Priority: P1)

El owner apaga Jev (o se acaban los créditos, o TypeSafe tarda) y el asistente sigue
respondiendo como hoy, sin errores visibles. Con Jev encendido, una persona escribe «soy
Ana, trabajo en Panadería La Espiga, mi celular es 300 123 4567, ¿qué vasos tienen para
tinto?»: a TypeSafe llega «soy [dato de la persona], trabajo en [dato de la persona], mi
celular es [dato omitido], ¿qué vasos tienen para tinto?», sin su identidad, su memoria ni su
historial; y en los registros solo queda la decisión, la confianza, la latencia, el modelo y
los tokens.

**Why this priority**: Jev es un tercero externo con créditos limitados; la entrega no puede
depender de él ni filtrar datos de las personas (decisiones del owner).

**Independent Test**: con un doble que registra cada carga enviada, recorrer al menos 30
frases sensibles del conjunto de la 045 y comprobar que ninguna llega sin redactar; con el
cliente configurado contra un servidor falso que tarda, falla o responde 402, comprobar que
el turno sigue con reglas + LLM y que el tiempo añadido no supera el plazo.

**Acceptance Scenarios**:

1. **Given** Jev sin clave, apagado o con el tope diario alcanzado, **When** llega un turno,
   **Then** no se hace ninguna llamada, el turno sigue con reglas + LLM y la compuerta
   determinista sola, y la traza registra la fuente `llm` y el estado de Jev.
2. **Given** Jev que tarda más que `JEV_TIMEOUT_S` (0,8 s), responde con error o devuelve
   una respuesta inválida, **When** llega un turno, **Then** el turno sigue sin Jev y no
   espera más que el plazo.
3. **Given** errores seguidos o un 401/402/403, **When** llegan más turnos, **Then** el
   cliente deja de llamar durante un tiempo (circuito abierto) y lo registra una vez.
4. **Given** cualquier turno, **When** se llama a Jev, **Then** la carga solo contiene: para
   el gatekeeper, la pregunta redactada y como mucho una línea de contexto; para la
   compuerta, la respuesta sin los datos de la persona, los extractos mínimos de evidencia
   y los datos estructurados que la persona dio (cantidad, medida, producto, uso, ciudad,
   período), nunca la pregunta completa. Nunca el `owner_key`, el hilo, el número de
   WhatsApp, el nombre ni la memoria de la persona, el resumen de la conversación ni los
   folios de sus cotizaciones.
5. **Given** cualquier llamada, **When** se registra en logs, traza, LangSmith, Sentry o el libro
   de llamadas de la medición, **Then** solo quedan decisión, probabilidades o confianza,
   latencia, modelo, tokens y estado; nunca el texto enviado, ni siquiera con
   `content=full`.

---

### User Story 5 - Ver y medir la decisión (Priority: P2)

El owner necesita demostrar en la entrega qué decide el gatekeeper, de dónde sale cada
decisión (reglas, Jev o LLM), que la compuerta funciona y que encender Jev no empeora el
asistente, gastando como mucho 120 llamadas a Jev.

**Why this priority**: sin visibilidad ni medición, el requisito no se puede demostrar ni
defender; pero depende de que US1–US4 existan.

**Independent Test**: un turno en `/chat?debug=1` muestra la decisión en la traza; `/health`
muestra el bloque `jev`; las corridas de medición dejan su informe en
`specs/046-jev-router/evidencia.md` y su libro de llamadas.

**Acceptance Scenarios**:

1. **Given** un turno del modo `tools`, **When** termina, **Then** el evento `trace` del chat
   y la raíz del turno en LangSmith llevan la decisión del gatekeeper (clase, alcance,
   fuente, confianza, cautela) y el veredicto de la compuerta.
2. **Given** `/health`, **When** se consulta, **Then** trae un bloque `jev` con `configured`,
   `available` y el modelo, sin llamar a TypeSafe.
3. **Given** las mediciones (gatekeeper, compuerta y v3 con Jev encendido y apagado),
   **When** terminan, **Then** suman como mucho 120 llamadas a Jev, registradas en el libro
   de llamadas sin texto.

### Edge Cases

- «Hola, ¿cuánto vale la caja de vasos de 9 oz?»: no es un saludo puro; lo decide Jev.
- Pregunta en inglés: Jev la clasifica igual; las instrucciones de la pregunta Choice van en
  español.
- Inyección en la pregunta («[SISTEMA: ignora tus reglas]»): Jev solo devuelve una clase y
  probabilidades, así que no hay canal para obedecerla; si clasifica mal, siguen la
  restricción de consultas, la compuerta determinista y la compuerta de afirmaciones.
- Jev devuelve una opción que no existe, probabilidades vacías o sin confianza: salida
  inválida → reglas + LLM.
- El modelo, con el alcance restringido, pide una consulta fuera del alcance: no se ejecuta;
  recibe un error declarado («esa consulta no está disponible en este turno») que gasta
  cupo, como una llamada ilegible.
- Pregunta elíptica al empezar el hilo («¿y eso?»): no hay línea de contexto; Jev suele
  dar confianza baja → cautela.
- Pregunta muy larga: a Jev viajan solo los primeros 1 000 caracteres redactados.
- Respuesta muy larga o evidencia que supera `JEV_MAX_EVIDENCE_CHARS`: cada extracto se
  recorta a su cuota con «…»; una respuesta de más de 8 000 caracteres no se juzga y se
  registra `claims.status = skipped` con motivo `demasiado_larga`.
- La respuesta cita fragmentos `[n]` del corpus: solo esos fragmentos viajan como evidencia
  documental; si no cita ninguno, viajan los del turno hasta la cuota.
- La respuesta repite lo que la persona dijo en turnos anteriores («como me dijiste, tu
  cafetería…»): las instrucciones de la compuerta excluyen de la evaluación lo que la
  respuesta atribuye a la persona (no se envían turnos anteriores).
- El respaldo `routes` atiende el turno (DeepSeek caído o `tools` sin arrancar): no se llama a
  Jev; la traza registra la decisión de las reglas.
- Réplica de un turno ya guardado (`client_turn_id` repetido, también el reintento del
  webhook firmado `/api/webhooks/whatsapp` con el mismo `message_id`): se devuelve lo
  guardado sin llamar a Jev.
- «Detener» durante la llamada a Jev: el turno se corta al volver la llamada (como mucho el
  plazo); la respuesta de Jev se descarta.
- Varios procesos de uvicorn: el tope diario y el circuito son por proceso (documentado).
- El alias `jev-latest` cambia de versión: el modelo va fijado (`jev-1.14.0`); un cambio de
  versión exige repetir la medición de la compuerta y del gatekeeper.
- Un 429 (límite de tasa) o 529 (sobrecarga): fallo sin reintento en el turno; cuenta para
  el circuito.
- Las pruebas de CI corren con un `.env` local que trae la clave: una fixture de las pruebas
  apaga Jev y cualquier intento de conexión con TypeSafe falla la prueba.
- Comandos locales (`make api`, `carvajal eval-agent`, `carvajal eval-router`) con la clave
  en el `.env`: Jev sigue apagado (`JEV_ENABLED` es `false` por defecto) salvo opt-in
  explícito (`JEV_ENABLED=true`, o `--jev-ledger` en `eval-agent`), y toda llamada se anota en
  el libro si `JEV_LEDGER` está definido (FR-031).
- La respuesta cita el folio de una cotización guardada de la persona: el folio viaja como
  `[folio]` en la respuesta y en la evidencia.
- Saludo coloquial que las reglas no detectan («quiubo pues, todo bien?»): lo decide Jev; si
  termina en cautela, la instrucción de cautela pide responder con
  `sin_consulta(motivo="conversacion")` sin buscar.

## Requirements *(mandatory)*

### Functional Requirements

**Gatekeeper (router del modo `tools`)**

- **FR-001**: Cada turno del modo `tools` MUST recibir una decisión del gatekeeper antes del
  primer paso del modelo, con: clase (`herramienta_estructurada`, `rag`, `mixta` o
  `respuesta_directa`, o ninguna si no hubo clasificación), alcance aplicado (`direct`,
  `rag`, `structured` o `all`), fuente (`reglas`, `jev` o `llm`), confianza (si la hay), si
  se aplicó cautela y, cuando decidió el LLM, la clase derivada de las consultas que ejecutó
  (`llm_class`, FR-006).
- **FR-002**: Las reglas MUST decidir primero y sin llamar a Jev: saludo o capacidad,
  despedida y «olvida todo» → respuesta directa sin herramientas (no se busca en saludos);
  preferencia declarada → agente con las 20 consultas; corrección de cantidad o entrega
  sobre el contexto comercial del hilo → consultas estructuradas.
- **FR-003**: Si ninguna regla decide y Jev está disponible, el sistema MUST hacer **una**
  sola pregunta Choice a Jev por turno, sin reintentos, con las cuatro opciones y sus
  descripciones en español del contrato
  ([contracts/jev-gatekeeper.md](./contracts/jev-gatekeeper.md)).
- **FR-004**: La decisión aplicada MUST restringir el turno: `respuesta_directa` → nodo de
  respuesta directa sin herramientas; `rag` → consultas del grupo documental y
  `sin_consulta`; `herramienta_estructurada` → consultas del grupo estructurado y
  `sin_consulta`; `mixta` → las 20. El grupo de cada consulta se declara en el registro de
  herramientas. Una llamada fuera del alcance MUST NOT ejecutarse y devuelve un error
  declarado que gasta cupo.
- **FR-005**: Con confianza baja el sistema MUST aplicar **cautela**: las 20 consultas y una
  instrucción que obliga a consultar antes de afirmar y a pedir una aclaración concreta (con
  `sin_consulta`) si la pregunta admite varias lecturas, o a declarar lo que no encontró y
  ofrecer lo que sí puede buscar; si el mensaje es solo conversación (saludo,
  agradecimiento), pide responder con `sin_consulta(motivo="conversacion")` sin buscar. Hay
  confianza baja si la confianza de Jev es menor que
  `JEV_ROUTER_MIN_CONFIDENCE` (0,6 inicial), si la clase es `respuesta_directa` con confianza
  menor que `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` (0,8 inicial) o si Jev excluye las consultas
  estructuradas cuando las reglas ven una señal estructurada fuerte. Con confianza baja
  nunca se va a la respuesta directa.
- **FR-006**: Sin Jev (no configurado, apagado, sin cupo, circuito abierto, error, plazo
  vencido o salida inválida) el sistema MUST seguir el comportamiento actual: reglas y, si
  no deciden, el modelo elige entre las 20 consultas (fuente `llm`), sin instrucción de
  cautela y sin error visible. Al cerrar el turno, la clase que eligió el LLM se deriva sin
  costo ni red del `route_group` de las consultas ejecutadas (solo `rag` → `rag`; solo
  `structured` → `herramienta_estructurada`; ambas → `mixta`; solo `sin_consulta` o ninguna →
  `respuesta_directa`) y se registra como `llm_class` en la traza y en LangSmith. La cautela
  solo existe con Jev: sin él no hay rama de confianza baja, por decisión del owner (sin Jev,
  «como hoy»).
- **FR-007**: Los umbrales MUST fijarse con la medición del gatekeeper según la regla
  declarada antes de medir (research R4) y ser configurables.
- **FR-008**: Score MUST NOT usarse en producción en esta spec: se mide como posible
  predictor de error junto a la confianza de Choice, en la misma petición; Box no aplica
  (el chat no recibe imágenes). Ambas decisiones se documentan.
- **FR-009**: El modo `routes` MUST NOT llamar a Jev (es el interruptor de emergencia y el
  respaldo); su evento `trace` MUST mostrar la decisión de las reglas con la misma forma
  (fuente `reglas`).
- **FR-010**: `JEV_ROUTER_MODE=shadow` MUST consultar a Jev y registrar la decisión sin
  restringir el turno; `off` no consulta.

**Compuerta de afirmaciones (Noul)**

- **FR-011**: Con la compuerta activa, el texto que el modelo redactó y que la compuerta
  determinista dejaría salir MUST pasar por una pregunta Noul de Jev en los caminos con
  evidencia (`evidence`) y sin consulta (`no_query`). Quedan fuera: la respuesta directa
  (atajo por reglas o decidida por Jev: no consulta nada y ya pasa el filtro determinista de
  `no_query`, que no deja salir cifras, fechas ni nombres sin respaldo), las abstenciones,
  los reemplazos neutros, los turnos solo de memoria, el respaldo determinista y los textos
  que solo contienen preguntas a la persona.
- **FR-012**: Si la probabilidad de respaldo es menor que `JEV_CLAIMS_THRESHOLD` (0,3 desde
  el 2026-11-26; antes 0,5, ver `evidencia.md` §10.7), el texto MUST sustituirse por el
  mismo texto neutro que la compuerta determinista usa al rechazar (reemplazo neutro si el
  turno pidió un dato o trajo sedes; si el turno solo consultó búsquedas deterministas
  —opciones del catálogo o sedes del directorio—, esos datos presentados sin redacción del
  modelo, desde el 2026-11-26; si no, la abstención ajustada a lo consultado, sin fuentes),
  y eso es lo que se emite, se persiste y se devuelve por WhatsApp.
- **FR-013**: El criterio de la pregunta Noul MUST contar como no respaldada una valoración o
  recomendación presentada como hecho que ninguna consulta devolvió («la de 16 oz es la más
  usada para sopas») y MUST NOT contar como inventado un dato reformateado (un teléfono con el
  indicativo actualizado «(601)», otros separadores, una unidad equivalente).
- **FR-014**: Con la compuerta activa (evaluada una vez al empezar el turno), el camino
  `evidence` MUST
  retener el texto completo hasta el veredicto (no se emite nada antes) y mostrar una línea
  de estado fija mientras se revisa. Con la compuerta apagada o Jev no disponible al empezar
  el turno, la retención es la de hoy (48 caracteres).
- **FR-015**: Si Jev falla o vence su plazo en la compuerta, MUST emitirse el texto que pasó
  la compuerta determinista y registrarse `claims.status = unavailable`.
- **FR-016**: `JEV_CLAIMS_MODE=off` MUST apagar la compuerta y devolver la retención de hoy.
- **FR-030**: La compuerta MUST respetar el plazo del turno (`agent_turn_timeout_s`): solo se
  llama a Jev si al cerrar el paso queda un plazo mayor que `JEV_TIMEOUT_S` más un margen
  (0,2 s); si no, `claims.status = unavailable` (motivo `sin_plazo`) y se emite lo que pasó la
  compuerta determinista. Cuando ya hay texto final retenido, el turno se marca como con
  texto (`begin_text()`) antes de la llamada, para que el plazo no descarte una respuesta ya
  redactada y la cambie por el respaldo.

**Privacidad y minimización**

- **FR-017**: La carga del gatekeeper MUST contener solo la pregunta del turno redactada
  (como mucho 1 000 caracteres) y, solo si la pregunta es elíptica o corta, una línea de
  contexto: la pregunta anterior de la persona redactada (como mucho 160 caracteres).
- **FR-018**: La carga de la compuerta MUST contener solo: (a) la respuesta, con los datos de
  la persona sustituidos (`outgoing_answer`: los tramos que el filtro de la 045 encuentra en
  la pregunta y el nombre, la empresa y las notas de su memoria; los teléfonos, direcciones
  y demás datos de las consultas que no vienen de la persona se conservan para que Jev pueda
  verificarlos); (b) los extractos mínimos de evidencia: los datos de las consultas del turno
  que pueden respaldar texto (sin `sin_consulta`, memoria ni errores; del corpus, solo los
  fragmentos citados si la respuesta cita alguno), con un tope total de
  `JEV_MAX_EVIDENCE_CHARS`; y (c) como un extracto más (`dicho_por_la_persona`), solo los
  datos estructurados que la persona dio en la pregunta según la extracción de las reglas
  (cantidad, capacidad o medida, familia de producto, uso, temperatura, ciudad, indicador y
  período), redactados; nunca la pregunta completa. Los folios de las cotizaciones de la
  persona (valores `folio`/`id` que devuelven `consultar_cotizacion` y `cotizar`) se
  sustituyen por `[folio]` en la evidencia y en la respuesta.
- **FR-019**: La pregunta y la línea de contexto del gatekeeper MUST pasar por
  `outgoing_question`: el filtro de datos sensibles de la 045 (`redact`: documentos,
  teléfonos y correos, tarjetas, cuentas, claves) y un enmascarado del nombre y la empresa de
  la persona (los declarados en el propio texto y los guardados en su memoria) y de sus
  notas. La empresa declarada se enmascara solo si viene de un marcador fuerte («trabajo
  en», «mi empresa es», «mi negocio se llama») o tiene forma de nombre propio; las ciudades
  nunca se enmascaran. La respuesta de la compuerta pasa por `outgoing_answer` (FR-018), no
  por `redact` completo.
- **FR-020**: Las cargas MUST NOT contener `owner_key`, `thread_id`, el número de WhatsApp,
  cookies o cabeceras de sesión, la memoria del dueño, el resumen de la conversación, el
  contexto comercial, ni respuestas de turnos anteriores.
- **FR-021**: Los logs, la traza SSE, LangSmith (con `content=metadata` y con
  `content=full`), Sentry y el libro de llamadas de la medición MUST guardar solo: tipo de
  llamada, decisión o veredicto, probabilidades o confianza, umbral, latencia, modelo, tokens
  y estado. Nunca el texto enviado. El adaptador y `agent/gatekeeper/` registran sus
  excepciones sin `exc_info` ni textos, y Sentry se configura sin variables locales
  (`include_local_variables=False`) o con un `before_send` que elimina los frames de
  `adapters/judge/` y `agent/gatekeeper/`.

**Operación y visibilidad**

- **FR-022**: La configuración MUST incluir `JEV_ENABLED` (por defecto `false`: Jev se
  enciende de forma explícita, en producción desde el `.env` del servidor), `JEV_LEDGER`,
  `TYPESAFE_API_KEY`, `JEV_BASE_URL`, `JEV_MODEL` (fijado en
  `jev-1.14.0`), `JEV_TIMEOUT_S` (0,8), `JEV_ROUTER_MODE`, `JEV_ROUTER_MIN_CONFIDENCE`,
  `JEV_ROUTER_DIRECT_MIN_CONFIDENCE`, `JEV_CLAIMS_MODE`, `JEV_CLAIMS_THRESHOLD`,
  `JEV_MAX_CALLS_PER_DAY` y `JEV_MAX_EVIDENCE_CHARS`. La clave solo sale del entorno y nunca
  se registra.
- **FR-023**: El cliente de Jev MUST respetar un plazo total por llamada de `JEV_TIMEOUT_S`,
  no reintentar dentro del turno, reutilizar conexiones, aplicar un tope diario de llamadas
  por proceso y abrir un circuito ante fallos seguidos (60 s) o ante 401, 402 o 403
  (15 min).
- **FR-024**: `/health` MUST incluir un bloque `jev` con `configured`, `enabled`, `available`,
  `model`, `router`, `claims`, `calls_today` y `last_error` (categoría), sin llamar a
  TypeSafe.
- **FR-025**: El evento `trace` del chat MUST incluir `gatekeeper` (clase, alcance, fuente,
  confianza, cautela, regla, `llm_class` y estado de Jev) y `claims` (estado y probabilidad); el panel de
  depuración (`?debug=1`) MUST mostrar la decisión. La raíz del turno en LangSmith MUST
  llevar los mismos datos como metadatos y un hijo por llamada a Jev.

**Medición**

- **FR-026**: Antes de medir MUST congelarse dos conjuntos: `data/eval/gatekeeper_046.json`
  (20 preguntas con la clase esperada) y `data/eval/claims_046.json` (16 respuestas con su
  evidencia y su etiqueta).
- **FR-027**: La medición con Jev real MUST sumar como mucho 120 llamadas (humo, gatekeeper,
  compuerta, v3 en modo `tools` con Jev, la demostración en vivo opcional y la verificación
  en producción), una corrida de cada una, con un libro de llamadas sin texto; cada corrida
  usa su propio contador (`--max-jev-calls`; en `eval-agent`, cuotas separadas para el
  gatekeeper y la compuerta) en lugar del tope diario. El v3 con Jev apagado se corre en la
  misma rama y el mismo día como línea base. Los prompts de cautela y de respuesta directa se
  miden además sin créditos de Jev, reproduciendo los veredictos grabados (FR-032).
- **FR-028**: La evidencia MUST quedar en `specs/046-jev-router/evidencia.md` y la
  investigación previa en `docs/research/jev.md`.
- **FR-029**: Todo lo nuevo MUST cubrirse con pruebas sin red ni LLM reales, con dobles del
  puerto de Jev y un servidor HTTP falso para el adaptador.
- **FR-031**: Ningún comando local MUST llamar a Jev sin opt-in explícito: `make api` solo con
  `JEV_ENABLED=true`; `carvajal eval-agent` solo con `--jev-ledger` (sin él, el juez no se
  construye aunque haya clave); `eval-gatekeeper` y `eval-claims` exigen `--ledger`. Si
  `JEV_LEDGER` está definido, el cliente anota cada llamada en el libro, venga del comando que
  venga. La skill `evaluar-agente` se actualiza en el mismo commit que cambia este flujo.
- **FR-032**: Los veredictos del gatekeeper grabados en la medición MUST poder reproducirse
  sin llamar a Jev (`ReplayJudge` desde el libro) para correr el agente real (solo DeepSeek)
  sobre los ítems de `gatekeeper_046.json` que terminaron en cautela o en respuesta directa,
  de modo que `GATEKEEPER_CAUTION` y `DIRECT_INSTRUCTIONS["gatekeeper_direct"]` se midan
  aunque el v3 no los ejercite.

### Key Entities *(include if feature involves data)*

- **Decisión del gatekeeper**: clase, alcance, fuente, confianza, probabilidades, cautela,
  regla, motivo y metadatos de la llamada a Jev. Pertenece a un turno (canal del estado del
  hilo que se reinicia en cada turno).
- **Veredicto de la compuerta**: estado (`supported`, `unsupported`, `skipped`,
  `unavailable`), probabilidad de respaldo, umbral y metadatos de la llamada.
- **Llamada a Jev**: tipo (`router`, `claims`), estado, latencia, modelo resuelto y tokens.
  Nunca el texto.
- **Grupo de una consulta**: `rag`, `structured` o `any`, declarado en el registro de
  herramientas de la 045.
- **Conjuntos de medición**: gatekeeper (pregunta, contexto opcional, clase esperada, origen)
  y compuerta (pregunta, respuesta, evidencia, etiqueta, origen).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En `gatekeeper_046.json` (20 preguntas, una corrida), Jev acierta la clase en
  al menos 16/20; con los umbrales finales, las decisiones aplicadas (sin cautela) aciertan
  al menos el 90 %, y ninguna pregunta que necesita datos se envía a `respuesta_directa`. Se
  informan además las reglas y el enrutador LLM sobre las mismas preguntas.
- **SC-002**: Cero llamadas a Jev y cero consultas en saludos, agradecimientos, despedidas y
  «olvida todo», en pruebas sin red y en las trazas del v3. Un saludo coloquial que las
  reglas no detectan y que termina en cautela no ejecuta ninguna consulta con datos (prueba
  sin red).
- **SC-003**: En `claims_046.json` (16 respuestas, una corrida), la compuerta detecta al
  menos 9 de las 10 no respaldadas (8 perturbadas, v3-001 y v3-005) y marca como no
  respaldada como mucho 1 de las 6 buenas (incluida v3-027, con el teléfono «(601)», que llega visible a Jev porque no es de la
  persona).
- **SC-004**: En el v3 en modo `tools`, con Jev encendido frente a apagado (misma rama, mismo
  día, una corrida cada uno): ítems que pasan con Jev ≥ ítems sin Jev − 1; violaciones de
  garantía y turnos con dato sin fuente con Jev ≤ sin Jev; cada sustitución hecha por la
  compuerta se audita y se lista; al menos el 90 % de los turnos que no decide una regla
  tienen decisión de Jev (el resto se declara: plazo o error); los turnos sin decisión porque
  se agotó la cuota de la corrida se excluyen del denominador y se declaran.
- **SC-005**: Sin Jev (sin clave, apagado, tope, circuito, plazo vencido, 5xx, 401/402/403,
  salida inválida), el 100 % de las pruebas sin red pasan con el comportamiento actual y
  ninguna excepción llega al turno; con Jev que no responde, el turno no espera más que
  `JEV_TIMEOUT_S` + 0,1 s por llamada.
- **SC-006**: En el v3 con Jev: p95 de cada llamada a Jev ≤ 0,5 s; fin del turno p95 con Jev
  ≤ fin del turno p95 sin Jev + 1,0 s y < 20 s. El primer texto se informa: con la compuerta
  activa coincide con el fin del turno (decisión registrada en *Clarifications*).
- **SC-007**: Cero datos personales en las cargas enviadas a Jev en al menos 30 frases
  sensibles y 10 turnos con memoria (nombre, empresa, notas) verificados con un doble que
  registra las cargas; cero textos enviados en logs, traza, LangSmith, Sentry y libro de
  llamadas.
- **SC-008**: La medición completa usa como mucho 120 llamadas a Jev, registradas en el libro;
  las pruebas de CI y los comandos locales sin opt-in hacen cero llamadas.
- **SC-009**: El 100 % de los turnos del modo `tools` llevan `gatekeeper` y `claims` en el
  evento `trace` y en la raíz de LangSmith, y `/health` trae el bloque `jev`, verificado por
  prueba.
- **SC-010**: Sin regresiones: `make check` en verde, las suites de la 045 en verde y los
  casos dorados del cotizador 18/18.

## Clarifications

### Session 2026-11-24 (decisiones del owner)

- Q: ¿Jev es opcional? → A: no, es obligatorio para la entrega; la API se probó el
  2026-11-24 (`POST https://api.typesafe.ai/v1/systemone`, modelo resuelto `jev-1.14.0`).
- Q: ¿Dónde va Noul y con qué umbral? → A: después de la compuerta determinista, umbral 0,5;
  si Jev marca la respuesta como no respaldada, abstención con el mensaje neutro existente.
  Una afirmación sin fuente como «la de 16 oz es la más usada para sopas» cuenta como no
  respaldada; reformatear un teléfono a «(601)» no cuenta como inventar.
- Q: ¿Qué se envía a TypeSafe? → A: solo lo estrictamente necesario para la trazabilidad y
  las mejoras: al router, la pregunta con datos sensibles redactados y como mucho una línea
  de contexto; a la compuerta, la respuesta y los extractos mínimos de evidencia; nunca
  `owner_key`, número de WhatsApp, nombre ni memoria. Logs y traza: solo decisión,
  probabilidades o confianza, latencia, modelo y tokens.
- Q: ¿Y si Jev no está o falla? → A: reglas + LLM como hoy y la compuerta determinista sola,
  con un plazo de unos 800 ms. La entrega funciona con o sin Jev.
- Q: ¿Cuánto se mide? → A: poco: como mucho 120 llamadas a Jev en total.

### Session 2026-11-24 (decisiones autónomas, para revisión del owner)

El detalle y las alternativas están en [research.md](./research.md).

- Q: ¿Qué opciones tiene la pregunta Choice? → A: cuatro: `herramienta_estructurada`, `rag`,
  `mixta` y `respuesta_directa`, en ese orden (la documentación de Jev advierte un sesgo hacia
  la primera opción; la más costosa de equivocar, la respuesta directa, va al final). `mixta`
  existe porque el v3 tiene preguntas que piden a la vez una cifra o un producto y un dato
  documental (v3-003, v3-004). No hay opción `fuera_de_alcance`: la abstención determinista
  ya resuelve lo que el corpus no tiene, y un rechazo por clasificación podría dejar sin
  respuesta preguntas que sí se pueden responder (R2).
- Q: ¿La clase restringe todo el turno o solo el primer paso? → A: todo el turno, con tres
  salvaguardas: cautela con confianza baja, la regla de desacuerdo con señales fuertes de las
  reglas y la opción `mixta`. Una restricción solo del primer paso no garantiza nada
  auditable (R3).
- Q: ¿Qué es «derivar» y qué es «responder con cautela»? → A: ambas son la misma salida con
  confianza baja: el modelo recibe las 20 consultas y la instrucción de cautela; deriva
  cuando pide una aclaración concreta o declara lo que no encontró y ofrece lo que sí puede
  buscar (o, en necesidades a la medida, los canales que ya devuelven las consultas), y
  responde con cautela cuando consulta antes de afirmar. Nunca se inventan canales (R4).
- Q: ¿Se usa Score? → A: no en producción. La confianza de Choice ya mide la duda de la
  decisión y Noul ya mide el respaldo; Score se añade a la misma petición del gatekeeper solo
  en la medición, sin llamadas extra, para ver si predice errores mejor que la confianza. Si
  lo hace, se propone en una spec aparte. Box no aplica: el chat no recibe imágenes (R5).
- Q: ¿Se retiene el texto completo para la compuerta o se emite y se corrige después? → A:
  se retiene. Emitir y sustituir después mostraría unos segundos una afirmación sin fuente
  (principio III) y obligaría a cambiar cómo el servicio, la API síncrona y el puente de
  WhatsApp unen los eventos `token` y `fallback`. El costo es que, con la compuerta activa,
  el primer texto llega cuando termina la redacción (≈ fin del turno + 0,3 s); el criterio de
  latencia pasa a ser el fin del turno (SC-006) y `JEV_CLAIMS_MODE=off` devuelve el streaming
  de hoy (R6).
- Q: ¿Qué texto sustituye una respuesta rechazada? → A: el mismo que la compuerta
  determinista usa al rechazar (`_replacement` o la abstención ajustada a lo consultado),
  para no crear un tercer mensaje neutro ni perder una petición de dato (R7).
- Q: ¿Se envía la pregunta a la compuerta? → A: no completa. El owner fijó que a la compuerta
  van la respuesta y la evidencia mínima; para no marcar como inventado lo que la persona dijo
  («500 vasos»), viaja como un extracto más (`dicho_por_la_persona`) solo lo estructurado que
  la persona dio según la extracción de las reglas (cantidad, medida, producto, uso, ciudad,
  período), redactado. No se envían turnos anteriores; las instrucciones excluyen lo que la
  respuesta atribuye a la persona. Si la medición muestra falsas alarmas por esta
  minimización, la PR propone al owner enviar la pregunta redactada (R8, R15).
- Q: ¿Se redactan los teléfonos del directorio en la evidencia o en la respuesta? → A: no.
  Son datos públicos de empresas y sin ellos Jev no podría verificar un teléfono inventado
  ni aceptar uno reformateado (el camino con evidencia no tiene filtro determinista de
  cifras). Por eso la respuesta no pasa por `redact` completo: `outgoing_answer` solo
  sustituye los tramos que el filtro encuentra en la pregunta de la persona, el nombre, la
  empresa y las notas de su memoria, y los folios de sus cotizaciones. Las ciudades no se
  enmascaran: no identifican y son necesarias para comparar sedes (R8).
- Q: ¿Se juzga la respuesta directa decidida por Jev? → A: no, igual que el atajo por
  reglas. No consulta nada, su instrucción prohíbe cifras, fechas y nombres de Carvajal y ya
  pasa el filtro determinista de `no_query`; juzgarla con evidencia vacía haría que Jev
  marcara como no respaldadas frases propias del asistente («soy un asistente virtual de
  Carvajal») y la persona recibiría «no tengo ese dato» (R6).
- Q: ¿Qué modelo? → A: `jev-1.14.0` fijado (hoy es lo que resuelve `jev-latest`); un alias
  móvil cambiaría el umbral sin aviso. Cambiarlo exige repetir la medición (R9).
- Q: ¿El modo `routes` usa Jev? → A: no. Es el interruptor de emergencia y el respaldo cuando
  DeepSeek falla: debe depender de los menos servicios posibles; además su router por reglas
  siempre decide y sus rutas `tool`/`commercial` necesitan la extracción que hacen las
  reglas, que Jev no da. Su traza muestra la decisión de las reglas (R10).
- Q: ¿Un tope de gasto? → A: `JEV_MAX_CALLS_PER_DAY` (1 000 por proceso; ≈ 500 turnos con
  dos llamadas) y un circuito ante 401/402/403, porque la API no informa el saldo (R9).
- Q: ¿Jev se enciende solo porque hay clave? → A: no. `JEV_ENABLED` es `false` por defecto:
  el `.env` local del owner trae la clave y cada `make api` o `eval-agent` gastaría créditos
  fuera del libro. Producción lo enciende en su `.env`; en local hace falta un opt-in
  explícito y, con `JEV_LEDGER`, cada llamada queda en el libro (R9, FR-031).
- Q: ¿Cómo se reparten las 120 llamadas? → A: 1 de humo, 20 del gatekeeper (con Score en la
  misma petición), 16 de la compuerta, hasta 75 del v3 con Jev (cuotas de la corrida: 45
  del gatekeeper y 30 de la compuerta; si se agota una, los turnos restantes siguen sin esa
  llamada y se declaran), hasta 6 de la demostración en vivo opcional y 2 de la verificación
  en producción (un turno real tras el despliegue). Jev está apagado por defecto en todo
  comando local, así que ninguna otra llamada queda fuera del libro (R11).
- Q: ¿Cómo se entrega? → A: encima de la rama de la 045; por pedido del owner de juntar PRs,
  puede ir en la misma PR que la 045 o, si la 045 ya se mergeó, en una PR propia rebasada
  sobre `main` (R14).

## Assumptions

- La 045 queda como está en su rama (commit `88ee0d6` o posterior; hoy `314f9a7`) y se
  rebasa sobre `main` antes de implementar la 046: registro `TOOLS` con 20 `ToolSpec`,
  `evidence_role`,
  `agent/memory/sensitive.py` (`redact`, `find_sensitive`), `agent/memory/store.py`
  (`classify`, `MemoryProfile`) y el canal `memory` del estado.
- La clave de TypeSafe está disponible para la medición y para producción; el owner autorizó
  configurarla en el `.env` del servidor. Producción ya sirve `mode=tools`.
- La API de Jev mantiene el contrato probado (`openapi.json` del 2026-11-24): Choice devuelve
  `choice`, `confidence` y `probabilities`; Noul devuelve `noul`; ambos con `usage`.
- Precio de lista: USD 0,042 por millón de tokens de entrada; salida gratis. Con unos 2 300
  tokens por llamada de la compuerta y menos de 500 por la del gatekeeper, el costo por turno
  es del orden de USD 0,0001.
- Las latencias de la prueba (p95 284–349 ms) se midieron desde la máquina de desarrollo;
  desde el servidor de producción pueden variar.
- Fuera de alcance: rerank del retrieval con Jev (la prueba lo descartó), uso de Jev en el
  modo `routes`, Score en producción, Box y retención cero de datos (solo para clientes
  enterprise). La revisión del DPA de TypeSafe y de su plazo de retención no queda fuera de
  alcance: es una tarea previa a activar Jev en producción (T063); si no se completa, la PR
  pide al owner la aceptación explícita del riesgo declarado (research R8).
