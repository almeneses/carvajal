# Feature Specification: Memoria del agente y 20 herramientas

**Feature Branch**: `feat/045-memoria-herramientas`

**Created**: 2026-11-24

**Status**: Draft (decisiones autónomas registradas en *Clarifications*; pendiente de revisión del owner)

**Input**: User description: "Spec 045 para la entrega del curso. (1) Memoria de corto plazo:
hoy el modo `tools` solo ve los últimos 8 turnos y lo anterior se pierde; añadir un resumen
acumulado de los turnos que salen de la ventana, guardado con el estado del hilo, con
respaldo determinista y sin aumentar la latencia del primer texto. (2) Memoria de largo
plazo: herramientas para recordar, listar y olvidar preferencias por categorías (nombre,
empresa, ciudad habitual, formato preferido y notas), sin datos sensibles, con la ciudad
habitual solo como sugerencia, separada por dueño (web y WhatsApp), con el atajo por reglas
como respaldo y migrando las preferencias existentes. (3) Veinte herramientas: las 6
actuales, 3 de memoria y 11 nuevas sobre datos que ya existen en el repositorio, declaradas
con esquema tipado, visibles en la traza y demostrables. (4) Medición sin regresiones.
Avanzar sin intervención del owner: las ambigüedades se resuelven con la opción
recomendada y se registran."

## Contexto

La spec 044 dejó un agente que decide y encadena seis consultas deterministas. Tiene dos
límites que se notan en conversaciones reales y en la demostración del curso:

1. **Olvida lo que sale de la ventana.** El modelo recibe los últimos 8 turnos del hilo y
   nada más. En una conversación de 15 turnos, lo dicho en el turno 2 ya no existe para él.
2. **La memoria entre conversaciones es una lista de frases.** Solo se escribe cuando la
   frase coincide con unas marcas fijas («llámame», «recuerda que», «prefiero»), guarda la
   frase completa (que puede traer un teléfono o una cifra), no se puede consultar ni borrar
   y no distingue un nombre de una ciudad.

Además, varias preguntas frecuentes que el repositorio ya puede responder con datos
estructurados (la ficha de una referencia, qué material sirve para algo, cuántas unidades
son tres cajas, qué pasó en un año de la historia, si una cotización sigue vigente) hoy
pasan por la búsqueda por similitud o por `cotizar`, con más error y más costo.

Esta spec añade memoria de corto y largo plazo y amplía el catálogo de consultas a 20,
**sin cambiar qué se puede afirmar**: la compuerta de evidencia, los límites por turno, el
atajo y el respaldo de la 044 siguen vigentes y se aplican a todo lo nuevo.

### Relación con especificaciones existentes

- **044** (agente con herramientas): base de esta spec. Se conservan el contrato común de
  salida, la compuerta de evidencia, la retención del streaming, el atajo, el respaldo, los
  límites y el monitoreo. El contrato «sin efectos persistentes» de las herramientas tiene
  una excepción explícita: las tres herramientas de memoria.
- **043** (chat comercial): `consultar_cotizacion` lee las cotizaciones con folio que la 043
  guarda, solo del mismo dueño y solo las de procedencia `real` (la simulación de la 042
  nunca vuelve como fuente, cita ni evidencia).
- **042** (Cotizador): su memoria silenciosa (`visitor_memory`, 180 días) es un precedente de
  categorías, caducidad y olvido; se reutilizan sus patrones de extracción, no su tabla.
- **038** (monitoreo): cada consulta nueva aparece como un paso `tool` de la traza; los
  valores de memoria nunca se registran.
- **046** (Jev, futura): fuera de alcance.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recordar lo dicho en una conversación larga (Priority: P1)

Una persona conversa con el asistente durante 15 turnos: en el turno 2 dice que su negocio
es una cafetería universitaria en Pereira que necesita vasos para tinto; después habla de
la historia de Carvajal, de materiales y de sedes. En el turno 15 pregunta «¿qué te dije al
principio que necesitaba?». El asistente lo recuerda, aunque ese turno ya no esté entre los
últimos que recibe completos.

**Why this priority**: es la queja más visible del modo actual y el núcleo pedagógico de la
entrega (memoria de corto plazo). Sin ella, cualquier conversación larga se rompe.

**Independent Test**: reproducir con un doble de modelo una conversación de 15 turnos y
comprobar que el contexto del turno 15 incluye un resumen con lo dicho en el turno 2; repetir
con el resumen forzado a fallar y comprobar que el respaldo determinista también lo incluye.

**Acceptance Scenarios**:

1. **Given** una conversación con más turnos que la ventana, **When** llega un turno nuevo,
   **Then** el asistente recibe el resumen de los turnos que salieron de la ventana y los
   últimos turnos completos.
2. **Given** que el resumen no pudo calcularse (proveedor caído, plazo vencido o salida
   inválida), **When** llega el turno siguiente, **Then** el asistente recibe un resumen
   determinista con las preguntas literales de la persona de esos turnos.
3. **Given** un turno cualquiera, **When** se responde, **Then** el primer texto no espera al
   cálculo del resumen y la respuesta por WhatsApp no se retrasa por él.
