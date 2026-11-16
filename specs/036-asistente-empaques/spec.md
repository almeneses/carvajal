# Feature Specification: Asistente comercial de empaques para foodservice

**Feature Branch**: `036-asistente-empaques`
**Created**: 2026-11-16
**Status**: Draft
**Input**: User description: "Extender el asistente para que un restaurante, cafetería u
organizador de eventos describa lo que necesita empacar y reciba referencias de Carvajal
Empaques técnicamente válidas para ese uso, con cantidad en cajas, precio de referencia
donde exista y el canal para comprar; el enrutamiento debe distinguir esta intención de las
preguntas documentales y de indicadores, y poder compararse entre reglas, un LLM y un modelo
de decisión."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recibir una recomendación técnicamente válida (Priority: P1)

Una persona que administra una cafetería escribe en el chat qué necesita («vasos para café
de 12 oz», «contenedores para sopa que se puedan calentar en microondas»). El asistente
identifica el uso, la temperatura y la capacidad, y recomienda referencias del catálogo de
Carvajal Empaques cuyo material es apto para ese uso según la guía de materiales. Cada
recomendación muestra la ficha de origen y la fecha en que se capturó.

**Why this priority**: es el producto mínimo. Una recomendación que pone un vaso de
poliestireno para bebida caliente es un error objetivo y destruye la confianza; sin
validación técnica el asistente no es defendible.

**Independent Test**: preguntar «necesito vasos para café caliente de 12 oz» y comprobar
que ninguna referencia sugerida es de un material no apto para contenido caliente y que
cada una cita su ficha con fecha.

**Acceptance Scenarios**:

1. **Given** una solicitud para bebida o comida caliente, **When** se recomienda, **Then**
   ninguna referencia es de un material que la guía marca como no apto para contenido
   caliente.
2. **Given** una solicitud que exige calentar en microondas, **When** se recomienda,
   **Then** solo aparecen materiales que la guía declara aptos para microondas.
3. **Given** una solicitud a la que le falta el uso o la temperatura, **When** se envía,
   **Then** el asistente pregunta por el dato que falta en lugar de suponerlo.
4. **Given** una solicitud que ninguna referencia del catálogo cumple, **When** se envía,
   **Then** el asistente lo dice y no ofrece una alternativa que viole la regla.
5. **Given** una preferencia explícita por materiales renovables o compostables, **When**
   se recomienda, **Then** se priorizan las referencias de papel o fibra de caña y se
   indica si ninguna cumple el resto de requisitos.

---

### User Story 2 - Calcular cantidad y precio de referencia (Priority: P1)

La misma persona indica cuántas unidades necesita («unas 5.000»). El asistente calcula
cuántas cajas comprar según las unidades por caja de cada referencia y, cuando el catálogo
tiene precio (tienda de México), muestra el precio de referencia con su moneda, si incluye
impuestos y la fecha de captura. Nunca promete disponibilidad ni presenta la cifra como
oferta comercial.

**Why this priority**: sin cantidad y costo la recomendación no sirve para decidir una
compra; es la diferencia entre un buscador y un asesor.

**Independent Test**: pedir 5.000 vasos de una referencia de 1.000 unidades por caja y
comprobar que el resultado es 5 cajas y que el precio mostrado coincide con el de la ficha
y lleva fecha.

**Acceptance Scenarios**:

1. **Given** una cantidad en unidades, **When** se calcula, **Then** el número de cajas se
   redondea hacia arriba y se informa el excedente de unidades.
2. **Given** una referencia con precio en el catálogo, **When** se cotiza, **Then** el
   total es cajas × precio por caja, con moneda, nota de impuestos y fecha de captura.
3. **Given** una referencia sin precio (por ejemplo, del catálogo de Colombia), **When** se
   pide el costo, **Then** el asistente declara que no tiene precio y no lo estima.
