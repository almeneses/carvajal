# Feature Specification: Cotizador conversacional de productos Carvajal

**Feature Branch**: `042-cotizador`
**Created**: 2026-11-21
**Status**: Prototipo implementado en la rama `prototype` (2026-11-21), con streaming SSE; pendientes T039, T040, T041 y T046 de [`tasks.md`](./tasks.md).
**Input**: User description: "Agregar una sección Cotizador con la misma interfaz del chat,
dedicada solo a productos Carvajal: alguien escribe «cotízame 20 vasos para bebida
caliente» y el chat responde si lo tiene, el precio, la cantidad solicitada, el costo
total y el proveedor o proveedores donde puede comprarlo; si hay localización, mostrar un
mapa; si pide domicilio, decir el tiempo o la fecha estimada de entrega; y ofrecer
alternativas similares si las hay." Plan de referencia:
[`docs/plans/0002-cotizador-preliminar.md`](../../docs/plans/0002-cotizador-preliminar.md).

## Clarifications

### Sesión 2026-11-21 (preliminar, a confirmar en `/speckit.clarify`)

- Mercado inicial: **Colombia, precios en COP y línea de empaques**. Otras líneas de
  Carvajal y otros países quedan fuera hasta que exista un catálogo curado.
- La sección es **exclusiva**: una consulta ajena a la cotización se deriva al chat
  general; no se responde aquí con datos documentales ni financieros.
- La cotización usa **presentaciones comerciales conocidas** (unidad, paquete o caja).
  El sistema no supone que un proveedor venda unidades sueltas.
- «Lo tiene» significa **existencia conocida**, no inventario confirmado en tiempo real.
  Sin existencia conocida, la respuesta lo declara; no se promete disponibilidad.
- La demo usa **datos simulados explícitos**, separados del catálogo curado y activados
  por una bandera; nunca se presentan como stock, oferta ni plazo de una empresa real.
- La cotización completa exige **ciudad**: sin ella el Cotizador la pide antes de mostrar
  precios y proveedores.
- Las ventanas de entrega de la demo se calculan en **días hábiles con los festivos de
  Colombia**, además del horario de corte del proveedor.
- El mapa se incrusta en el hilo (OpenStreetMap) centrado en la sede de la oferta y no
  reemplaza dirección ni teléfono. Sin conexión o sin coordenadas, la respuesta conserva
  esos datos en texto.
- No hay aviso general de «modo demo» en la sección: la procedencia se marca en cada dato
  simulado (precio, existencia, producto) y en la entrega.
- El prototipo entrega el turno por SSE (texto y tarjetas) y un mapa incrustado de
  OpenStreetMap con la sede de la oferta; la CSP habilita solo ese origen en `frame-src`.
- La memoria es silenciosa y por reglas: solo se guarda lo que coincide con un patrón
  conocido, asociado a la cookie firmada, con caducidad de 180 días y borrado a pedido.
- Los productos reales se leen del catálogo curado; la demo solo añade 48 referencias
  simuladas y las condiciones comerciales. Cada oferta tiene una ficha Markdown citable.
- La cotización se registra con identificador y vigencia de 7 días; el registro no se
  recalcula, guarda lo que se mostró.
- Toda la cotización —tarjetas de producto, oferta, proveedores, mapa y domicilio— se
  resuelve **inline dentro del hilo del chat**, con los tokens del mockup institucional
  (`prompt-carvajal-ui-institucional.md`): DM Sans, azul `#003A5D`, cian `#00B5E5`,
  paneles blancos, bordes `#D8E5ED` y esquinas de 14–17 px. No hay pantalla aparte ni panel
  lateral de cotización.

Pendiente de decisión antes del plan:

- ~~¿La estimación de entrega de la demo usa días hábiles con festivos de Colombia o solo
  días calendario?~~ **Resuelto:** días hábiles con los festivos de Colombia.
- ~~¿Se admite cotizar sin ciudad cuando el producto tiene precio pero los proveedores
  dependen de la ubicación?~~ **Resuelto:** no. El Cotizador pide la ciudad antes de
  presentar la cotización completa; sin ella no muestra precios ni proveedores.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cotizar un producto desde la conversación (Priority: P1)

