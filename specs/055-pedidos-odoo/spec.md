# Feature Specification: Pedidos en Odoo tras la cotización

**Feature Branch**: `feat/odoo-pedidos`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26) en la rama, PR #279; producción sigue en `main` hasta la
fusión. Revisión adversarial del 2026-11-26 corregida en `b893c28` (paso de confirmación,
privacidad, idempotencia y Odoo); las pruebas se actualizan en la misma PR. Correo saliente
pendiente (SMTP). Evidencia: [evidencia.md](./evidencia.md).

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
pide nombre y correo, los muestra para que la persona los confirme y crea en Odoo un pedido
de venta confirmado, con la oportunidad del CRM y la traza en el chatter. Al confirmar el
pedido, Odoo envía al cliente el correo de confirmación con el PDF; sin servidor SMTP ese
correo no sale (ver [Fuera de alcance y pendientes](#fuera-de-alcance-y-pendientes)).

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
4. **Nombre y correo por reglas.** Se leen sin el modelo y sin Jev, en uno o dos mensajes
   (también dictados por voz), y Jev no clasifica ninguna respuesta de los pasos del pedido.
5. **Confirmación antes de crear el pedido** (revisión adversarial). Con los dos datos, el
   asistente los muestra («📋 Voy a hacer el pedido con estos datos… ¿Lo confirmo?») y solo
   un «sí» crea el pedido. Así un nombre mal leído o un correo con un error de dedo se
   corrigen antes de que Odoo cree el cliente y envíe el correo.
6. **Lo que se guarda no lleva los datos.** El turno con el nombre o el correo se guarda y se
   monitorea sin ellos (pregunta sustituida, correo enmascarado, nombre como «(tu nombre)»);
   la persona sí los ve en el turno. Así el historial que lee el modelo en los turnos
   siguientes, el resumen de memoria y LangSmith no los tienen.
7. **Sin CRM configurado, nada cambia.** Sin `ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY` el folio
   no ofrece el pedido y el flujo es el de la 054.

### Relación con especificaciones existentes

- **054** (flujos de cotización): el paso del pedido sigue a T6 sobre el mismo borrador
  (`quote_drafts`), con tres preguntas pendientes nuevas: `pedido`, `datos_pedido` y
  `confirmar_pedido`. La 054 recibe una enmienda que enlaza esta spec.
- **042/054** (motor del cotizador): las líneas del pedido usan las presentaciones y precios
  que el motor ya calculó; esta spec no estima precios.
- **046** (Jev): ninguna respuesta de los pasos del pedido va a Jev (`ORDER_AWAITING` está
  excluido de la compuerta); las reglas los deciden todos.
- **038** (LangSmith): el turno fijo del pedido se monitorea como los demás, pero con la
  pregunta y la respuesta sin nombre ni correo; además, el contenido se redacta salvo con
  `MONITORING_INCLUDE_CONTENT` (solo desarrollo y evaluación).
- **051/053** (fotos): las fotos del catálogo se suben también a los productos de Odoo.

## User Scenarios & Testing *(mandatory)*

Las pruebas viven en `tests/test_pedidos_odoo.py` (reglas puras, adaptador JSON-2 con un
transporte simulado y servicio real sobre SQLite con un CRM simulado). Se actualizan en la
misma PR con el diseño de `b893c28`.

### User Story 1 - Hacer el pedido de una cotización registrada en el chat web (Priority: P1)

Una persona confirma su cotización y recibe el folio. Al final del mensaje el asistente
pregunta si quiere hacer el pedido, con los botones «Hacer el pedido» y «Ahora no». Acepta,
envía «Ana Pérez, ana@correo.com», revisa los datos que le muestra el asistente, pulsa
«Confirmar pedido» y recibe el número del pedido. En Odoo queda el pedido de venta
confirmado con sus líneas, el cliente, la oportunidad ganada y la nota en el chatter.

**Why this priority**: es el pedido del owner completo en el canal que el equipo controla.

**Independent Test**: el recorrido completo del servicio en `tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** el CRM configurado y una cotización confirmada, **When** se registra el folio,
   **Then** el mensaje T6 termina con «🛒 ¿Quieres hacer el pedido? Te pido tu nombre y tu
   correo y te llega la confirmación.» y la web muestra «Hacer el pedido» / «Ahora no».
2. **Given** la oferta, **When** la persona dice «sí», «Sí, quiero hacer el pedido», «quiero
   comprarla» o «hagamos el pedido», **Then** el asistente pide nombre y correo.
3. **Given** que pidió los datos, **When** la persona envía nombre y correo, **Then** el
   asistente responde «📋 Voy a hacer el pedido con estos datos:», el nombre y el correo en
   negrita y «¿Lo confirmo? Si algo está mal, escríbeme el dato correcto.», con los botones
   «Confirmar pedido» / «Ahora no»; todavía no llama a Odoo.
4. **Given** la confirmación, **When** la persona dice «sí» o «Sí, confírmalo», **Then** se
   crea el pedido y la respuesta es «🧾 **Pedido S00001 confirmado** · cotización #folio» y
   «La confirmación del pedido llega a **ana@correo.com**».
5. **Given** el pedido creado, **When** la persona dice otra vez «quiero comprarla», **Then**
   responde «Ese pedido ya quedó creado con el número **S00001**» y no crea otro.
6. **Given** todo el paso del pedido, **When** se cuenta lo que pasó por el modelo, **Then**
   solo la cotización inicial usó el agente.
7. **Given** la oferta, **When** la persona responde directamente con «Ana Pérez,
   ana@correo.com» (sin un «sí» antes), **Then** se toman como los datos del pedido y pasa a
   la confirmación, sin Jev ni el modelo.

---

### User Story 2 - Hacer el pedido por WhatsApp (Priority: P1)

El mismo flujo por WhatsApp, con texto en lugar de botones. El pedido de Odoo dice que llegó
por WhatsApp.

**Why this priority**: el owner pidió los dos canales.

**Independent Test**: el escenario de WhatsApp de `tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** una conversación de WhatsApp con folio, **When** la persona dice «hagamos el
   pedido», envía nombre y correo y responde «sí» a la confirmación, **Then** el pedido se
   crea con el canal «WhatsApp».
2. **Given** una conversación del chat web, **When** se crea el pedido, **Then** el canal es
   «chat web».

---

### User Story 3 - Datos en dos mensajes, incompletos, dictados o corregidos (Priority: P1)

La persona envía primero el nombre y después el correo, o al revés, o lo dicta por voz. El
asistente pide lo que falta; si no entiende ninguno, muestra el formato de ejemplo. En la
confirmación puede corregir un dato.

**Why this priority**: es la forma más común de responder en un chat y no debe romper el
pedido.

**Independent Test**: las pruebas de reglas puras y el recorrido en dos mensajes de
`tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** que pidió los datos, **When** la persona escribe «Ana Pérez», **Then** responde
   «Gracias, Ana Pérez. ¿A qué **correo** te envío la confirmación del pedido?».
2. **Given** solo el correo, **When** llega, **Then** pregunta «¿A **nombre** de quién hago el
   pedido?».
3. **Given** un texto sin nombre ni correo («12345», «ok gracias»), **When** llega, **Then**
   responde «No alcancé a leer tu nombre y tu correo. Envíamelos así: «Ana Pérez,
   ana@correo.com». Si prefieres no hacer el pedido ahora, escribe «ahora no».».
4. **Given** «mi nombre es juan gómez y mi correo es JUAN.G@Empresa.co», **When** se lee,
   **Then** el nombre es «Juan Gómez» y el correo «juan.g@empresa.co».
5. **Given** «Juan, juan@x.co, gracias» o «maría de la cruz», **When** se leen, **Then** los
   nombres son «Juan» y «María de la Cruz».
6. **Given** el correo dictado «ana punto perez arroba gmail punto com», **When** se lee,
   **Then** el correo es «ana.perez@gmail.com» («guion» y «guion bajo» dan `-` y `_`).
7. **Given** la confirmación, **When** la persona escribe un correo nuevo o «me llamo Carlos
   Ruiz», **Then** el dato se corrige y vuelve a mostrar la confirmación con los datos
   nuevos.

---

### User Story 4 - «Ahora no», interrupciones y reintento ante un fallo (Priority: P2)

La persona no quiere el pedido todavía, pregunta otra cosa a mitad del pedido, o Odoo no
responde. La cotización sigue registrada y el pedido se puede pedir después.

**Why this priority**: el pedido es opcional y un fallo externo no debe perder la cotización.

**Independent Test**: los escenarios de «ahora no» y de fallo de Odoo de
`tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** cualquier paso del pedido, **When** la persona dice «Ahora no» (o «no», «no,
   mejor no», «no quiero el pedido», «ya no», «todavía no, gracias», «lo pienso»,
   «después»), **Then** responde «Listo, tu cotización #folio queda registrada. Si decides
   comprar, escribe «hacer el pedido». ¿Te ayudo con algo más?», borra los datos parciales y
   no llama a Odoo.
2. **Given** «ahora no», **When** en el mensaje siguiente escribe «hacer el pedido», **Then**
   el asistente vuelve a pedir nombre y correo.
3. **Given** un paso del pedido, **When** la persona pregunta algo con «?» («¿cuánto tarda la
   entrega?»), **Then** la pregunta va al agente, el paso sigue pendiente y la respuesta
   termina una sola vez con el recordatorio («↩️ ¿Seguimos con el pedido de tu cotización?»,
   «↩️ Para el pedido me faltan tu **nombre** y tu **correo**.» o «↩️ ¿Confirmo el pedido con
   los datos que me diste?»); si el mensaje siguiente tampoco sigue el pedido, el paso se
   suelta.
4. **Given** Odoo caído, **When** la persona confirma los datos, **Then** responde «No pude
   crear el pedido en este momento. Tu cotización sigue registrada: responde **sí** en unos
   minutos y lo intento de nuevo, o **ahora no** para dejarlo.»; los datos quedan en la
   confirmación y un «sí» reintenta con el mismo folio, sin volver a pedirlos.

---

### User Story 5 - Catálogo de Colombia en Odoo (Priority: P2)

El operador sincroniza las referencias de Colombia con Odoo, con su precio por caja y, si lo
pide, su foto, para que los pedidos usen productos reales y el equipo los vea en Ventas.

**Why this priority**: el owner lo pidió; sin catálogo, el pedido crea los productos al vuelo
y Odoo queda incompleto.

**Independent Test**: la prueba de fotos del adaptador en `tests/test_pedidos_odoo.py`; la
pasada real está en [evidencia.md](./evidencia.md).

**Acceptance Scenarios**:

1. **Given** Odoo vacío, **When** se corre `scripts/odoo_catalogo.py`, **Then** hay un
   producto por referencia de Colombia (`default_code` = SKU) en la categoría «Empaques
   Carvajal» y el servicio de envío `ENVIO-CARVAJAL`.
2. **Given** el catálogo ya sincronizado, **When** se corre otra vez, **Then** solo escribe
   lo que cambió (nombre, precio, descripción o archivado).
3. **Given** un producto archivado en Odoo, **When** se sincroniza o llega en un pedido,
   **Then** se reactiva en lugar de crear un duplicado.
4. **Given** `--imagenes`, **When** se corre, **Then** sube la foto del catálogo solo a los
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
📋 Voy a hacer el pedido con estos datos:
- Nombre: **Ana Pérez**
- Correo: **ana@correo.com**
¿Lo confirmo? Si algo está mal, escríbeme el dato correcto.
    [Confirmar pedido]  [Ahora no]                           ← awaiting = confirmar_pedido

› Sí, confírmalo
🧾 **Pedido S00001 confirmado** · cotización #<folio>
La confirmación del pedido llega a **ana@correo.com**. Un asesor te contacta para
coordinar el pago y la entrega.                              ← awaiting = ninguno; order = S00001
```

«Hacer el pedido» envía «Sí, quiero hacer el pedido» y «Confirmar pedido» envía «Sí,
confírmalo». En voz, cada plantilla tiene su versión hablada sin Markdown ni emojis; la
confirmación dice el nombre pero no lee el correo («Voy a hacer el pedido a nombre de Ana
Pérez, con el correo que escribiste. ¿Lo confirmo?») y la del pedido dice «Listo, tu pedido
quedó confirmado. La confirmación llega a tu correo y un asesor te contacta para el pago y
la entrega.». En WhatsApp la negrita sale con `*` como en la 054.

En Odoo (`adapters/crm/odoo.py`), en este orden:

1. Busca un pedido con `client_order_ref` = folio y estado distinto de `cancel`. Si está
   confirmado (`sale`), lo devuelve como existente y no crea nada.
2. Busca el cliente por correo con `=ilike` y el correo escapado (`%` y `_` literales) y lo
   reutiliza solo si también coincide el nombre (sin distinguir mayúsculas ni espacios
   repetidos); si no, crea un contacto nuevo (en `es_CO`, con la ciudad).
3. Si el pedido del folio quedó en borrador (un intento anterior falló antes de
   confirmarlo), le pone el cliente actual. Si no existe, busca los productos por SKU
   (también los archivados, que reactiva), crea los que falten y crea el pedido con las
   líneas y `origin` «Asistente Carvajal (canal) · cotización #folio».
4. Lo confirma con `action_confirm` y `send_email`.
5. Deja la nota en el chatter (`message_post` con `body_is_html=True`).
6. Si el pedido no tiene oportunidad, crea «Pedido de la cotización #folio», la liga al
   pedido (`opportunity_id`) y la marca como ganada. Si el CRM falla, el pedido sigue.

### Edge Cases

- **Doble aceptación**: un segundo «sí», «quiero comprarla» o «hacer el pedido» tras crear el
  pedido responde el número que ya existe y no llama a Odoo.
- **Mismo folio en Odoo**: si el pedido ya está confirmado (reintento del turno, doble clic),
  Odoo devuelve el existente; no hay un segundo pedido. Si quedó en borrador, el reintento lo
  confirma; un pedido cancelado no cuenta y se crea otro.
- **Cliente que ya existe**: se reutiliza si coinciden el correo y el nombre, y no se cambia
  su ficha. Con el mismo correo y otro nombre se crea un contacto nuevo: quien escribe el
  correo de otra persona no le asigna pedidos a su ficha. El equipo fusiona contactos
  repetidos si hace falta.
- **Otro producto o cantidad en un paso del pedido** («quiero 500 vasos»): no es el pedido;
  el turno va al agente. Si el agente cotiza, el borrador del pedido se reinicia y la
  cotización anterior queda registrada sin pedido. Con una «@» en el mensaje se lee como
  dato del pedido.
- **«¿Puedo comprarla con tarjeta?»** y cualquier pregunta con «?» sin nombre ni correo van
  al agente sin perder el paso, con el recordatorio una sola vez (US4, escenario 3). Si el
  agente cotiza otra cosa en ese turno, el borrador se reinicia.
- **«No quiero hacer el pedido»**: la negación nunca acepta el pedido; es un rechazo.
- **Cancelar en cualquier paso**: «ahora no», «no, mejor no», «cancelar» u «olvídalo» dejan
  la cotización registrada sin pedido y borran los datos parciales.
- **Nombre inválido**: con cifras o símbolos, con menos de dos letras, de más de seis
  palabras o con una muletilla o palabra del pedido («ok», «gracias», «listo», «mejor»,
  «quiero», «pedido»…) no es un nombre. Las muletillas de los bordes se quitan («hola, Ana»
  → «Ana»), las partículas de apellido van en minúscula y el nombre se recorta a 80
  caracteres.
- **Nombre ya dado**: una palabra suelta no lo reemplaza; solo un mensaje con un correo o con
  una marca («me llamo…», «mi nombre es…», «a nombre de…», «soy…»).
- **Línea sin precio publicado**: entra al pedido a $0 con «Precio por confirmar con el
  vendedor» en su descripción y la nota «Sin precio publicado: …».
- **Envío**: con costo confirmado entra como línea de servicio `ENVIO-CARVAJAL`; pedido y sin
  costo, queda la nota «Envío por confirmar con el vendedor.».
- **SKU que Odoo no tiene**: se crea en el momento con el precio de la línea; si está
  archivado, se reactiva.
- **Odoo falla a medias**: si la oportunidad, la nota del chatter o «ganada» fallan, el pedido
  sigue y queda un aviso en los logs; si falla el pedido, la persona recibe el mensaje de
  reintento y sus datos quedan en la confirmación.

## Requirements *(mandatory)*

### Functional Requirements

**Oferta y aceptación**

- **FR-001**: El folio (T6) DEBE ofrecer el pedido solo si el CRM está configurado
  (`ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY`); sin CRM, el flujo es el de la 054.
- **FR-002**: El pedido se acepta con un «sí» a la oferta o con una frase de compra («quiero
  comprarla», «hagamos el pedido», «la compro», «hacer el pedido»); una frase con producto,
  cantidad, «?» o negación («no quiero hacer el pedido») no acepta el pedido.
- **FR-003**: «Ahora no» y equivalentes («no, mejor no», «no quiero el pedido», «ya no»,
  «todavía no, gracias»…) dejan la cotización registrada sin pedido en cualquier paso del
  pedido; «hacer el pedido» lo retoma mientras el borrador siga en el folio.
- **FR-004**: La web DEBE mostrar los botones «Hacer el pedido» / «Ahora no» con la oferta y
  «Confirmar pedido» / «Ahora no» con la confirmación.

**Datos de quien compra**

- **FR-005**: Nombre y correo DEBEN leerse por reglas, en uno o dos mensajes (el correo
  también dictado: «arroba», «punto», «guion», «guion bajo»), y pedir lo que falte. Una
  respuesta a la oferta que ya trae nombre o correo se toma como los datos.
- **FR-006**: Ninguna respuesta de los pasos del pedido (`pedido`, `datos_pedido`,
  `confirmar_pedido`) DEBE ir a Jev. Un mensaje de esos pasos en el que las reglas leen un
  nombre o un correo NUNCA DEBE ir al LLM; una pregunta con «?» y sin datos va al agente sin
  perder el paso.
- **FR-007**: Los datos viven en el borrador (base local) solo mientras se piden: DEBEN
  vaciarse al crear el pedido y al rechazarlo. Si Odoo falla, se conservan para reintentar.
- **FR-022**: Con los dos datos, el asistente DEBE mostrarlos y preguntar «¿Lo confirmo?»
  antes de crear el pedido; solo un «sí» (o una frase de compra) lo crea.
- **FR-023**: En la confirmación, un correo nuevo o un nombre con marca DEBE corregir el dato
  y volver a mostrar la confirmación.
- **FR-024**: Tras una pregunta a mitad del pedido, la respuesta del agente DEBE terminar
  una vez con el recordatorio del paso; si el mensaje siguiente tampoco sigue el pedido, el
  paso se suelta. Si el agente cotiza otra cosa, el borrador se reinicia.

**Pedido en Odoo**

- **FR-008**: El pedido DEBE ser idempotente por folio (`client_order_ref`, sin contar los
  cancelados): un pedido confirmado se devuelve como existente y uno que quedó en borrador
  se confirma, nunca se anuncia como confirmado sin confirmarlo.
- **FR-009**: Ante un fallo de Odoo, la persona DEBE recibir el mensaje de reintento y poder
  repetirlo con un «sí»; la cotización no se pierde.
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
- **FR-015**: El pedido DEBE confirmarse en Odoo con `send_email` para que Odoo envíe el
  correo de confirmación de ventas (plantilla `sale.mail_template_sale_confirmation` con el
  PDF). Sin SMTP el correo no sale; la respuesta al cliente no promete un envío ya hecho
  («La confirmación del pedido llega a …»).
- **FR-016**: DEBE quedar traza: nota HTML en el chatter del pedido con el canal y el folio, y
  una oportunidad del CRM creada después de confirmar, ligada al pedido y marcada como
  ganada; si esas trazas fallan, el pedido sigue.
- **FR-025**: El cliente de Odoo se reutiliza solo si coinciden el correo (sin comodines) y el
  nombre; si no, se crea uno nuevo.

**Catálogo**

- **FR-017**: El catálogo de Colombia DEBE poder sincronizarse con Odoo (un producto por SKU,
  precio por caja de la ficha o de la primera oferta en COP) más el servicio de envío, de
  forma idempotente; un producto archivado se reactiva en lugar de duplicarse.
- **FR-018**: Con `--imagenes`, la foto del catálogo DEBE subirse solo a los productos que no
  tengan una.

### Privacidad

- **FR-019**: Nombre y correo solo viajan a Odoo. Los logs del pedido llevan el folio, el
  número del pedido y, ante un error, el código HTTP y el tipo; nunca el nombre, el correo ni
  el mensaje de Odoo.
- **FR-020**: El turno con datos del pedido se guarda y se monitorea sin ellos: la pregunta
  como «🛒 (datos para el pedido)», y la respuesta (texto, voz y tarjeta del flujo) con el
  correo enmascarado (`a***@dominio`) y el nombre como «(tu nombre)». La persona ve el texto
  completo en el turno. El historial que llega a DeepSeek en los turnos siguientes y el
  resumen de memoria salen de lo guardado.
- **FR-021**: La API key de Odoo es la de un usuario técnico (`carvajal-bot`), vive solo en el
  entorno del servidor y no se registra en logs ni en salidas.

### Key Entities

- **Borrador** (054): agrega `awaiting` `pedido`, `datos_pedido` y `confirmar_pedido`,
  `buyer` (nombre y correo mientras se piden) y `order` (número del pedido en Odoo).
- **Comprador**: nombre y correo; los dos son obligatorios.
- **Solicitud de pedido**: folio, comprador, canal, ciudad, líneas, total y notas.
- **Línea de pedido**: SKU, nombre, descripción, cantidad de presentaciones, precio por
  presentación y si es servicio.
- **Pedido** (Odoo): número visible (`S00001`), identificador, oportunidad y si ya existía.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un recorrido real folio → «sí» → nombre y correo → «sí, confírmalo» deja en
  Odoo un pedido confirmado con producto y envío, la oportunidad ganada y el correo de
  confirmación con PDF en el chatter.
- **SC-002**: Repetir el pedido del mismo folio da 0 pedidos nuevos (pruebas y Odoo real).
- **SC-003**: En las pruebas, 0 llamadas a Jev en los pasos del pedido y 0 turnos con el
  nombre o el correo leídos por las reglas que pasen por el modelo.
- **SC-004**: Sin `ODOO_*`, el folio no ofrece el pedido (0 cambios frente a la 054).
- **SC-005**: Una segunda pasada del catálogo no crea productos ni sube fotos repetidas.
- **SC-006**: `make check` en verde.
- **SC-007**: Los mensajes guardados de un recorrido de pedido no contienen el nombre ni el
  correo en claro.

## Assumptions

- Odoo corre en la misma VPS que la app y la app lo alcanza por la red de Docker; no se
  publica a internet (solo `127.0.0.1:8169`).
- Los precios del pedido son los de referencia del catálogo y del motor (con su vendedor y
  vigencia); el pago y la entrega los coordina un asesor después.
- La base de Odoo es de demostración («Carvajal Empaques (demo)»), en COP y sin impuestos por
  defecto.
- El correo sale con el servidor SMTP que configure el owner. Hasta entonces Odoo deja el
  mensaje en el chatter y el `mail.mail` en excepción, sin reintentarlo solo: se reenvía
  desde Odoo cuando haya servidor.

## Fuera de alcance y pendientes

- **SMTP**: el correo de confirmación no sale hasta configurar el servidor de correo saliente
  (credenciales del owner); ver [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente).
- **Verificación del correo** (riesgo residual aceptado): cualquiera puede escribir un correo
  ajeno y Odoo le enviará la confirmación del pedido a esa persona. Lo mitigan el paso de
  confirmación, el cupo diario por visitante o número (`DAILY_REQUESTS_LIMIT`) y la allowlist
  de WhatsApp; el chat web solo tiene el cupo. No hay verificación del correo (código o
  enlace): queda pendiente.
- **Pago**: ni enlace de pago ni pasarela; el asesor lo coordina.
- **Inventario y entrega**: sin `stock`, reservas ni fechas de entrega en Odoo.
- **Catálogo de México**: solo se sincroniza Colombia (COP).
- **Estado del pedido de vuelta al chat**: el asistente no consulta en Odoo si el pedido se
  pagó, se entregó o se canceló.
- Facturación electrónica e impuestos calculados por Odoo.

## Revisión adversarial (2026-11-26)

Una revisión adversarial de la primera versión confirmó 24 hallazgos; `b893c28` los corrige
con el diseño descrito arriba. El resumen, agrupado en las nueve correcciones, está en
[evidencia.md §5](./evidencia.md#5-revisión-adversarial-2026-11-26).