4. **Given** cualquier cotización, **When** se muestra, **Then** incluye el aviso de que es
   un precio de referencia y no confirma existencias ni condiciones de venta.

---

### User Story 3 - Saber dónde comprar (Priority: P2)

Después de la recomendación, la persona pregunta dónde comprar. Si está en México, el
asistente remite a la tienda en línea de la referencia. Si está en Colombia, lista los
distribuidores publicados para su ciudad.

**Why this priority**: cierra el recorrido de compra; sin canal la recomendación queda en
el aire, pero el valor central (recomendar bien) ya existe sin ella.

**Independent Test**: indicar «estoy en Medellín» y comprobar que se listan los
distribuidores de Medellín del directorio publicado, con su fuente.

**Acceptance Scenarios**:

1. **Given** un país México, **When** se pregunta dónde comprar, **Then** se ofrece el
   enlace de la ficha en la tienda en línea.
2. **Given** una ciudad de Colombia con distribuidores publicados, **When** se pregunta,
   **Then** se listan con los datos que publica el directorio y su fuente.
3. **Given** una ciudad sin distribuidores publicados, **When** se pregunta, **Then** se
   informa y se mencionan las ciudades que sí tienen.
4. **Given** que el país no se ha indicado, **When** se pide precio o canal, **Then** el
   asistente pregunta el país antes de responder.

---

### User Story 4 - Enrutar sin romper lo existente (Priority: P2)

El asistente distingue una solicitud comercial de empaques de las demás intenciones:
una pregunta institucional sobre Carvajal Empaques («¿en qué países tiene plantas?») sigue
yendo a la respuesta documental; una pregunta de indicadores sigue yendo a la herramienta
de indicadores; un saludo sigue siendo directo. Las necesidades industriales o
agroindustriales a la medida, que no tienen ficha, se derivan a contacto comercial.

**Why this priority**: la capacidad nueva no puede degradar las rutas que ya funcionan; el
enrutamiento es además el objeto de estudio del curso.

**Independent Test**: ejecutar el conjunto de evaluación de enrutamiento y comprobar que
las preguntas existentes conservan su ruta y que las comerciales se clasifican como tales.

**Acceptance Scenarios**:

1. **Given** una pregunta de compra o especificación de empaques, **When** se enruta,
   **Then** va a la ruta comercial con los datos extraídos.
2. **Given** una pregunta institucional que menciona empaques, **When** se enruta, **Then**
   va a la ruta documental y responde con citas del corpus.
3. **Given** una necesidad de empaque a la medida (industrial o agroindustrial), **When**
   se enruta, **Then** se informa que requiere asesoría comercial y no se recomienda una
   referencia de catálogo.
4. **Given** una pregunta mixta («¿qué vaso me sirve y cuánto vendió Carvajal en 2024?»),
   **When** se envía, **Then** el asistente atiende la parte comercial y avisa que la otra
   requiere una pregunta aparte.

---

### User Story 5 - Comparar estrategias de enrutamiento (Priority: P3)

Quien evalúa el sistema ejecuta el mismo conjunto de preguntas etiquetadas con tres
enrutadores intercambiables —reglas deterministas, un LLM con salida estructurada y un
modelo de decisión especializado— y obtiene una tabla comparable de precisión, extracción
de datos, latencia y costo.

**Why this priority**: es el aporte de evaluación del proyecto, pero el asistente funciona
sin él; depende de que las historias 1 a 4 existan.

**Independent Test**: ejecutar la orden de evaluación con cada enrutador y comprobar que
produce un informe con las mismas métricas para los tres.

**Acceptance Scenarios**:

1. **Given** el conjunto etiquetado, **When** se evalúa un enrutador, **Then** el informe
   reporta precisión de ruta, precisión por dato extraído, latencia media y costo estimado.
2. **Given** un enrutador externo no disponible, **When** se evalúa, **Then** se reporta
   como no disponible y los demás terminan.
