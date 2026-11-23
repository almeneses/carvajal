# Feature Specification: Agente con herramientas para el chat de Carvajal

**Feature Branch**: `feat/agente-tool-calling`

**Created**: 2026-11-23

**Status**: Implemented (2026-11-23), detrás de `AGENT_MODE=tools`; valor por defecto pendiente de decisión del owner ([evidencia](./evidencia.md))

**Input**: User description: "Agente con tool calling para el chat de Carvajal (spec 044).
Sustituir el enrutador determinista por regex + una sola llamada al LLM por un agente
LangGraph donde DeepSeek (deepseek-chat) decide qué herramientas llamar y en qué orden:
buscar_corpus, buscar_producto, cotizar, buscar_sedes e indicador_financiero. Las reglas
duras se quedan en las herramientas, que son deterministas. El enrutador por reglas actual
se conserva solo como atajo para saludos y preferencias declaradas y como respaldo si el
proveedor falla. Se conservan el streaming token a token, la persistencia transaccional
idempotente, el folio de cotización, la memoria de preferencias, las citas y el monitoreo.
El historial debe pasar al modelo como mensajes de conversación. Criterio de éxito: igual o
mejor que la línea base y mejor generalización en un conjunto nuevo de preguntas."

## Contexto

Hoy cada turno del chat lo clasifica un enrutador por reglas en una sola ruta (`direct`,
`rag`, `tool` o `commercial`); el modelo de lenguaje solo redacta con lo que esa ruta
preparó. El enrutador acierta el 100 % de su conjunto de evaluación porque sus reglas se
escribieron contra esas mismas preguntas, pero falla con formulaciones no previstas, no
puede combinar varias necesidades en un turno (salvo el caso especial «mixto») y no puede
volver a buscar cuando la primera evidencia no alcanza.

Esta especificación cambia **quién decide** qué consultar: el asistente elige y encadena
consultas al corpus, al catálogo, al cotizador, al directorio de sedes y a los indicadores
financieros. **No cambia qué se puede afirmar**: cada consulta conserva sus reglas
deterministas y la política de evidencia de la constitución (principio III).

### Relación con especificaciones existentes

- **036** (asistente de empaques): sus reglas de material, cálculo de cajas y canales se
  conservan dentro de las consultas; el enrutador intercambiable (FR-013 de la 036) pasa a
  ser el atajo y el respaldo de esta spec.
- **038** (monitoreo): cada turno sigue teniendo una traza raíz; ahora incluye las consultas
  que el asistente hizo.
- **043** (chat comercial unificado): el hilo único, el folio, la idempotencia y la
  persistencia en una sola transacción se conservan sin cambios de contrato.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolver una necesidad compuesta en un solo turno (Priority: P1)

Una persona que administra una cafetería en Barranquilla escribe «necesito unos 2 mil vasitos
para tinto, ¿cuánto me salen y dónde los compro?». El asistente identifica el producto, valida
el material para bebida caliente, cotiza la cantidad con los precios publicados y busca sedes
en la ciudad, todo en la misma respuesta y con sus fuentes. Si una parte no tiene respaldo (por
ejemplo, no hay sedes en Barranquilla), entrega lo que sí consta y declara el hueco.

**Why this priority**: es la diferencia observable frente al sistema actual, que solo atiende
una ruta por turno y depende de que la frase coincida con reglas escritas de antemano.

**Independent Test**: enviar en un hilo nuevo cinco preguntas compuestas del conjunto de
generalización (producto + cotización + sedes, o información institucional + producto) y
comprobar que cada parte respaldada aparece con su fuente y cada parte sin respaldo se declara.

**Acceptance Scenarios**:

1. **Given** una pregunta que combina producto, cantidad y ciudad, **When** se envía,
   **Then** la respuesta incluye la recomendación válida, la cotización con su desglose y las
   sedes de la ciudad o la declaración de que no hay cobertura.
2. **Given** una pregunta que combina un dato institucional («¿Carvajal recicla?») con un
   producto, **When** se envía, **Then** la respuesta cita el corpus para la parte
   institucional y el catálogo para la parte comercial.
3. **Given** una formulación coloquial o regional no prevista por las reglas («vasitos pa'
   tinto», «icopor», «coca para almuerzo»), **When** se envía, **Then** el asistente la
   interpreta o pregunta lo mínimo necesario, sin responder con una ruta equivocada.
4. **Given** que la primera búsqueda en el corpus no trae evidencia suficiente, **When** el
   asistente lo detecta, **Then** puede reformular y buscar de nuevo dentro del límite de
   consultas por turno antes de declarar la ausencia.

