# Feature Specification: Enrutador de decisiones con Jev cloud

**Feature Branch**: `041-enrutador-jev`
**Created**: 2026-11-18
**Status**: Draft
**Input**: User description: "Usar Jev cloud como modelo de decisión del enrutador del
asistente, aprovechando que el adaptador para un modelo de decisión ya existe y que la GPU
local no alcanza para un modelo propio."

## Contexto

La spec 036 dejó dos compromisos abiertos: el enrutador intercambiable (FR-013) y el informe
comparativo de los tres enrutadores (`rules`, `llm` y `decision`) con las mismas métricas
(SC-006). El adaptador `decision` está implementado y probado con un modelo falso, pero sin
medir: falta elegir el paquete y configurar el modelo.

Esta spec elige **Jev cloud** (TypeSafe System One) como modelo de decisión. Jev no genera
texto: recibe un estado y preguntas tipadas (sí/no, elección, puntuación) y devuelve
probabilidades calibradas en una sola llamada. Esa diferencia con un LLM es el centro del
diseño: la decisión de enrutamiento se compone de preguntas cerradas, y los datos abiertos
(capacidad, cantidad, ciudad, período) no se le piden a Jev.

La alternativa local (OpenJev sobre GPU propia) se descartó por hardware, lo que convierte a
TypeSafe en una dependencia externa con costo por llamada y con la pregunta del visitante
saliendo del servidor del proyecto.

La medición depende de la spec 039: el catálogo de Colombia cambia las preguntas comerciales
(país y precio) del conjunto de evaluación. Medir antes de la 039 es medir un blanco móvil.

## Clarifications

### Session 2026-11-18

- Q: ¿Jev en la nube o un modelo local? → A: Nube (TypeSafe), porque la GPU disponible no
  sostiene un modelo propio. Consecuencia asumida: la pregunta del visitante y los turnos
  enviados salen del servidor del proyecto; la llave vive solo en el entorno.
- Q: ¿Jev reemplaza la extracción de datos del router? → A: No. Jev clasifica ruta e
  intención; capacidad, cantidad, ciudad y período se conservan por la vía actual y se
  validan igual. Jev nunca rellena un dato que no se le pidió.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enrutar con Jev sin cambiar el comportamiento (Priority: P1)

El asistente clasifica la pregunta de un visitante con Jev y responde por la misma ruta, con
la misma intención y los mismos datos extraídos que hoy. Con Jev apagado, el asistente se
comporta exactamente como antes.

**Why this priority**: sin paridad con el enrutador por reglas no hay nada que medir ni que
adoptar; una regresión de ruta rompe el chat completo.

**Independent Test**: correr el conjunto de evaluación completo con Jev apagado y con Jev
encendido y comparar ruta, intención y datos extraídos pregunta por pregunta.

**Acceptance Scenarios**:

1. **Given** la configuración por defecto, **When** llega cualquier pregunta, **Then** el
   enrutador es el de reglas y no se hace ninguna llamada a Jev.
2. **Given** Jev encendido y una respuesta válida, **When** llega una pregunta, **Then** la
   ruta y la intención son valores permitidos del contrato vigente y la decisión se valida
   antes de usarse.
3. **Given** una pregunta con cantidad, capacidad, ciudad o período, **When** Jev clasifica,
   **Then** esos datos provienen de la vía actual, se validan igual y Jev no los inventa.
4. **Given** Jev apagado, **When** se corre la suite de pruebas del router y del grafo,
   **Then** no hay cambios de comportamiento ni pruebas nuevas en rojo.

---

### User Story 2 - Degradar sin bloquear el chat (Priority: P1)

Si Jev no responde, responde lento, devuelve algo inválido o no está seguro, el visitante
recibe igual su respuesta por el enrutador de reglas, con el motivo registrado. El chat nunca
se queda esperando al proveedor.

**Why this priority**: el enrutador es el primer paso de cada turno; una dependencia externa
sin degradación convierte una caída de TypeSafe en una caída del chat.

**Independent Test**: simular timeout, error del proveedor, salida incompatible y confianza
baja, y comprobar en los cuatro casos que la respuesta sale con reglas y que el motivo queda
en el registro.

**Acceptance Scenarios**:

1. **Given** una confianza por debajo del umbral, **When** Jev responde, **Then** se usa el
   enrutador de reglas y la razón de la decisión deja visible que fue por confianza.
2. **Given** un timeout del proveedor, **When** llega una pregunta, **Then** la decisión se
   toma con reglas dentro del tiempo máximo y el turno no se bloquea.
3. **Given** fallos consecutivos del proveedor, **When** se alcanza el umbral del circuito,
   **Then** se dejan de hacer llamadas durante la ventana de enfriamiento y se responde con
   reglas.
4. **Given** una salida con ruta e intención incompatibles o un valor fuera del contrato,
   **When** se valida, **Then** se descarta y se responde con reglas, sin reintentar con el
   mismo texto.
5. **Given** el proveedor recuperado después de una caída, **When** termina la ventana de
   enfriamiento, **Then** las llamadas se reanudan sin reiniciar la aplicación.

---

### User Story 3 - Saber qué sale y qué cuesta (Priority: P2)

Quien opera el servicio puede ver cuántas llamadas se hicieron, con qué confianza, con qué
latencia y cuántas cayeron a reglas, sin que el texto de las preguntas quede en los registros.
Puede apagar Jev con configuración y volver al comportamiento anterior.

**Why this priority**: es una dependencia externa con costo por turno y con datos del
visitante; sin control de costo ni de privacidad no se puede encender en producción.

**Independent Test**: correr 50 preguntas con Jev encendido y revisar los registros y las
métricas: aparecen conteos, confianza y latencia; no aparece ningún texto de pregunta ni de
turno.

**Acceptance Scenarios**:

1. **Given** Jev encendido, **When** se procesa un turno, **Then** se registran enrutador
   usado, si hubo respaldo, la confianza, la latencia y el motivo, sin el contenido.
2. **Given** una pregunta con nombre propio o preferencia personal, **When** se envía a Jev,
   **Then** el texto no queda en los registros del proyecto.
3. **Given** la llave ausente o inválida al arrancar, **When** se construye el enrutador,
   **Then** hay un error controlado, la aplicación sigue con reglas y el aviso no incluye la
   llave.
4. **Given** la llave configurada, **When** se revisan repositorio, imagen y registros,
   **Then** la llave no aparece en ninguno.
5. **Given** el límite de contexto configurado, **When** la pregunta o los turnos lo exceden,
   **Then** se envía un recorte determinista y acotado, nunca el hilo completo.

---

### User Story 4 - Decidir con evidencia (Priority: P2)

Antes de encender Jev en producción, el equipo corre la comparación de los tres enrutadores
sobre el conjunto de desarrollo y sobre un grupo ciego, y registra si Jev mejora o no. Si no
mejora, queda apagado y la 036 cierra su criterio con la medición hecha.

**Why this priority**: la 036 pide medir para elegir; encender por preferencia convierte una
dependencia y un costo en deuda sin justificación.

**Independent Test**: generar el informe de `rules`, `llm` y `decision` sobre ambos conjuntos
y verificar que incluye las mismas métricas, la fecha y el modelo usado.

**Acceptance Scenarios**:

1. **Given** los tres enrutadores configurados, **When** se corre la evaluación, **Then** el
   informe trae precisión de ruta e intención, precisión por dato, violaciones de material,
   salidas inválidas, latencia y llamadas al modelo.
2. **Given** el grupo ciego, **When** se escribe, **Then** tiene al menos 30 preguntas
   redactadas por alguien que no vio las reglas, queda versionado y no se ajusta después de
   la corrida.
3. **Given** una corrida con el proveedor caído, **When** se interpreta el informe, **Then**
   esa corrida se descarta y se repite, en vez de contarse como calidad del enrutador.
4. **Given** el resultado, **When** Jev no supera a reglas en el grupo ciego, **Then** queda
   apagado en producción y la decisión se registra en la documentación y en la evidencia de
   la 036.

---

### Edge Cases

- Pregunta vacía o de más de 500 caracteres: se rechaza antes del enrutador, como hoy; no se
  consume llamada a Jev.
- Seguimiento conversacional («¿y si son 4.500?»): si se envían turnos previos, entran en el
  límite de contexto; si no se envían, el contrato de seguimiento de las reglas sigue
  funcionando.
