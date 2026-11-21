# Feature Specification: Reglas compartidas del chat, lector SSE único y HTMX solo en Configuración

**Feature Branch**: `refactor/044-chat-sse-interfaz`
**Created**: 2026-11-20
**Status**: Draft
**Input**: User description: "Centraliza las reglas compartidas de validación, identidad y cuota entre el formulario, el stream web y el stream de API. Conserva rutas, formatos de respuesta, errores, cookies y orden de controles. Comparte el lector SSE entre chat y FAQ después de probar el cierre, los bloques partidos y las diferencias actuales entre consumidores. Carga HTMX solo en Configuración. Conserva captura, accesibilidad, dictado, lectura, manos libres y el formulario sin streaming."

## Contexto

La app única FastAPI/Jinja2 tiene tres entradas al chat que comparten reglas de
validación, identidad y cuota: el formulario `POST /chat` (HTML y redirección), el
stream del shell `POST /chat/stream` (SSE) y el stream de la API
`POST /api/chat/stream` (SSE). Hoy cada vista repite las mismas constantes, el mismo
orden de controles y el mismo traductor de eventos; la FAQ reutiliza parte de esas
reglas. Además, `chat-stream.js` y `faq.js` contienen dos copias casi idénticas del
lector SSE, y `base.html` carga `htmx.min.js` en todas las pantallas aunque solo
Configuración lo usa.

Esta spec es una **refactorización sin cambio de comportamiento**: el contrato
observable de rutas, formatos, errores, cookies y orden de controles se fija con
pruebas de caracterización **antes** de tocar el código y debe seguir idéntico después.

## Clarifications

### Session 2026-11-20

- Q: ¿Se admite cambiar el comportamiento al compartir el lector SSE? → A: Solo el
  mínimo necesario para eliminar la duplicación. El lector común procesa también un
  último bloque sin línea en blanco (lo que ya hacía el chat); para la FAQ es una
  mejora compatible porque el servidor siempre termina sus bloques, y se documenta
  como diferencia intencional en las pruebas. El resto de diferencias entre
  consumidores (formateo final del chat, notificación `chat:turn-complete`,
  acumulación de FAQ) se conserva con callbacks.
- Q: ¿HTMX deja de estar disponible en el resto de pantallas? → A: Sí. Solo
  Configuración lo usa (`hx-post`, `hx-target`, `hx-swap`, `hx-disabled-elt` y los
  eventos `htmx:*` de `captura.js`). Las demás pantallas no lo cargan y conservan su
  comportamiento actual, que no depende de HTMX.
- Q: ¿Se puede cambiar el contrato HTTP? → A: No. Rutas, métodos, códigos, cuerpos,
  cabeceras, cookies y orden de CSRF → validación → cuota se conservan exactamente.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Una sola fuente de reglas para las tres entradas del chat (Priority: P1)

Quien mantiene la app necesita que el formulario, el stream web y el stream de API
decidan con las mismas reglas: mismo límite de longitud, mismos mensajes, misma
identidad anónima, misma cookie y misma cuota, en el mismo orden. Así un cambio de
regla se hace una vez y no se corre el riesgo de que una entrada cobre cupo de más,
permita una pregunta vacía o filtre un detalle interno que las otras no filtran.

**Why this priority**: sin esta unificación, cualquier cambio futuro vuelve a divergir
y el riesgo de regresión es mayor que el ahorro de líneas.

**Independent Test**: se ejecuta la matriz de pruebas de caracterización contra las
tres rutas y se comprueba que, ante la misma entrada, responden con el mismo código,
formato, mensaje y cookies que antes del refactor; y que con CSRF inválido no se
consume cuota ni se llama al proveedor.

**Acceptance Scenarios**:

1. **Given** una petición con CSRF inválido a cualquiera de las tres entradas, **When**
   se procesa, **Then** responde 403 con el formato de esa entrada (página HTML con
   `X-Carvajal-Error` en el formulario; bloque `error` SSE en los streams), no consume
   cuota y no llama al proveedor.
2. **Given** una pregunta vacía o de más de 500 caracteres, **When** se envía, **Then**
   responde 422 con el mensaje vigente antes de tocar la cuota y antes de llamar al
   proveedor.
3. **Given** una cuota agotada o un fallo al consultarla, **When** se envía una
   pregunta válida con CSRF válido, **Then** responde 429 con `Retry-After` o 503 con
   el mensaje genérico, respectivamente, en el formato de esa entrada y sin llamar al
   proveedor.
4. **Given** un visitante nuevo, **When** completa una petición aceptada, **Then**
   recibe la cookie de identidad firmada con las mismas propiedades (HttpOnly,
   SameSite=Lax, Path=/, Secure solo en HTTPS) que antes del refactor.
5. **Given** el mismo error de proveedor, **When** se compara la respuesta del stream
   web con la del stream de API, **Then** la secuencia y el contenido de los eventos
   SSE son equivalentes y ninguno filtra el detalle interno.

