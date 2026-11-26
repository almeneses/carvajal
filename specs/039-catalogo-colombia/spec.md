# Feature Specification: Catálogo de empaques de Colombia con precios de referencia

**Feature Branch**: `039-catalogo-colombia`
**Created**: 2026-11-17
**Status**: Implemented
**Input**: User description: "Hacer la ampliación del catálogo para incluir productos y
precios en Colombia."

## Contexto

La spec 036 dejó el catálogo con 52 referencias de la tienda de México, con precio, y el
directorio de 29 distribuidores de Colombia. Las referencias de Colombia quedaron pendientes
(T009: el catálogo PDF, de unas 180 referencias, sin precio). Por eso hoy un restaurante de
Bogotá recibe referencias mexicanas y ningún precio. El sitio de Carvajal Empaques Colombia
no publica precios: la ampliación necesita una fuente de precios colombiana explícita y
fechada.

Esta spec **mantiene** la regla de la 036 FR-006: si no hay precio publicado en Colombia, se
declara la ausencia y se remite a los distribuidores. No se convierte ni se estima un precio
desde México: cada país tiene sus propias referencias y sus propios precios.

## Clarifications

### Session 2026-11-17

- Q: ¿De dónde salen los precios de Colombia? → A: De las tiendas en línea públicas de los
  distribuidores del directorio y de comercios que publiquen precio. Si no hay precio
  colombiano, no se muestra precio.

### Session 2026-11-18

- Q: ¿Se estima el precio de una referencia colombiana convirtiendo el de una mexicana? →
  A: No. Se descarta la conversión entre países: una referencia de Colombia solo muestra
  precios publicados para Colombia, con su fuente y su fecha. Sin precio colombiano, se
  declara la ausencia y se listan los distribuidores. La regla de la 036 FR-006 queda
  vigente sin excepciones.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recibir referencias que se venden en Colombia (Priority: P1)

Una persona que administra una cafetería en Cali pide «vasos para café de 12 oz». El
asistente recomienda referencias del portafolio de Colombia, validadas con la misma guía de
materiales, cada una con su ficha o página del catálogo y la fecha de captura. Ya no recibe
referencias que solo existen en México.

**Why this priority**: sin referencias de Colombia, el asistente no sirve al mercado de
origen de la empresa; es la base sobre la que se pone el precio.

**Independent Test**: preguntar «vasos para café caliente de 12 oz en Cali» y comprobar que
todas las referencias son de Colombia, cumplen la regla de material y citan su fuente con
fecha.

**Acceptance Scenarios**:

1. **Given** una solicitud con país Colombia o una ciudad colombiana, **When** se
   recomienda, **Then** solo aparecen referencias del portafolio de Colombia.
2. **Given** una referencia de Colombia, **When** se muestra, **Then** cita la página del
   catálogo o la ficha de origen y su fecha de captura.
3. **Given** una solicitud de Colombia que ninguna referencia colombiana cumple, **When**
   se envía, **Then** el asistente lo dice y no ofrece referencias de México como si se
   vendieran en Colombia.
4. **Given** una referencia colombiana, **When** se valida, **Then** se aplican las mismas
   reglas de material y temperatura que en México.

---

### User Story 2 - Ver un precio de referencia en pesos colombianos (Priority: P1)

La misma persona pide 5.000 vasos. El asistente calcula las cajas y, cuando hay precio para
esa referencia en Colombia, muestra el total en pesos colombianos (COP), si incluye IVA, la
fecha del precio y quién lo publica. Si no hay precio, lo dice y remite a los distribuidores
de su ciudad.

**Why this priority**: el precio es lo que convierte la recomendación en una decisión de
compra; es la razón principal de la ampliación.

**Independent Test**: pedir 5.000 unidades de una referencia con precio colombiano y comprobar
el número de cajas, el total en COP, la nota de IVA, la fecha y la fuente; y pedir una
referencia sin precio colombiano y comprobar que declara la ausencia, no convierte desde
México y lista los distribuidores.

**Acceptance Scenarios**:

1. **Given** una referencia con precio en Colombia, **When** se cotiza, **Then** el total
   es cajas × precio por caja en COP, con nota de IVA, fecha y fuente del precio.
2. **Given** una referencia sin precio colombiano, **When** se pide el costo, **Then** el
   asistente declara que no tiene precio, no lo estima ni lo convierte desde México y lista
   los distribuidores de la ciudad.
3. **Given** un precio publicado por un tercero (distribuidor o comercio), **When** se
   muestra, **Then** queda identificado como precio de ese tercero y no como precio de
   Carvajal.