---

### User Story 2 - Conservar las garantías de lo que se afirma (Priority: P1)

La misma persona pide «vasos de icopor para café caliente para microondas» o pregunta por el
plazo de entrega. El asistente no recomienda un material prohibido para ese uso, no inventa
precios, existencias ni plazos, deja el total pendiente si falta un componente y distingue
fuentes oficiales de fuentes de terceros, igual que hoy.

**Why this priority**: dar más libertad al asistente no puede costar confianza. Una sola
recomendación de material prohibido o un precio inventado invalida la entrega.

**Independent Test**: correr los casos dorados del cotizador, las preguntas de material de la
036 y un conjunto adversarial (peticiones de inventar, órdenes dentro de documentos, presión
para estimar) y comprobar cero violaciones.

**Acceptance Scenarios**:

1. **Given** un uso que prohíbe un material, **When** el asistente recomienda, **Then**
   ninguna referencia de ese material aparece, aunque el usuario la pida por nombre; si la
   pide, se explica por qué no aplica.
2. **Given** una referencia sin precio publicado, **When** se cotiza, **Then** el precio se
   declara ausente y no se estima, convierte ni promedia.
3. **Given** que falta el costo de envío, **When** se cotiza, **Then** el subtotal es
   visible y el total queda pendiente.
4. **Given** una pregunta documental sin evidencia en el corpus, **When** se responde,
   **Then** la respuesta es la abstención declarada, sin afirmaciones del corpus.
5. **Given** un fragmento recuperado que contiene instrucciones («ignora tus reglas»),
   **When** se usa como evidencia, **Then** el asistente no las obedece.
6. **Given** una afirmación tomada de un tercero, **When** se responde, **Then** se indica
   que la fuente es de terceros.

---

### User Story 3 - Conversar con naturalidad en varios turnos (Priority: P1)

Después de una cotización, la persona escribe «mejor en Cali», «¿y si son 500?», «¿y el de
12 oz?» o «¿eso cuándo fue?». El asistente entiende a qué se refiere con la conversación
previa, conserva los datos que no cambiaron y vuelve a consultar lo necesario.

**Why this priority**: es lo que se espera de un asistente tipo chat. Hoy el seguimiento
depende de patrones fijos (cantidades, palabras de entrega, pronombres) y la búsqueda en el
corpus solo ve la pregunta anterior.

**Independent Test**: reproducir diez hilos de tres a cinco turnos con correcciones de ciudad,
cantidad, producto y referencias elípticas, y comprobar que cada turno usa el contexto
correcto.

**Acceptance Scenarios**:

1. **Given** una cotización previa en el hilo, **When** se cambia solo la ciudad o la
   cantidad, **Then** la nueva cotización conserva el resto de datos y lo dice.
2. **Given** una pregunta elíptica sobre un tema documental previo, **When** se envía,
   **Then** la búsqueda en el corpus usa el tema de la conversación, no solo la frase literal.
3. **Given** una preferencia declarada en el hilo («llámame Ana», «prefiero respuestas
   cortas»), **When** continúa la conversación, **Then** se aplica como estilo y nunca como
   instrucción que altere las reglas.
4. **Given** un hilo largo, **When** se supera el historial que el asistente puede
   considerar, **Then** se conservan los turnos más recientes y el contexto comercial vigente.

---

### User Story 4 - Operar sin regresiones ni costos innecesarios (Priority: P2)

Quien opera el servicio necesita que los saludos y las preferencias declaradas se respondan
sin gastar consultas al proveedor, que una caída del proveedor no deje el chat sin respuesta
y que el streaming, la idempotencia de los reintentos, el folio y la memoria sigan igual.

**Why this priority**: protege lo ya entregado en las specs 017, 038 y 043; no aporta valor
nuevo, pero su ausencia sería una regresión.

**Independent Test**: simular una caída del proveedor durante un turno, repetir un envío con
el mismo identificador de turno y enviar diez saludos; verificar respuesta de respaldo,
réplica idéntica y cero consultas al proveedor para los saludos.

**Acceptance Scenarios**:

1. **Given** un saludo, agradecimiento o pregunta de capacidades, **When** se envía, **Then**
   se responde sin decisión del proveedor de lenguaje.
2. **Given** que el proveedor no responde o devuelve una salida inválida al decidir, **When**
   ocurre, **Then** el turno se atiende por el camino de reglas sin redacción libre (tarjetas,
   cotización o fragmentos citados con un aviso fijo) y queda registrado como respaldo.