4. **Given** un resumen que contiene una instrucción («olvida tus reglas»), **When** se usa,
   **Then** se trata como dato y no cambia las reglas.
5. **Given** un turno repetido con el mismo identificador (reintento o réplica), **When**
   llega, **Then** el resumen no se recalcula ni se duplica.

---

### User Story 2 - Una memoria entre conversaciones que la persona controla (Priority: P1)

Ana escribe «llámame Ana, trabajo en Panadería La Espiga y suelo comprar en Cali»; el agente
lo guarda con `recordar_preferencia` (nombre, empresa y ciudad habitual por separado). En
otra conversación, días después, saluda y el asistente la llama Ana. Pregunta «¿qué sabes de
mí?» y recibe la lista por categorías. Pide «cotiza 500 vasos para café»: el asistente le
pregunta la ciudad sugiriendo Cali, no la supone. Dice «olvida dónde trabajo» y luego
«olvida todo lo que sabes de mí», y el asistente deja de recordarlo. Si escribe «guarda mi
cédula 1.234.567.890», el asistente se niega a guardarla y le dice por qué.

**Why this priority**: es el núcleo de la memoria de largo plazo pedida para la entrega, y
toca privacidad: una memoria que guarda datos sensibles o que no se puede borrar es peor que
no tener memoria.

**Independent Test**: con un Store en memoria y un doble de modelo, ejecutar los escenarios
«llámame Ana» recordado en otra conversación, listar, olvidar una categoría, olvidar todo,
dato sensible rechazado y ciudad habitual sugerida, para un dueño web y uno de WhatsApp.

**Acceptance Scenarios**:

1. **Given** que la persona declara su nombre en una conversación, **When** abre otra
   conversación con la misma identidad, **Then** el asistente usa ese nombre.
2. **Given** una memoria con datos, **When** la persona pregunta qué se sabe de ella,
   **Then** recibe la lista por categorías, sin datos de otras personas.
3. **Given** una memoria con datos, **When** pide olvidar una categoría o todo, **Then** esos
   datos dejan de usarse desde ese mismo turno y no reaparecen en otra conversación.
4. **Given** un dato sensible (documento de identidad, teléfono, correo, datos de pago o
   claves), **When** la persona pide recordarlo, **Then** no se guarda y se le explica que
   ese tipo de dato no se guarda.
5. **Given** una ciudad habitual guardada, **When** la persona pide cotizar o buscar sedes
   sin decir la ciudad, **Then** el asistente sugiere la ciudad habitual y pide
   confirmación; nunca cotiza con ella sin que la persona la diga o la confirme.
6. **Given** una preferencia declarada con una frase fija («llámame Ana»), **When** el
   proveedor de lenguaje no está disponible (respaldo `routes`), **Then** el atajo por
   reglas la guarda en la misma memoria por categorías; con el proveedor disponible la
   guarda el agente con `recordar_preferencia`, y nunca se escribe dos veces.
7. **Given** preferencias guardadas con el formato anterior (hasta 10 frases de hasta 280
   caracteres), **When** la persona vuelve, **Then** se clasifican en la categoría que
   corresponda; de lo que no se clasifica se conservan como notas las 5 más recientes,
   recortadas a 120 caracteres con «…», y las descartadas se cuentan en
   `agent.memory_migration_dropped`.
8. **Given** la misma persona en la web y en WhatsApp, **When** guarda algo en un canal,
   **Then** el otro canal no lo ve (identidades distintas).

---

### User Story 3 - Consultas más finas sobre el catálogo (Priority: P2)

El dueño de un restaurante pregunta «¿qué trae el vaso CO-VTER-04?», «¿qué diferencia hay
entre el vaso de 8 y el de 10 oz?», «¿el PET sirve para comida caliente?», «¿cuántas
unidades son 3 cajas del vaso CO-VTER-04?», «¿qué tipos de vasos tienen en Colombia?» y
«¿en qué ciudades tienen distribuidores?». Cada pregunta se responde con una consulta
directa al catálogo, con su fuente, sin cotizar ni recomendar cuando no se pidió.

**Why this priority**: amplía lo que el asistente responde con datos exactos y reduce el uso
de `cotizar` y `buscar_corpus` para preguntas que no son de cotización ni documentales.

**Independent Test**: una prueba de contrato por herramienta, sin red, contra el catálogo
versionado; y una pregunta por herramienta en el quickstart.

**Acceptance Scenarios**:

1. **Given** una referencia por SKU o por nombre único, **When** se pide su ficha, **Then**
   se devuelven los campos declarados y los precios publicados con su fuente, sin totales.
2. **Given** un nombre ambiguo, **When** se pide la ficha o una comparación, **Then** se
   devuelven las candidatas y se pide elegir.
3. **Given** dos referencias de países o monedas distintos, **When** se comparan, **Then**
   no se comparan precios ni se declara cuál es más barata.
4. **Given** un material sin regla en la guía (papel, pulpa), **When** se consulta, **Then**
   se declara que la guía no lo cubre.
