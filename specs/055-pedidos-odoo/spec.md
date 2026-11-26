# Feature Specification: Pedidos en Odoo tras la cotización

**Feature Branch**: `feat/odoo-pedidos`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26) en la rama, PR #279; producción sigue en `main` hasta la
fusión. Correo saliente pendiente (SMTP). Evidencia: [evidencia.md](./evidencia.md).

**Input**: Pedido del owner (2026-11-26): «que el agente de la app pueda crear órdenes de
compra si el cliente confirma una compra de una cotización, por chat web o por WhatsApp»;
«que envíe correo con la confirmación al cliente y que quede traza y alguna orden en el
odoo»; «registra el catálogo en odoo también». Pidió nombre y correo del cliente y «lo más
rápido posible pero que funcione».

## Contexto

La spec 054 cierra cada cotización con un resumen, «¿La confirmo?» y, con un «sí», el folio y
el PDF (T6 «registrada»). Hasta aquí el asistente cotiza, pero no vende: nada queda en un
sistema comercial y nadie recibe una confirmación de compra. La 054 dejó «pedidos, reservas o
pagos» fuera de alcance.

Esta spec agrega un paso **opcional** después del folio: el asistente ofrece hacer el pedido,
pide nombre y correo y crea en Odoo un pedido de venta confirmado, con la oportunidad del CRM
y la traza en el chatter. Odoo envía al cliente el correo de confirmación con el PDF del
pedido.

## Decisiones (2026-11-26)

1. **Pedido de venta (`sale.order`), no orden de compra (`purchase.order`).** Lo que el
   cliente llama «orden de compra» es, para Carvajal, una venta. En Odoo `purchase.order` es
   lo que la empresa le compra a un proveedor: usarla pondría al cliente como proveedor y
   dejaría sin efecto la plantilla de confirmación, el CRM y los informes de ventas. El
   pedido de venta trae el campo de la referencia del cliente (`client_order_ref`), que
   guarda el folio de la cotización y hace idempotente el pedido.
2. **Odoo 19 propio en la VPS**, en su propio proyecto de compose (`carvajal-odoo`,
   plantilla en [`deploy/odoo/`](../../deploy/odoo/)), frente a:
   - **El Odoo 13 del host y el Odoo 15 de Maidensoft**: son de otros proyectos y versiones
     antiguas; mezclar datos o módulos ahí ataría Carvajal a su ciclo y a su riesgo. No se
     tocan.
   - **Odoo 17**: ya no tiene soporte.
   - **HubSpot** (SaaS): sacaría nombre y correo de los clientes del servidor.
   - **EspoCRM**: gestiona contactos y oportunidades, pero no trae pedido de venta con
     plantilla de confirmación y PDF en su núcleo; habría que construirlos.
   - Odoo 19 trae de serie pedido de venta, CRM, plantilla de confirmación con PDF, chatter y
     la API JSON-2 con API key por usuario. Cumple «lo más rápido posible pero que funcione»
     con módulos estándar (`sale_management`, `crm`, `sale_crm`, `contacts`) y sin addon
     propio.
3. **La integración vive en la app, no en un MCP.** Los dos canales (chat web y WhatsApp por
   el puente MCP) pasan por el mismo servicio del chat, así que un solo adaptador sirve a
   ambos. Un MCP sería una herramienta que decide el modelo: el nombre y el correo pasarían
   por el LLM y la web no tiene MCP.
4. **Nombre y correo por reglas.** Se leen sin el modelo y sin Jev, en uno o dos mensajes.
5. **Sin CRM configurado, nada cambia.** Sin `ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY` el folio
   no ofrece el pedido y el flujo es el de la 054.

### Relación con especificaciones existentes

- **054** (flujos de cotización): el paso del pedido sigue a T6 sobre el mismo borrador
  (`quote_drafts`), con dos preguntas pendientes nuevas: `pedido` y `datos_pedido`. La 054
  recibe una enmienda que enlaza esta spec.
- **042/054** (motor del cotizador): las líneas del pedido usan las presentaciones y precios
  que el motor ya calculó; esta spec no estima precios.
- **046** (Jev): Jev puede interpretar la respuesta corta a «¿Quieres hacer el pedido?» como
  a cualquier pregunta del flujo; nunca recibe los datos del paso `datos_pedido`.