Quien atiende una cafetería escribe «cotízame 20 vasos para bebida caliente». El Cotizador
identifica el producto y el uso, busca referencias compatibles y responde con una opción
principal: referencia, presentación que debe comprar, unidades solicitadas, unidades que
recibe, sobrantes, precio unitario de la presentación, subtotal y total conocido, más los
proveedores donde puede comprarlo. Ofrece hasta dos alternativas similares e indica en qué
se diferencian.

**Why this priority**: es el producto mínimo pedido. Sin producto, precio, cantidad y
proveedor no hay cotización.

**Independent Test**: enviar «cotízame 20 vasos para bebida caliente» y comprobar que la
respuesta muestra, como mínimo, la referencia, las unidades solicitadas, la presentación
comprable, los sobrantes, el precio con moneda e impuestos declarados, el total conocido y
al menos un proveedor, o el motivo por el que falta cada dato.

**Acceptance Scenarios**:

1. **Given** una solicitud con producto, uso y cantidad, **When** se cotiza, **Then** la
   respuesta distingue unidades solicitadas, cantidad comprable en presentaciones enteras y
   sobrantes, sin presentar las solicitadas como unidades que el proveedor vende sueltas.
2. **Given** una solicitud de 20 unidades y una presentación de 50, **When** se cotiza,
   **Then** propone una presentación, informa 30 unidades sobrantes y calcula el subtotal
   sobre 50 unidades, no sobre 20.
3. **Given** un producto con varias ofertas, **When** se cotiza, **Then** muestra cada
   oferta con su proveedor, sede, precio, impuestos y fecha del dato, sin promediarlas ni
   sumar monedas distintas.
4. **Given** un producto sin precio publicado ni simulado, **When** se cotiza, **Then**
   declara que no hay precio y no lo estima, no lo convierte desde otro país y no lo
   promedia.
5. **Given** un producto agotado o con stock insuficiente para la cantidad comprable,
   **When** se cotiza, **Then** no afirma que está disponible y ofrece alternativas o el
   canal de contacto.
6. **Given** un producto que las reglas técnicas descartan para el uso pedido, **When** se
   cotiza, **Then** no se ofrece como opción y se explica el descarte sin relajar la regla.
7. **Given** una solicitud sin cantidad, **When** se cotiza, **Then** puede mostrar el
   precio de la presentación y pide la cantidad antes de afirmar un total.
8. **Given** una solicitud ambigua sobre el producto, **When** se procesa, **Then** pregunta
   por el dato que falta en lugar de suponer la referencia.
9. **Given** una solicitud sin ciudad, **When** se cotiza, **Then** pide la ciudad antes de
   mostrar precios, totales y proveedores, y conserva producto y cantidad para cuando la
   indique.

---

### User Story 2 - Saber qué dato está confirmado, cuál falta y cuál es simulado (Priority: P1)

La persona debe poder distinguir un dato publicado por una fuente real, un dato ausente y
un dato de demostración. La sección lo advierte al entrar y cada dato simulado queda
identificado donde aparece.

**Why this priority**: presentar inventario, envío o plazo simulados como reales induce a
una compra equivocada; la confianza es parte del producto mínimo.

**Independent Test**: revisar una respuesta con datos de demo y otra sin demo y comprobar
que la primera muestra el aviso de simulación y que la segunda declara la ausencia en lugar
de inventar valores.

**Acceptance Scenarios**:

1. **Given** el modo demo activo, **When** se abre el Cotizador, **Then** no hay aviso
   general y cada dato simulado se identifica en la tarjeta donde aparece.
2. **Given** una respuesta que mezcla un precio real y un stock simulado, **When** se
   muestra, **Then** cada dato identifica su procedencia.
3. **Given** el modo demo desactivado, **When** se cotiza un producto sin datos de
   existencia o entrega, **Then** se declara que no se conocen y no se rellena el hueco.

---

### User Story 3 - Ver los proveedores en un mapa (Priority: P2)

Cuando la cotización incluye proveedores con ubicación conocida, la persona ve un mapa con
un marcador por sede, puede identificar cada marcador con su proveedor y abrir la dirección
o el enlace de mapas.

