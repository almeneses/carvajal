# Feature Specification: Pedidos en Odoo tras la cotización

**Feature Branch**: `feat/odoo-pedidos`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26) y fusionada en `main` (PR #279, `9877548`); activa en
producción con `ODOO_*` y el SMTP de Gmail configurado en Odoo. Auditoría de cobertura del
2026-11-27 (48 huecos) corregida en `fix/pedidos-cobertura` ([evidencia.md
§9](./evidencia.md#9-auditoría-de-cobertura-2026-11-27)), y la revisión de esa rama (18
hallazgos confirmados, [evidencia.md §10](./evidencia.md#10-revisión-de-la-rama-de-cobertura-2026-11-27)). Revisión adversarial del 2026-11-26 corregida en `b893c28` (paso de confirmación,
privacidad, idempotencia y Odoo); la segunda ronda, en `fcbf3a2` (reglas explícitas por paso
y recibo del pedido en PDF por web y WhatsApp); la tercera, sobre `1209217` en la misma PR
(«sí» limpio, turnos privados, reglas generales primero, correo dictado con cifras, PDF y
contactos en Odoo, precios vencidos), y la verificación final (datos antes que las reglas
generales, despedidas, correcciones en minúsculas y correo hablado enmascarado), en
`7610e60`; `make check` en verde en la rama. Evidencia: [evidencia.md](./evidencia.md).

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
pedido, Odoo envía al cliente el correo de confirmación con el PDF, por el servidor SMTP de
Gmail configurado en Odoo (ver
[`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente)), y el pedido queda con la
actividad «Coordinar pago y entrega del pedido» para el vendedor.

### Cómo se cumple el pedido del owner sobre el recibo

| Lo que pidió | Cómo se cumple |
|---|---|
| «se debe guardar en el odoo» | El «sí» a la confirmación crea un `sale.order` confirmado (`state = sale`) con el folio en `client_order_ref`, el cliente, las líneas, la nota en el chatter y la oportunidad ganada |
| «se debe enviar un correo» | Odoo lo envía al confirmar (`action_confirm` con `send_email`, plantilla `sale.mail_template_sale_confirmation`) con el PDF del pedido adjunto, por el servidor SMTP de Gmail configurado en Odoo; el mensaje queda también en el chatter |
| «una copia o recibo al cliente (whatsapp o web)» | La respuesta del chat es el recibo en texto (número del pedido, folio, fecha, líneas, total e IVA, correo de la confirmación) y entrega el **PDF del pedido que generó Odoo** (el mismo que adjunta al correo): en la web, el enlace «📄 Descarga el recibo de tu pedido S…» a `/pedidos/{folio}.pdf`, que abre con la cookie del visitante dueño; en WhatsApp, el documento adjunto (`ADJUNTAR_PDF:` con `MEDIA_BASE_URL`) o un enlace firmado (`PUBLIC_BASE_URL`). La firma usa un dominio propio del recibo (un enlace de cotización no abre el pedido) y vence a los 7 días. Si Odoo no responde, la ruta da 503 y el enlace se puede reintentar |

El recibo del chat no depende del correo: llega aunque el servidor de correo falle.

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
   `confirmar_pedido` cualquier dato corrige y solo un «sí» limpio (sin reparos ni nombre
   nuevo; tercera ronda) crea el pedido. Reemplaza la primera versión, que adivinaba un
   nombre en cualquier frase y lo defendía solo con una lista de palabras prohibidas y con
   «un nombre ya dado no cambia sin marca». Las reglas generales (chiste por insulto al
   precio, «retomar», pedir un cambio del carrito) van antes que las del pedido, salvo un
   mensaje con datos (un correo o un nombre con marca), que se lee primero.
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
   texto, que no depende de Odoo ni de SMTP. Es el primer PDF del pedido creado por quien
   creó el pedido (el bot), no el más reciente: un documento interno del chatter nunca sale.
9. **Solo se reutilizan contactos del asistente** (tercera ronda). Un contacto de Odoo se
   reutiliza solo si lo creó el asistente (`res.partner.ref = asistente-carvajal`) y
   coinciden correo y nombre. La ficha de un cliente real del equipo trae dirección, NIT o
   teléfono, que saldrían en el PDF del pedido de quien escribió su nombre y su correo.
10. **Sin precios vencidos** (tercera ronda). El pedido usa los precios de la cotización; si
    la vigencia de alguna línea pasó, no se confirma y hay que cotizar de nuevo.

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
- **Prompts** (`carvajal/prompts.py`): `AGENT_SYSTEM`, `STRUCTURED_SYSTEM` y
  `COMMERCIAL_INSTRUCTIONS` no niegan el pedido (auditoría del 2026-11-27): el asistente no
  toma pagos ni reservas ni pide datos de pago o personales; si la persona quiere comprar,
  cotiza, y al confirmar la cotización el sistema ofrece el pedido. A mitad del pedido
  responde sin negarlo ni pedir datos (el sistema recuerda el paso) y nunca inventa el estado
  de un pedido.
- **Puente de WhatsApp** (`apps/mcp/bridge.py`): «PDF» a mitad del pedido y el recibo a
  pedido (FR-034 y FR-035).

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
   S… en PDF.» y la línea `ADJUNTAR_PDF: <url firmada>`, que OpenClaw envía como documento,
   solo si `MEDIA_BASE_URL` es https (o loopback): el recibo lleva datos personales; con
   `MEDIA_BASE_URL` http público o sin ella, por el enlace firmado de `PUBLIC_BASE_URL`
   (https); sin ninguna base segura, la promesa se quita.

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
   ana@example.com». Si prefieres no hacer el pedido ahora, escribe «ahora no».». El correo
   del ejemplo (`render.EXAMPLE_EMAIL`, dominio reservado por la RFC 2606) copiado tal cual
   cuenta como un correo que falta: nunca llega a Odoo.
4. **Given** «mi nombre es juan gómez y mi correo es JUAN.G@Empresa.co», **When** se lee,
   **Then** el nombre es «Juan Gómez» y el correo «juan.g@empresa.co».
5. **Given** «Juan, juan@x.co, gracias» o «maría de la cruz», **When** se leen, **Then** los
   nombres son «Juan» y «María de la Cruz».
6. **Given** el correo dictado «ana punto perez arroba gmail punto com», **When** se lee,
   **Then** el correo es «ana.perez@gmail.com» («guion» y «guion bajo» dan `-` y `_`). Con
   cifras sueltas o en palabras antes de «arroba» («ana punto perez dos dos arroba gmail
   punto com» o «… perez 22 arroba …») el correo es «ana.perez22@gmail.com»; en «Ana Pérez,
   ana punto perez dos dos arroba gmail punto com» el nombre queda «Ana Pérez». En voz, la
   confirmación lee el correo («…con el correo ana punto perez22 arroba gmail punto com.
   ¿Lo confirmo?»).
7. **Given** la confirmación, **When** la persona escribe un correo nuevo («quiero cambiar
   el correo a carla@gmail.com»), «me llamo Carlos Ruiz», «no, es Carla Ruiz», «sí, pero a
   nombre de Marta Gómez», «sí, pero cambia el nombre a Carla Ruiz», «sí, Carla Ruiz», el
   nombre escrito como nombre propio («Marta Gómez») o, tras «no», en minúsculas («no, carla
   ruiz»), **Then** el dato se corrige y vuelve a mostrar la confirmación con los datos
   nuevos; todavía no crea el pedido. Vale lo último tras «es» o «:»: «No es Ana, es Carla»
   corrige a «Carla»; «no es Ana es Carla Ruiz» y «corrige el nombre: Carla Ruiz», a «Carla
   Ruiz».
8. **Given** la confirmación, **When** la persona escribe solo «no», «está mal», «sí,
   pero…», «sí, pero el correo está mal», un cambio de los datos sin el dato («sí, pero
   cambia el correo», «el correo está mal», «quiero cambiar el nombre») o un «no» seguido de
   algo que no es cancelar ni un nombre («no, espera»), **Then** pregunta «¿Qué dato corrijo? Escríbeme
   el **nombre** o el **correo** correcto, o **ahora no** para dejar el pedido.», conserva
   los datos y no crea el pedido.
9. **Given** la oferta, **When** la persona responde «Sí, adelante», «sí señor», «está bien» o
   «cuéntame de la empresa», **Then** ninguna se toma como nombre: las afirmaciones aceptan
   el pedido y la frase sigue al resto de las reglas.
10. **Given** la oferta, **When** la persona responde «Sí, a nombre de Ana Pérez», «Sí, soy
    Ana», «Sí, Ana Pérez» o «Ana Pérez, quiero hacer el pedido», **Then** se toma el nombre, se pide el correo y el turno se
    guarda sin el nombre.

---

### User Story 4 - «Ahora no», interrupciones y reintento ante un fallo (Priority: P2)

La persona no quiere el pedido todavía, pregunta otra cosa a mitad del pedido, o Odoo no
responde. La cotización sigue registrada y el pedido se puede pedir después.

**Why this priority**: el pedido es opcional y un fallo externo no debe perder la cotización.

**Independent Test**: los escenarios de «ahora no» y de fallo de Odoo de
`tests/test_pedidos_odoo.py`.

**Acceptance Scenarios**:

1. **Given** cualquier paso del pedido, **When** la persona dice «Ahora no» (o «no, mejor
   no», «no quiero el pedido», «ya no», «todavía no, gracias», «lo pienso», «lo voy a
   pensar», «solo estaba mirando», «después», «olvídalo», «cancélalo», «no, olvídalo»,
   «cancela el pedido», «cancela todo») o se despide («chao», «chau», «adiós», «bye», «nos
   vemos», «gracias, adiós»), **Then** responde «Listo, tu cotización #folio queda
   registrada sin pedido. Si cambias de opinión, escribe «hacer el pedido» en tu próximo
   mensaje. ¿Te ayudo con algo más?», borra los datos parciales y no llama a Odoo; ninguna de
   esas frases se toma como nombre. Un «no» solo rechaza en la oferta y en los datos; en la
   confirmación pregunta qué corregir (US3, escenario 8) y solo cancela acompañado de
   palabras de cancelación («no, olvídalo», «no, gracias», «no quiero»).
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
6. **Given** la confirmación con precios cuya vigencia ya pasó, **When** la persona dice
   «sí», **Then** no se llama a Odoo y responde «Los precios de <líneas> ya vencieron, así
   que no puedo confirmar el pedido con esa cotización. Pídeme cotizarlos de nuevo y hacemos
   el pedido.»; el paso se cierra y los datos se borran.
7. **Given** un paso del pedido, **When** la persona escribe, sin correo ni nombre con
   marca, el insulto por precio («está muy caro, ladrones»), «retomar» o pide un cambio del
   carrito («quiero cambiar la 2 a 800»), **Then** lo deciden las reglas generales o el
   agente, como fuera del pedido, y no se crea el pedido. Un cambio de los datos («sí, pero
   cambia el correo») no es de las reglas generales: lo atiende el paso (US3, escenarios 7 y
   8).

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
ana@example.com»). A ese correo te llega la confirmación.    ← awaiting = datos_pedido

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
envío por confirmar, el total es el de los productos con «(envío por confirmar)»; si
alguna línea no tiene precio, el total es el de las que lo tienen con «sin <producto>
(precio por confirmar)», y si ninguna lo tiene, «Total por confirmar con el vendedor». La fecha es la de hoy en
Bogotá. El correo es el del cliente del pedido en Odoo (`OrderResult.email`): el que se
escribió o, en un reintento sobre un pedido del folio ya confirmado, el de ese pedido. La
última línea, `RECEIPT_PROMISE`, no llega así a nadie:

- **Web** (`apps/api/views/stream.py` y el historial en `apps/api/views/chat.py`):
  `render.receipt_offer` la cambia por «📄 [Descarga el recibo de tu pedido S00001](<url>)»,
  con la URL de `/pedidos/{folio}.pdf` (sobre `PUBLIC_BASE_URL` si está). Sin URL, la línea
  se quita. Un turno reemitido (mismo `client_turn_id`) llega como un solo «token» con el
  texto guardado, y `_sse` le aplica también `pdf_offer` y `receipt_offer`
  (`_with_links_in_token`): el reintento enlaza el PDF de la cotización y el recibo.
- **WhatsApp** (`apps/mcp/bridge.py`, `_with_receipt`): el servicio marca el turno con
  `flow.order = {name, folio}`; con `MEDIA_BASE_URL` la promesa pasa a «📄 Te envío el
  recibo de tu pedido S00001 en PDF.» más `ADJUNTAR_PDF: <url firmada>` (OpenClaw lo envía
  como documento, como el PDF de la cotización); con solo `PUBLIC_BASE_URL`, a «📄 Recibo de
  tu pedido S00001 en PDF. Ábrelo aquí:» y el enlace firmado; sin ninguna, se quita.

«Hacer el pedido» envía «Sí, quiero hacer el pedido» y «Confirmar pedido» envía «Sí,
confírmalo». En voz, cada plantilla tiene su versión hablada sin Markdown ni emojis; la
confirmación dice el nombre y lee el correo para que quien lo dictó lo compruebe («Voy a
hacer el pedido a nombre de Ana Pérez, con el correo ana arroba correo punto com. ¿Lo
confirmo?», `order.spoken_address`; lo guardado cambia ese correo leído por «el correo que
escribiste») y la del pedido dice «Listo, tu pedido
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
  «guion bajo», y las cifras sueltas o en palabras antes de «arroba»: «ana punto perez dos
  dos arroba gmail punto com» → `ana.perez22@gmail.com`). Un correo en el mensaje siempre
  es un dato del pedido.
- **Datos primero, después las reglas generales** (`_defers`, en los tres pasos): un correo
  o un nombre con marca se leen antes («quiero cambiar el correo a carla@gmail.com» corrige
  el correo). Sin datos, el insulto por precio (`is_price_insult`), «retomar» (`is_resume`) y
  un cambio del carrito (`wants_edit`: «quiero cambiar la 2 a 800») devuelven `None`: los
  decide el resto de las reglas o el agente, y el pedido no se crea. Un cambio de los datos
  sin el dato («sí, pero cambia el correo», «el correo está mal», «quiero cambiar el
  nombre»; `order.edits_buyer`) no cuenta como `wants_edit`: lo atiende el paso.
- **Marcas de nombre** (`name_marked`): «mi nombre es…», «el nombre es…», «me llamo…», «a
  nombre de…», «soy…» y «cambia el nombre a/por…».
- **Adioses** (`is_goodbye`): «chao», «chau», «adiós», «bye», «nos vemos», también tras
  «gracias», «bueno», «ok» o «listo» («gracias, adiós»). Dejan la cotización sin pedido
  en los tres pasos; en la confirmación un adiós nunca crea el pedido («ok, chao» cierra sin
  pedido y «sí, chao» pregunta qué corregir).
- **Preguntas**: `_without_questions` quita los «¿…?» del mensaje; lo que queda, antes o
  después de la pregunta, puede traer los datos. `_question_only` es una pregunta cuyo resto
  no trae correo ni nombre.

| Paso (`awaiting`) | Mensaje | Resultado |
|---|---|---|
| `pedido` (la oferta) | Un correo, o un nombre con marca («soy…», «me llamo…», «a nombre de…») o escrito como nombre propio en el mensaje sin preguntas («Ana Pérez», cada palabra con mayúscula; `name_in(strict=True)`), también tras un «sí» («Sí, a nombre de Ana Pérez», «Sí, soy Ana», «Sí, Ana Pérez») o antes de la primera coma («Ana Pérez, quiero hacer el pedido») | Son los datos: pasa a pedir el que falte o a la confirmación (turno privado) |
| | «ahora no», «no», «no quiero el pedido», «olvídalo», «cancélalo», «cancela el pedido», «cancela todo», «lo voy a pensar», «chao», «gracias, adiós» (`declines_order`, `is_cancel_all`, `is_leaving`, `is_goodbye`) | Rechazo (`render.order_declined`) |
| | «sí», «sí, adelante», «dale», «está bien», «quiero comprarla», «hagamos el pedido» (`is_affirmation`, `wants_purchase`) | Pide nombre y correo (`datos_pedido`) |
| | Otra cosa (una pregunta, «cuéntame de la empresa», un nombre en minúsculas) | Sigue el resto de las reglas y el agente; el paso queda con el recordatorio único |
| `datos_pedido` | Una pregunta sin datos fuera de los «¿…?» (`_question_only`) | Va al agente sin perder el paso, con el recordatorio único |
| | Rechazo o adiós sin correo ni nombre con marca (`declines_order`, `is_cancel_all`, `is_leaving`, `is_goodbye`) | Rechazo |
| | Lo demás | El correo del mensaje y el nombre pedido: el nombre con marca, el que ya se había dado o, si falta y el mensaje no es una confirmación (`is_confirmation`), el mensaje sin preguntas como nombre (`name_in`, que descarta cifras, símbolos y muletillas; «olvídalo», «chao» o «retomar» nunca son nombre) |
| `confirmar_pedido` | Un correo o un nombre con marca («quiero cambiar el correo a carla@gmail.com», «sí, pero cambia el nombre a Carla Ruiz») | Corrige ese dato y vuelve a mostrar la confirmación; nunca crea el pedido en ese turno, aunque diga «sí» o «quiero hacer el pedido» |
| | Una pregunta sin datos | Va al agente sin perder el paso, con el recordatorio único |
| | Un «sí» limpio: afirmación o frase de compra («sí», «sí, confírmalo», «ok», «listo», «quiero comprarla») sin «pero», «aunque», «cambia…», «corrig…» ni «mal», sin nombre de corrección y con los dos datos | **Crea el pedido** (es el único camino) |
| | Un nombre de corrección (`correction_name`): suelto («Marta Gómez») o tras «sí,» («sí, Carla Ruiz») escrito como nombre propio (`name_in(strict=True)`); tras «no» también en minúsculas («no, carla ruiz», «no, es Marta Gómez», «no, el nombre es…»); vale lo último tras «es» o «:» («no es Ana, es Carla» → «Carla»; «no es Ana es Carla Ruiz», «corrige el nombre: Carla Ruiz» → «Carla Ruiz») | Corrige el nombre |
| | «no», «nop», «no es», «está mal», «incorrecto», «hay un error» (`is_bare_no`), una afirmación con reparos («sí, pero…», «sí, pero el correo está mal») o un cambio de los datos sin el dato (`edits_buyer`: «sí, pero cambia el correo», «el correo está mal», «quiero cambiar el nombre») | Pregunta qué corregir (`render.which_correction`); los datos siguen |
| | Un rechazo o un adiós (`declines_order`, `is_cancel_all`, `is_leaving`, `is_goodbye`) con palabras de cancelación (`_cancels`: ahora, después, luego, todavía, olvídalo, cancela, déjalo, mejor, no quiero, gracias, pensar, ya no, por ahora, chao, adiós…) o «cancela todo»: «ahora no», «no, mejor no», «no, gracias», «olvídalo», «cancélalo», «no, olvídalo», «cancela el pedido», «lo voy a pensar», «chao» | Rechazo |
| | Lo demás («pues bueno», «déjalo», «no, espera», un nombre en minúsculas sin «no» delante) | Pregunta qué corregir (`render.which_correction`); los datos siguen |

Tras «no», el nombre puede venir en minúsculas: «no, carla ruiz» y «no, es carla» corrigen
el nombre y no se leen como rechazo. Un «no» seguido de algo que no es cancelar ni un nombre
pregunta qué corregir y no borra los datos.

Tras crear el pedido, «quiero comprarla» o un «sí» suelto responden «Ese pedido ya quedó
creado con el número **S…**» y no llaman a Odoo; tras un rechazo, «quiero comprarla» o «hacer
el pedido» vuelven a pedir nombre y correo.

**Recordatorio único** (`flow._order_off_flow`): cuando el agente responde un turno a mitad
del pedido y no cotiza, la respuesta termina con `render.order_reminder` del paso; si el
mensaje siguiente tampoco sigue el pedido, el paso se suelta con un borrador nuevo.

**Turnos con datos**: llevan `Reply.private` todo turno que pasa por `_with_buyer` (los que
leen o muestran nombre o correo, también la oferta con datos) y todos los que deciden las
reglas en `datos_pedido` y `confirmar_pedido`: el «sí» que crea el pedido, el rechazo
(`_declined`) y «¿Qué dato corrijo?». El servicio los guarda y los monitorea sin los datos
(FR-020). La pregunta que va al agente (`Pass`) y lo que deciden las reglas generales no son
turnos del pedido.

### En Odoo

Al crear el pedido (`adapters/crm/odoo.py`, `OdooOrders.place`), en este orden:

1. Busca un pedido con `client_order_ref` = folio y estado distinto de `cancel`. Si está
   confirmado (`sale`), lo devuelve como existente y no crea otro pedido; si le falta la
   oportunidad (la respuesta de Odoo se perdió tras confirmar), la crea.
2. Busca el cliente por correo con `=ilike` y el correo escapado (`%` y `_` literales),
   solo entre los contactos que creó el asistente (`ref = asistente-carvajal`,
   `PARTNER_REF`), y lo reutiliza solo si también coincide el nombre (sin distinguir
   mayúsculas ni espacios repetidos); si no, crea un contacto nuevo con ese `ref` (en
   `es_CO`, con la ciudad). Una ficha del equipo (sin el `ref`) nunca se reutiliza.
3. Si el pedido del folio quedó en borrador (un intento anterior falló antes de
   confirmarlo), le pone el cliente actual. Si no existe, busca los productos por SKU
   (también los archivados, que reactiva), crea los que falten y crea el pedido con las
   líneas y `origin` «Asistente Carvajal (canal) · cotización #folio».
4. Lo confirma con `action_confirm` y `send_email`.
5. Deja la nota en el chatter (`message_post` con `body_is_html=True`).
6. Si el pedido no tiene oportunidad, crea «Pedido de la cotización #folio», la liga al
   pedido (`opportunity_id`) y la marca como ganada. Si el CRM falla, el pedido sigue.

Antes de todo esto, el servicio (`ChatService._place_order`) comprueba la vigencia de los
precios (`expired_lines`): con alguna línea vencida no llama a Odoo y responde
`render.order_expired`.

`action_confirm` que responde 422 porque otro turno ya confirmó el pedido no es fallo
(`_confirm` relee el estado). `place` devuelve `OrderResult` con `email`: el del comprador al
crear o retomar el pedido y, si el pedido del folio ya estaba confirmado, el del cliente de
ese pedido (`res.partner`); es el correo que muestra el recibo. Para el recibo,
`OdooOrders.order_pdf(folio)` busca el pedido del folio en `sale` con su `create_uid` y
devuelve el **primer** `ir.attachment` PDF de ese `sale.order` creado por ese mismo usuario
(`create_uid` = el bot, `order="id asc"`): el que Odoo generó al confirmar y adjuntó al
correo, no un documento interno subido al chatter ni un PDF posterior. Sin pedido o sin
adjunto, `None`.

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
- **Cliente que ya existe**: se reutiliza solo un contacto que creó el asistente
  (`ref = asistente-carvajal`) con el mismo correo y el mismo nombre, y no se cambia su
  ficha. Con el mismo correo y otro nombre, o si la ficha es de un cliente real del equipo
  (sin ese `ref`, aunque coincidan correo y nombre), se crea un contacto nuevo: quien escribe
  el correo de otra persona no le asigna pedidos a su ficha, ni su dirección, NIT o teléfono
  salen en el PDF de su pedido. Los contactos que el asistente creó antes de la tercera ronda
  no llevan el `ref` y tampoco se reutilizan. El equipo fusiona contactos repetidos si hace
  falta.
- **Reintento con el correo corregido**: si Odoo confirmó un pedido pero la respuesta se
  perdió y la persona corrige el correo antes del «sí» de reintento, el pedido del folio ya
  existe con el correo anterior; el recibo muestra ese (el del pedido en Odoo), no el nuevo.
- **Precios vencidos**: si la vigencia del vendedor de alguna línea pasó antes del «sí»
  final, el pedido no se confirma (`render.order_expired`) y hay que cotizar de nuevo.
- **Recibo sin precios**: si ninguna línea tiene precio, el recibo dice «Total por confirmar
  con el vendedor» en lugar de un total de $0; si solo algunas, el total de las que lo tienen
  con «sin <producto> (precio por confirmar)».
- **Turno reemitido en la web**: el reintento con el mismo `client_turn_id` llega como
  «token» y enlaza igual el PDF de la cotización y el recibo (`_with_links_in_token`).
- **Otro producto en un paso del pedido** («quiero 500 vasos»): no es el pedido; el turno va
  al agente con un borrador nuevo, cotice o no (si pide la ciudad o lista referencias, su
  lista sigue viva y no se mezcla el recordatorio del pedido). La cotización anterior queda
  registrada sin pedido. Con un correo (escrito o dictado) o un nombre con marca en el
  mensaje, se lee como dato del pedido.
- **Cifras sin producto** (un teléfono, una dirección, un correo dictado con números): ya no
  sacan del paso. Un teléfono o una dirección no son nombre ni correo, así que se vuelven a
  pedir los datos, sin ir al modelo.
- **«¿Puedo comprarla con tarjeta?»** y cualquier pregunta sin datos fuera de los «¿…?» van
  al agente sin perder el paso, con el recordatorio una sola vez (US4, escenario 3). Con
  datos antes o después de la pregunta («Ana Pérez, ¿cuánto tarda?», «¿cuánto tarda? Ana
  Pérez»), los datos ganan y la pregunta no se responde en ese turno.
- **«No quiero hacer el pedido»**: la negación nunca acepta el pedido; es un rechazo.
- **Cancelar en cualquier paso**: «ahora no», «no, mejor no», «olvídalo», «cancélalo», «no,
  olvídalo», «cancela el pedido», «cancela todo», «cancelar» o una despedida sin compra
  («lo voy a pensar», «solo estaba mirando») o un adiós («chao», «adiós», «gracias, adiós»)
  dejan la cotización registrada sin pedido y borran los datos parciales; nunca se toman como
  nombre. El mensaje no promete más de lo
  que hace: «hacer el pedido» lo retoma solo en el mensaje siguiente. Un «no, …» seguido de
  un correo, de un nombre con marca o de un nombre (en la confirmación, también en
  minúsculas: «no, carla ruiz») corrige; no cancela.
- **«No» o «sí, pero…» ante «¿Lo confirmo?»**: un «no» solo, «está mal», un «sí» con
  reparos («sí, pero…»), un cambio de los datos sin el dato («sí, pero cambia el correo») o
  un «no» seguido de algo que no es cancelar («no, espera») preguntan qué dato corregir y
  conservan los datos; para dejar el pedido, «ahora no» o «no, gracias».
- **Nombre inválido**: con cifras o símbolos, con menos de dos letras, de más de seis
  palabras o con una muletilla o palabra del pedido («ok», «gracias», «listo», «mejor»,
  «quiero», «pedido», «adelante», «señor»…) no es un nombre. Las muletillas de los bordes se
  quitan («hola, Ana» → «Ana»; «sí, pero a nombre de Marta» → «Marta»), las partículas de
  apellido van en minúscula y el nombre se recorta a 80 caracteres. Esta lista ya no es la
  única defensa: cada paso decide primero si espera un nombre (ver [Reglas por
  paso](#reglas-por-paso)).
- **Nombre en la oferta**: solo cuenta escrito como nombre propio o con marca, solo o tras un
  «sí» («Sí, Ana Pérez», «Sí, soy Ana») o antes de la primera coma («Ana Pérez, quiero hacer
  el pedido»: toma el nombre y pide el correo); «ana pérez» en minúsculas como respuesta a la oferta
  no se toma como dato (sigue el resto de las reglas).
- **Correo dictado con cifras** («ana punto perez 22 arroba…» o «ana punto perez dos dos
  arroba…»): las cifras sueltas o en palabras («cero» a «nueve») antes de «arroba» entran al
  correo (`ana.perez22@gmail.com`) y no al nombre. La confirmación por voz lee el correo
  para que la persona lo compruebe; lo guardado lo cambia por «el correo que escribiste».
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
- **«PDF» a mitad del pedido (WhatsApp)**: «PDF», «sí, mándame el PDF» o «pe de efe» en
  `pedido`, `datos_pedido` o `confirmar_pedido` entregan el PDF de la cotización y la pregunta
  pendiente (`render.order_reminder`), sin turno, sin crear el pedido y sin tocar el borrador.
  Antes, en `confirmar_pedido`, el atajo del PDF mandaba la cotización y el «sí» no creaba el
  pedido.
- **Recibo a pedido (WhatsApp)**: con el pedido confirmado, «PDF», «mándame el recibo» o «el
  PDF del pedido» reenvían el recibo; «el PDF de la cotización» trae la cotización y «¿cuándo
  recibo el pedido?» va al agente.
- **«Cancela el pedido» o «¿cómo va mi pedido?» tras crearlo**: respuesta fija con el número
  (`render.order_cancel_request`, `render.order_status`): no se anula ni se consulta desde el
  chat, lo gestiona el asesor. Vale también después de otros turnos: el número queda en la
  cotización (`payload["order"]`) y el servicio responde con la última cotización del hilo
  con pedido. Solo con un determinante («mi pedido», «la compra») y nunca con un producto o
  una cantidad («quiero ver vasos para otro pedido» se cotiza).
- **Sin JavaScript** (o con el stream caído): `POST /chat` guarda el turno y redirige (PRG,
  303); el `GET` siguiente muestra una sola vez el texto visible del turno del pedido (en
  memoria del proceso unos dos minutos). Recargar no reenvía el formulario y muestra lo
  guardado, sin los datos.
- **Voz en la web**: la confirmación dice el nombre y el correo reales en el turno en vivo
  solo en la voz del navegador (`done.voz_resumen`). La nota de voz del servidor (TTS de
  OpenAI) lee lo guardado en tono neutro («a tu nombre, con el correo que escribiste»), como
  el historial: los datos no van a OpenAI. El folio con CRM termina en «¿Quieres hacer el
  pedido?».

## Requirements *(mandatory)*

### Functional Requirements

**Oferta y aceptación**

- **FR-001**: El folio (T6) DEBE ofrecer el pedido solo si el CRM está configurado
  (`ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY`); sin CRM, el flujo es el de la 054.
- **FR-002**: El pedido se acepta con una afirmación a la oferta («sí», «sí, adelante»,
  «dale», «está bien») o con una frase de compra («quiero comprarla», «hagamos el pedido», «la
  compro», «hacer el pedido»); una frase con producto, «?» o negación («no quiero hacer el
  pedido») no acepta el pedido, y una afirmación nunca se toma como nombre; el nombre propio
  o con marca que la acompaña («Sí, Ana Pérez», «Sí, soy Ana») sí es un dato.
- **FR-003**: «Ahora no» y equivalentes («no, mejor no», «no quiero el pedido», «ya no»,
  «todavía no, gracias», «olvídalo», «cancélalo», «cancela el pedido», «cancela todo», una
  despedida sin compra como «lo voy a pensar»…) dejan la cotización registrada sin pedido en
  cualquier paso del pedido y nunca se toman como nombre; «hacer el pedido» lo retoma en el
  mensaje siguiente, y el mensaje de rechazo lo dice así, sin prometer más. Un mensaje de
  rechazo que trae un correo o un nombre de corrección corrige el dato en vez de cancelar.
- **FR-004**: La web DEBE mostrar los botones «Hacer el pedido» / «Ahora no» con la oferta y
  «Confirmar pedido» / «Ahora no» con la confirmación.

**Datos de quien compra**

- **FR-005**: Nombre y correo DEBEN leerse por reglas explícitas por paso, en uno o dos
  mensajes (el correo también dictado: «arroba», «punto», «guion», «guion bajo» y las cifras
  sueltas o en palabras antes de «arroba»), y pedir lo que falte. En la oferta solo cuentan
  como datos un correo o un nombre con marca o escrito como nombre propio; en
  `datos_pedido`, el mensaje es el nombre pedido si no es una afirmación ni una muletilla.
  Los datos antes o después de una pregunta «¿…?» cuentan. En voz, la confirmación DEBE leer
  el correo.
- **FR-006**: Ninguna respuesta de los pasos del pedido (`pedido`, `datos_pedido`,
  `confirmar_pedido`) DEBE ir a Jev. Un mensaje de esos pasos en el que las reglas leen un
  nombre o un correo NUNCA DEBE ir al LLM; una pregunta con «?» y sin datos va al agente sin
  perder el paso.
- **FR-007**: Los datos viven en el borrador (base local) solo mientras se piden: DEBEN
  vaciarse al crear el pedido y al rechazarlo. Si Odoo falla, se conservan para reintentar.
- **FR-022**: Con los dos datos, el asistente DEBE mostrarlos y preguntar «¿Lo confirmo?»
  antes de crear el pedido; solo un «sí» limpio (una afirmación o una frase de compra sin
  datos nuevos, sin nombre de corrección y sin «pero», «aunque», «cambia…», «corrig…» ni
  «mal») lo crea.
- **FR-023**: En la confirmación, un correo nuevo o un nombre con marca DEBEN corregir el
  dato y volver a mostrar la confirmación, aunque el mensaje también diga «sí»; un nombre
  suelto o tras «sí,» corrige solo escrito como nombre propio, tras «no» también en
  minúsculas, y vale lo último tras «es» o «:» («no es Ana, es Carla» → «Carla»). Un «no»
  solo, un «sí» con reparos, un cambio de los datos sin el dato o un «no» seguido de algo
  que no es cancelar ni un nombre DEBEN preguntar qué dato corregir, sin cancelar ni borrar
  los datos; un «no» cancela solo con palabras de cancelación.
- **FR-024**: Tras una pregunta a mitad del pedido, la respuesta del agente DEBE terminar
  una vez con el recordatorio del paso; si el mensaje siguiente tampoco sigue el pedido, el
  paso se suelta. Otro producto en un paso del pedido DEBE salir del pedido con un borrador
  nuevo, cotice o no el agente en ese turno.
- **FR-031**: En los pasos del pedido los datos (un correo o un nombre con marca) DEBEN
  leerse antes que las reglas generales; sin datos, DEBEN ganar las reglas generales: el
  insulto por precio, «retomar» y un cambio del carrito (`wants_edit`) no los lee el paso del
  pedido y nunca crean el pedido. Un cambio de los datos sin el dato (`edits_buyer`) lo
  atiende el paso.

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
  PDF). El correo sale por el servidor SMTP de Odoo (Gmail en producción); la respuesta al
  cliente no promete un envío ya hecho por el chat («La confirmación del pedido llega a …»).
- **FR-016**: DEBE quedar traza: nota HTML en el chatter del pedido con el canal y el folio, y
  una oportunidad del CRM creada después de confirmar, ligada al pedido y marcada como
  ganada; si esas trazas fallan, el pedido sigue.
- **FR-025**: El cliente de Odoo se reutiliza solo si lo creó el asistente
  (`res.partner.ref = asistente-carvajal`) y coinciden el correo (sin comodines) y el nombre;
  si no, se crea uno nuevo con ese `ref`. Una ficha del equipo nunca se reutiliza.
- **FR-032**: El pedido NO DEBE confirmarse con precios vencidos: si la vigencia de alguna
  línea pasó, no se llama a Odoo y la persona recibe `render.order_expired` (cotizar de
  nuevo).

**Recibo del pedido**

- **FR-026**: La respuesta al pedido confirmado DEBE ser un recibo en el chat: número del
  pedido, folio, fecha, líneas, total con la situación del IVA («Total por confirmar con el
  vendedor» si ninguna línea tiene precio; «sin <producto> (precio por confirmar)» si solo
  algunas) y el correo del cliente del pedido en Odoo
  (`OrderResult.email`), al que llega la confirmación.
- **FR-027**: El cliente DEBE poder obtener el PDF del pedido que Odoo generó al confirmarlo
  (`OdooOrders.order_pdf`: el primer `ir.attachment` PDF del `sale.order` confirmado del
  folio creado por quien creó el pedido; nunca un documento interno del chatter): en la web,
  con el enlace a `GET /pedidos/{folio}.pdf`, también en un turno reemitido; en WhatsApp,
  como documento adjunto (`ADJUNTAR_PDF:` con `MEDIA_BASE_URL` https o de loopback) o como
  enlace firmado (`PUBLIC_BASE_URL`). El recibo NO DEBE viajar por http público ni servirse
  a una navegación del navegador por http (se redirige a HTTPS).
- **FR-028**: `/pedidos/{folio}.pdf` DEBE servir el PDF solo al dueño de la cotización del
  folio: la cookie del visitante web o un enlace firmado con HMAC (`SESSION_SECRET`) sobre
  un dominio propio del recibo (`quote_links.ORDER_DOMAIN`, distinto del de cotizaciones)
  que vence a los 7 días (`MAX_LINK_DAYS`). Cualquier otro caso DEBE responder 404 sin
  revelar si el folio existe.
- **FR-029**: Sin Odoo configurado o con Odoo sin responder, la ruta DEBE responder 503 y el
  enlace DEBE poder reintentarse.
- **FR-030**: Un canal que no pueda entregar el PDF (sin URL pública) DEBE quitar la promesa
  del recibo en vez de prometer un PDF que no llega.

**Auditoría de cobertura (2026-11-27)**

- **FR-033**: Con el pedido creado, «cancela el pedido» y «¿cómo va mi pedido?» DEBEN recibir
  una respuesta fija con el número, sin el modelo y sin cambiar `draft.order`, también tras
  otros turnos (el número se guarda en la cotización): la anulación y el estado los gestiona
  el asesor. Solo con un determinante ante el pedido y nunca con un producto o una cantidad.
- **FR-034**: En WhatsApp, un pedido del PDF en cualquier paso del pedido DEBE entregar el
  PDF de la cotización y la pregunta pendiente, sin correr un turno, sin crear el pedido y
  sin cambiar el borrador.
- **FR-035**: En WhatsApp, con un pedido confirmado para la última cotización, «PDF» o un
  pedido del recibo DEBEN reenviar el recibo (adjunto o enlace firmado); un pedido que nombra
  la cotización la entrega a ella.
- **FR-036**: Al confirmar un pedido nuevo, Odoo DEBE recibir una actividad «Coordinar pago y
  entrega del pedido» para el usuario de `ODOO_SALES_LOGIN`, sin datos del comprador; si
  falla, el pedido sigue. Si el login no existe, la actividad NO DEBE programarse en el
  usuario técnico: queda un error en los logs y `sales_user: missing` en `/health` para el
  operador. `ODOO_SALES_LOGIN=` (vacío) DEBE llegar vacío al contenedor (sin actividad).
- **FR-037**: `/health` DEBE publicar el bloque `odoo` (configurado, estado y, si está
  configurado, si responde), con un plazo total de un segundo que nunca bloquee `/health`
  más que eso, caché, sin URL ni secretos y sin cambiar el estado global. El motivo del fallo
  (`reason`) y el usuario de ventas (`sales_user`) solo DEBEN verse con el token del operador.
- **FR-038**: En la web, la voz del navegador del turno en vivo de la confirmación DEBE decir
  los datos reales; la nota de voz del servidor y la del historial DEBEN ser neutras (sin
  datos: no van a OpenAI, FR-019); la voz del folio con CRM DEBE terminar en la pregunta del
  pedido. Lo guardado sigue sin datos (FR-020).
- **FR-039**: Los prompts NO DEBEN decir que el asistente no toma pedidos: el pedido lo
  ofrece el sistema; el asistente no toma pagos ni pide datos de pago o personales.

**Catálogo**

- **FR-017**: El catálogo de Colombia DEBE poder sincronizarse con Odoo (un producto por SKU,
  precio por caja de la ficha o de la primera oferta en COP) más el servicio de envío, de
  forma idempotente; un producto archivado se reactiva en lugar de duplicarse.
- **FR-018**: Con `--imagenes`, la foto del catálogo DEBE subirse solo a los productos que no
  tengan una.

### Privacidad

- **FR-019**: Nombre y correo solo viajan a Odoo: ni al modelo, ni a Jev, ni a OpenAI (la nota
  de voz del servidor, `/api/voz/hablar`, sintetiza lo guardado sin ellos; solo la voz del
  navegador los lee). Los logs del pedido llevan el folio, el número del pedido y, ante un
  error, el código HTTP y el tipo; nunca el nombre, el correo ni el mensaje de Odoo.
- **FR-020**: Los turnos con datos del pedido (todos los que deciden las reglas en
  `datos_pedido` y `confirmar_pedido`, incluidos el «sí» que crea el pedido, el rechazo y
  «¿Qué dato corrijo?», y la oferta con datos) se guardan y se monitorean sin ellos: la
  pregunta como «🛒 (datos para el pedido)», y la respuesta (texto, voz y tarjeta del flujo)
  con el correo enmascarado (`a…@dominio`, sin caracteres de Markdown), el correo leído en
  voz como «el correo que escribiste» y el nombre como «(tu nombre)». La persona ve el texto
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
- **Pedido** (Odoo): número visible (`S00001`), identificador, oportunidad, si ya existía y
  el correo de su cliente (`OrderResult.email`).
- **Contacto del asistente** (Odoo): `res.partner` con `ref = asistente-carvajal`; el único
  que se reutiliza.
- **Recibo**: el texto del pedido confirmado en el chat y el PDF del pedido en Odoo (el
  primer `ir.attachment` PDF del `sale.order` creado por quien creó el pedido), servido en
  `/pedidos/{folio}.pdf` al dueño de la
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
- El correo sale por el servidor SMTP de Gmail configurado en el Odoo de producción. Si el
  servidor falla, Odoo deja el mensaje en el chatter y el `mail.mail` en excepción, sin
  reintentarlo solo: se reenvía desde Odoo.

## Fuera de alcance y pendientes

- ~~**SMTP**~~: resuelto. El servidor de correo saliente de Gmail está configurado en el Odoo
  de producción y la confirmación sale; ver
  [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente).
- **Verificación del correo** (riesgo residual aceptado): cualquiera puede escribir un correo
  ajeno y Odoo le enviará la confirmación del pedido a esa persona. Lo mitigan el paso de
  confirmación, el cupo diario por visitante o número (`DAILY_REQUESTS_LIMIT`) y la allowlist
  de WhatsApp; el chat web solo tiene el cupo. No hay verificación del correo (código o
  enlace): queda pendiente.
- **Concurrencia** (riesgo residual): la idempotencia por folio es «buscar y luego crear», sin
  bloqueo por conversación ni restricción única en Odoo. Dos «sí» que se cruzan antes de que
  exista el pedido pueden crear dos pedidos con el mismo folio (y dos correos). Se tolera el
  caso del pedido ya confirmado por el otro turno (422); serializar por folio queda
  pendiente. La tercera ronda lo volvió a plantear y los revisores lo refutaron como
  improbable; se mantiene aceptado.
- **Reenvío con datos enmascarados** (aceptado): un turno del pedido reenviado con el mismo
  `client_turn_id` muestra lo guardado («(tu nombre)», `a…@dominio`) y no el texto que vio
  la persona. Es la consecuencia de no guardar los datos; no filtra nada.
- **Pago**: ni enlace de pago ni pasarela; el asesor lo coordina.
- **Inventario y entrega**: sin `stock`, reservas ni fechas de entrega en Odoo.
- **Catálogo de México**: solo se sincroniza Colombia (COP).
- **Estado del pedido de vuelta al chat**: el asistente no consulta en Odoo si el pedido se
  pagó, se entregó o se canceló; «¿cómo va mi pedido?» y «cancela el pedido» reciben una
  respuesta fija que remite al asesor (FR-033).
- **`draft.order` tras un turno ajeno al flujo** (resuelto el 2026-11-27): el borrador se
  reinicia y pierde el número, pero el número queda en la cotización (`payload["order"]`):
  FR-033 sigue valiendo y el recibo por WhatsApp lo lee de ahí, sin llamar a Odoo.
- **Recarga sin JavaScript**: tras el PRG, la primera vista muestra los datos del turno; una
  recarga muestra lo guardado (enmascarado), sin reenviar el formulario.
- **Tarjeta de Odoo en `/metricas`**: no hay; el estado está en el bloque `odoo` de
  `/health`.
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

Una tercera ronda sobre `1209217` confirmó 21 hallazgos más («sí, pero…» y nombres nuevos que
creaban el pedido con los datos viejos, turnos con datos sin marcar, rechazos tomados como
nombre, reglas generales tapadas por el pedido, un mensaje de rechazo que prometía de más, el
PDF más reciente y los documentos internos como recibo, la dirección y el NIT de contactos
reutilizados, el correo dictado con cifras, «no es Ana, es Carla» y el reintento con el correo
corregido). Se resuelven en la misma PR con el «sí» limpio, `Reply.private` en todo el paso,
`_defers`, `PARTNER_REF`, el primer PDF de quien creó el pedido y la comprobación de precios
vencidos; la verificación final, en `7610e60`, lee los datos antes que las reglas generales,
cierra el pedido con un adiós, corrige el nombre en minúsculas tras «no» y enmascara el
correo leído en voz. Siguen aceptados la concurrencia de dos «sí» y el reenvío enmascarado (ver
[Fuera de alcance y pendientes](#fuera-de-alcance-y-pendientes)). El resumen está en
[evidencia.md §7](./evidencia.md#7-revisión-adversarial-tercera-ronda-2026-11-26).

## Auditoría de cobertura (2026-11-27)

Tras la fusión de la PR #279, una auditoría verificó 48 huecos: textos que contradecían el
flujo (prompts, constitución, README, specs e índices), comportamientos sin definir (el PDF a
mitad del pedido, cancelar o preguntar el estado tras el pedido, el aviso al equipo), la voz
y el Markdown del correo enmascarado, operación (`/health`, HTTPS, `.env.example`) y pruebas
que faltaban. Las correcciones están en FR-033 a FR-039 y en los casos límite de arriba; el
detalle por grupo, en [evidencia.md §9](./evidencia.md#9-auditoría-de-cobertura-2026-11-27).
