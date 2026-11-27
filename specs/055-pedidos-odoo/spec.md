# Feature Specification: Pedidos en Odoo tras la cotización

**Feature Branch**: `feat/odoo-pedidos`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26) en la rama, PR #279; producción sigue en `main` hasta la
fusión. Revisión adversarial del 2026-11-26 corregida en `b893c28` (paso de confirmación,
privacidad, idempotencia y Odoo); la segunda ronda, en `fcbf3a2` (reglas explícitas por paso
y recibo del pedido en PDF por web y WhatsApp). Las pruebas se actualizan y la suite se corre
en la misma PR. Correo saliente pendiente (SMTP). Evidencia: [evidencia.md](./evidencia.md).

**Input**: Pedido del owner (2026-11-26): «que el agente de la app pueda crear órdenes de
compra si el cliente confirma una compra de una cotización, por chat web o por WhatsApp»;
«que envíe correo con la confirmación al cliente y que quede traza y alguna orden en el
odoo»; «registra el catálogo en odoo también». Pidió nombre y correo del cliente y «lo más
rápido posible pero que funcione». Después (2026-11-26): «si la orden se confirma, se debe
guardar en el odoo y se debe enviar un correo, además de que se envíe una copia o recibo al
cliente (whatsapp o web)».

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

### Cómo se cumple el pedido del owner sobre el recibo

| Lo que pidió | Cómo se cumple |
|---|---|
| «se debe guardar en el odoo» | El «sí» a la confirmación crea un `sale.order` confirmado (`state = sale`) con el folio en `client_order_ref`, el cliente, las líneas, la nota en el chatter y la oportunidad ganada |
| «se debe enviar un correo» | Odoo lo envía al confirmar (`action_confirm` con `send_email`, plantilla `sale.mail_template_sale_confirmation`) con el PDF del pedido adjunto. **Solo sale con servidor SMTP**: hasta que el owner entregue las credenciales queda en el chatter y en excepción |
| «una copia o recibo al cliente (whatsapp o web)» | La respuesta del chat es el recibo en texto (número del pedido, folio, fecha, líneas, total e IVA, correo de la confirmación) y entrega el **PDF del pedido que generó Odoo** (el mismo que adjunta al correo): en la web, el enlace «📄 Descarga el recibo de tu pedido S…» a `/pedidos/{folio}.pdf`, que abre con la cookie del visitante dueño; en WhatsApp, el documento adjunto (`ADJUNTAR_PDF:` con `MEDIA_BASE_URL`) o un enlace firmado (`PUBLIC_BASE_URL`). La firma usa un dominio propio del recibo (un enlace de cotización no abre el pedido) y vence a los 7 días. Si Odoo no responde, la ruta da 503 y el enlace se puede reintentar |

El recibo del chat no depende de SMTP: llega aunque el correo todavía no salga.

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
4. **Nombre y correo por reglas explícitas por paso** (segunda ronda de la revisión). Se
   leen sin el modelo y sin Jev, en uno o dos mensajes (también dictados por voz), y Jev no
   clasifica ninguna respuesta de los pasos del pedido. Cada paso sabe qué espera: en la
   oferta nadie pidió todavía el nombre, así que solo cuentan un correo o un nombre escrito
   como nombre propio; en `datos_pedido` el mensaje es el nombre que se pidió; en
   `confirmar_pedido` cualquier dato corrige y solo un «sí» crea el pedido. Reemplaza la
   primera versión, que adivinaba un nombre en cualquier frase y lo defendía solo con una
   lista de palabras prohibidas y con «un nombre ya dado no cambia sin marca».
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
8. **El recibo es el PDF del pedido de Odoo, no uno propio.** Odoo ya genera el PDF del
   pedido al confirmarlo (lo adjunta al correo y queda como `ir.attachment` del
   `sale.order`). La app lo lee por la API (`OdooOrders.order_pdf(folio)`) y lo sirve al
   dueño de la cotización: el cliente recibe el mismo documento que el correo y que ve el
   equipo en Odoo, sin una segunda plantilla que mantener. En el chat va además el recibo en
   texto, que no depende de Odoo ni de SMTP.