**Why this priority**: pedido explícito; ayuda a decidir dónde comprar, pero la cotización
sigue siendo útil sin mapa.

**Independent Test**: cotizar en una ciudad con proveedores y comprobar que hay un marcador
por sede, que cada marcador nombra su proveedor y que la dirección y el teléfono siguen
visibles como texto.

**Acceptance Scenarios**:

1. **Given** proveedores con coordenadas conocidas, **When** se muestra la cotización,
   **Then** el mapa presenta un marcador por sede y una lista equivalente en texto.
2. **Given** proveedores con dirección pero sin coordenadas, **When** se muestra la
   cotización, **Then** se listan nombre, dirección y teléfono y se indica que no hay
   ubicación en el mapa.
3. **Given** que el mapa no carga, **When** se muestra la cotización, **Then** la lista de
   proveedores sigue disponible y no se pierde información.
4. **Given** una ciudad sin proveedores, **When** se cotiza, **Then** se dice que no hay
   sedes registradas y se listan las ciudades que sí tienen.

---

### User Story 4 - Estimar la entrega a domicilio (Priority: P2)

Si la persona pide domicilio, el Cotizador pregunta el destino si falta y responde con la
cobertura, el costo de envío conocido y una ventana estimada de entrega; si no puede
calcularla, explica qué falta.

**Why this priority**: pedido explícito; agrega valor a la decisión de compra sin bloquear
la cotización básica.

**Independent Test**: pedir «con domicilio» sin destino, comprobar que lo pregunta; indicar
una ciudad con cobertura y comprobar que muestra zona, envío y ventana, o el motivo de la
ausencia.

**Acceptance Scenarios**:

1. **Given** una solicitud de domicilio sin destino, **When** se procesa, **Then** pregunta
   ciudad o zona y no inventa un plazo.
2. **Given** un destino con cobertura conocida, **When** se cotiza, **Then** muestra envío
   y ventana estimada, y aclara que la estimación no garantiza la entrega.
3. **Given** un destino sin cobertura conocida, **When** se cotiza, **Then** lo declara y
   ofrece retiro en sede o contacto con el proveedor.
4. **Given** un pedido que supera el horario de corte, **When** se estima, **Then** la
   ventana empieza en el siguiente día hábil aplicable.
5. **Given** un pedido cuya ventana cae en festivo colombiano o día sin atención,
   **When** se estima, **Then** la ventana se corre al siguiente día hábil y no cuenta el
   festivo como día de entrega.

---

### User Story 5 - Refinar y comparar la cotización (Priority: P3)

La persona ajusta la conversación: «mejor 100», «en Cali», «sin domicilio», «muéstrame otra
más barata». El Cotizador conserva el contexto comercial del hilo y recalcula solo lo
afectado.

**Why this priority**: mejora el uso repetido; la cotización de un turno ya entrega valor.

**Independent Test**: cotizar 20 unidades, luego escribir «mejor 100» y comprobar que
mantiene producto y ciudad, y recalcula presentaciones, subtotal y alternativas.

**Acceptance Scenarios**:

1. **Given** una cotización previa, **When** la persona cambia la cantidad, **Then** se
   recalculan presentaciones, sobrantes y totales sobre el mismo producto.
2. **Given** una cotización previa, **When** cambia la ciudad, **Then** se actualizan
   proveedores y mapa sin perder el producto.
3. **Given** una pregunta ajena al catálogo comercial en este hilo, **When** se envía,
   **Then** el Cotizador explica que solo cotiza productos e invita a usar el chat general.
4. **Given** dos hilos distintos, **When** se alterna entre Chat y Cotizador, **Then** las
   conversaciones y sus estados comerciales no se mezclan.

### User Story 6 - Que me recuerde sin preguntar (Priority: P2)

Una persona cuenta de pasada «me llamo Ana, trabajo en una cafetería en Bogotá y siempre
pido a domicilio». En los turnos siguientes, y en hilos nuevos, el Cotizador usa su nombre,
su ciudad y su preferencia de entrega sin volver a pedirlos y sin anunciar que los guardó.