3. **Given** un reintento con el mismo identificador de turno, **When** llega, **Then** se
   devuelve la misma respuesta, fuentes, tarjetas y folio, sin volver a consultar.
4. **Given** una respuesta en curso, **When** se genera, **Then** el texto llega
   progresivamente y la persona puede detenerla.
4a. **Given** que el asistente está consultando antes de escribir, **When** empieza cada
   consulta, **Then** la persona ve una línea de estado amigable con el paso en curso
   («Estoy buscando en la información de Carvajal…», «Estoy calculando cuánto te salen
   2.000 vasos…»), que se reemplaza al llegar el texto y que el lector de pantalla anuncia.
5. **Given** un turno que excede el límite de consultas o de tiempo, **When** ocurre, **Then**
   el asistente responde con lo obtenido hasta ese momento y declara lo que faltó.

---

### User Story 5 - Medir si el cambio vale la pena (Priority: P2)

El owner necesita comparar el asistente nuevo con el actual con las mismas evaluaciones y con
un conjunto de preguntas que nadie usó para escribir reglas, antes de cambiar el
comportamiento por defecto.

**Why this priority**: sin esta medición, el cambio de arquitectura es una apuesta; con ella,
la decisión de activarlo es verificable.

**Independent Test**: correr las evaluaciones con el asistente actual y el nuevo sobre el
mismo corpus y catálogo, y obtener un informe con las métricas de ambos lado a lado.

**Acceptance Scenarios**:

1. **Given** las evaluaciones existentes (enrutamiento, cotizador, recuperación), **When** se
   corren con ambos asistentes, **Then** el informe muestra ambos resultados con las mismas
   métricas.
2. **Given** el conjunto de generalización, **When** se evalúa, **Then** se mide la tasa de
   turnos resueltos correctamente por cada asistente.
3. **Given** un turno en el monitoreo, **When** se inspecciona, **Then** se ven las consultas
   hechas, su orden, su resultado resumido, los tokens y la latencia.

### Edge Cases

- El asistente intenta encadenar consultas sin fin (bucle) o repite la misma consulta con los
  mismos datos.
- El asistente intenta responder un dato comercial o documental sin haber consultado.
- Una consulta devuelve error (índice caído, catálogo no cargado): el turno sigue con las
  demás partes y declara la que falló.
- Una bebida o comida se nombra sin decir la temperatura pero la implica («tinto»,
  «aromática», «sopa»): se deduce la temperatura; si no se puede deducir, se pregunta antes
  de cotizar. Nunca se cotiza un material de bebida fría para una bebida caliente (hallazgo
  del 2026-11-23: «2.000 vasos de 7 onzas para tinto, Bogotá» cotizaba un vaso de PP).
- El usuario pide explícitamente inventar, estimar o «suponer» un precio o plazo.
- Pregunta en otro idioma: se responde en ese idioma con las mismas reglas.
- Petición fuera de alcance (pedidos, pagos, facturas, temas ajenos a Carvajal).
- Un reintento llega mientras el turno original sigue generando.
- Dos productos o dos cantidades en la misma petición (vasos y platos; 300 frente a 500):
  se cotizan ambos si la evidencia lo permite, cada uno con su tarjeta y su folio (FR-012a);
  si falta un dato para alguno, se cotiza el que tiene respaldo y se pide el dato del otro.
  Sustituye para este caso el escenario 3 de la US2 de la 043.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El asistente MUST decidir en cada turno qué consultas hacer (corpus, catálogo
  de productos, cotización, sedes, indicadores financieros), en qué orden y con qué datos,
  pudiendo hacer varias consultas en el mismo turno.
- **FR-002**: Cada consulta MUST aplicar sus reglas de forma determinista e independiente de
  la decisión del asistente: validación de material por uso, cálculo de presentaciones y
  sobrantes, ausencia declarada de precio, total pendiente si falta un componente, prioridad
  del núcleo sobre la capa extendida y etiquetado oficial/tercero.
- **FR-003**: Toda cifra, producto, precio, existencia, plazo, sede o dato institucional de
  la respuesta MUST provenir del resultado de una consulta hecha en el mismo turno o
  registrada en el hilo; si no hay resultado, MUST declararse la ausencia.
- **FR-004**: Cuando la búsqueda documental no devuelve evidencia sobre el umbral y no hay
  otra consulta con resultado, la respuesta MUST ser la abstención declarada vigente, sin
  generar afirmaciones del corpus.