- **038** (LangSmith): el turno fijo del pedido se monitorea como los demás; el contenido se
  redacta salvo con `MONITORING_INCLUDE_CONTENT` (solo desarrollo y evaluación).
- **051/053** (fotos): las fotos del catálogo se suben también a los productos de Odoo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hacer el pedido de una cotización registrada en el chat web (Priority: P1)

Una persona confirma su cotización y recibe el folio. Al final del mensaje el asistente
pregunta si quiere hacer el pedido, con los botones «Hacer el pedido» y «Ahora no». Acepta,
envía «Ana Pérez, ana@correo.com» y recibe el número del pedido. En Odoo queda el pedido de
venta confirmado con sus líneas, el cliente, la oportunidad ganada y la nota en el chatter.

**Why this priority**: es el pedido del owner completo en el canal que el equipo controla.

**Independent Test**: `tests/test_pedidos_odoo.py::test_el_folio_ofrece_el_pedido_y_lo_crea_con_nombre_y_correo`
(servicio real sobre SQLite y un CRM simulado).

**Acceptance Scenarios**:

1. **Given** el CRM configurado y una cotización confirmada, **When** se registra el folio,
   **Then** el mensaje T6 termina con «🛒 ¿Quieres hacer el pedido? Te pido tu nombre y tu
   correo y te llega la confirmación.» y la web muestra «Hacer el pedido» / «Ahora no».
2. **Given** la oferta, **When** la persona dice «sí», «Sí, quiero hacer el pedido», «quiero
   comprarla» o «hagamos el pedido», **Then** el asistente pide nombre y correo.
3. **Given** que pidió los datos, **When** la persona envía nombre y correo, **Then** se crea
   el pedido y la respuesta es «🧾 **Pedido S00001 confirmado** · cotización #folio» con el
   correo en negrita.
4. **Given** el pedido creado, **When** la persona dice otra vez «quiero comprarla», **Then**
   responde «Ese pedido ya quedó creado con el número **S00001**» y no crea otro.
5. **Given** todo el paso del pedido, **When** se cuenta lo que pasó por el modelo, **Then**
   solo la cotización inicial usó el agente.

---

### User Story 2 - Hacer el pedido por WhatsApp (Priority: P1)

El mismo flujo por WhatsApp, con texto en lugar de botones. El pedido de Odoo dice que llegó
por WhatsApp.

**Why this priority**: el owner pidió los dos canales.

**Independent Test**: `test_whatsapp_marca_el_canal_del_pedido`.

**Acceptance Scenarios**:

1. **Given** una conversación de WhatsApp con folio, **When** la persona dice «hagamos el
   pedido» y envía nombre y correo, **Then** el pedido se crea con el canal «WhatsApp».
2. **Given** una conversación del chat web, **When** se crea el pedido, **Then** el canal es
   «chat web».

---

### User Story 3 - Datos en dos mensajes o incompletos (Priority: P1)

La persona envía primero el nombre y después el correo, o al revés. El asistente pide lo que
falta. Si no entiende ninguno, muestra el formato de ejemplo.

**Why this priority**: es la forma más común de responder en un chat y no debe romper el
pedido.

**Independent Test**: `test_los_datos_se_completan_en_dos_mensajes`,
`test_los_datos_de_quien_compra_salen_del_texto` y el escenario de US1.

**Acceptance Scenarios**:

1. **Given** que pidió los datos, **When** la persona escribe «Ana Pérez», **Then** responde
   «Gracias, Ana Pérez. ¿A qué **correo** te envío la confirmación del pedido?».
2. **Given** solo el correo, **When** llega, **Then** pregunta «¿A **nombre** de quién hago el
   pedido?».
3. **Given** un texto sin nombre ni correo («12345»), **When** llega, **Then** responde «No
   alcancé a leer tu nombre y tu correo. Envíamelos así: «Ana Pérez, ana@correo.com». Si
   prefieres no hacer el pedido ahora, escribe «ahora no».».
4. **Given** «mi nombre es juan gómez y mi correo es JUAN.G@Empresa.co», **When** se lee,
   **Then** el nombre es «Juan Gómez» y el correo «juan.g@empresa.co».