**Why this priority**: repetir los mismos datos en cada cotización es la fricción más
visible del flujo; recordarlos convierte una consulta en una herramienta de trabajo diaria.

**Independent Test**: escribir el perfil en un turno, abrir un hilo nuevo y pedir una
cotización sin ciudad: debe cotizar en la ciudad recordada y saludar por su nombre.

**Acceptance Scenarios**:

1. **Given** un perfil recordado con ciudad, **When** se pide una cotización sin ciudad,
   **Then** se usa la ciudad recordada y no se pregunta por ella.
2. **Given** un perfil recordado con preferencia de domicilio, **When** no se menciona la
   entrega, **Then** la cotización incluye el domicilio como si se hubiera pedido.
3. **Given** un perfil recordado, **When** la persona dice otra ciudad, **Then** manda lo
   que acaba de decir.
4. **Given** que la persona escribe «olvida mis datos», **When** se procesa, **Then** se
   borra todo el perfil y la siguiente cotización vuelve a preguntar la ciudad.
5. **Given** un perfil sin uso durante 180 días, **When** se consulta, **Then** se descarta
   y no se aplica.

### User Story 7 - Recibir una cotización auditable (Priority: P2)

Cada cotización entregada lleva un identificador, la fecha hasta la que vale y un enlace a
la ficha de la oferta, donde se ve de dónde salió el precio y qué campos son simulados. La
persona puede volver a la ficha desde el mismo hilo y un asesor puede auditar la cotización.

**Why this priority**: sin identidad ni vigencia no hay nada que reclamar ni comparar; es
la diferencia entre una demo y una cotización que una empresa puede sostener.

**Independent Test**: pedir una cotización, comprobar que muestra `#id` y vigencia, abrir la
ficha enlazada y encontrar la fuente, la fecha y los campos simulados.

**Acceptance Scenarios**:

1. **Given** una cotización completa, **When** se entrega, **Then** muestra identificador y
   vigencia y queda registrada con el total y la moneda.
2. **Given** una cotización con oferta, **When** se abre su ficha, **Then** la ficha declara
   producto, sede, precio, procedencia y campos simulados.
3. **Given** una ficha inexistente o un identificador manipulado, **When** se solicita,
   **Then** responde 404.
4. **Given** una aclaración o una consulta fuera de alcance, **When** se responde, **Then**
   no se emite ninguna cotización.

---

### Edge Cases

- Cantidad cero, negativa, no numérica o desproporcionada frente al mínimo del proveedor.
- Producto inexistente en el catálogo, o existente solo en otro país.
- Producto sin precio, sin stock conocido, agotado o con stock menor a la cantidad
  comprable.
- Producto alternativo con capacidad o material distintos: la diferencia debe ser explícita.
- Precio desactualizado o con vigencia vencida: se muestra con aviso, no se corrige.
- Varias ofertas del mismo proveedor en sedes distintas o con monedas distintas.
- Ciudad escrita con o sin tildes, con error de ortografía o fuera del país.
- Domicilio sin zona, zona ambigua, pedido en día no hábil o después del corte.
- Mapa sin coordenadas, sin red o con marcadores superpuestos.
- Respuesta interrumpida durante el cálculo: no se muestra un total parcial como final.
- Consulta con parte comercial y parte documental en el mismo mensaje.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La aplicación MUST ofrecer una sección Cotizador con la misma experiencia de
  conversación del chat (compositor, historial, streaming y navegación propia). El texto del
  turno llega por SSE y las tarjetas en un evento aparte; el dictado queda pendiente para el
  siguiente corte.
- **FR-002**: El Cotizador MUST atender solo solicitudes de cotización de productos del
  catálogo Carvajal y MUST derivar al chat general las consultas ajenas, explicando el
  límite.
- **FR-003**: El sistema MUST interpretar producto, uso, cantidad, capacidad, país, ciudad y
  solicitud de domicilio, y conservarlos durante el hilo mientras sigan siendo válidos.
  MUST pedir la ciudad antes de presentar precios, totales y proveedores cuando falte.
- **FR-004**: El sistema MUST validar la compatibilidad técnica de cada referencia con el
  uso pedido con las reglas vigentes y MUST no relajarlas para completar una cotización.