---

### User Story 2 - Un lector SSE común para chat y FAQ (Priority: P1)

Quien mantiene la interfaz necesita una sola implementación del lector de eventos que
resista bloques partidos entre lecturas, varios `data:`, finales de línea CRLF, el
cierre del stream y un último bloque sin línea en blanco, y que chat y FAQ la usen sin
perder su comportamiento propio.

**Why this priority**: hoy son dos copias que ya divergieron en el último bloque; cada
arreglo de lectura habría que hacerlo dos veces y el error es invisible en pruebas
manuales.

**Independent Test**: se sirve el lector común desde `/static/`, se carga en las
pantallas de chat y FAQ, y las pruebas de Node reproducen los casos límite con flujos
fragmentados y comprueban que cada consumidor conserva sus callbacks.

**Acceptance Scenarios**:

1. **Given** un bloque `event:`/`data:` partido entre dos lecturas del flujo, **When**
   se recibe completo, **Then** el consumidor recibe un único evento con el nombre y
   los datos correctos.
2. **Given** un evento con dos líneas `data:`, **When** se interpreta, **Then** las
   líneas se unen con salto de línea y el JSON resultante se entrega una sola vez.
3. **Given** un flujo con separadores `\r\n`, **When** se lee, **Then** se reconocen
   igual que `\n` y no se generan eventos vacíos.
4. **Given** un último bloque sin línea en blanco al cerrarse el flujo, **When** se
   termina de leer, **Then** el lector común lo entrega; chat lo formatea y notifica
   como hasta ahora y FAQ lo acumula como un lote válido.
5. **Given** un evento `done`, **When** el chat lo procesa, **Then** mantiene su
   formateo final, sus marcas y la notificación `chat:turn-complete`; la FAQ mantiene
   su aplicación del lote y su acumulación.
6. **Given** JavaScript sin el lector común disponible, **When** se carga el chat,
   **Then** el formulario conserva la degradación al POST normal sin streaming.

---

### User Story 3 - HTMX solo en la pantalla que lo usa (Priority: P2)

Una persona que navega el chat, la FAQ o el corpus no descarga HTMX, porque esas
pantallas no lo necesitan; la captura web de Configuración sí lo conserva y sigue
funcionando igual.

**Why this priority**: reduce peso y superficie en las pantallas de uso frecuente sin
tocar la funcionalidad; es de menor riesgo que las dos historias anteriores pero se
verifica con la misma matriz.

**Independent Test**: se piden las páginas de chat, FAQ, corpus, documento y
Configuración, y se comprueba que solo la última referencia `htmx.min.js`; la captura
mantiene sus atributos `hx-*` y su script de progreso.

**Acceptance Scenarios**:

1. **Given** la pantalla de Configuración, **When** se carga, **Then** solicita
   `/static/htmx.min.js` y conserva el formulario de captura con sus atributos HTMX.
2. **Given** las pantallas de chat, FAQ, corpus o documento, **When** se cargan,
   **Then** no solicitan `/static/htmx.min.js` y siguen funcionando con su JavaScript
   propio.
3. **Given** el chat, **When** se revisa su HTML, **Then** conserva el formulario sin
   streaming, el dictado, la lectura, la autolectura, manos libres y los controles
   accesibles de la spec 040.
4. **Given** la CSP vigente, **When** se revisan las páginas, **Then** no aparece
   `unsafe-inline` ni estilos inline nuevos y todos los scripts se sirven desde
   `/static/`.

---

### Edge Cases

- Pregunta con solo espacios: se considera vacía y responde 422.
- Pregunta de exactamente 500 caracteres: es válida; 501 no.
- Cookie de identidad manipulada: se regenera sin error, como hoy.
- Cuota que falla con la base caída: 503 genérico, sin detalle interno, sin llamar al
  proveedor.
- Petición a `/chat/new` con CSRF inválido: conserva su página de error actual.
- Stream cortado antes del primer token: el chat reenvía por el POST normal solo si no
  hubo contenido ni cierre; manos libres no reenvía y devuelve el texto a revisión.
- Evento SSE con JSON inválido: se ignora, como hoy.
- Bloque sin líneas `data:`: se ignora, como hoy.
- FAQ sin corpus núcleo o con `exclude` inválido: conserva 422 y no cobra cuota.
- Página sin JavaScript: el formulario del chat sigue enviando por `POST /chat`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Las tres entradas del chat MUST compartir una única definición del
  máximo de 500 caracteres, los mensajes de pregunta vacía, pregunta larga, límite
  diario y sesión CSRF caducada.
- **FR-002**: MUST conservarse las rutas y métodos actuales: `POST /chat` (página HTML
  y 303), `POST /chat/stream` (SSE), `POST /api/chat/stream` (SSE) y el uso compartido
  de identidad y cuota por `POST /api/faq`.