- Jev devuelve una intención válida para otra ruta: se considera salida inválida y se
  responde con reglas, sin corregir a mano.
- Ruta `tool` sin período reconocible: se mantiene el comportamiento actual
  (`missing_period`) aunque Jev haya propuesto `tool`.
- Proveedor lento pero dentro del tiempo: se usa su respuesta; si llega después del timeout,
  se descarta.
- Dos corridas de evaluación seguidas con modelos o versiones distintas del proveedor: el
  informe registra modelo y fecha para que no se comparen peras con manzanas.
- Catálogo de Colombia aún no implementado (spec 039): el conjunto ciego evita preguntas de
  precio colombiano hasta que la 039 esté cerrada; la corrida definitiva se repite después.
- Cuota diaria agotada: se responde 429 antes del enrutador; Jev no se llama.
- Fallo de base al consultar la cuota: 503, como hoy; el enrutador no interviene.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El enrutador MUST seguir siendo seleccionable por configuración, con el de
  reglas como valor por defecto y Jev apagado salvo activación explícita.
- **FR-002**: Con Jev activo, la decisión MUST aportar ruta e intención y MUST validarse
  contra el contrato vigente antes de usarse; lo que no cumpla se descarta.
- **FR-003**: Los datos que Jev no puede producir (capacidad, cantidad, ciudad, período,
  parte no comercial) MUST conservarse por la vía actual y validarse igual; Jev MUST NOT
  rellenar ni inventar esos datos.
- **FR-004**: MUST existir un umbral de confianza configurable; por debajo, la decisión MUST
  tomarse con reglas y el motivo MUST quedar visible en la decisión.
- **FR-005**: MUST existir un tiempo máximo de espera configurable y acotado; superado, el
  turno MUST resolverse con reglas.
- **FR-006**: Tras un número configurable de fallos consecutivos, el sistema MUST dejar de
  llamar al proveedor durante una ventana de enfriamiento y MUST reanudar sin reiniciar.
- **FR-007**: Sin llave o con llave inválida, MUST haber un error controlado al construir el
  enrutador; la aplicación MUST seguir operando con reglas.
- **FR-008**: La llave MUST leerse solo del entorno; MUST NOT aparecer en el repositorio, la
  imagen, los registros ni los mensajes de error.
- **FR-009**: El estado enviado al proveedor MUST estar limitado en número de turnos y en
  caracteres, con un recorte determinista y documentado.
- **FR-010**: Los registros MUST NOT contener el texto de la pregunta, de los turnos ni de la
  respuesta del proveedor; solo métricas y motivos.
- **FR-011**: Cada decisión MUST registrar enrutador usado, si hubo respaldo, confianza,
  latencia y motivo del respaldo, para alimentar la medición y la operación.
- **FR-012**: Las reglas de negocio existentes (materiales, precios, cuota, seguridad y
  validación de la salida) MUST NOT delegarse a Jev ni relajarse por su uso.
- **FR-013**: El informe comparativo MUST cubrir `rules`, `llm` y `decision` con las mismas
  métricas de la spec 036 (SC-006), sobre el conjunto de desarrollo y el grupo ciego.
- **FR-014**: El grupo ciego MUST tener al menos 30 preguntas, autoría independiente de las
  reglas, versión en el repositorio y MUST NOT ajustarse después de la corrida.
- **FR-015**: Jev MUST NOT encenderse en producción sin una decisión registrada y sin
  evidencia de mejora sobre el grupo ciego; si no mejora, MUST quedar apagado.
- **FR-016**: El servicio MUST registrar el número de llamadas y la latencia por corrida para
  poder estimar el costo; el presupuesto máximo es
  [NEEDS CLARIFICATION: no se definió un tope mensual ni qué hacer al alcanzarlo].
- **FR-017**: La dirección del servicio MUST ser configurable para pruebas y para un servicio
  compatible, sin cambios de código; en producción apunta a TypeSafe.
- **FR-018**: Cada corrida de evaluación MUST registrar fecha y versión o identificador del
  modelo usado, y MUST descartar corridas con el proveedor caído.
- **FR-019**: La adopción MUST quedar documentada en la constitución y en un ADR antes de
  encender el servicio en producción.