- **FR-005**: El sistema MUST cotizar en presentaciones comerciales conocidas de cada
  oferta y MUST informar unidades solicitadas, unidades comprables, presentaciones a
  comprar y sobrantes.
- **FR-006**: El sistema MUST calcular dinero con precisión decimal exacta, incluyendo
  subtotal, impuestos según la fuente, envío y total conocido, y MUST no sumar monedas
  distintas ni presentar un subtotal como total final cuando falte un componente.
- **FR-007**: El sistema MUST mostrar por cada oferta el precio, la moneda, la nota de
  impuestos, la fuente que lo publica, su fecha y el aviso de precio de referencia.
- **FR-008**: El sistema MUST declarar la ausencia de precio, existencia, cobertura o plazo
  en lugar de estimarlos, convertirlos desde otro país o promediarlos.
- **FR-009**: El sistema MUST distinguir existencia conocida, existencia desconocida y
  existencia simulada, y MUST no presentar ninguna de ellas como inventario confirmado en
  tiempo real.
- **FR-010**: El sistema MUST ofrecer hasta dos alternativas similares compatibles con el
  uso y explicar en qué se diferencian de la opción principal.
- **FR-011**: El sistema MUST listar los proveedores asociados a la opción con nombre, sede,
  dirección, teléfono y fuente del dato cuando existan.
- **FR-012**: El sistema MUST mostrar un mapa incrustado con la ubicación del proveedor de
  la oferta y MUST conservar la lista textual de sedes con su dirección, teléfono y enlace
  de respaldo cuando el mapa no cargue.
- **FR-013**: El sistema MUST pedir el destino cuando se solicite domicilio y MUST mostrar
  cobertura, costo de envío y ventana estimada cuando existan datos, aclarando que la
  estimación no garantiza la entrega. La ventana MUST contarse en días hábiles y excluir
  los festivos de Colombia y los días sin atención del proveedor.
- **FR-014**: El sistema MUST identificar la procedencia de cada dato simulado en el punto
  donde aparece (precio, existencia, producto y entrega) y MUST no usar un aviso general en
  la sección.
- **FR-015**: El sistema MUST permitir desactivar los datos simulados y, en ese modo, MUST
  limitarse a datos publicados en el catálogo.
- **FR-016**: Los hilos y el estado comercial del Cotizador MUST quedar aislados de los del
  chat general, incluida la validación en servidor del hilo solicitado.
- **FR-017**: El Cotizador MUST conservar las protecciones vigentes de identidad anónima,
  CSRF, cuota diaria, límite de longitud, saneamiento de respuestas y manejo de errores.
- **FR-018**: El sistema MUST soportar seguimientos que cambien cantidad, ciudad, domicilio
  o preferencia de alternativa sin repetir la solicitud completa.
- **FR-019**: El sistema MUST registrar la fecha del dato comercial que muestra y avisar
  cuando supere la antigüedad admitida.
- **FR-020**: El sistema MUST conservar el comportamiento actual del chat general y del
  flujo comercial existente, o documentar y probar cualquier cambio de contrato.
- **FR-023**: El catálogo curado MUST ser la única fuente de productos; la demo solo MAY
  añadir una extensión simulada y las condiciones comerciales, sin duplicar los productos
  reales en otro archivo.
- **FR-024**: El sistema MUST exponer una ficha citable por oferta, con producto, sede,
  precio, procedencia, fecha y campos simulados, enlazada desde la cotización.
- **FR-025**: El sistema MUST registrar cada cotización completa con identificador,
  vigencia, total, moneda, dueño e hilo, y MUST no emitir registro para aclaraciones.
- **FR-022**: El sistema MUST recordar en silencio los detalles personales que la persona
  cuente (nombre, lugar de trabajo, ciudad, preferencias y domicilio), aplicarlos en turnos
  e hilos siguientes y MUST no escribirlos en las trazas ni en el payload persistido de la
  cotización. MUST borrarlos cuando la persona lo pida y descartarlos tras 180 días sin uso.