### Relación con especificaciones existentes

- **054** (flujos de cotización): el paso del pedido sigue a T6 sobre el mismo borrador
  (`quote_drafts`), con tres preguntas pendientes nuevas: `pedido`, `datos_pedido` y
  `confirmar_pedido`. La 054 recibe una enmienda que enlaza esta spec. El recibo reutiliza
  sus dos pruebas de dueño del PDF de la cotización (cookie del visitante y enlace firmado
  con `SESSION_SECRET`), con otro dominio de firma.
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
   crea el pedido y la respuesta es el recibo: «🧾 **Pedido S00001 confirmado** · cotización
   #folio», la fecha, las líneas numeradas, el total con el IVA y «La confirmación del pedido
   llega a **ana@correo.com**», y termina con «📄 [Descarga el recibo de tu pedido
   S00001](…/pedidos/<folio>.pdf)».
5. **Given** el pedido creado, **When** la persona dice otra vez «quiero comprarla», **Then**
   responde «Ese pedido ya quedó creado con el número **S00001**» y no crea otro.
6. **Given** todo el paso del pedido, **When** se cuenta lo que pasó por el modelo, **Then**
   solo la cotización inicial usó el agente.
7. **Given** la oferta, **When** la persona responde directamente con «Ana Pérez,
   ana@correo.com» (sin un «sí» antes), **Then** se toman como los datos del pedido y pasa a
   la confirmación, sin Jev ni el modelo.
8. **Given** el pedido confirmado en la web, **When** la persona abre el enlace del recibo con
   su cookie, **Then** descarga el PDF del pedido de Odoo (`pedido-<folio>.pdf`); otro
   visitante, sin cookie o con un folio ajeno, recibe 404, y con Odoo caído, 503.

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
3. **Given** el pedido confirmado por WhatsApp con `MEDIA_BASE_URL`, **When** llega la
   respuesta, **Then** la promesa del recibo se cambia por «📄 Te envío el recibo de tu pedido
   S… en PDF.» y la línea `ADJUNTAR_PDF: <url firmada>`, que OpenClaw envía como documento;
   sin `MEDIA_BASE_URL` y con `PUBLIC_BASE_URL`, por el enlace firmado; sin ninguna, la
   promesa se quita.

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
7. **Given** la confirmación, **When** la persona escribe un correo nuevo, «me llamo Carlos
   Ruiz», «no, es Carla Ruiz», «sí, pero a nombre de Marta Gómez» o el nombre escrito como
   nombre propio («Marta Gómez»), **Then** el dato se corrige y vuelve a mostrar la
   confirmación con los datos nuevos; todavía no crea el pedido.
8. **Given** la confirmación, **When** la persona escribe solo «no» o «está mal», **Then**
   pregunta «¿Qué dato corrijo? Escríbeme el **nombre** o el **correo** correcto, o **ahora
   no** para dejar el pedido.» y conserva los datos.
9. **Given** la oferta, **When** la persona responde «Sí, adelante», «sí señor», «está bien» o
   «cuéntame de la empresa», **Then** ninguna se toma como nombre: las afirmaciones aceptan
   el pedido y la frase sigue al resto de las reglas.

---

### User Story 4 - «Ahora no», interrupciones y reintento ante un fallo (Priority: P2)

La persona no quiere el pedido todavía, pregunta otra cosa a mitad del pedido, o Odoo no
responde. La cotización sigue registrada y el pedido se puede pedir después.

**Why this priority**: el pedido es opcional y un fallo externo no debe perder la cotización.