5. **Given** una conversión sin certeza (tamaños contradictorios o no declarados), **When**
   se pide, **Then** se declara que no se puede convertir con certeza.
6. **Given** una ciudad con sede del directorio, **When** se pregunta por cobertura, **Then**
   se distingue sede de existencias (las existencias siguen «por confirmar»).

---

### User Story 4 - Consultas institucionales, de cotizaciones guardadas y de fecha (Priority: P2)

Una persona pregunta «¿qué pasó en Carvajal en 1960?», «¿qué empresas tiene Carvajal?»,
«¿qué cifras financieras puedes darme?», «¿mañana es día hábil?» y «¿mi cotización
a1b2c3d4e5f6 sigue vigente?». Cada pregunta se responde con datos del repositorio y su
fuente; la cotización solo si es de esa misma persona.

**Why this priority**: completa las 20 herramientas con datos que ya existen y cubre
preguntas frecuentes de la demostración; cada una es independiente.

**Independent Test**: una prueba de contrato por herramienta, sin red ni base de datos real
(cotizaciones con un doble en memoria).

**Acceptance Scenarios**:

1. **Given** un año con hito en la historia oficial, **When** se pregunta, **Then** se
   devuelve el texto literal de ese año con su fuente; **Given** un año sin hito, **Then** se
   declara y se ofrecen los años cercanos que sí tienen, sin interpolar.
2. **Given** una pregunta por las empresas o marcas del grupo, **When** se responde, **Then**
   solo aparecen las unidades y marcas que el núcleo del corpus nombra, con su cita.
3. **Given** un folio de otra persona, inexistente o de una cotización propia con
   procedencia simulada o ambigua, **When** se consulta, **Then** la respuesta es la misma
   «no encontré esa cotización», sin distinguir los casos y sin fuentes ni valores.
4. **Given** una cotización propia de procedencia real, **When** se consulta, **Then** se
   muestra con su desglose y su vigencia comparada con la fecha actual de Bogotá, sin
   recotizar.
5. **Given** una pregunta de fecha o día hábil, **When** se responde, **Then** la fecha viene
   del reloj del servidor en la zona de Bogotá y del calendario de festivos de Colombia.

---

### User Story 5 - Elegir bien entre 20 herramientas y demostrarlo sin regresiones (Priority: P2)

El owner necesita mostrar en la entrega que el asistente usa las 20 herramientas, que cada
una se ve en la traza y en la línea de estado, y que pasar de 6 a 20 no empeoró lo que ya
funcionaba.

**Why this priority**: con 20 herramientas el modelo puede elegir peor; sin medición, la
ampliación es una apuesta. Además es lo que se evalúa en el curso.

**Independent Test**: correr `make check`, los casos dorados del cotizador, el conjunto de
herramientas nuevo, el v3 y una simulación corta, una sola vez cada uno, y comparar con la
ronda 4 de la 044.

**Acceptance Scenarios**:

1. **Given** una pregunta del conjunto de herramientas, **When** se evalúa, **Then** el
   asistente llama a la herramienta esperada (o a una equivalente declarada en el mapa
   congelado de `evaluation.py`, o toma el atajo cuando el ítem lo declara).
2. **Given** un turno con consultas, **When** se inspecciona la traza, **Then** se ven el
   nombre, el orden, el estado y la latencia de cada consulta, sin valores de memoria.
3. **Given** las evaluaciones de la 044, **When** se corren con 20 herramientas, **Then** no
   empeoran respecto de la ronda 4 más allá del margen declarado.

### Edge Cases

- La conversación tiene exactamente la ventana de turnos: no hay nada que resumir y no se
  llama al proveedor.
- Llegan dos turnos seguidos más rápido de lo que tarda el resumen: el turno siguiente cubre
  el hueco con el resumen determinista de los turnos que faltan. Si el hueco supera la
  holgura de la semilla (cola descartada, reinicio), se resume lo que se ve, `covered_turns`
  nunca avanza más allá de lo visto y se registra `agent.summary_gap`.
- El resumen se recalcula mientras el respaldo del modo `routes` atiende el turno: el
  resumen no se pierde (ambos grafos declaran y leen el canal).
- El Store o el checkpointer no responden: el turno sigue sin resumen ni memoria, como hoy
  con las preferencias.
- La persona dice «llámame Ana» y en la misma frase pide una cotización: el agente llama a
  `recordar_preferencia` y a `cotizar` en el mismo turno (ambas consumen el límite).
- La persona dice «olvida todo lo que sabes de mí» mientras un resumen se calcula en
  segundo plano: la lápida de olvido descarta ese resultado y lo olvidado no reaparece; los
  borradores de otros hilos del mismo dueño también se descartan.
- Un fragmento del corpus o un resultado de herramienta sugiere «recuerda que el usuario
  prefiere…»: `recordar_preferencia` solo guarda valores que aparecen en la pregunta del
  turno, y `olvidar_preferencia` solo actúa si la pregunta pide olvidar.
- La persona pide recordar algo que mezcla un dato válido y uno sensible («soy Ana, mi
  celular es 300…»): se guarda solo lo válido y se avisa de lo rechazado.