- **FR-021**: El sistema MUST presentar la cotización, las alternativas, los proveedores, el
  mapa y el domicilio **dentro del hilo de la conversación**, con los tokens visuales del
  mockup institucional vigente, legibles en escritorio y móvil, y MUST no exigir salir del
  chat para completar el flujo.

### Key Entities

- **Referencia de producto**: producto del catálogo con familia, material, capacidad, uso
  declarado, unidades por caja y procedencia.
- **Presentación de venta**: unidad, paquete o caja en la que una oferta se vende, con su
  cantidad de unidades y precio.
- **Oferta**: combinación de referencia, proveedor y presentación con precio, moneda,
  impuestos, fecha del dato y origen (real o simulado).
- **Proveedor y sede**: empresa, ciudad, dirección, teléfono, coordenadas opcionales y
  procedencia del dato.
- **Existencia**: unidades disponibles por oferta y sede en una fecha; puede ser conocida,
  desconocida o simulada.
- **Zona de entrega**: destino con cobertura, costo de envío y ventana estimada, con su
  calendario y horario de corte.
- **Cotización**: producto, cantidad solicitada, presentación elegida, cantidades y
  sobrantes, precios, proveedores, domicilio y alternativas de un turno.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En una revisión de 10 solicitudes de ejemplo, el 100 % identifica unidades
  solicitadas, unidades comprables y sobrantes, o declara por qué no puede calcularlos.
- **SC-002**: El 100 % de las pruebas de cantidades y dinero (cero, negativos, mínimos,
  múltiplos, impuestos y redondeo) devuelve el resultado esperado con precisión decimal.
- **SC-003**: El 100 % de los datos simulados (precio, existencia, producto y entrega) va
  identificado donde aparece; ninguno se presenta como real y la sección no usa un aviso
  general.
- **SC-004**: El 100 % de las cotizaciones con producto válido muestra al menos un proveedor
  con nombre y sede, o explica por qué no hay ninguno.
- **SC-005**: El 100 % de las cotizaciones con proveedor geolocalizado muestra el mapa
  incrustado con su ubicación y la lista textual equivalente.
- **SC-006**: El 100 % de las solicitudes de domicilio con destino conocido muestra ventana
  estimada o el motivo por el que no puede calcularse; ninguna inventa una fecha.
- **SC-007**: Una persona completa una cotización de ejemplo (producto, cantidad, proveedor
  y total) en menos de 3 minutos y sin instrucciones externas.
- **SC-008**: La regresión del chat general, del flujo comercial existente y de los
  contratos de seguridad y streaming queda en verde.
- **SC-011**: El 100 % de las cotizaciones completas queda registrada con identificador y
  vigencia, y el 100 % de sus ofertas enlaza una ficha que responde 200.
- **SC-012**: Los casos dorados de cotización (13 escenarios con totales y presentaciones
  esperados) se ejecutan en CI y fallan ante cualquier diferencia.
- **SC-010**: En una prueba con nombre, ciudad y domicilio recordados, el 100 % de los
  turnos siguientes los aplica sin preguntar; tras «olvida mis datos», ninguna cotización
  vuelve a usar el perfil.
- **SC-009**: Una persona completa la cotización de ejemplo sin abandonar el hilo: producto,
  cantidad, proveedor, mapa y domicilio se ven en la misma conversación, a 390 px y a
  1440 px.

## Assumptions

- El alcance inicial son empaques de Colombia y precios en COP; el catálogo actual de
  México se conserva sin convertir precios.
- La existencia y la entrega reales dependen de proveedores con los que todavía no hay
  integración; la demo los simula de forma explícita.
- Las reglas de material y uso publicadas en el catálogo curado siguen siendo la autoridad
  técnica; un vaso apto para contenido caliente no se presenta como apto para cualquier
  temperatura no documentada.
- El mapa requiere un proveedor de teselas externo; su elección se decide en el plan
  técnico y no se asume disponible sin red.
- El vendor externo de mapas no recibe datos de la conversación ni del cliente.
- La cotización no crea pedidos, reservas ni pagos; su salida es informativa.
- Los datos simulados se activan solo en entornos donde se declaren; producción sin esa
  bandera no los muestra.
