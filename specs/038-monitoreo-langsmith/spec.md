# Feature Specification: Monitoreo del agente con LangSmith

**Feature Branch**: `038-monitoreo-langsmith`
**Created**: 2026-11-17
**Status**: Draft
**Input**: User description: "Incluir el monitoreo con LangSmith."

## Clarifications

### Session 2026-11-17

- Q: ¿En qué entornos se activa el monitoreo y con qué contenido? → A: En producción (VPS)
  solo con metadatos: ruta, enrutador, pasos, tokens, tiempos y errores, sin el texto de
  preguntas, respuestas ni fragmentos. En desarrollo y en las corridas de evaluación se
  envía el contenido completo.

### Session 2026-11-18

- Q: ¿Qué pasa con la marca del visitante cuando el turno no se muestrea? → A: La marca se
  guarda con el identificador de la petición y se enlaza a la traza solo si existe; nunca se
  fuerza una traza por una marca, porque el muestreo existe para respetar el límite del plan.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Seguir un turno del chat de punta a punta (Priority: P1)

Una persona del equipo recibe el reporte de una respuesta mala («me recomendó un vaso que no
es para café caliente»). Abre el monitoreo, busca el turno por hilo, fecha o identificador de
petición y ve en un solo lugar qué ruta eligió el router y con qué enrutador, qué datos
extrajo, qué fragmentos recuperó, qué herramienta o paso comercial se ejecutó, qué le llegó
al modelo de lenguaje, qué contestó, cuántos tokens usó y cuánto tardó cada paso.

**Why this priority**: hoy diagnosticar una respuesta exige reproducirla a mano y leer logs
JSON dispersos. Ver el recorrido completo del agente es el valor central del monitoreo.

**Independent Test**: con el monitoreo activado y muestreo del 100 %, enviar «vasos para
café de 12 oz en México» y comprobar que existe una traza con la decisión del router, los
pasos de la ruta `commercial`, la llamada al modelo con sus tokens y la latencia de cada
paso.

**Acceptance Scenarios**:

1. **Given** el monitoreo activado y un turno muestreado, **When** se completa un turno de
   cualquier ruta (`direct`, `rag`, `tool`, `commercial`), **Then** existe una traza con la
   ruta, el enrutador usado y cada paso ejecutado con su duración.
2. **Given** un turno de la ruta `rag` en desarrollo, **When** se abre su traza, **Then**
   muestra los fragmentos recuperados con su documento de origen y su puntuación; en
   producción muestra solo los documentos de origen y las puntuaciones.
3. **Given** un turno con llamada al modelo de lenguaje, **When** se abre su traza, **Then**
   muestra el proveedor, el modelo, los tokens de entrada y salida y la latencia.
4. **Given** un turno muestreado y el identificador de petición que aparece en los logs,
   **When** se busca en el monitoreo, **Then** se encuentra la traza de ese turno.
5. **Given** un turno que falla, **When** se abre su traza, **Then** el paso que falló queda
   marcado con el tipo de error.

---

### User Story 2 - Monitoreo opcional y sin fuga de datos personales (Priority: P1)

Quien despliega el proyecto decide si el monitoreo se activa. Sin configuración no sale
ningún dato del proceso. Con el monitoreo activado, las trazas no llevan la IP, las cookies,
los tokens CSRF ni el identificador crudo del visitante; si el servicio de monitoreo no
responde, el chat sigue funcionando igual.

**Why this priority**: el monitoreo envía el contenido de las conversaciones a un servicio
externo. Es la misma regla que ya cumple Sentry (opcional, sin PII por defecto) y es
condición para activarlo en cualquier entorno.

**Independent Test**: arrancar sin configuración y verificar que no hay tráfico hacia el
servicio; activarlo, cortar la conexión al servicio y comprobar que el turno se responde y
se persiste igual.

**Acceptance Scenarios**:

1. **Given** que no hay credenciales de monitoreo configuradas, **When** se usa el chat,
   **Then** no se envía ningún dato al servicio de monitoreo.