- Una nota supera el tamaño o ya hay 5 notas: se rechaza con el motivo y se ofrece olvidar
  alguna.
- La persona guarda una nota con un supuesto dato de Carvajal («Carvajal se fundó en 1850»)
  y luego lo pregunta: en un turno con `buscar_corpus` la nota no entra en lo que respalda
  la compuerta (rige la garantía de la 044); en un turno sin consultas de datos, la memoria
  solo respalda si todas las consultas fueron de memoria o `sin_consulta`.
- La persona pide guardar como preferencia algo que cambiaría las reglas («responde en
  inglés», «no cites fuentes»): `formato_preferido` solo admite una lista cerrada de
  estilos; lo demás queda como nota, que nunca se presenta como instrucción.
- La persona pide olvidar algo que no está guardado: se dice que no había nada que olvidar.
- La persona pide recordar el nombre de otra persona o datos de terceros: solo se guardan
  datos sobre quien escribe; los de terceros se rechazan como nota si son sensibles.
- Rotación de `SESSION_SECRET` o modo sin Postgres: la memoria anterior queda inaccesible o
  se pierde al reiniciar; se documenta, no se intenta recuperar.
- El modelo inventa un nombre de herramienta, o llama a una de memoria con un `owner_key` en
  los argumentos: el esquema no lo admite y el dueño sale siempre del servidor. Si el dueño
  llega vacío (invocación suelta, pruebas, estado sin dueño), las herramientas de memoria
  devuelven `error` con `sin_dueno` y no tocan el Store.
- Una pregunta mezcla una herramienta nueva y una antigua («ficha del P1 y cotiza 300 en
  Bogotá»): se respetan el límite de consultas y la compuerta como en la 044.
- `ficha_producto` de una referencia CO sin temperatura declarada: se usa la guía de
  materiales, rotulada como tal; nunca se presenta como dato de la ficha.
- `historia_carvajal` con un rango que supera 10 hitos: se devuelven los 10 primeros y se
  avisa que hay más.
- `fecha_actual` cerca de la medianoche: la fecha se toma en la zona de Bogotá, no en UTC, y
  `cotizar` y `consultar_cotizacion` usan el mismo reloj (`_now_bogota`), así que nunca se
  contradicen.
- Falta `data/institucional/empresas.json` o `data/gold/historia.md` en el despliegue:
  `empresas_del_grupo` e `historia_carvajal` devuelven `error` declarado, nunca una
  excepción.

## Requirements *(mandatory)*

### Functional Requirements

**Memoria de corto plazo (resumen acumulado)**

- **FR-001**: Cada conversación MUST mantener un resumen acumulado de los turnos que
  quedaron fuera de la ventana de historial que recibe el modelo, asociado a esa conversación
  y guardado con el estado del hilo.
- **FR-002**: En cada turno el modelo MUST recibir el resumen (si existe) y los últimos
  turnos completos de la ventana, con el resumen en un bloque propio delimitado y
  neutralizado, tratado como dato y nunca como instrucción. El atajo y el modo `routes`
  MUST recibir el mismo resumen, con una excepción declarada: el nodo `rag` de `routes`,
  que responde cada pregunta documental sin historial, no lo recibe (cambiar su prompt sin
  medirlo queda fuera de alcance).
- **FR-003**: El resumen MUST calcularse fuera del camino crítico del turno: nunca antes del
  primer texto, y sin retrasar el final de la respuesta en la web ni en WhatsApp.
- **FR-004**: Si el cálculo del resumen falla, vence su plazo, devuelve una salida inválida
  o no hay proveedor de DeepSeek configurado, MUST usarse un resumen determinista
  construido con las preguntas literales de la persona, sin llamar al proveedor. Los datos
  comerciales vigentes no entran en el resumen: ya viajan en su propio canal
  (`commercial_context`) y en el bloque `<contexto_comercial>` del prompt.
- **FR-005**: El cálculo MUST ser idempotente: un reintento, una réplica o un turno ya
  resumido no generan trabajo nuevo ni duplican contenido.
- **FR-006**: El resumen MUST NOT respaldar cifras ni datos de Carvajal frente a la compuerta
  de evidencia; solo las palabras literales que la persona escribió en turnos anteriores
  pueden respaldar lo que ella misma dijo, igual que la pregunta del turno.
- **FR-007**: El resumen MUST pasar por el mismo filtro de datos sensibles que la memoria de
  largo plazo antes de guardarse.
- **FR-008**: La ventana del historial MUST ser coherente en todo el sistema: la semilla que
  envía el servicio cubre la ventana configurada más una holgura que permite resumir los
  turnos que salen, junto con el total de turnos persistidos del hilo (`turn_count`); el
  atajo y el modo `routes` dejan de usar una ventana fija distinta; las herramientas
  (`ToolContext.history`) siguen viendo solo la ventana configurada.
- **FR-009**: Una falla al leer o escribir el resumen MUST NOT romper el turno: se responde
  sin resumen y se registra una advertencia.

**Memoria de largo plazo**