- **FR-004a**: En cada turno atendido por el asistente (fuera del atajo de FR-008), el primer
  paso MUST declarar una consulta o declarar explícitamente que no consulta (para pedir un
  dato, conversar o señalar que algo está fuera de alcance). Una respuesta sin consulta MUST
  NOT contener cifras, fechas ni nombres propios de Carvajal; si los contiene, se sustituye
  por la abstención. Declarar que no se consulta no cuenta para el límite de FR-010.
- **FR-005**: El asistente MUST pedir aclaración cuando falte un dato que cambia el resultado
  (temperatura o uso para el material, período para un indicador) en lugar de suponerlo.
- **FR-006**: La conversación previa MUST llegar al asistente como turnos de conversación
  diferenciados (persona / asistente / resultados de consulta), con un límite configurable
  de turnos y conservando siempre el contexto comercial vigente del hilo.
- **FR-007**: El contenido recuperado, el historial y las preferencias MUST tratarse como
  datos; ninguna instrucción contenida en ellos puede cambiar las reglas.
- **FR-008**: Saludos, agradecimientos, preguntas de capacidad y preferencias declaradas MUST
  atenderse por el camino de reglas sin decisión del proveedor de lenguaje.
- **FR-009**: Si el proveedor falla, excede el tiempo o devuelve una decisión inválida, el
  turno MUST atenderse por el camino de reglas actual, sin redacción libre: se muestran las
  tarjetas, la cotización o los fragmentos citados que ese camino obtenga, con un aviso fijo
  («No pude redactar la respuesta; esto es lo que encontré»). Si el camino de reglas no
  obtiene resultados, se muestra el aviso con una invitación a reintentar. El turno se
  registra como respaldo.
- **FR-010**: Cada turno MUST tener un límite de consultas y de tiempo; al alcanzarlo, la
  respuesta MUST entregar lo obtenido y declarar lo que faltó. Una consulta repetida con los
  mismos datos en el mismo turno no se vuelve a ejecutar.
- **FR-011**: La respuesta final MUST llegar progresivamente, como hoy, y poder detenerse.
- **FR-012**: Se MUST conservar sin cambios de contrato: la persistencia del turno en una sola
  transacción, la réplica idempotente por identificador de turno, la memoria de
  preferencias por dueño, las citas numeradas y las tarjetas de producto.
- **FR-012a**: Cada cotización que aparece en la respuesta final como tarjeta MUST recibir su
  propio folio; las cotizaciones calculadas durante el turno que no se muestran no se
  registran. Todas las cotizaciones mostradas se guardan en la misma transacción del turno y
  la réplica idempotente devuelve los mismos folios.
- **FR-013**: Las fuentes de todas las consultas del turno MUST conservarse con la respuesta
  almacenada y en la API, numeradas de forma consistente con las citas del texto.
- **FR-014**: El monitoreo MUST registrar por turno las consultas hechas, su orden, si
  tuvieron resultado, si se usó el camino de reglas o el respaldo, los tokens y la latencia,
  sin datos personales en producción.
- **FR-015**: El comportamiento MUST poder alternarse por configuración entre el asistente
  actual y el nuevo, para evaluar ambos sobre el mismo corpus y catálogo antes de cambiar el
  valor por defecto.
- **FR-016**: Se MUST crear y congelar, antes de implementar, un conjunto de generalización de
  al menos 50 preguntas (incluidas compuestas, coloquiales, de seguimiento y adversariales)
  con su resultado esperado. El asistente de desarrollo propone el borrador sin consultar
  las reglas del enrutador actual ni su conjunto de evaluación; el owner lo revisa, lo
  ajusta y lo congela. El informe de evaluación MUST declarar este origen como limitación
  (posible sesgo del modelo que redactó las preguntas).
- **FR-017**: Las evaluaciones existentes (enrutamiento, cotizador, recuperación) MUST poder
  correrse contra el asistente nuevo y producir un informe comparable con la línea base.
- **FR-018**: Mientras el asistente consulta, la interfaz MUST mostrar una línea de estado con
  el paso en curso, redactada en lenguaje cercano, en el tono del asistente y sin jerga
  técnica (nunca nombres internos de consultas). Puede incluir el dato principal que la
  persona aportó (producto, cantidad, ciudad), pero nunca resultados, precios ni fuentes.
  La línea se reemplaza al empezar el texto de la respuesta, se anuncia de forma accesible y
  no se guarda en el historial.

### Key Entities *(include if feature involves data)*