2. **Given** el monitoreo activado, **When** se registra un turno, **Then** la traza no
   contiene IP, cookies, tokens CSRF, claves de API ni el identificador crudo del visitante.
3. **Given** el monitoreo activado y el servicio caído o lento, **When** se envía una
   pregunta, **Then** la respuesta llega sin error y sin retraso perceptible, y la falla del
   monitoreo queda en los logs.
4. **Given** un muestreo configurado (por ejemplo, 20 % de los turnos), **When** se usan
   muchos turnos, **Then** se traza aproximadamente esa proporción.
5. **Given** el monitoreo activado en producción, **When** se registra un turno, **Then** la
   traza no contiene el texto de la pregunta, de la respuesta ni de los fragmentos.
6. **Given** el monitoreo activado, **When** se abre el diagnóstico de salud, **Then** se ve
   si está activo, el muestreo y el modo de contenido, sin credenciales.

---

### User Story 3 - Comparar enrutadores y evaluaciones en el tiempo (Priority: P2)

Al correr la evaluación del enrutador (110 preguntas) o la de recuperación, el equipo puede
registrar cada corrida como un experimento con su versión de código y su configuración, y
compararla con corridas anteriores sin copiar tablas a mano.

**Why this priority**: hoy las corridas viven en `docs/resultados-router.md` y se pegan a
mano. Es útil para la entrega, pero el chat funciona sin esto.

**Independent Test**: correr la evaluación del enrutador con la opción de registro y ver en
el monitoreo un experimento con las métricas que ya produce el informe local.

**Acceptance Scenarios**:

1. **Given** una corrida de evaluación con registro, **When** termina, **Then** el
   experimento guarda el enrutador, el modelo, el commit y las mismas métricas del informe
   local (precisión de ruta, datos extraídos, violaciones de reglas, latencia, llamadas).
2. **Given** dos experimentos, **When** se comparan, **Then** se ven las preguntas cuya
   ruta cambió entre una corrida y otra.
3. **Given** una corrida sin registro, **When** termina, **Then** el informe local se genera
   igual que hoy.

---

### User Story 4 - Marcar respuestas malas desde el chat (Priority: P3)

El visitante puede indicar con un clic si una respuesta le sirvió o no; la marca queda
asociada al turno para revisarla después.

**Why this priority**: acelera encontrar los casos que importan, pero añade interfaz y no es
necesario para diagnosticar.

**Independent Test**: marcar una respuesta como «no me sirvió» y encontrar la marca asociada
al turno en los datos del proyecto.

**Acceptance Scenarios**:

1. **Given** una respuesta del asistente, **When** el visitante la marca, **Then** la marca
   se guarda con el identificador de la petición, el chat lo confirma sin recargar y, si ese
   turno tiene traza, se enlaza a ella.
2. **Given** el monitoreo desactivado, **When** se muestra el chat, **Then** no aparece el
   control de marca.
3. **Given** un turno sin traza por el muestreo, **When** el visitante lo marca, **Then** la
   marca se conserva con su identificador de petición y no se crea una traza nueva.

---

### Edge Cases

- Respuesta en streaming que el visitante cancela a mitad: la traza queda cerrada como
  cancelada, con lo que alcanzó a generarse.
- Turno de aclaración de la ruta comercial (`commercial_clarify`): se traza como turno
  completo aunque no llame al modelo.
- Ruta `rag` con abstención (sin contexto): la traza muestra que no hubo llamada al modelo.
- Preguntas con datos personales que el visitante escribe por su cuenta (nombre, teléfono):
  en producción no salen, porque solo se envían metadatos; en desarrollo se usan datos de
  prueba.
- Límite del plan del servicio alcanzado: el monitoreo deja de registrar sin afectar el
  chat y lo avisa en los logs.
- Evaluaciones en CI: nunca registran experimentos ni requieren credenciales.
- Turno no muestreado que el visitante marca: la marca se conserva con su identificador de
  petición y queda sin traza asociada; el muestreo no se fuerza por una marca.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El monitoreo MUST estar desactivado por defecto y activarse solo con
  configuración explícita de despliegue.