**Independent Test**: los escenarios de «ahora no» y de fallo de Odoo de
`tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** cualquier paso del pedido, **When** la persona dice «Ahora no» (o «no, mejor
   no», «no quiero el pedido», «ya no», «todavía no, gracias», «lo pienso», «después»,
   «cancela el pedido», «cancela todo»), **Then** responde «Listo, tu cotización #folio queda
   registrada. Si decides comprar, escribe «hacer el pedido». ¿Te ayudo con algo más?», borra
   los datos parciales y no llama a Odoo. Un «no» solo rechaza en la oferta y en los datos; en
   la confirmación pregunta qué corregir (US3, escenario 8).
2. **Given** «ahora no», **When** en el mensaje siguiente escribe «hacer el pedido», **Then**
   el asistente vuelve a pedir nombre y correo.
3. **Given** un paso del pedido, **When** la persona pregunta algo con «?» («¿cuánto tarda la
   entrega?»), **Then** la pregunta va al agente, el paso sigue pendiente y la respuesta
   termina una sola vez con el recordatorio («↩️ ¿Seguimos con el pedido de tu cotización?»,
   «↩️ Para el pedido me faltan tu **nombre** y tu **correo**.» o «↩️ ¿Confirmo el pedido con
   los datos que me diste?»); si el mensaje siguiente tampoco sigue el pedido, el paso se
   suelta.
5. **Given** un paso del pedido, **When** la persona pide otro producto («quiero 500 vasos»)
   sin correo ni nombre con marca, **Then** el turno sale del pedido con un borrador nuevo,
   cotice o no el agente en ese turno: no hay recordatorio del pedido y el mensaje siguiente
   («Bogotá», una referencia) no se lee como nombre.
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
🧾 **Pedido S00001 confirmado** · cotización #<folio>        ← el recibo (render.order_placed)
Fecha: 26 nov 2026
1. <producto> × <unidades> · $50.000
Total **$58.000** · <IVA>
La confirmación del pedido llega a **ana@correo.com**. Un asesor te contacta para coordinar
el pago y la entrega.
📄 Te envío el recibo del pedido en PDF.                     ← RECEIPT_PROMISE; cada canal la cambia
                                                             ← awaiting = ninguno; order = S00001
```

Las líneas y el total salen del borrador (las mismas de la cotización registrada); con el
envío por confirmar, el total es el de los productos con «(envío por confirmar)». La fecha es
la de hoy en Bogotá. La última línea, `RECEIPT_PROMISE`, no llega así a nadie:

- **Web** (`apps/api/views/stream.py` y el historial en `apps/api/views/chat.py`):
  `render.receipt_offer` la cambia por «📄 [Descarga el recibo de tu pedido S00001](<url>)»,
  con la URL de `/pedidos/{folio}.pdf` (sobre `PUBLIC_BASE_URL` si está). Sin URL, la línea
  se quita.
- **WhatsApp** (`apps/mcp/bridge.py`, `_with_receipt`): el servicio marca el turno con
  `flow.order = {name, folio}`; con `MEDIA_BASE_URL` la promesa pasa a «📄 Te envío el
  recibo de tu pedido S00001 en PDF.» más `ADJUNTAR_PDF: <url firmada>` (OpenClaw lo envía
  como documento, como el PDF de la cotización); con solo `PUBLIC_BASE_URL`, a «📄 Recibo de
  tu pedido S00001 en PDF. Ábrelo aquí:» y el enlace firmado; sin ninguna, se quita.

«Hacer el pedido» envía «Sí, quiero hacer el pedido» y «Confirmar pedido» envía «Sí,
confírmalo». En voz, cada plantilla tiene su versión hablada sin Markdown ni emojis; la
confirmación dice el nombre pero no lee el correo («Voy a hacer el pedido a nombre de Ana
Pérez, con el correo que escribiste. ¿Lo confirmo?») y la del pedido dice «Listo, tu pedido
quedó confirmado. La confirmación llega a tu correo, te envío el recibo por escrito y un
asesor te contacta para el pago y la entrega.». En WhatsApp la negrita sale con `*` como en
la 054.

### Reglas por paso

`flow._order_step` decide cada mensaje con reglas explícitas del paso en curso
(`quotation/flow.py` y `quotation/order.py`). Antes de mirar el paso:

- **Otro producto**: si el mensaje nombra un producto (`product_family`) y no trae un correo
  ni un nombre con marca, sale del pedido con un **borrador nuevo** (`Pass(QuoteDraft())`):
  la cotización anterior queda registrada sin pedido, cotice o no el agente en ese turno.