4. **Given** un precio con más de 90 días desde su captura, **When** se muestra, **Then** se
   advierte que puede estar desactualizado.
5. **Given** cualquier cotización, **When** se muestra, **Then** incluye el aviso de precio
   de referencia sin confirmación de existencias ni condiciones de venta.

---

### User Story 3 - Saber a quién comprarle en mi ciudad (Priority: P2)

Después de la cotización, la persona ve los distribuidores publicados para su ciudad y, si
el precio vino de un distribuidor, ese distribuidor aparece identificado.

**Why this priority**: une el precio con el canal; mejora la utilidad, pero la spec 036 ya
lista distribuidores por ciudad.

**Independent Test**: cotizar en Medellín y comprobar que aparecen los distribuidores de
Medellín y, si el precio es de uno de ellos, cuál.

**Acceptance Scenarios**:

1. **Given** una ciudad con distribuidores, **When** se cotiza, **Then** se listan con los
   datos de contacto publicados (sin nombres de personas ni correos personales).
2. **Given** una ciudad sin distribuidores, **When** se cotiza, **Then** se indican los de
   la ciudad más cercana publicada o el canal nacional, y se dice que no hay uno local.

---

### User Story 4 - Actualizar productos y precios de forma reproducible (Priority: P3)

El equipo regenera el catálogo de Colombia con una orden, a partir de las fuentes
capturadas, y obtiene un manifiesto con cuántas filas se agregaron, cambiaron o se
descartaron y por qué.

**Why this priority**: los precios caducan; sin regeneración reproducible el catálogo se
vuelve una foto vieja. No bloquea la primera entrega.

**Independent Test**: regenerar el catálogo dos veces sobre las mismas fuentes y obtener el
mismo resultado; cambiar un precio en la fuente y ver la diferencia en el manifiesto.

**Acceptance Scenarios**:

1. **Given** las mismas fuentes, **When** se regenera, **Then** el catálogo resultante es
   idéntico.
2. **Given** un precio cambiado en la fuente, **When** se regenera, **Then** el manifiesto
   registra el valor anterior, el nuevo y la fecha.
3. **Given** una fila sin datos mínimos (unidades por caja, material o capacidad), **When**
   se regenera, **Then** se descarta con el motivo en el manifiesto.
4. **Given** una tienda cuyo `robots.txt` prohíbe la captura o no publica precio, **When**
   se regenera, **Then** esa tienda no se captura y el manifiesto registra el motivo.

---

### Edge Cases

- La misma referencia existe en México y Colombia con códigos distintos: se tratan como
  referencias separadas por país; no se cruzan precios.
- Referencia colombiana sin precio colombiano: no se usa el precio de México aunque exista una
  referencia parecida; se declara la ausencia y se listan los distribuidores.
- Precio publicado por unidad, por paquete o por caja: se normaliza a precio por caja con
  las unidades de la ficha; si no se puede normalizar, no se muestra precio.
- Varias fuentes con precios distintos para la misma referencia: se muestra cada precio con
  su fuente y su fecha; no se promedia.
- Precio sin IVA o sin indicación de IVA: se muestra la nota «IVA no indicado» en lugar de
  suponer.
- Capacidad en el catálogo PDF en mililitros o centímetros cúbicos: se convierte a onzas
  antes de filtrar.
- Página del catálogo PDF ilegible o sin capa de texto: se transcribe a mano y la fila queda
  marcada como curada a mano en el manifiesto.
- Persona en Colombia que pide precio en dólares o pesos mexicanos: se responde en COP con el
  precio colombiano si existe; no se convierte moneda.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El catálogo MUST incluir las referencias foodservice del portafolio de
  Colombia con los mismos campos que las de México, con país CO.
- **FR-002**: Las recomendaciones para Colombia MUST usar solo referencias de Colombia.
- **FR-003**: Las reglas de material de la spec 036 MUST aplicarse sin cambios a las
  referencias de Colombia.
- **FR-004**: Todo precio de Colombia MUST mostrarse en COP, con nota de IVA, fecha de
  captura, fuente identificada y aviso de precio de referencia.
- **FR-005**: El sistema MUST distinguir la procedencia del precio: oficial de Carvajal o de
  un tercero, nombrando al tercero.
- **FR-006**: El sistema MUST declarar la ausencia de precio cuando no hay un precio
  colombiano publicado, MUST NOT estimar ni convertir un precio desde México u otro país,
  y MUST NOT promediar precios entre fuentes.