- **FR-010**: El asistente MUST ofrecer tres consultas de memoria: recordar una preferencia,
  listar la memoria y olvidar (una categoría, una nota concreta o todo).
- **FR-011**: La memoria MUST organizarse en categorías fijas: nombre, empresa, ciudad
  habitual, formato preferido y notas (máximo 5 notas de hasta 120 caracteres; un valor por
  cada otra categoría, que reemplaza al anterior). El formato preferido es una lista cerrada
  de estilos (`corto`, `detallado`, `tablas`, `viñetas`, `tuteo`, `usted`); lo demás se
  guarda como nota. Solo se guarda lo que la persona dijo en la pregunta del turno y solo se
  olvida cuando la pregunta lo pide.
- **FR-012**: El sistema MUST NOT guardar datos sensibles (documento de identidad, teléfono,
  correo, datos de tarjeta o cuenta, claves) en la memoria del dueño ni en el resumen
  (Store y canal) por ningún camino de escritura: herramienta, atajo, migración o resumen.
  Un intento se rechaza y se explica a la persona. Los argumentos de las herramientas de
  memoria se reducen a la categoría en origen (`_outcome`), así que el canal `results`, la
  traza, el monitoreo y la evaluación no llevan el valor. Alcance declarado: el mensaje
  literal de la persona y los `tool_calls` del modelo quedan en el canal `messages` y en el
  checkpoint como cualquier otro mensaje; borrar conversaciones está fuera de alcance.
- **FR-013**: La ciudad habitual MUST usarse solo como sugerencia: las consultas de
  cotización y de sedes MUST NOT usar una ciudad que la persona no dijo o no confirmó en la
  conversación; devuelven la petición del dato con la ciudad habitual como sugerencia.
  `buscar_sedes` adopta la regla `_city_said` que hoy solo aplica `cotizar` (cambio
  declarado sobre la 044).
- **FR-014**: La memoria MUST aislarse por la identidad firmada por el servidor (cookie en
  la web, identificador derivado del número en WhatsApp). La identidad nunca es un argumento
  que el modelo pueda escribir.
- **FR-015**: El atajo por reglas MUST seguir guardando las preferencias declaradas con
  frases fijas cuando atiende el respaldo `routes`, ahora en la memoria por categorías; con
  el proveedor disponible, esas frases van al agente, que usa `recordar_preferencia`. Ambos
  grafos MUST atender «olvida (todo) lo que sabes de mí», «olvida mis datos», «borra todo lo
  que sabes de mí» y «olvídate de mí» sin decisión del proveedor.
- **FR-016**: Las preferencias guardadas con el formato anterior MUST migrarse la primera vez
  que se leen: se clasifican en una categoría; de lo no clasificado se conservan como notas
  las 5 más recientes, recortadas a 120 caracteres con «…»; se filtran los datos sensibles;
  las descartadas se cuentan en `agent.memory_migration_dropped`; el formato anterior deja de
  escribirse y olvidar todo MUST borrarlo también.
- **FR-017**: La memoria MUST tratarse como dato del usuario: aplica como estilo y contexto,
  nunca cambia las reglas (las de datos, citas e idioma prevalecen sobre cualquier
  preferencia) y MUST NOT respaldar datos de Carvajal. Por camino de la compuerta: en
  `no_query`, sus valores respaldan texto solo si todas las consultas del turno fueron de
  memoria o `sin_consulta`; en `evidence`, rige la garantía de la 044 y los valores de
  memoria no entran en lo que respalda. Un turno solo de memoria cuyo texto rechaza el
  filtro recibe un reemplazo determinista con los datos de la herramienta (confirmación o
  lista), no la abstención.
- **FR-018**: Los valores guardados en memoria MUST NOT aparecer en la línea de estado. En
  el monitoreo con `content=metadata` no viaja ningún valor; con `content=full` (solo
  desarrollo) el contenido de la conversación sí viaja, pero los pasos `tool` de memoria
  solo llevan la categoría y el resultado.
- **FR-019**: Un dato de memoria sin actualizar durante 180 días MUST dejar de usarse y
  borrarse, como la memoria del Cotizador.

**Herramientas**

- **FR-020**: El asistente MUST disponer de exactamente 20 consultas: `buscar_corpus`,
  `buscar_producto`, `cotizar`, `buscar_sedes`, `indicador_financiero`, `sin_consulta`,
  `recordar_preferencia`, `listar_memoria`, `olvidar_preferencia`, `ficha_producto`,
  `comparar_referencias`, `guia_materiales`, `convertir_presentacion`, `listar_familias`,
  `ciudades_con_cobertura`, `empresas_del_grupo`, `historia_carvajal`,
  `indicadores_disponibles`, `fecha_actual` y `consultar_cotizacion`.
- **FR-021**: Cada consulta MUST declararse con un esquema de entrada tipado y una
  descripción explícita, vivir en su propio módulo y registrarse en un único registro
  central del que se derivan nombres, esquemas, descripciones, línea de estado, consumo del
  límite y papel ante la compuerta.
