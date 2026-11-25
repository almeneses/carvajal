# Feature Specification: De la foto a la cotización

**Feature Branch**: `feat/053-foto-a-cotizacion`

**Created**: 2026-11-25

**Status**: Implemented (2026-11-25). Pendiente: evaluación del agente (T017, requiere permiso para gastar tokens).

**Input**: User description: «Quiero que en el chat web se vea la foto que se sube, al estilo
de WhatsApp. También quiero que entre las opciones solo muestre la más probable (o la mejor)
y máximo 2 alternativas y que se puedan elegir mediante voz o texto como "dame esa", "vamos
con alternativa 1", "vamos con la 2" o por ese estilo y se continúe con el flujo de
cotización (pregunte por la ciudad si es que no se ha definido antes, cantidad, confirmar
cotización, etc.)». Después: «Muestra las alternativas en un grid responsive de 2x2 en la
versión web, sube el máximo de alternativas a 4».

Decisiones del owner (2026-11-25):

- **La foto se ve** en el compositor y en el mensaje enviado. Para el historial se guarda en
  el servidor **una miniatura** (no la foto original, que sigue sin guardarse).
- **Confirmar en todas**: ninguna cotización, venga o no de una foto, se registra con folio
  sin que la persona la confirme.
- **Espera propia**: Carva con lupa y «Mirando tu foto…» en lugar de «Pensando…».
- **Que se descubra**: una sugerencia en la bienvenida («📷 Envía una foto de tu plato y te
  digo qué empaque usar»).
- **En computador**, arrastrar y pegar la foto, con las mismas validaciones de hoy.
- **Antes de construir**, un diseño en Stitch de la tarjeta de la foto adjunta y del estado
  «mirando», alineado con el rediseño del chat.
