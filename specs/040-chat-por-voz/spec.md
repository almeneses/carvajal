# Feature Specification: Chat por voz: dictado de preguntas y respuestas habladas

**Feature Branch**: `040-chat-por-voz`
**Created**: 2026-11-17
**Status**: Draft
**Input**: User description: "Incluir el servicio para que se le pueda dictar al chat y que
el chat le hable a uno también."

## Clarifications

### Session 2026-11-17

- Q: ¿Dónde se convierte la voz a texto y el texto a voz? → A: En el navegador, con sus
  capacidades nativas. El audio no pasa por el servidor del proyecto; la calidad y el
  soporte dependen del navegador, y donde no hay soporte el chat sigue siendo escrito.

### Decisiones de alcance tras la revisión del PR #184

- El chat por voz conserva la cuota por visitante, el límite de longitud y las
  protecciones actuales. Añadir un límite por IP al chat queda fuera de la 040.
- La lectura automática se ofrece tras completar una respuesta recibida por
  streaming. Sin streaming sigue disponible «Escuchar» manual.
- Manos libres solo se activa con el borrador vacío. Si la persona escribe
  durante el modo, se desactiva y conserva su borrador sin enviarlo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dictar una pregunta (Priority: P1)

Una persona que atiende una cafetería tiene las manos ocupadas. Toca el botón del micrófono,
dice «necesito vasos para café de doce onzas en Bogotá» y ve el texto transcrito en la caja
de mensaje. Lo revisa, lo corrige si hace falta y lo envía como cualquier pregunta escrita.

**Why this priority**: dictar es la mitad de la capacidad pedida y la más útil en un
mostrador; sin ella no hay chat por voz.

**Independent Test**: dictar tres preguntas del conjunto de evaluación y comprobar que el
texto aparece en la caja de mensaje sin enviarse solo, y que al enviarlo el router toma la
misma ruta que con la pregunta escrita.

**Acceptance Scenarios**:

1. **Given** un navegador con micrófono, **When** la persona toca el micrófono, **Then** el
   chat pide permiso de micrófono la primera vez y muestra claramente que está escuchando.
2. **Given** que la persona terminó de hablar o tocó «detener», **When** se procesa el
   audio, **Then** el texto aparece en la caja de mensaje, editable y sin enviarse.
3. **Given** el permiso de micrófono denegado o un navegador sin soporte, **When** se toca
   el micrófono, **Then** el chat explica por qué no puede escuchar y la escritura sigue
   disponible.
4. **Given** una grabación más larga que el máximo permitido, **When** se alcanza el límite,
   **Then** la grabación se detiene y se transcribe lo dicho hasta ahí.
5. **Given** cifras y unidades dichas en voz («doce onzas», «cinco mil vasos»), **When** se
   transcriben, **Then** el router extrae la capacidad y la cantidad igual que de «12 oz» y
   «5.000».
6. **Given** la primera vez que se usa el micrófono, **When** se activa el dictado, **Then**
   el chat avisa que el navegador puede procesar la voz con su propio servicio antes de
   escuchar.

---

### User Story 2 - Escuchar la respuesta (Priority: P1)

La persona puede oír cualquier respuesta del asistente con un botón «Escuchar», y puede
activar la opción «Leer respuestas en voz alta» para que las siguientes que lleguen por
streaming se lean solas. Puede pausar o detener la lectura en cualquier momento.

**Why this priority**: es la otra mitad pedida («que el chat le hable a uno») y aporta
accesibilidad.

**Independent Test**: pedir una recomendación con tarjetas de producto, tocar «Escuchar» y
comprobar que se oye la respuesta y un resumen de cada tarjeta con los mismos datos que se
ven en pantalla.

**Acceptance Scenarios**:

1. **Given** una respuesta del asistente, **When** se toca «Escuchar», **Then** se lee en
   voz en español y el botón cambia a «Detener».
2. **Given** «Leer respuestas en voz alta» activado, **When** termina de llegar una
   respuesta por streaming, **Then** se lee sin otra acción; la preferencia se recuerda
   para ese visitante.
3. **Given** una respuesta con tarjetas de producto, **When** se lee, **Then** se dice el
   nombre, las cajas y el precio con su moneda y su fecha exactamente como en pantalla, y se
   omiten URLs y códigos técnicos.
4. **Given** una respuesta con citas, **When** se lee, **Then** se dice «según» y el título
   de la fuente en lugar de leer los enlaces.
5. **Given** que la persona empieza a dictar mientras se lee una respuesta, **When** toca el
   micrófono, **Then** la lectura se detiene.
6. **Given** la lectura automática activada, **When** la respuesta llega sin streaming
   o se abre de nuevo el historial, **Then** no se reproduce sola y «Escuchar» sigue disponible.

---

### User Story 3 - Conversación manos libres (Priority: P3)

Con el modo manos libres activado, al terminar de leerse la respuesta el chat vuelve a
escuchar, y la persona puede encadenar preguntas («¿y si son 4.500?») sin tocar la pantalla.
Un silencio prolongado o la palabra «terminar» cierran el modo.

**Why this priority**: es la experiencia completa de voz, pero suma complejidad (envío sin
revisión) y no es necesaria para el valor principal.

**Independent Test**: hacer una pregunta comercial y un seguimiento solo por voz y comprobar
que el segundo turno conserva el contexto del primero.

**Acceptance Scenarios**:

1. **Given** el modo manos libres, **When** termina la lectura, **Then** el chat vuelve a
   escuchar y lo indica de forma visible.