3. **Given** cualquier enrutador, **When** se usa en producción, **Then** la decisión queda
   registrada con la ruta, los datos extraídos y el enrutador que la tomó.

---

### Edge Cases

- Capacidad en mililitros en lugar de onzas: se convierte antes de filtrar.
- Cantidad ambigua («una caja», «mil vasos»): «una caja» se interpreta como una caja de la
  referencia elegida; si no hay referencia elegida, se pregunta.
- Bebida «tibia» o temperatura no declarada: se pregunta en lugar de suponer frío.
- Precio capturado con oferta vigente hasta una fecha ya vencida: se muestra el precio
  regular y se advierte que la oferta pudo terminar.
- Referencia cuyo material no está en la guía (papel, pulpa): se valida con la temperatura
  y el uso que declara su propia ficha; si la ficha no declara ninguno de los dos, se excluye
  de las recomendaciones con restricción de temperatura. El material queda listado en el
  manifiesto del catálogo.
- Ficha con datos contradictorios (título contra descripción, temperatura contra uso
  declarado): se usa el dato coherente, se descarta el dudoso y la contradicción queda
  anotada en el manifiesto.
- Cantidades muy grandes (más de 100 cajas): se calcula, pero se sugiere contacto
  comercial por volumen.
- Petición de hacer el pedido o pagar: el asistente aclara que no procesa pedidos y remite
  al canal de compra.
- Referencias duplicadas entre la tienda y el catálogo PDF: se muestran una sola vez.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema MUST reconocer una solicitud comercial de empaques y extraer, cuando
  existan, uso, temperatura del contenido, capacidad, cantidad, país y ciudad.
- **FR-002**: El sistema MUST pedir aclaración cuando falte el uso o la temperatura para
  recomendar, o el país para cotizar o indicar canal; nunca MUST suponerlos.
- **FR-003**: Toda referencia recomendada MUST cumplir las reglas de la guía de materiales
  para el uso y la temperatura solicitados. Si la guía no cubre su material, MUST cumplir la
  temperatura y el uso declarados en su propia ficha.
- **FR-004**: El cálculo de cajas MUST hacerse con aritmética determinista, redondeando
  hacia arriba; el modelo de lenguaje no MUST calcular cantidades ni totales.
- **FR-005**: Todo precio mostrado MUST incluir moneda, nota de impuestos y fecha de
  captura, y el aviso de precio de referencia.
- **FR-006**: El sistema MUST declarar la ausencia de precio en lugar de estimarlo.
- **FR-007**: Cada referencia mostrada MUST citar su fuente (URL de la ficha o documento) y
  su fecha de captura.
- **FR-008**: El sistema MUST indicar el canal de compra según el país: tienda en línea
  para México y distribuidores publicados por ciudad para Colombia.
- **FR-009**: Las rutas existentes (directa, documental e indicadores) MUST conservar su
  comportamiento para las preguntas que ya cubren.
- **FR-010**: Las necesidades a la medida sin ficha de catálogo MUST derivarse a contacto
  comercial sin recomendar referencias.
- **FR-011**: El sistema no MUST procesar pedidos, pagos ni datos de facturación.
- **FR-012**: La decisión de enrutamiento MUST registrarse con la ruta, los datos extraídos
  y el enrutador usado.
- **FR-013**: El enrutador MUST ser intercambiable sin modificar las rutas ni las
  herramientas.
- **FR-014**: Existe un conjunto de evaluación versionado con preguntas comerciales y no
  comerciales etiquetadas con ruta, datos esperados y restricciones de la referencia
  correcta.
- **FR-015**: El catálogo MUST poder regenerarse a partir del corpus capturado con una
  orden reproducible, conservando la procedencia de cada fila.

### Key Entities *(include if feature involves data)*

- **Referencia de empaque**: producto vendible con identificador, nombre, familia (vaso,
  tapa, contenedor, charola, cubierto, envase, plato), uso, capacidad, material, marca,
  unidades por paquete y por caja, temperatura de uso declarada en la ficha, precio, moneda
  y vigencia cuando existen, país, fuente y fecha de captura.