- **FR-020**: El cambio MUST NOT añadir pasos de instalación al runtime web: la dependencia
  del cliente HTTP ya existe y la llave es configuración, no paquete.

### Key Entities *(include if feature involves data)*

- **Decisión de enrutamiento**: ruta, intención, datos extraídos y motivo; es el contrato que
  ya consumen el grafo y la evaluación.
- **Respuesta del proveedor de decisión**: opciones elegidas y sus probabilidades; no es texto
  libre ni se guarda.
- **Circuito del proveedor**: estado abierto o cerrado, fallos consecutivos y momento de
  reanudación.
- **Registro de medición**: enrutador, resultado, confianza, latencia y motivo, sin contenido.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con Jev apagado, las 110 preguntas del conjunto de desarrollo conservan ruta,
  intención y datos extraídos: 0 diferencias.
- **SC-002**: El 100 % de los casos de timeout, error, salida inválida y confianza baja se
  resuelve con reglas y deja el motivo registrado.
- **SC-003**: La decisión con Jev tiene una latencia p95 de 300 ms o menos en condiciones
  normales; con el proveedor caído, ningún turno espera más de 1 s por el enrutador.
- **SC-004**: Tras una corrida de 50 preguntas, 0 apariciones del texto de pregunta o de turno
  en los registros del proyecto.
- **SC-005**: El informe comparativo cubre los tres enrutadores sobre las 110 preguntas de
  desarrollo y al menos 30 ciegas, con las métricas de la 036.
- **SC-006**: 0 apariciones de la llave en repositorio, imagen y registros, verificado por
  revisión automática y búsqueda manual.
- **SC-007**: Jev supera al enrutador de reglas en precisión de ruta sobre el grupo ciego por
  un margen de [NEEDS CLARIFICATION: no se definió el margen mínimo que justifica el costo y
  la dependencia].
- **SC-008**: El número de llamadas al proveedor con Jev apagado es 0, medido en una corrida
  completa de la suite y del conjunto de evaluación.

## Assumptions

- Existe una cuenta de TypeSafe con llave y presupuesto para las corridas de evaluación.
- El servicio se usa desde el servidor del proyecto, no desde el navegador; la llave nunca
  llega al cliente.
- El idioma de las preguntas es español (Colombia y México); otros idiomas quedan fuera.
- Jev se limita a clasificar: la respuesta al visitante la sigue generando el LLM actual.
- La extracción de datos abiertos sigue en el enrutador de reglas (ver Clarifications).
- La cuota diaria de 20 preguntas por visitante acota el peor caso de llamadas por visitante.
- La spec 039 se implementa antes de la corrida definitiva; mientras tanto la 041 queda en
  Draft.
- La observabilidad con LangSmith (spec 038) es opcional y no es requisito para medir; el
  informe de `eval-router` basta.

## Fuera de alcance

- Modelo local (OpenJev u otro) y entrenamiento o ajuste fino.
- Reemplazar el LLM que redacta la respuesta o enrutar entre modelos.
- Caché de decisiones o deduplicación de preguntas.
- Uso de Jev desde el navegador o expuesto al visitante.
- Cambios a la cuota, la identidad o la seguridad del chat.

## Dependencias y riesgos

- **036**: dueña de FR-013 y SC-006; esta spec cierra la medición con el modelo elegido. Antes
  de implementar hay que cerrar `T001`, `T002` y `T043` de la 036.
- **039**: define el catálogo y las preguntas de Colombia que usa el conjunto ciego.
- **016 y 022**: cuota y seguridad del chat se mantienen intactas.
- **Riesgo de proveedor**: caída o cambio de precio de TypeSafe; se mitiga con respaldo a
  reglas, circuito y apagado por configuración.
- **Riesgo de privacidad**: la pregunta sale del servidor; se mitiga con límite de contexto,
  registros sin contenido y aviso en la política del servicio.
- **Riesgo de sobreajuste**: el grupo ciego se escribe antes y no se toca después; si no hay
  mejora, no se enciende.

## Historial de cambios

- 2026-11-18: versión inicial en Draft. Elige Jev cloud y deja abiertos el tope de costo y el
  margen mínimo de mejora para `/speckit.clarify`.