- Spec y PR nuevas; la 051 (#247) ya está en `main`.

## Contexto

La spec 051 dejó la foto funcionando de punta a punta: Jev clasifica, una tabla elige hasta
tres referencias y la respuesta queda guardada en el hilo. Pero en la web la foto nunca se
ve (la burbuja dice «📷 Foto de un alimento»), las tres sugerencias se listan sin jerarquía y
el paso a la cotización depende de que el modelo entienda «la segunda» leyendo el texto
anterior, sin ninguna regla que lo garantice. Además, cualquier cotización completa se
registra sola con folio, aunque la persona solo estuviera preguntando.

### Relación con especificaciones existentes

- **051** (fotos): se reutilizan la preparación, la clasificación y la tabla de
  recomendación. Cambia la presentación (recomendada + alternativas) y se guarda una
  miniatura; la foto original sigue sin guardarse.
- **042/044** (cotizador y agente con herramientas): la herramienta de cotizar y el motor no
  cambian su cálculo. Cambia el último paso: una cotización completa queda como
  **propuesta** y solo se registra (folio, fila, PDF) cuando la persona la confirma.
- **049/051-rediseño** (chat y tarjetas): la confirmación plegable que celebra el folio se
  mantiene, ahora al confirmar.
- **048/050** (voz): la elección y la confirmación por voz llegan como texto dictado; no se
  agrega reconocimiento nuevo.
- **WhatsApp** (#237, #244, #250): el PDF y el folio salen solo después de confirmar.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Elegir una opción por voz o texto y seguir a la cotización (Priority: P1)

Tras la recomendación de una foto, la persona dice o escribe «dame esa», «vamos con la
alternativa 1» o «la 2». El asistente entiende cuál eligió, sin adivinar, y sigue con la
cotización: pide la cantidad y la ciudad si no las sabe y presenta la propuesta para
confirmar.

**Why this priority**: es el puente entre «qué empaque uso» y «cuánto me cuesta»; sin él, la
recomendación no lleva a una venta.

**Independent Test**: con una recomendación guardada (recomendada + 4 alternativas), enviar
cada frase de un conjunto de prueba y comprobar que se resuelve a la referencia correcta y
que el siguiente paso es la pregunta por lo que falta o la propuesta.

**Acceptance Scenarios**:

1. **Given** una recomendación con «Recomendada: A» y alternativas 1–4, **When** la persona
   escribe «dame esa» o «la recomendada», **Then** se cotiza A.
2. **Given** la misma recomendación, **When** dice «vamos con la alternativa 1», «la 2» u
   «opción 3», **Then** se cotiza la alternativa con ese número.
3. **Given** que no se sabe la cantidad, **When** elige, **Then** el asistente pregunta la
   cantidad y recuerda la referencia elegida.
4. **Given** que la ciudad ya se dijo antes en la conversación, **When** elige y da la
   cantidad, **Then** no vuelve a preguntar la ciudad; **Given** que nunca se dijo,
   **Then** la pregunta.
5. **Given** cuatro alternativas, **When** dice «la 5», **Then** responde que hay cuatro
   alternativas y las nombra, sin cotizar nada.
6. **Given** que la elección y los datos llegan juntos («vamos con la 2, son 500 para
   Cali»), **Then** pasa directo a la propuesta.
7. **Given** una nota de voz con la elección (web o WhatsApp), **Then** el resultado es el
   mismo que por texto.
8. **Given** la web, **When** toca «Elegir» en una tarjeta, **Then** equivale a escribir la
   elección.

---

### User Story 2 - Confirmar antes de registrar cualquier cotización (Priority: P1)

Cuando el asistente tiene producto, cantidad y ciudad, presenta la cotización como
**propuesta** (sin folio) y pregunta si la confirma. Solo con un «sí» queda registrada con
folio, vigencia y PDF.

**Why this priority**: decisión del owner; evita folios y PDFs de consultas exploratorias y
da a la persona un momento para corregir.

**Independent Test**: completar una cotización y comprobar que no hay folio ni fila
registrada; confirmar y comprobar que ahora sí; corregir la cantidad en lugar de confirmar y
comprobar que sale una propuesta nueva sin folio.

**Acceptance Scenarios**:

1. **Given** una cotización completa, **Then** la respuesta muestra producto, presentación,
   cantidad, ciudad y totales marcados como propuesta, pregunta «¿La confirmo?» y no tiene
   folio.
2. **Given** una propuesta en el turno anterior, **When** la persona dice «sí», «confírmala»,
   «dale» o toca «Confirmar cotización», **Then** se registra con folio y se muestra la
   confirmación que ya existe («✓ Cotización guardada»).
3. **Given** una propuesta, **When** la persona cambia un dato («mejor 800»), **Then** sale
   una propuesta nueva y la anterior no se registra.
4. **Given** una propuesta, **When** la persona habla de otra cosa, **Then** no se registra
   nada; un «sí» posterior, si ya no responde a esa propuesta, no la registra.
5. **Given** WhatsApp, **Then** el folio y el PDF llegan solo después del «sí».
6. **Given** que alguien confirma dos veces (doble toque o reintento), **Then** hay un solo
   folio.

---

### User Story 3 - Recomendada y hasta cuatro alternativas (Priority: P1)

La respuesta a la foto destaca **una** referencia recomendada y debajo hasta **cuatro**
alternativas numeradas. En la web, las alternativas van en una cuadrícula de 2×2 que pasa a
una columna en pantallas angostas.

**Why this priority**: una sola recomendación clara decide más rápido; las alternativas
numeradas son las que la persona nombra al elegir (US1).

**Independent Test**: con un perfil que tenga al menos cinco candidatas aptas, comprobar una
recomendada y cuatro alternativas numeradas en el texto y en las tarjetas; con menos
candidatas, las que haya, sin huecos.

**Acceptance Scenarios**:

1. **Given** cinco o más candidatas aptas, **Then** se muestra 1 recomendada y 4
   alternativas numeradas del 1 al 4.
2. **Given** dos candidatas, **Then** 1 recomendada y 1 alternativa; **Given** una, solo la
   recomendada.
3. **Given** la web en un ancho de computador, **Then** las alternativas forman una
   cuadrícula de 2 columnas; **Given** un teléfono, **Then** una columna.
4. **Given** WhatsApp, **Then** el texto dice «Te recomiendo…» y «Alternativas: 1. … 4. …».
5. **Given** cualquier sugerencia, **Then** sigue cumpliendo la regla de materiales de la 051
   (ninguna de un material no apto).

---

### User Story 4 - Ver la foto como en WhatsApp (Priority: P2)

Al adjuntar la foto, el compositor muestra su miniatura; al enviarla, la burbuja de la
persona muestra la imagen con el texto que la acompaña debajo. Mientras se analiza, Carva
aparece con una lupa y el mensaje «Mirando tu foto…». Al recargar la página, la foto sigue en
el historial.

**Why this priority**: hace evidente qué se envió y da confianza; no cambia la
recomendación.

**Independent Test**: adjuntar una foto y comprobar la miniatura en el compositor, la imagen
en la burbuja enviada y el estado «Mirando tu foto…»; recargar y comprobar la miniatura en el
historial; comprobar que no se guardó la foto original.

**Acceptance Scenarios**:

1. **Given** una foto adjunta, **Then** el compositor muestra su miniatura con la opción de
   quitarla.
2. **Given** el envío, **Then** la burbuja muestra la imagen y, debajo, el texto (si hay).
3. **Given** el análisis en curso, **Then** se ve Carva con lupa y «Mirando tu foto…»
   (anunciado a lectores de pantalla).
4. **Given** una recarga, **Then** la burbuja muestra la miniatura guardada; **Given** otro
   visitante, **Then** no puede ver esa miniatura.
5. **Given** que el análisis falla o la foto se rechaza, **Then** no se guarda miniatura.

---

### User Story 5 - Adjuntar arrastrando o pegando, y descubrir la función (Priority: P3)

En computador, la persona arrastra una foto sobre el chat o la pega desde el portapapeles.
En la bienvenida, una sugerencia invita a probar la foto.

**Why this priority**: comodidad y descubrimiento; la función ya es usable con el botón.

**Independent Test**: arrastrar y pegar una imagen válida y una inválida; comprobar las
mismas validaciones del botón; comprobar la sugerencia en la bienvenida solo cuando la foto
está disponible.

**Acceptance Scenarios**:

1. **Given** una foto JPEG o PNG de hasta 10 MB arrastrada o pegada, **Then** queda adjunta
   igual que con el botón.
2. **Given** otro formato o más de 10 MB, **Then** el mismo aviso que con el botón.
3. **Given** el clasificador no disponible, **Then** ni la sugerencia ni el arrastre ofrecen
   la foto.
4. **Given** la bienvenida, **When** toca la sugerencia, **Then** se abre el selector de
   foto.

---

### Edge Cases

- **Elección ambigua**: «esa» después de una respuesta sin opciones no elige nada; el turno
  sigue el flujo normal.
- **Opciones viejas**: la elección se resuelve contra las opciones de la recomendación más
  reciente del hilo, mientras no haya otra lista de productos después.
- **«La primera»**: los números y ordinales nombran alternativas («la primera» = alternativa
  1); «esa», «dame esa», «la recomendada» y «la mejor» nombran la recomendada.
- **Elección y cambio en el mismo mensaje** («la 2 pero en 12 oz»): se trata como una nueva
  búsqueda de producto, no como elección literal.
- **Propuesta vencida**: si los precios de la propuesta ya no son válidos al confirmar, se
  recalcula y se presenta otra propuesta en lugar de registrar la vieja.
- **Varias cotizaciones en un turno**: un «sí» confirma todas las propuestas de ese turno.
- **Miniatura sin JavaScript o con un navegador sin soporte**: el mensaje enviado muestra el
  texto «📷 Foto de un alimento» como hoy.
- **Pegar texto**: pegar texto en el compositor sigue pegando texto; solo una imagen activa
  la foto.

## Requirements *(mandatory)*

### Functional Requirements

**Recomendación**

- **FR-001**: La respuesta a una foto DEBE presentar una referencia recomendada y hasta
  cuatro alternativas numeradas del 1 al 4, en el orden de la tabla de la 051.
- **FR-002**: Recomendada y alternativas DEBEN cumplir la regla de materiales de la 051.
- **FR-003**: En la web, la recomendada DEBE destacarse y las alternativas DEBEN ir en una
  cuadrícula de dos columnas que pasa a una sola columna en pantallas angostas.
- **FR-004**: Cada tarjeta DEBE ofrecer una acción «Elegir» que envíe la elección como
  mensaje.

**Elección**

- **FR-005**: El sistema DEBE resolver de forma determinista, sin depender del modelo, las
  frases de elección sobre la recomendación más reciente del hilo: recomendada («esa», «dame
  esa», «la recomendada», «la mejor», «me sirve esa») y alternativas por número u ordinal
  («alternativa 1», «la 2», «opción 3», «la segunda»).
- **FR-006**: Una elección fuera de rango DEBE responderse con las opciones válidas, sin
  cotizar.
- **FR-007**: Tras elegir, el flujo DEBE continuar con la cotización de esa referencia:
  pedir la cantidad si falta, la ciudad solo si no se conoce de la conversación, y presentar
  la propuesta.
- **FR-008**: La elección DEBE funcionar igual por texto y por voz, en la web y en WhatsApp.

**Confirmación**

- **FR-009**: Ninguna cotización DEBE registrarse (folio, fila, PDF) sin confirmación
  explícita de la persona.
- **FR-010**: Una cotización completa DEBE presentarse como propuesta, con todos sus datos y
  la pregunta de confirmación; en la web, con un botón «Confirmar cotización».
- **FR-011**: Una confirmación («sí», «confírmala», «dale», «listo», el botón) en respuesta a
  la propuesta del turno inmediatamente anterior DEBE registrarla y mostrar el folio con la
  confirmación existente.
- **FR-012**: Un cambio de datos DEBE producir una propuesta nueva; ninguna propuesta
  reemplazada se registra.
- **FR-013**: Confirmar dos veces la misma propuesta DEBE producir un solo folio.
- **FR-014**: En WhatsApp, el folio y el PDF DEBEN enviarse solo después de confirmar.

**Foto visible**

- **FR-015**: El compositor DEBE mostrar la miniatura de la foto adjunta, con opción de
  quitarla.
- **FR-016**: La burbuja de la persona DEBE mostrar la foto y debajo el texto que la
  acompaña.
- **FR-017**: El sistema DEBE guardar, solo si el análisis terminó, una miniatura pequeña
  (lado mayor de 320 px como máximo, sin metadatos) asociada al mensaje; la foto original NO
  DEBE guardarse.
- **FR-018**: La miniatura DEBE verse solo por su dueño y DEBE seguir el ciclo de vida del
  mensaje.
- **FR-019**: El aviso de privacidad DEBE decir que se guarda una miniatura en la
  conversación.
- **FR-020**: Durante el análisis DEBE mostrarse el estado «Mirando tu foto…» con Carva y una
  lupa, anunciado a lectores de pantalla.

**Comodidad y descubrimiento**

- **FR-021**: En computador DEBE poder adjuntarse la foto arrastrándola o pegándola, con las
  mismas validaciones del botón (JPEG o PNG, hasta 10 MB).
- **FR-022**: La bienvenida DEBE ofrecer la sugerencia de enviar una foto cuando la foto está
  disponible.

**Calidad**

- **FR-023**: El diseño de la tarjeta de foto adjunta y del estado «mirando» DEBE hacerse en
  Stitch antes de construirlos.
- **FR-024**: Los cambios en el agente DEBEN evaluarse contra la línea base antes de abrir la
  PR (skill `evaluar-agente`), con permiso del owner para gastar tokens.

### Key Entities

- **Recomendación de foto**: recomendada + alternativas numeradas, guardada en el turno del
  asistente; es la lista contra la que se resuelven las elecciones.
- **Elección**: la referencia que la persona nombró («recomendada» o «alternativa N»).
- **Propuesta de cotización**: una cotización calculada sin folio, guardada en el turno;
  pasa a **cotización registrada** al confirmarse.
- **Miniatura**: imagen pequeña sin metadatos del mensaje de la persona.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En un conjunto de al menos 40 frases de elección (texto y transcripciones de
  voz), el 95 % o más se resuelve a la referencia correcta y ninguna a una incorrecta (las
  dudosas preguntan).
- **SC-002**: 0 cotizaciones registradas sin confirmación en las pruebas de los dos modos del
  agente y del canal de WhatsApp.
- **SC-003**: De la elección a la propuesta, con los datos completos, en un solo turno.
- **SC-004**: La miniatura aparece en el compositor en menos de 0,5 s tras elegir la foto.
- **SC-005**: La cuadrícula de alternativas no produce desplazamiento horizontal entre 360 y
  1440 px de ancho.
- **SC-006**: La evaluación del agente no empeora frente a la línea base salvo por el cambio
  esperado (propuesta en lugar de folio).

## Assumptions

- El ancho de 320 px de la miniatura basta para la burbuja; la imagen grande no se necesita
  después del análisis.
- La miniatura vive con el mensaje: si el mensaje se borra, se borra.
- «Confirmar» acepta las palabras habituales de asentimiento en español de Colombia y México;
  una respuesta ambigua no confirma.
- La propuesta conserva los precios con los que se calculó mientras sigan vigentes; si no,
  se recalcula (Edge Cases).
- El paso de confirmación es nuevo para el cotizador por texto también: el texto del
  asistente y las pruebas que esperaban folio en el primer turno cambian.