- **FR-007**: Los precios con más de 90 días desde su captura MUST mostrarse con la
  advertencia de posible desactualización.
- **FR-008**: El cálculo de cajas y totales MUST ser determinista, igual que en la spec 036.
- **FR-009**: El catálogo de Colombia MUST regenerarse con una orden reproducible que
  conserve la procedencia por fila y produzca un manifiesto de cambios y descartes.
- **FR-010**: Los datos de distribuidores MUST seguir excluyendo nombres de personas y
  correos personales.
- **FR-011**: La captura de precios desde sitios web MUST cumplir la captura responsable
  (robots.txt, `Crawl-delay`, rate limit, sin formularios ni sesiones autenticadas).
- **FR-012**: El conjunto de evaluación del enrutador y del catálogo MUST incluir preguntas
  de Colombia con la referencia y el total esperados.

### Key Entities *(include if feature involves data)*

- **Referencia de empaque (CO)**: la misma entidad de la spec 036, con país CO, página del
  catálogo o ficha de origen y fecha de captura.
- **Precio de referencia**: valor por caja, moneda COP, IVA incluido / no incluido / no
  indicado, fuente (oficial o tercero con nombre), URL o documento, fecha de captura.
- **Fuente de precios**: origen publicado de precios para Colombia, con su tipo, su permiso
  de captura (robots.txt) y su frecuencia de actualización esperada.
- **Manifiesto del catálogo**: filas agregadas, cambiadas, descartadas y curadas a mano,
  con motivos.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de las referencias foodservice del catálogo PDF de Colombia tiene
  destino: queda en el catálogo o se descarta con su motivo en el manifiesto. De ellas, al
  menos el 90 % queda en el catálogo.
- **SC-002**: 0 % de recomendaciones para Colombia con referencias de otro país o que violen
  una regla de material en el conjunto de evaluación.
- **SC-003**: 100 % de los precios de Colombia mostrados llevan COP, nota de IVA, fecha y
  fuente.
- **SC-003a**: 0 precios de Colombia estimados o convertidos desde México u otro país.
- **SC-004**: 100 % de los totales coinciden con el cálculo esperado en las preguntas de
  evaluación de Colombia (al menos 20 preguntas nuevas).
- **SC-005**: Ninguna regresión en las preguntas de México ni en las pruebas existentes del
  catálogo y del enrutador.

## Assumptions

- El alcance sigue siendo foodservice y mesa, como en la spec 036.
- La fuente de productos son las fichas oficiales de Carvajal Empaques Colombia
  (`colombia.carvajalempaques.com`), cruzadas con los productos de los distribuidores (T009 de
  la spec 036, que esta spec absorbe).
- Los precios provienen de las tiendas en línea públicas de los distribuidores del
  directorio y de comercios colombianos que publiquen precio, capturados con las reglas de
  captura responsable. Si no hay precio colombiano, no se muestra precio y se listan los
  distribuidores. No se convierte ni se estima desde México. Si un distribuidor no tiene
  tienda en línea o su `robots.txt` lo prohíbe, no se captura.
- Los precios son referencias fechadas; la actualización periódica automática queda fuera
  de alcance: se regenera con la orden de US4.
- El asistente no procesa pedidos ni pagos y no vincula a Carvajal ni a los distribuidores
  con ningún precio.

## Historial de cambios

- 2026-11-18: se elimina la estimación por conversión desde México (FR-006a, FR-006b y
  SC-003a). Una referencia colombiana solo muestra precios publicados para Colombia; si no
  los hay, se declara la ausencia y se listan los distribuidores. La regla de la 036 FR-006
  queda vigente sin excepciones.
- 2026-11-18 (implementación): las referencias de Colombia salen de las fichas oficiales de
  `colombia.carvajalempaques.com` y no del catálogo PDF, que no estaba disponible; los precios,
  de la tienda de EDEXA, distribuidor del directorio. Se ajusta el supuesto de la fuente de
  productos; los requisitos no cambian. A pedido del equipo, el catálogo de México se amplía de
  52 a 98 referencias con las fichas de la tienda que no estaban en el corpus.
- 2026-11-25 (enmienda, uso académico): las 98 referencias de México se copian a Colombia como
  `CO-MX-<sku>` con el precio convertido a COP. FR-006 y SC-003a siguen rigiendo para las 102
  referencias propias; para las copias, el precio lleva procedencia `sistema`, se cotiza en Bogotá y en 2 ciudades más
  y se presenta como «precio de referencia del catálogo». Ver `CONSTITUTION.md` §9.