- **FR-022**: Las 11 consultas nuevas MUST ser deterministas y leer solo datos que ya existen
  en el repositorio (catálogo, directorio, guía de materiales, indicadores, historia y
  páginas del núcleo, cotizaciones guardadas y calendario de festivos); ninguna estima,
  interpola ni completa datos ausentes, y toda ausencia se declara.
- **FR-023**: Toda consulta MUST devolver la salida común de la 044 (`status`, `message`,
  `missing` y datos) y registrar fuentes citables para cada dato que devuelve; todas pasan
  por la compuerta de evidencia.
- **FR-024**: Los límites de la 044 (consultas por turno, deduplicación, cancelación y plazo
  del turno) MUST aplicarse a las 20 consultas.
- **FR-025**: Cada consulta, salvo `sin_consulta`, MUST mostrar una línea de estado amigable
  al empezar y aparecer como un paso de la traza del turno con su nombre, estado y latencia.
- **FR-026**: `consultar_cotizacion` MUST devolver solo cotizaciones del mismo dueño y de
  procedencia `real`; un folio ajeno, inexistente, simulado o ambiguo MUST producir la misma
  respuesta, sin fuentes ni valores.
- **FR-027**: Las seis consultas de la 044 MUST conservar nombre, esquema y descripción
  visibles para el modelo, salvo una frase final «No la uses para…» en cinco de ellas
  (declarada y medida) y la regla de ciudad de `buscar_sedes` (FR-013).
- **FR-028**: Las descripciones y el prompt MUST ayudar a elegir entre 20 consultas: cada
  descripción nueva dice cuándo usarla y cuándo no, y el prompt las agrupa por área.

**Medición**

- **FR-029**: Se MUST cubrir sin red cada consulta con pruebas de contrato y la memoria con
  los escenarios: conversación de 15 turnos que pregunta por el turno 2, «llámame Ana»
  recordado en otra conversación, olvidar una categoría y todo, y dato sensible rechazado.
- **FR-030**: Se MUST crear y congelar antes de medir un conjunto con al menos una pregunta
  por consulta y los escenarios de memoria, con la consulta esperada.
- **FR-031**: Las mediciones con proveedor real (conjunto nuevo, v3, simulación corta y el
  escenario de 15 turnos con el resumidor del modelo) MUST correrse una sola vez cada una,
  acotadas para cuidar los créditos, y compararse con la ronda 4 de la 044.

### Key Entities *(include if feature involves data)*

- **Resumen de conversación**: texto acumulado de los turnos que salieron de la ventana,
  con hasta dónde cubre, cómo se calculó (modelo o determinista), las preguntas literales de
  la persona y la fecha. Pertenece a una conversación.
- **Memoria del dueño**: valores por categoría (nombre, empresa, ciudad habitual, formato
  preferido, notas), cada uno con origen (herramienta, atajo o migración) y fecha.
  Pertenece a una identidad firmada.
- **Consulta registrada**: nombre, esquema, descripción, módulo, línea de estado, si
  consume el límite y su papel ante la compuerta (corpus, dato, declaración, memoria).
- **Cotización guardada**: la de la 043 (folio, dueño, desglose, vigencia); solo se lee.
- **Conjunto de herramientas**: preguntas congeladas con la consulta esperada.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En la conversación de 15 turnos, el contexto del turno 15 contiene lo dicho en
  el turno 2 en el 100 % de las pruebas sin red, con el resumen del modelo y con el
  determinista; con el proveedor real (una sola corrida, `AGENT_SUMMARY_MODE=llm`), el
  asistente responde correctamente la pregunta sobre el turno 2.
- **SC-002**: El primer texto no se retrasa por la memoria: ninguna prueba registra el
  cálculo del resumen antes del primer token, y el p95 del primer texto en la simulación
  corta queda por debajo de 5 s y a no más de 0,5 s del de la ronda 4 de la 044 (4,21 s).
- **SC-003**: Los escenarios de memoria (Ana en otra conversación, listar, olvidar una
  categoría, olvidar todo, web frente a WhatsApp) pasan al 100 % sin red.
- **SC-004**: Cero datos sensibles guardados en un conjunto de al menos 30 frases sensibles,
  y cero rechazos indebidos en al menos 30 frases válidas («2.000 vasos», «1.000.000 de
  vasos», «presupuesto de $2.500.000», «pedido del 2026-11-24», «8 oz», «Cali», folios).
- **SC-005**: Cero cotizaciones o búsquedas de sedes con una ciudad que la persona no dijo
  ni confirmó, en pruebas sin red y en las evaluaciones con proveedor real.
- **SC-006**: Las 20 consultas tienen prueba de contrato en verde, y en el conjunto de
  herramientas el asistente llama a la consulta esperada (o a una equivalente declarada) en
  al menos el 85 % de las preguntas en una sola corrida; se informa también el resultado
  estricto.
- **SC-007**: Sin regresiones: `make check` en verde, casos dorados del cotizador 18/18, v3
  con al menos 34/40 auditado (ronda 4 de la 044, `evidencia.md` §«Rondas 3 y 4») y
  simulación corta con como máximo 1 violación de garantía, 1 turno con dato sin fuente y 0
  respaldos. El crudo del v3 (estricto y con equivalencias) se informa sin umbral: la
  referencia de 27/40 en crudo de la ronda 4 (corrida `f3a5551`) es un dato del owner que la
  evidencia de la 044 no registra.