2. **Given** el modo manos libres, **When** la persona termina de hablar, **Then** el texto
   se envía sin revisión manual y se muestra en el hilo como un mensaje normal.
3. **Given** 10 segundos sin voz o la palabra «terminar», **When** ocurre, **Then** el modo
   se apaga.
4. **Given** un borrador no vacío, **When** la persona intenta activar manos libres,
   **Then** el modo no se activa y el borrador queda intacto. Si escribe con el modo
   activo, este se apaga y ningún resultado tardío se envía ni reemplaza lo escrito.

---

### Edge Cases

- Ruido de fondo o audio vacío: no se envía nada; el chat pide repetir.
- Transcripción con palabras que cambian el sentido («doce» por «dos»): por eso, fuera del
  modo manos libres, el texto se revisa antes de enviar.
- Pregunta dictada que supera los 500 caracteres del chat: se recorta con aviso, igual que
  la escrita.
- Lector de pantalla activo: la lectura automática queda apagada por defecto para no
  duplicar la voz; «Escuchar» sigue disponible.
- Respuesta que todavía está llegando por streaming: se lee solo al completarse, incluso
  en modo automático.
- Varios hilos o pestañas: solo se lee la respuesta del hilo visible.
- Cuota diaria agotada: el dictado funciona, pero el envío respeta la cuota como cualquier
  pregunta.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El chat MUST ofrecer un control de micrófono que transcriba voz en español a
  texto en la caja de mensaje.
- **FR-002**: Fuera del modo manos libres, el texto transcrito no MUST enviarse sin que la
  persona lo confirme.
- **FR-003**: El chat MUST indicar de forma visible y accesible cuándo está escuchando y
  cuándo dejó de hacerlo.
- **FR-004**: La grabación MUST tener un máximo de 60 segundos por pregunta.
- **FR-005**: El audio no MUST guardarse: solo el texto enviado entra al historial, igual que
  una pregunta escrita.
- **FR-006**: Cada respuesta del asistente MUST poder escucharse con un control de lectura
  que permita pausar y detener.
- **FR-007**: El visitante MUST poder activar la lectura automática al completarse las
  respuestas por streaming. La opción MUST recordarse por visitante y estar apagada por
  defecto. Sin streaming y al abrir el historial, la lectura es solo manual.
- **FR-008**: La lectura de tarjetas de producto MUST decir los mismos datos de cantidad,
  precio, moneda y fecha que se muestran, sin leer URLs ni códigos técnicos.
- **FR-009**: Sin micrófono, sin permiso o sin soporte del navegador, el chat MUST seguir
  funcionando por escrito y explicar la limitación.
- **FR-010**: Los controles de voz MUST operarse con teclado y tener nombres accesibles,
  conforme al nivel de accesibilidad del chat (spec 024).
- **FR-011**: El envío del texto dictado MUST respetar la cuota diaria por visitante,
  el límite de longitud y la seguridad actual del chat (CSRF, CSP). Dictar o leer
  sin enviar no consume cuota. La 040 no incorpora un límite nuevo por IP.
- **FR-012**: El modo manos libres MUST poder activarse y desactivarse, y apagarse automáticamente tras
  10 segundos de silencio o con la palabra «terminar» (P3). MUST exigir borrador vacío
  para activarse, desactivarse ante escritura manual y conservar ese texto sin enviarlo.
- **FR-013**: El audio MUST procesarse en el navegador y no MUST enviarse al servidor del
  proyecto. Antes del primer dictado, el chat MUST avisar que el navegador puede procesar
  la voz con su propio servicio.

### Key Entities *(include if feature involves data)*

- **Dictado**: grabación temporal de voz y su transcripción; la grabación se descarta al
  transcribir.
- **Lectura**: reproducción en voz de una respuesta, con estado (leyendo, pausada,
  detenida).
- **Preferencias de voz del visitante**: lectura automática y modo manos libres, activados o
  no.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con 20 preguntas del conjunto de evaluación grabadas por al menos dos voces, al
  menos el 90 % de las transcripciones lleva al router a la misma ruta y a los mismos datos
  extraídos que el texto escrito.
- **SC-002**: El texto dictado aparece en la caja de mensaje en menos de 3 segundos después
  de terminar de hablar (p95) en una conexión de banda ancha.
- **SC-003**: La lectura de una respuesta empieza en menos de 2 segundos después de tocar
  «Escuchar» (p95).
- **SC-004**: 0 archivos de audio persistidos en el servidor o en la base de datos tras una
  sesión de prueba.
- **SC-005**: Los controles de voz pasan la revisión automática de accesibilidad del chat
  sin errores nuevos.

## Assumptions

- El idioma es español (Colombia y México); otros idiomas quedan fuera de alcance.
- El chat vive en el shell Jinja2/HTMX que sirve la API en `:8000`; la SPA `apps/web` y el
  prototipo Streamlit se retiraron en #163.
- No hay identificación de la persona por su voz ni llamadas telefónicas.
- El diseño «Asistente Carvajal» ya prevé el botón de micrófono en la caja de mensaje.
- La voz se procesa en el navegador (ver Clarifications). Algunos navegadores envían el
  audio del dictado a su propio servicio de reconocimiento; el proyecto no lo recibe ni lo
  guarda, y el chat lo advierte antes del primer dictado.
- Los navegadores sin reconocimiento de voz (por ejemplo, Firefox de escritorio) no muestran
  el micrófono o explican que no está disponible; la lectura en voz suele estar disponible
  en todos.