- **Consulta**: una acción que el asistente puede pedir (buscar en el corpus, buscar
  producto, cotizar, buscar sedes, consultar indicador). Tiene datos de entrada validados,
  un resultado estructurado y fuentes. La declaración de «no consulto» (FR-004a) se registra
  como una acción más, pero no aporta evidencia ni fuentes.
- **Resultado de consulta**: datos devueltos con su procedencia (documento, URL, fecha,
  oficial/tercero) o la declaración de ausencia; es la única base para afirmar.
- **Turno del agente**: pregunta, decisiones tomadas, consultas y resultados, respuesta
  final, fuentes, tarjetas y un folio por cada cotización mostrada; se persiste completo.
- **Contexto comercial del hilo**: producto, uso, temperatura, cantidad, ciudad y entrega
  vigentes, que sobreviven entre turnos y se actualizan con las correcciones.
- **Conjunto de generalización**: preguntas congeladas con resultado esperado, separadas del
  conjunto usado para construir las reglas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero recomendaciones de material prohibido y cero precios, existencias o plazos
  sin fuente en los conjuntos de evaluación existentes, el de generalización y el adversarial.
- **SC-002**: Los 12 casos dorados del cotizador siguen pasando y la recuperación no cae más
  de 0,03 en Recall@10 ni en nDCG@10 frente a la línea base.
- **SC-003**: En el conjunto de generalización, el asistente nuevo resuelve correctamente al
  menos 15 puntos porcentuales más de turnos que el actual, y al menos el 85 % en total.
- **SC-004**: Al menos el 90 % de las preguntas compuestas del conjunto de generalización
  reciben todas sus partes respaldadas en un solo turno.
- **SC-005**: Las preguntas sin evidencia (abstención esperada) se abstienen en el 100 % de
  los casos.
- **SC-006**: El 95 % de los turnos muestran el primer texto en menos de 5 segundos y terminan
  en menos de 20 segundos con el proveedor configurado.
- **SC-006a**: En el 95 % de los turnos con consultas, la línea de estado aparece en menos de
  1 segundo desde el envío.
- **SC-007**: Los saludos y preferencias declaradas consumen cero decisiones del proveedor, y
  una caída del proveedor produce una respuesta de respaldo en el 100 % de los casos probados.
- **SC-008**: El costo medio por turno se reporta en el informe de evaluación y no supera el
  triple del costo del asistente actual.

## Clarifications

### Session 2026-11-23

- Q: ¿Quién escribe el conjunto de generalización? → A: el asistente de desarrollo propone
  un borrador y el owner lo revisa, ajusta y congela; el sesgo se declara en el informe.
- Q: Cuando el proveedor no responde, ¿con qué se redacta la respuesta de respaldo? → A:
  sin redacción libre; camino de reglas con tarjetas, cotización o fragmentos citados y un
  aviso fijo (FR-009).
- Q: Si el agente cotiza varias veces en un turno, ¿cuántas cotizaciones con folio se
  registran? → A: una por cada cotización mostrada como tarjeta en la respuesta final; las
  intermedias no se registran; todas en la misma transacción (FR-012a).
- Q: Mientras el agente consulta, ¿la persona ve en qué paso va? → A: sí, una línea de estado
  con mensajes amigables que se reemplaza al empezar el texto (FR-018).

## Assumptions

- El proveedor de lenguaje es DeepSeek, decisión del owner del 2026-11-23; el respaldo local
  no necesita soportar consultas encadenadas.
- El límite por defecto es de 4 consultas y 45 segundos por turno, ajustable por
  configuración.
- La temperatura del proveedor sigue en 0, como exige la constitución.
- El conjunto de generalización se congela en `data/eval/` antes de la implementación y no
  se usa para ajustar reglas ni instrucciones; si se ajustan, se crea uno nuevo.
- El único cambio de interfaz de esta spec es la línea de estado de FR-018. Mostrar las
  fuentes en pantalla o conservar los pasos desplegables sobre la respuesta queda para una
  spec de interfaz posterior.
- El asistente actual sigue siendo el valor por defecto hasta que el owner apruebe el cambio
  con el informe de SC-001 a SC-008.
- La constitución hoy dice que en la ruta documental sin contexto «no se llama al LLM» y que
  el router es `direct`/`tool`/`rag`. Con esta spec el proveedor decide antes de buscar; la
  garantía equivalente es FR-004 (abstención sin afirmaciones). El plan debe proponer la
  enmienda en su *Constitution Check*.
- Fuera de alcance: pedidos, pagos, facturas, nuevas fuentes de datos y cambios al
  catálogo o al corpus.