- **FR-002**: Con el monitoreo activado, cada turno muestreado del chat MUST producir una
  traza con la ruta, el enrutador usado, los datos extraídos (intención, herramienta,
  período y número de preferencias aplicadas, sin texto libre) y cada paso del grafo con su
  duración.
- **FR-003**: La traza MUST incluir las llamadas al modelo de lenguaje con proveedor, modelo,
  tokens y latencia, y los fragmentos recuperados con su documento de origen.
- **FR-004**: La traza MUST llevar el identificador de petición de los logs y un
  identificador seudónimo del hilo, de modo que logs y trazas se crucen.
- **FR-005**: Las trazas MUST NOT contener IP, cookies, tokens CSRF, claves de API ni el
  identificador crudo del visitante.
- **FR-006**: En producción, el monitoreo MUST enviar solo metadatos (ruta, enrutador,
  pasos, tokens, tiempos y errores), sin el texto de preguntas, respuestas ni fragmentos
  recuperados. El contenido completo MUST limitarse a desarrollo y evaluación.
- **FR-007**: El sistema MUST permitir un porcentaje de muestreo configurable.
- **FR-008**: Una falla, lentitud o límite del servicio de monitoreo MUST NOT alterar la
  respuesta, la persistencia ni la cuota del chat.
- **FR-009**: La evaluación del enrutador y la de recuperación MUST poder registrarse como
  experimentos de forma opcional, sin cambiar el informe local.
- **FR-010**: Las pruebas y el CI MUST NOT requerir credenciales ni enviar datos al servicio.
- **FR-011**: El estado del monitoreo (activo o no, muestreo, modo de contenido) MUST verse en
  el diagnóstico de salud sin exponer credenciales.
- **FR-012**: El visitante MUST poder marcar una respuesta como útil o no útil; la marca
  MUST guardarse con el identificador de la petición y MUST asociarse a la traza cuando ese
  turno tenga una. Una marca MUST NOT forzar la creación de una traza (P3; se entrega en
  esta spec).

### Key Entities *(include if feature involves data)*

- **Traza**: registro de un turno, con identificador de petición, hilo seudónimo, ruta,
  enrutador, estado (completo, cancelado, fallido), duración total y pasos.
- **Paso**: nodo del grafo, herramienta o llamada al modelo, con entradas y salidas
  (o solo metadatos), duración y error si lo hubo.
- **Experimento**: corrida de evaluación con conjunto de preguntas, enrutador, modelo,
  commit y métricas por pregunta y agregadas.
- **Marca del visitante**: útil o no útil, asociada a `request_id` y, si existe, a la traza.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con el monitoreo activado y muestreo del 100 %, el 100 % de los turnos de las
  cuatro rutas tiene traza.
- **SC-002**: 0 trazas contienen IP, cookies, tokens CSRF, claves o el identificador crudo
  del visitante, y 0 trazas de producción contienen texto de conversaciones.
- **SC-003**: Con el servicio de monitoreo inalcanzable, el 100 % de los turnos se responde
  y la latencia al primer fragmento no empeora más de 100 ms (p95) frente al monitoreo
  desactivado.
- **SC-004**: Encontrar la traza de un turno muestreado a partir de su identificador de
  petición toma menos de un minuto.
- **SC-005**: Las dos corridas del enrutador ya documentadas pueden repetirse como
  experimentos y sus métricas coinciden con el informe local.

## Assumptions

- El servicio es LangSmith en su modalidad en la nube; su integración con LangGraph y
  LangChain, que el agente ya usa, permite trazar sin reescribir los nodos.
- Se usa un plan con límite mensual de trazas; el muestreo permite mantenerse dentro.
- Sentry sigue siendo el canal de errores de la aplicación y los logs JSON siguen siendo la
  fuente de operación; LangSmith los complementa para el comportamiento del agente.
- En producción el monitoreo envía solo metadatos; el contenido completo se reserva para
  desarrollo y evaluación (ver Clarifications).
- La marca del visitante (US4) es P3, pero forma parte de esta entrega (PR4).