- **Regla de material**: material con su rango de temperatura, aptitud para microondas,
  refrigeración, congelación y contenido caliente, código de reciclaje y aplicaciones
  recomendadas.
- **Distribuidor**: empresa publicada en el directorio, con ciudad, datos de contacto
  publicados, fuente y fecha de captura.
- **Solicitud comercial**: datos extraídos de la conversación (uso, temperatura,
  capacidad, cantidad, país, ciudad, preferencia ambiental) y los que faltan.
- **Recomendación**: referencias válidas, cajas, total cuando aplica, canal de compra y
  fuentes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 % de recomendaciones que violan una regla de material en el conjunto de
  evaluación.
- **SC-002**: 100 % de los cálculos de cajas y totales coinciden con el cálculo esperado.
- **SC-003**: 100 % de los precios mostrados llevan moneda, nota de impuestos y fecha.
- **SC-004**: Al menos 90 % de precisión de ruta en el conjunto de evaluación para el
  enrutador por defecto.
- **SC-005**: Ninguna regresión en las pruebas existentes del router y del grafo ni en la
  evaluación de recuperación de `data/eval/`.
- **SC-006**: El informe comparativo cubre los tres enrutadores con las mismas métricas.

## Assumptions

- El alcance es foodservice y mesa (vasos, tapas, contenedores, charolas, cubiertos,
  envases). Industrial y agroindustrial a la medida quedan fuera: se derivan.
- Solo la tienda de México publica precios; Colombia se recomienda sin precio. Perú y El
  Salvador quedan fuera por falta de fichas.
- Los datos provienen del corpus capturado el 2026-10-23; los precios son de esa fecha y
  la actualización periódica queda fuera de alcance.
- La guía de materiales es la autoridad técnica; si una ficha contradice la guía, manda la
  guía y la contradicción se registra. La guía solo cubre PP, EPS, PS, PET y aluminio; para
  papel y pulpa la autoridad es la ficha del producto.
- El asistente no sustituye al asesor comercial ni vincula a Carvajal con ningún precio.
- El modelo de decisión especializado es opcional: el asistente funciona con el enrutador
  por reglas.

## Historial de cambios

- 2026-11-16 (PR 2, #135): al curar los datos se encontró que la guía de materiales no cubre
  papel ni pulpa, que son el material de casi todos los vasos para bebida caliente. Se
  ajustan FR-003, un caso borde y un supuesto para validar esos materiales con su ficha, y
  se añade el caso de fichas contradictorias.
- 2026-11-16 (PR 4): al implementar FR-002 se precisa cómo se aplica, sin cambiar el
  requisito. La temperatura es el dato que decide el material: se toma de la pregunta o se
  deduce del uso declarado («café» → caliente, «microondas» → caliente), y el uso se deduce
  de la familia y la temperatura. Si no se puede obtener la temperatura, el router pide
  aclaración. La falta de país no bloquea la recomendación: el nodo recomienda sin precio y
  pide el país para cotizar e indicar el canal.
- 2026-11-16 (PR 6): casos límite de US4. Una pregunta que nombra a Carvajal y un producto
  sin señal de compra ni datos concretos es institucional (`rag`). La derivación de FR-010
  usa los únicos canales publicados en el corpus (tienda de México y directorio de
  distribuidores de Colombia), porque no hay un contacto comercial de Empaques aparte. En
  una pregunta mixta se atiende la parte comercial y la otra se pide en un mensaje aparte.
- 2026-11-16 (PR 7): el modelo de decisión se carga desde `DECISION_MODEL` en lugar de un
  extra de instalación, porque todavía no se eligió el paquete (Jev, GLiNER2.5-Decide u
  otro). Los enrutadores LLM y de decisión caen al de reglas si su salida no cumple el
  contrato, y la razón lo deja visible para la evaluación.