- **Correo**: `find_email` lee el correo escrito o dictado («arroba», «punto», «guion»,
  «guion bajo»). Un correo en el mensaje siempre es un dato del pedido.

| Paso (`awaiting`) | Mensaje | Resultado |
|---|---|---|
| `pedido` (la oferta) | Un correo, o un nombre con marca («soy…», «me llamo…», «a nombre de…») o escrito como nombre propio («Ana Pérez», cada palabra con mayúscula; `name_in(strict=True)`), y que no sea una afirmación | Son los datos: pasa a pedir el que falte o a la confirmación |
| | «ahora no», «no», «no quiero el pedido», «cancela el pedido», «cancela todo»… | Rechazo (`render.order_declined`) |
| | «sí», «sí, adelante», «dale», «está bien», «quiero comprarla», «hagamos el pedido» (`is_affirmation`, `wants_purchase`) | Pide nombre y correo (`datos_pedido`) |
| | Otra cosa (una pregunta, «cuéntame de la empresa», un nombre en minúsculas) | Sigue el resto de las reglas y el agente; el paso queda con el recordatorio único |
| `datos_pedido` | Una pregunta sin datos antes del «¿» (`_question_only`) | Va al agente sin perder el paso, con el recordatorio único |
| | Rechazo sin correo ni nombre con marca | Rechazo |
| | Lo demás | El correo del mensaje y el nombre pedido: el nombre con marca, el que ya se había dado o, si falta y el mensaje no es una afirmación, el texto del mensaje como nombre (`name_in`, que descarta cifras, símbolos y muletillas) |
| `confirmar_pedido` | Un correo o un nombre con marca | Corrige ese dato y vuelve a mostrar la confirmación; nunca crea el pedido en ese turno, aunque diga «sí» o «quiero hacer el pedido» |
| | «sí», «sí, confírmalo», «quiero comprarla» con los dos datos | **Crea el pedido** (es el único camino) |
| | «no», «nop», «no es», «está mal», «incorrecto», «hay un error» (`is_bare_no`) | Pregunta qué corregir (`render.which_correction`); los datos siguen |
| | «no, es Marta Gómez», «no, el nombre es…» (`correction_name`) | Corrige el nombre |
| | «ahora no», «no, mejor no», «cancela el pedido», «cancela todo» sin un nombre de corrección | Rechazo (excepción conocida en [Edge Cases](#edge-cases): «olvídalo», «cancélalo») |
| | Una pregunta sin datos | Va al agente sin perder el paso, con el recordatorio único |
| | Un nombre escrito como nombre propio («Marta Gómez») que no es una afirmación | Corrige el nombre; «pues bueno» no lo cambia |
| | Lo demás | Vuelve a mostrar la confirmación con los mismos datos |

Tras crear el pedido, «quiero comprarla» o un «sí» suelto responden «Ese pedido ya quedó
creado con el número **S…**» y no llaman a Odoo; tras un rechazo, «quiero comprarla» o «hacer
el pedido» vuelven a pedir nombre y correo.

**Recordatorio único** (`flow._order_off_flow`): cuando el agente responde un turno a mitad
del pedido y no cotiza, la respuesta termina con `render.order_reminder` del paso; si el
mensaje siguiente tampoco sigue el pedido, el paso se suelta con un borrador nuevo.

**Turnos con datos**: todo turno que pasa por `_with_buyer` (los que leen o muestran nombre o
correo) lleva `Reply.private`; el servicio lo guarda y lo monitorea sin los datos (FR-020).

### En Odoo

Al crear el pedido (`adapters/crm/odoo.py`, `OdooOrders.place`), en este orden:

1. Busca un pedido con `client_order_ref` = folio y estado distinto de `cancel`. Si está
   confirmado (`sale`), lo devuelve como existente y no crea otro pedido; si le falta la
   oportunidad (la respuesta de Odoo se perdió tras confirmar), la crea.
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

`action_confirm` que responde 422 porque otro turno ya confirmó el pedido no es fallo
(`_confirm` relee el estado). Para el recibo, `OdooOrders.order_pdf(folio)` busca el pedido
del folio en `sale` y devuelve el último `ir.attachment` PDF de ese `sale.order` (el que Odoo
adjuntó al correo de confirmación), o `None`.

### Edge Cases

- **Doble aceptación**: un segundo «sí», «quiero comprarla» o «hacer el pedido» tras crear el
  pedido responde el número que ya existe y no llama a Odoo.
- **Dos «sí» casi simultáneos** sobre el mismo folio: si el segundo encuentra el pedido en
  borrador cuando el primero ya lo confirmó, el 422 de `action_confirm` no es fallo
  (`OdooOrders._confirm` relee el estado y sigue si está en `sale`). Si los dos turnos buscan
  el folio antes de que ninguno cree el pedido, pueden quedar dos pedidos: no hay bloqueo por
  conversación (riesgo residual; ver [Fuera de alcance y pendientes](#fuera-de-alcance-y-pendientes)).
- **Mismo folio en Odoo**: si el pedido ya está confirmado (reintento del turno, doble clic),
  Odoo devuelve el existente; no hay un segundo pedido. Si quedó en borrador, el reintento lo
  confirma; un pedido cancelado no cuenta y se crea otro.
- **Cliente que ya existe**: se reutiliza si coinciden el correo y el nombre, y no se cambia
  su ficha. Con el mismo correo y otro nombre se crea un contacto nuevo: quien escribe el
  correo de otra persona no le asigna pedidos a su ficha. El equipo fusiona contactos
  repetidos si hace falta.
- **Otro producto en un paso del pedido** («quiero 500 vasos»): no es el pedido; el turno va
  al agente con un borrador nuevo, cotice o no (si pide la ciudad o lista referencias, su
  lista sigue viva y no se mezcla el recordatorio del pedido). La cotización anterior queda
  registrada sin pedido. Con un correo (escrito o dictado) o un nombre con marca en el
  mensaje, se lee como dato del pedido.
- **Cifras sin producto** (un teléfono, una dirección, un correo dictado con números): ya no
  sacan del paso. Un teléfono o una dirección no son nombre ni correo, así que se vuelven a
  pedir los datos, sin ir al modelo.
- **«¿Puedo comprarla con tarjeta?»** y cualquier pregunta sin datos antes del «¿» van al
  agente sin perder el paso, con el recordatorio una sola vez (US4, escenario 3).
- **«No quiero hacer el pedido»**: la negación nunca acepta el pedido; es un rechazo.
- **Cancelar en cualquier paso**: «ahora no», «no, mejor no», «cancela el pedido», «cancela
  todo» o «cancelar» dejan la cotización registrada sin pedido y borran los datos parciales.
  Un «no, …» seguido de un correo o de un nombre corrige; no cancela. En la confirmación,
  «olvídalo» y «cancélalo» todavía se leen como un nombre de corrección (se ven en la
  confirmación y no crean el pedido): pendiente en [evidencia.md
  §7](./evidencia.md#7-pendiente).
- **«No» ante «¿Lo confirmo?»**: un «no» solo o «está mal» preguntan qué dato corregir y
  conservan los datos; para dejar el pedido, «ahora no».
- **Nombre inválido**: con cifras o símbolos, con menos de dos letras, de más de seis
  palabras o con una muletilla o palabra del pedido («ok», «gracias», «listo», «mejor»,
  «quiero», «pedido», «adelante», «señor»…) no es un nombre. Las muletillas de los bordes se
  quitan («hola, Ana» → «Ana»; «sí, pero a nombre de Marta» → «Marta»), las partículas de
  apellido van en minúscula y el nombre se recorta a 80 caracteres. Esta lista ya no es la
  única defensa: cada paso decide primero si espera un nombre (ver [Reglas por
  paso](#reglas-por-paso)).
- **Nombre en la oferta**: solo cuenta escrito como nombre propio o con marca; «ana pérez» en
  minúsculas como respuesta a la oferta no se toma como dato (sigue el resto de las reglas).
- **Correo dictado con cifras sueltas** («ana punto perez 22 arroba…»): la lectura del correo
  puede quedar incompleta; la confirmación lo muestra antes de crear el pedido y la persona
  lo corrige escribiéndolo.
- **Línea sin precio publicado**: entra al pedido a $0 con «Precio por confirmar con el
  vendedor» en su descripción y la nota «Sin precio publicado: …».
- **Envío**: con costo confirmado entra como línea de servicio `ENVIO-CARVAJAL`; pedido y sin
  costo, queda la nota «Envío por confirmar con el vendedor.».
- **SKU que Odoo no tiene**: se crea en el momento con el precio de la línea; si está
  archivado, se reactiva.
- **Odoo falla a medias**: si la oportunidad, la nota del chatter o «ganada» fallan, el pedido
  sigue y queda un aviso en los logs; si falla el pedido, la persona recibe el mensaje de
  reintento y sus datos quedan en la confirmación. Si Odoo confirmó pero la respuesta se
  perdió, el reintento encuentra el pedido en `sale` y crea la oportunidad si faltaba.
- **Recibo con Odoo caído**: `/pedidos/{folio}.pdf` responde 503 («Servicio no disponible.»)
  y el enlace sirve después; el recibo en texto ya está en el chat.
- **Recibo sin prueba de dueño**: sin cookie del dueño ni firma válida, con la firma vencida,
  con un enlace de cotización (otro dominio de firma) o con un folio sin pedido confirmado,
  404 «El pedido no existe o el enlace venció.», sin revelar si el folio existe.
- **Reenvío de un turno del pedido** (mismo `client_turn_id`: reintento del webhook de
  WhatsApp, doble envío en la web): se repite lo guardado, que lleva el nombre como «(tu
  nombre)» y el correo enmascarado. No filtra datos; el texto se ve distinto del original.

## Requirements *(mandatory)*

### Functional Requirements

**Oferta y aceptación**

- **FR-001**: El folio (T6) DEBE ofrecer el pedido solo si el CRM está configurado
  (`ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY`); sin CRM, el flujo es el de la 054.
- **FR-002**: El pedido se acepta con una afirmación a la oferta («sí», «sí, adelante»,
  «dale», «está bien») o con una frase de compra («quiero comprarla», «hagamos el pedido», «la
  compro», «hacer el pedido»); una frase con producto, «?» o negación («no quiero hacer el
  pedido») no acepta el pedido, y una afirmación nunca se toma como nombre.
- **FR-003**: «Ahora no» y equivalentes («no, mejor no», «no quiero el pedido», «ya no»,
  «todavía no, gracias», «cancela el pedido», «cancela todo»…) dejan la cotización
  registrada sin pedido en cualquier paso del pedido; «hacer el pedido» lo retoma mientras el
  borrador siga en el folio. Un mensaje de rechazo que trae un correo o un nombre de
  corrección corrige el dato en vez de cancelar.
- **FR-004**: La web DEBE mostrar los botones «Hacer el pedido» / «Ahora no» con la oferta y
  «Confirmar pedido» / «Ahora no» con la confirmación.

**Datos de quien compra**

- **FR-005**: Nombre y correo DEBEN leerse por reglas explícitas por paso, en uno o dos
  mensajes (el correo también dictado: «arroba», «punto», «guion», «guion bajo»), y pedir lo
  que falte. En la oferta solo cuentan como datos un correo o un nombre con marca o escrito
  como nombre propio; en `datos_pedido`, el mensaje es el nombre pedido si no es una
  afirmación ni una muletilla.
- **FR-006**: Ninguna respuesta de los pasos del pedido (`pedido`, `datos_pedido`,
  `confirmar_pedido`) DEBE ir a Jev. Un mensaje de esos pasos en el que las reglas leen un
  nombre o un correo NUNCA DEBE ir al LLM; una pregunta con «?» y sin datos va al agente sin
  perder el paso.
- **FR-007**: Los datos viven en el borrador (base local) solo mientras se piden: DEBEN
  vaciarse al crear el pedido y al rechazarlo. Si Odoo falla, se conservan para reintentar.
- **FR-022**: Con los dos datos, el asistente DEBE mostrarlos y preguntar «¿Lo confirmo?»
  antes de crear el pedido; solo un «sí» (o una frase de compra) sin datos nuevos lo crea.
- **FR-023**: En la confirmación, un correo nuevo, un nombre con marca, un nombre tras «no,»
  o un nombre escrito como nombre propio DEBEN corregir el dato y volver a mostrar la
  confirmación, aunque el mensaje también diga «sí». Un «no» solo DEBE preguntar qué dato
  corregir, sin cancelar ni borrar los datos.
- **FR-024**: Tras una pregunta a mitad del pedido, la respuesta del agente DEBE terminar
  una vez con el recordatorio del paso; si el mensaje siguiente tampoco sigue el pedido, el
  paso se suelta. Otro producto en un paso del pedido DEBE salir del pedido con un borrador
  nuevo, cotice o no el agente en ese turno.

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

**Recibo del pedido**

- **FR-026**: La respuesta al pedido confirmado DEBE ser un recibo en el chat: número del
  pedido, folio, fecha, líneas, total con la situación del IVA y el correo al que llega la
  confirmación.
- **FR-027**: El cliente DEBE poder obtener el PDF del pedido que Odoo generó al confirmarlo
  (`OdooOrders.order_pdf`: el `ir.attachment` PDF del `sale.order` confirmado del folio):
  en la web, con el enlace a `GET /pedidos/{folio}.pdf`; en WhatsApp, como documento
  adjunto (`ADJUNTAR_PDF:` con `MEDIA_BASE_URL`) o como enlace firmado (`PUBLIC_BASE_URL`).
- **FR-028**: `/pedidos/{folio}.pdf` DEBE servir el PDF solo al dueño de la cotización del
  folio: la cookie del visitante web o un enlace firmado con HMAC (`SESSION_SECRET`) sobre
  un dominio propio del recibo (`quote_links.ORDER_DOMAIN`, distinto del de cotizaciones)
  que vence a los 7 días (`MAX_LINK_DAYS`). Cualquier otro caso DEBE responder 404 sin
  revelar si el folio existe.
- **FR-029**: Sin Odoo configurado o con Odoo sin responder, la ruta DEBE responder 503 y el
  enlace DEBE poder reintentarse.
- **FR-030**: Un canal que no pueda entregar el PDF (sin URL pública) DEBE quitar la promesa
  del recibo en vez de prometer un PDF que no llega.

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
- **Recibo**: el texto del pedido confirmado en el chat y el PDF del pedido en Odoo
  (`ir.attachment` del `sale.order`), servido en `/pedidos/{folio}.pdf` al dueño de la
  cotización; el turno lo marca con `flow.order = {name, folio}` para WhatsApp.

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
- **SC-008**: Tras un pedido confirmado, el dueño descarga el PDF del pedido de Odoo por la
  web (cookie) y por WhatsApp (adjunto o enlace firmado); un visitante ajeno o un enlace de
  cotización reciben 404 (pruebas y Odoo real).

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
- **Concurrencia** (riesgo residual): la idempotencia por folio es «buscar y luego crear», sin
  bloqueo por conversación ni restricción única en Odoo. Dos «sí» que se cruzan antes de que
  exista el pedido pueden crear dos pedidos con el mismo folio (y dos correos). Se tolera el
  caso del pedido ya confirmado por el otro turno (422); serializar por folio queda
  pendiente.
- **Reenvío con datos enmascarados** (aceptado): un turno del pedido reenviado con el mismo
  `client_turn_id` muestra lo guardado («(tu nombre)», `a***@dominio`) y no el texto que vio
  la persona. Es la consecuencia de no guardar los datos; no filtra nada.
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

Una segunda ronda sobre `b893c28` confirmó 21 hallazgos más (frases tomadas como nombre,
correcciones imposibles o que cancelaban, «cancela» sin efecto, datos con cifras que sacaban
del paso, otro producto que dejaba el paso pegado, turnos con datos guardados en claro,
concurrencia y Ruff). `fcbf3a2` los resuelve con las [reglas por paso](#reglas-por-paso);
el resumen está en
[evidencia.md §6](./evidencia.md#6-revisión-adversarial-segunda-ronda-2026-11-26).