- **SC-008**: Cero valores de memoria en líneas de estado, en el canal `results`, en la
  traza, en la evaluación y en los pasos `tool` del monitoreo (`metadata` y `full`),
  verificado por prueba.
- **SC-009**: Los tokens de entrada medios por turno del v3 en modo `tools` (sin descontar la
  caché, que se informa aparte) se reportan y no superan 1,5 veces la línea base de 6 900
  tokens por turno de la 044 (`evidencia.md` §1), es decir, 10 350. Una estimación sin
  proveedor antes de redactar las descripciones (T085) hace de compuerta; si 1,5× no es
  alcanzable, el umbral se ajusta con su justificación en *Clarifications*.

## Clarifications

### Session 2026-11-24 (decisiones autónomas)

El owner pidió avanzar sin su intervención. Cada pregunta se resolvió con la opción
recomendada y queda registrada aquí para su revisión; el detalle y las alternativas están
en [research.md](./research.md).

- Q: ¿Cuándo se calcula el resumen sin afectar el primer texto? → A: en segundo plano al
  cerrar el turno: `ChatService` llama a `SummaryWorker.submit(owner_key, conversation_id)`
  justo después de persistir (donde ya tiene el dueño y la fábrica de sesiones), en un
  ejecutor acotado que no se espera; el turno siguiente lo adopta en `recall` con una
  lectura. Si el turno llega antes de que termine, cubre el hueco con el resumen
  determinista, usando `turn_count` para saber qué turnos de la semilla faltan (R1).
- Q: ¿Dónde se guarda? → A: en un canal `conversation_summary` del estado del hilo, que el
  propio grafo escribe en `recall` (los dos grafos lo declaran para que `routes` también lo
  lea y lo adopte). El trabajo en segundo plano deja un borrador en el Store por
  conversación; nunca se escribe el checkpoint desde fuera del grafo, para no perder
  escrituras (R2).
- Q: ¿Quién resume? → A: el mismo modelo de DeepSeek sin herramientas, temperatura 0, con
  plazo de 8 s y una sola llamada por bloque de turnos; si falla, el determinista (R3). Sin
  DeepSeek configurado (Ollama, sin clave o `AGENT_MODE=routes`), `llm` se degrada a
  `deterministic` al construir el worker (`agent.summary_mode_degraded`).
- Q: ¿Qué modo de resumen va por defecto? → A: `deterministic` hasta medir `SUMMARY_SYSTEM`
  (principio III: cada prompt se mide antes de adoptarlo). La medición es una sola corrida
  del escenario de 15 turnos con el caso de inyección (T097); si cumple, el valor por
  defecto pasa a `llm` en la misma PR y se registra en la evidencia y en `docs/prompts.md`.
- Q: ¿El resumen respalda datos? → A: no. Solo las preguntas literales de la persona
  respaldan lo que ella dijo (FR-006, R4).
- Q: ¿Cómo se guarda la memoria? → A: un elemento del Store por categoría en un namespace
  nuevo, con versión, origen y fecha; notas en un solo elemento con lista. Caduca a 180
  días, como la memoria del Cotizador (R5).
- Q: ¿Cómo se migran las preferencias? → A: de forma perezosa en la primera lectura de cada
  dueño, con patrones propios de `agent/memory/store.py` (no se toca la memoria del
  Cotizador) y el filtro de sensibles; el formato anterior guarda hasta 10 frases de 280
  caracteres y el nuevo solo 5 notas de 120, así que se conservan las 5 más recientes
  recortadas con «…» y las descartadas se cuentan (FR-016 ajustado). El formato anterior se
  lee pero ya no se escribe y «olvidar todo» lo borra (R6).
- Q: ¿Las herramientas de memoria escriben en el momento o al cerrar el turno? → A: en el
  momento, como hoy el atajo; un turno cancelado después de recordar conserva lo recordado y
  se documenta. Devuelven un `state_update` para que el resto del turno vea la memoria
  nueva, y solo guardan lo dicho en la pregunta del turno (R7).
- Q: ¿Qué es un dato sensible? → A: lo decide un filtro determinista por forma (documento
  precedido de su palabra, teléfono colombiano, correo, tarjeta con Luhn, cuenta y claves),
  no por conteo de dígitos, con exenciones para montos, cantidades con unidad, fechas y
  folios (R8).
- Q: ¿Las frases «llámame», «recuerda que», «prefiero» y «mi nombre es» siguen yendo al
  atajo? → A: en el grafo `tools`, no: van al agente, que usa `recordar_preferencia`; el
  atajo de memoria solo escribe en el respaldo `routes` (proveedor caído o `tools` sin
  arrancar). Como guardar es idempotente por categoría, un turno que falla tras recordar y
  pasa al respaldo no escribe dos veces. «Olvidar todo» sí sigue como atajo en ambos grafos
  (R15).