---

### User Story 4 - «Ahora no» y reintento ante un fallo (Priority: P2)

La persona no quiere el pedido todavía; o Odoo no responde. La cotización sigue registrada y
el pedido se puede pedir después.

**Why this priority**: el pedido es opcional y un fallo externo no debe perder la cotización.

**Independent Test**: `test_ahora_no_deja_la_cotizacion_sin_pedido` y
`test_si_odoo_falla_se_puede_reintentar`.

**Acceptance Scenarios**:

1. **Given** la oferta, **When** la persona dice «Ahora no» (o «no», «lo pienso», «después»),
   **Then** responde «Listo, tu cotización #folio queda registrada. Si decides comprar,
   escribe «hacer el pedido». ¿Te ayudo con algo más?» y no llama a Odoo.
2. **Given** «ahora no», **When** más tarde escribe «hacer el pedido», **Then** el asistente
   vuelve a pedir nombre y correo.
3. **Given** Odoo caído, **When** llegan nombre y correo, **Then** responde «No pude crear el
   pedido en este momento. Tu cotización sigue registrada: escribe «hacer el pedido» en unos
   minutos y lo intento de nuevo.»; con «hacer el pedido» y los datos otra vez, el pedido se
   crea.

---

### User Story 5 - Catálogo de Colombia en Odoo (Priority: P2)

El operador sincroniza las referencias de Colombia con Odoo, con su precio por caja y, si lo
pide, su foto, para que los pedidos usen productos reales y el equipo los vea en Ventas.

**Why this priority**: el owner lo pidió; sin catálogo, el pedido crea los productos al vuelo
y Odoo queda incompleto.

**Independent Test**: `test_las_fotos_solo_se_suben_a_los_productos_sin_foto`; la pasada
real está en [evidencia.md](./evidencia.md).

**Acceptance Scenarios**:

1. **Given** Odoo vacío, **When** se corre `scripts/odoo_catalogo.py`, **Then** hay un
   producto por referencia de Colombia (`default_code` = SKU) en la categoría «Empaques
   Carvajal» y el servicio de envío `ENVIO-CARVAJAL`.
2. **Given** el catálogo ya sincronizado, **When** se corre otra vez, **Then** solo escribe
   lo que cambió (nombre, precio o descripción).
3. **Given** `--imagenes`, **When** se corre, **Then** sube la foto del catálogo solo a los
   productos que todavía no tienen una.

### Flujo paso a paso

Textos tal como salen de las plantillas (`render.py`):

```text
✅ **Cotización #<folio> registrada**                        ← T6 (spec 054)
1 producto · Total **$50.000** · válida hasta …
📄 …
🛒 ¿Quieres hacer el pedido? Te pido tu nombre y tu correo y te llega la confirmación.
    [Hacer el pedido]  [Ahora no]                            ← solo web; awaiting = pedido

› Sí, quiero hacer el pedido
¡Perfecto! Para el pedido envíame tu **nombre** y tu **correo** (por ejemplo: «Ana Pérez,
ana@correo.com»). A ese correo te llega la confirmación.     ← awaiting = datos_pedido

› Ana Pérez
Gracias, Ana Pérez. ¿A qué **correo** te envío la confirmación del pedido?

› ana@correo.com
🧾 **Pedido S00001 confirmado** · cotización #<folio>
Te enviamos la confirmación a **ana@correo.com**. Un asesor te contacta para coordinar el
pago y la entrega.                                           ← awaiting = ninguno; order = S00001
```

En voz, cada plantilla tiene su versión hablada sin Markdown ni emojis (por ejemplo, «Listo,
tu pedido quedó confirmado. Te enviamos la confirmación a tu correo y un asesor te contacta
para el pago y la entrega.»). En WhatsApp la negrita sale con `*` como en la 054.

En Odoo, en este orden: busca un pedido con `client_order_ref` = folio (si existe, lo
devuelve); reutiliza el cliente por correo o lo crea (en `es_CO`, con la ciudad); crea los
productos que falten; crea la oportunidad «Pedido de la cotización #folio»; crea el pedido
con las líneas y `origin` «Asistente Carvajal (canal) · cotización #folio»; lo confirma con
`send_email`; deja la nota en el chatter y marca la oportunidad como ganada.