- **FR-003**: MUST conservarse el orden CSRF → validación → cuota → proveedor en las
  tres entradas del chat; la FAQ MUST conservar su preparación del corpus antes de
  cobrar la cuota.
- **FR-004**: MUST conservarse códigos 403, 404, 422, 429 y 503, los formatos de
  respuesta (página HTML o bloque SSE `error`), `Retry-After`, `X-Carvajal-Error`,
  `Content-Type` y las cabeceras del stream (`Cache-Control: no-store`,
  `X-Accel-Buffering: no`).
- **FR-005**: MUST conservarse las cookies de identidad y CSRF con sus propiedades,
  y MUST conservarse el momento en que se emiten (incluido que un 403 o 422 de los
  streams no emita la cookie de identidad, como hoy).
- **FR-006**: Los errores MUST seguir ocultando detalles internos (LLM, embeddings,
  base de datos, trazas) y mantener los mensajes visibles actuales.
- **FR-007**: MUST existir un único lector SSE servido desde `/static/` que reconozca
  `event:`, una o varias líneas `data:`, separadores `\n` y `\r\n`, bloques partidos
  entre lecturas, el cierre del flujo y un último bloque sin línea en blanco.
- **FR-008**: El lector común MUST aceptar callbacks/opciones para conservar las
  diferencias legítimas entre consumidores: formateo y notificación final del chat y
  acumulación por lotes de la FAQ.
- **FR-009**: `htmx.min.js` MUST cargarse solo en Configuración; el resto de pantallas
  no MUST solicitarlo. La captura web MUST conservar sus atributos `hx-*` y su script
  de progreso.
- **FR-010**: MUST conservarse la captura web, la accesibilidad, el dictado, la
  lectura, la autolectura, manos libres y el formulario sin streaming, con el mismo
  HTML y los mismos scripts donde corresponde.
- **FR-011**: MUST mantenerse la CSP sin `unsafe-inline` y sin estilos inline; los
  scripts y estilos nuevos MUST servirse desde `/static/`.
- **FR-012**: MUST añadirse pruebas de caracterización antes del refactor para las
  tres entradas y para el lector SSE, y MUST quedar en verde `make check` y
  `node --test` sobre el chat por voz.
- **FR-013**: La refactorización no MUST modificar `apps/api/chat/service.py` ni
  `apps/api/chat/agent.py`, ni introducir dependencias o frameworks nuevos.

### Key Entities

- **Entrada del chat**: formulario, stream web o stream de API; comparte visitante,
  reglas y cuota, y difiere en el formato de respuesta.
- **Visitante anónimo**: identidad firmada, dueño derivado y condición de nuevo
  visitante; no es autenticación.
- **Evento SSE**: nombre y datos JSON; se entrega al consumidor una vez ensamblado.
- **Lector SSE**: componente estático único que ensambla el flujo y llama al
  consumidor.
- **Pantalla**: documento HTML que declara sus scripts; solo Configuración incluye
  HTMX.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de los escenarios de caracterización de las tres entradas
  (rutas, formatos, códigos, mensajes, cabeceras, cookies y orden de controles) pasa
  antes y después del refactor, sin cambios en las expectativas.
- **SC-002**: Para una misma petición válida o un mismo error, el stream web y el
  stream de API producen la misma secuencia de eventos y el mismo contenido observable
  para el cliente; las diferencias internas de registro no cuentan como diferencia de
  contrato.
- **SC-003**: 0 páginas distintas de Configuración solicitan `htmx.min.js`, y la
  captura web conserva sus atributos y su script de progreso.
- **SC-004**: 0 apariciones de `unsafe-inline` o de estilos inline nuevos; todos los
  scripts de las pantallas se sirven desde `/static/`.
- **SC-005**: El lector SSE queda en un único archivo estático y las dos pantallas lo
  usan; 0 copias del bucle de lectura en `chat-stream.js` y `faq.js`.
- **SC-006**: `make check` y todas las pruebas de Node quedan en verde, incluidas las
  nuevas de caracterización y del lector compartido.

## Assumptions

- El contrato HTTP/SSE vigente es el de las specs 016, 017, 022, 024 y 040; no se
  reabren sus decisiones.
- La FAQ conserva su orden propio (preparar corpus antes de cobrar cuota) porque así
  evita gastar una generación cuando el corpus no sirve; la unificación cubre las
  piezas compartidas, no fuerza el mismo orden en todo.
- El lector común procesa el último bloque sin separador, igual que hacía el chat; el
  servidor siempre cierra sus bloques, así que para la FAQ es una mejora compatible y
  no un cambio de contrato.
- La actualización de `CONSTITUTION.md` que exige la directiva del repositorio se
  consolida en la PR de documentación del goal; esta PR no la incluye para respetar
  el alcance de escritura acordado.
- No se cambia la cuota, el límite por IP (fuera de alcance) ni el modelo de
  identidad.