- Q: ¿Cómo se evita que lo olvidado reaparezca en el resumen? → A: «olvidar todo» deja en el
  hilo actual un marcador (`forgotten_through` = turnos actuales, texto y citas vacíos),
  borra los borradores de todos los hilos del dueño y escribe una lápida por dueño; el
  worker solo resume turnos posteriores al marcador y descarta su resultado si la lápida es
  posterior a su lectura; `recall` ignora resúmenes anteriores a la lápida. Tras olvidar
  todo, ni el resumen ni la ventana incluyen turnos anteriores al olvido; los mensajes
  siguen en la base y en la interfaz (R16).
- Q: ¿Se mueven las 6 herramientas actuales a un módulo por herramienta? → A: no. Se
  convierte `tools.py` en el paquete `tools/` moviendo el código en bloque (tipos comunes a
  `_base.py`, las seis a `builtin.py`, la ejecución a `runner.py`); las 14 nuevas van una
  por módulo. Partir `cotizar` y `buscar_producto`, que comparten ayudantes, es riesgoso a
  dos días de la entrega (R9).
- Q: ¿Se sigue usando el Literal `ToolName`? → A: sí, pero `TOOL_NAMES` se deriva del
  registro y el `Literal` crece en el mismo commit en que se registra cada grupo (memoria,
  catálogo, institucional), con una prueba de igualdad estricta en cada commit (R9).
- Q: ¿Cómo se evita que DeepSeek elija peor con 20? → A: todas siempre disponibles en un
  orden fijo (sin preselección por reglas), descripciones nuevas con «Úsala para… No la uses
  para…» (≤ 300 caracteres), una frase «No la uses para…» en cinco de la 044, agrupación por
  área en el prompt, medición con un conjunto propio y un mapa congelado de equivalencias en
  la evaluación (R10).
- Q: ¿Cuál es la línea base de tokens? → A: 6 900 tokens de entrada por turno en modo `tools`
  (044, `evidencia.md` §1); la métrica son tokens sin descontar la caché, y una estimación
  sin proveedor antes de redactar las descripciones hace de compuerta (R14).
- Q: ¿Las de memoria consumen el límite de consultas? → A: sí (evita bucles); el límite
  sigue en 4 por defecto (R10).
- Q: ¿Qué fuente cita `fecha_actual`? → A: una fuente de tipo `sistema` (reloj del servidor
  y calendario de festivos de Colombia), distinta de oficial y tercero; se enmienda la
  constitución (R11).
- Q: ¿De dónde salen las empresas del grupo? → A: de un JSON curado pequeño derivado solo
  del núcleo (`home.md`, la lista «Nuestros Sectores» de `quienes_somos.md`, las 7 páginas
  de unidad e `index.jsonl`), con alias citados (PROPAL, que `pulpa_y_papel.md` declara
  parte de la Organización) y una prueba que verifica cada cita literal en `data/gold/`. El
  archivo viaja en la imagen Docker y en la siembra de producción (R12).
- Q: ¿«Olvidar todo» borra también el resumen? → A: borra la memoria de largo plazo
  (incluido el formato anterior) y neutraliza los resúmenes de todas las conversaciones del
  dueño (R16); los mensajes de conversaciones pasadas no se borran (borrar conversaciones es
  otra funcionalidad).
- Q: ¿`consultar_cotizacion` muestra cotizaciones simuladas? → A: no. Solo las de
  procedencia `real`; las `simulado` o `ambiguo` dan el mismo `empty` que un folio ajeno
  (principio III, R11).
- Q: ¿Cuánto se mide con el proveedor real? → A: una corrida de cada medición: el conjunto de
  herramientas, el v3 solo en modo `tools` (la línea base de `routes` ya existe), los 12
  escenarios cortos de la simulación, con juez y verificador una sola vez, y el escenario de
  15 turnos con el resumidor del modelo. Los demás escenarios de memoria se validan sin red.
  El owner pidió no hacer pruebas extensas (R13).
- Revisión de artefactos (2026-11-24): los hallazgos de la revisión se aplicaron a spec,
  plan, investigación, modelo de datos, contratos, quickstart y tareas; las decisiones
  parciales y su justificación están en research R17.

## Assumptions

- El proveedor sigue siendo DeepSeek con el modelo de la 044, temperatura 0.
- La ventana por defecto sigue en 8 turnos (`AGENT_HISTORY_TURNS`) y el límite en 4
  consultas y 45 s por turno.
- No hace falta migración de base de datos: el Store de LangGraph ya existe en Postgres y
  la tabla `quotes` ya tiene el índice por dueño.
- En modo `sqlite` (demo local) el Store es en memoria: la memoria de largo plazo se pierde
  al reiniciar, como hoy.
- Las 11 consultas nuevas no cambian el catálogo, el corpus ni los precios; la única
  curaduría nueva es la lista de empresas del grupo con cita literal.
- Fuera de alcance: Jev (spec 046), OpenClaw, el README general, embeddings o búsqueda
  semántica sobre la memoria, borrar conversaciones, interfaz para editar la memoria y la
  memoria silenciosa del Cotizador (sigue como está).