### Edge Cases

- **Doble aceptación**: un segundo «sí», «quiero comprarla» o «hacer el pedido» tras crear el
  pedido responde el número que ya existe y no llama a Odoo.
- **Mismo folio en Odoo**: si el pedido ya existe (reintento del turno, doble clic), Odoo
  devuelve el existente; no hay un segundo pedido.
- **Cliente que ya existe**: se reutiliza por correo (sin distinguir mayúsculas) y no se
  cambia su nombre en Odoo.
- **«Quiero comprar 500 vasos»** tras el folio no es el pedido: es otra cotización y va al
  agente. «¿Puedo comprarla con tarjeta?» es una pregunta y también va al agente.
- **Cancelar mientras se piden los datos**: «ahora no», «cancelar» u «olvida todo» dejan la
  cotización registrada sin pedido y borran los datos parciales.
- **Una pregunta mientras se piden los datos** (cualquier texto con «?») sale del paso y va al
  agente. Como en cualquier turno después de T6, el borrador empieza de cero: los datos
  parciales se descartan y la oferta de ese folio ya no se retoma por reglas.
- **Nombre inválido**: con cifras, con menos de dos letras o de más de seis palabras no es un
  nombre; se recorta a 80 caracteres.
- **Línea sin precio publicado**: entra al pedido a $0 con «Precio por confirmar con el
  vendedor» en su descripción y la nota «Sin precio publicado: …».
- **Envío**: con costo confirmado entra como línea de servicio `ENVIO-CARVAJAL`; pedido y sin
  costo, queda la nota «Envío por confirmar con el vendedor.».
- **SKU que Odoo no tiene**: se crea en el momento con el precio de la línea.
- **Odoo falla a medias**: si la oportunidad, la nota del chatter o «ganada» fallan, el pedido
  sigue y queda un aviso en los logs; si falla el pedido, la persona recibe el mensaje de
  reintento.
- **Respuesta con los datos directamente a la oferta** («Ana Pérez, ana@correo.com» sin
  decir antes «sí»): el texto no acepta el pedido y no está en `datos_pedido`. *Brecha
  conocida*: hoy ese texto puede llegar a Jev y al agente; ver
  [evidencia.md §5](./evidencia.md#5-hallazgos-pendientes).

## Requirements *(mandatory)*

### Functional Requirements

**Oferta y aceptación**

- **FR-001**: El folio (T6) DEBE ofrecer el pedido solo si el CRM está configurado
  (`ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY`); sin CRM, el flujo es el de la 054.
- **FR-002**: El pedido se acepta con un «sí» a la oferta o con una frase de compra («quiero
  comprarla», «hagamos el pedido», «la compro», «hacer el pedido»); una frase con producto,
  cantidad o «?» no acepta el pedido.
- **FR-003**: «Ahora no» y equivalentes dejan la cotización registrada sin pedido; «hacer el
  pedido» lo retoma mientras el borrador siga en el folio.
- **FR-004**: La web DEBE mostrar los botones «Hacer el pedido» y «Ahora no» con la oferta.

**Datos de quien compra**

- **FR-005**: Nombre y correo DEBEN leerse por reglas, en uno o dos mensajes, y pedir lo que
  falte.
- **FR-006**: Los mensajes del paso `datos_pedido` NUNCA DEBEN ir a Jev ni al LLM.
- **FR-007**: Los datos parciales viven en el borrador solo mientras se piden y DEBEN
  vaciarse al crear el pedido, al fallar o al cancelar.

**Pedido en Odoo**

- **FR-008**: El pedido DEBE ser idempotente por folio (`client_order_ref`): un segundo
  intento devuelve el pedido existente.
- **FR-009**: Ante un fallo de Odoo, la persona DEBE recibir el mensaje de reintento y poder
  repetirlo; la cotización no se pierde.
- **FR-010**: Un pedido ya creado responde «Ese pedido ya quedó creado con el número **S…**»
  y no crea otro.
- **FR-011**: El pedido DEBE registrar el canal («chat web» o «WhatsApp») en el origen, en la
  oportunidad y en la nota.
- **FR-012**: Las líneas DEBEN usar las presentaciones y el precio por presentación que
  calculó el motor (por ejemplo, 2 cajas de 50 u. a $25.000), sin impuestos añadidos por
  Odoo.
- **FR-013**: El envío con costo confirmado DEBE entrar como línea de servicio
  `ENVIO-CARVAJAL`.
- **FR-014**: Lo que no tiene cifra (envío por confirmar, líneas sin precio) y la situación
  del IVA DEBEN quedar como notas del pedido.
- **FR-015**: El pedido DEBE confirmarse en Odoo y enviar el correo de confirmación de ventas
  (plantilla `sale.mail_template_sale_confirmation` con el PDF).
- **FR-016**: DEBE quedar traza: nota en el chatter del pedido con el canal y el folio, y una
  oportunidad del CRM marcada como ganada; si esas trazas fallan, el pedido sigue.

**Catálogo**

- **FR-017**: El catálogo de Colombia DEBE poder sincronizarse con Odoo (un producto por SKU,
  precio por caja de la ficha o de la primera oferta en COP) más el servicio de envío, de
  forma idempotente.
- **FR-018**: Con `--imagenes`, la foto del catálogo DEBE subirse solo a los productos que no
  tengan una.

### Privacidad

- **FR-019**: Nombre y correo solo viajan a Odoo. Los logs del pedido llevan el folio y el
  número del pedido, nunca el nombre ni el correo.
- **FR-020**: El historial del chat guarda los mensajes del pedido como cualquier turno (la
  persona ve lo que escribió).
- **FR-021**: La API key de Odoo es la de un usuario técnico (`carvajal-bot`), vive solo en el
  entorno del servidor y no se registra en logs ni en salidas.

### Key Entities

- **Borrador** (054): agrega `awaiting` `pedido` y `datos_pedido`, `buyer` (nombre y correo
  parciales) y `order` (número del pedido en Odoo).
- **Comprador**: nombre y correo; los dos son obligatorios.
- **Solicitud de pedido**: folio, comprador, canal, ciudad, líneas, total y notas.
- **Línea de pedido**: SKU, nombre, descripción, cantidad de presentaciones, precio por
  presentación y si es servicio.
- **Pedido** (Odoo): número visible (`S00001`), identificador y oportunidad.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un recorrido real folio → «sí» → nombre y correo deja en Odoo un pedido
  confirmado con producto y envío, la oportunidad ganada y el correo de confirmación con PDF
  en el chatter.
- **SC-002**: Repetir el pedido del mismo folio da 0 pedidos nuevos (pruebas y Odoo real).
- **SC-003**: En las pruebas, 0 llamadas a Jev con `awaiting="datos_pedido"` y 0 turnos del
  paso del pedido que pasen por el modelo.
- **SC-004**: Sin `ODOO_*`, el folio no ofrece el pedido (0 cambios frente a la 054).
- **SC-005**: Una segunda pasada del catálogo no crea productos ni sube fotos repetidas.
- **SC-006**: `make check` en verde.

## Assumptions

- Odoo corre en la misma VPS que la app y la app lo alcanza por la red de Docker; no se
  publica a internet (solo `127.0.0.1:8169`).
- Los precios del pedido son los de referencia del catálogo y del motor (con su vendedor y
  vigencia); el pago y la entrega los coordina un asesor después.
- La base de Odoo es de demostración («Carvajal Empaques (demo)»), en COP y sin impuestos por
  defecto.
- El correo sale con el servidor SMTP que configure el owner; hasta entonces queda en la cola
  de Odoo y en el chatter.

## Fuera de alcance y pendientes

- **SMTP**: el correo de confirmación no sale hasta configurar el servidor de correo saliente
  (credenciales del owner); ver [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md).
- **Pago**: ni enlace de pago ni pasarela; el asesor lo coordina.
- **Inventario y entrega**: sin `stock`, reservas ni fechas de entrega en Odoo.
- **Catálogo de México**: solo se sincroniza Colombia (COP).
- **Estado del pedido de vuelta al chat**: el asistente no consulta en Odoo si el pedido se
  pagó, se entregó o se canceló.
- Facturación electrónica e impuestos calculados por Odoo.
