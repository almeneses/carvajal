# Pedidos en Odoo (spec 055)

Cuando la persona confirma una cotización y recibe el folio, el asistente ofrece hacer el
pedido. Si acepta, pide nombre y correo, se los muestra para que los confirme y, con un «sí»,
crea en Odoo un **pedido de venta confirmado** (cliente, líneas con los precios de la
cotización, envío y folio). Al confirmarlo, Odoo envía al cliente el correo de confirmación
(solo si hay servidor SMTP; ver [Correo saliente](#correo-saliente)) y el chat le entrega el
**recibo**: en texto y con el PDF del pedido de Odoo (ver [Recibo del pedido](#recibo-del-pedido)).
Funciona igual en el chat web y en WhatsApp, porque los dos canales pasan por `ChatService`.

```text
✅ Cotización #A1B2C3 registrada            ← ya existía (spec 054)
🛒 ¿Quieres hacer el pedido? …              ← botones «Hacer el pedido» / «Ahora no»
› Sí, quiero hacer el pedido
Para el pedido envíame tu nombre y tu correo…
› Ana Pérez, ana@correo.com
📋 Voy a hacer el pedido con estos datos:   ← botones «Confirmar pedido» / «Ahora no»
- Nombre: Ana Pérez
- Correo: ana@correo.com
¿Lo confirmo? Si algo está mal, escríbeme el dato correcto.
› Sí, confírmalo
🧾 Pedido S00001 confirmado · cotización #A1B2C3   ← el recibo
Fecha: 26 nov 2026
1. <producto> × <unidades> · $50.000
Total $58.000 · <IVA>
La confirmación del pedido llega a ana@correo.com…
📄 Descarga el recibo de tu pedido S00001          ← web; en WhatsApp, el PDF adjunto
```

## Cómo lee la conversación

Todo el paso del pedido lo deciden reglas explícitas por paso (`quotation/flow.py`:
`_offer_step`, `_data_step` y `_confirm_step`; lectura en `quotation/order.py`), sin el
modelo y sin Jev. Cada paso sabe qué espera, en lugar de adivinar un nombre en cualquier
frase. Las preguntas pendientes son `pedido` (la oferta), `datos_pedido` y
`confirmar_pedido`.

En cualquier paso:

- **Los datos van primero; después, las reglas generales** (`flow._defers`). Un mensaje con
  un correo o un nombre con marca es un dato del pedido aunque pida un cambio («quiero
  cambiar el correo a carla@gmail.com» corrige el correo). Sin datos, el chiste ante un
  insulto por el precio («está muy caro, ladrones»), «retomar» y un cambio del carrito
  (`wants_edit`: «quiero cambiar la 2 a 800») no los lee el paso del pedido: los decide el
  resto de las reglas o el agente, y nunca crean el pedido. Un cambio de los datos sin el
  dato («sí, pero cambia el correo», «el correo está mal», «quiero cambiar el nombre»;
  `order.edits_buyer`) no es un cambio del carrito: lo atiende el paso (en la confirmación,
  «¿Qué dato corrijo?»).
- **Un correo** escrito o dictado («ana punto perez arroba gmail punto com» →
  `ana.perez@gmail.com`; «guion» y «guion bajo» dan `-` y `_`) siempre es un dato. Las
  cifras dictadas antes de «arroba», sueltas o en palabras, son parte del correo: «ana punto
  perez dos dos arroba gmail punto com» o «ana punto perez 22 arroba gmail punto com» dan
  `ana.perez22@gmail.com`, y en «Ana Pérez, ana punto perez dos dos arroba…» el nombre queda
  «Ana Pérez».
- **Otro producto** («quiero 500 vasos») sin correo ni nombre con marca sale del pedido con
  un borrador nuevo, cotice o no el agente en ese turno; la cotización anterior queda
  registrada sin pedido. Las cifras solas (un teléfono, una dirección) no sacan del paso.
- **Rechazar**: «ahora no», «no, mejor no», «no quiero el pedido», «ya no», «todavía no,
  gracias», «lo pienso», «olvídalo», «cancélalo», «no, olvídalo», «cancela el pedido»,
  «cancela todo», una despedida sin compra (`is_leaving`: «lo voy a pensar», «solo estaba
  mirando», «después te escribo», «no me interesa») o un adiós (`is_goodbye`: «chao»,
  «chau», «adiós», «bye», «nos vemos», «gracias, adiós»; en la confirmación un adiós nunca
  crea el pedido: «sí, chao» pregunta qué corregir).
  La cotización queda registrada sin pedido y los datos parciales se borran: «Listo, tu
  cotización #folio queda registrada sin pedido. Si cambias de opinión, escribe «hacer el
  pedido» en tu próximo mensaje. ¿Te ayudo con algo más?». Esas palabras nunca se toman como
  nombre. Un «no, …» con un correo, un nombre con marca o un nombre (en la confirmación,
  también en minúsculas) corrige, no cancela. En la confirmación, un «no» cancela solo si lo
  acompañan palabras de cancelación (ver la tabla).
- **Una pregunta** sin datos fuera de los «¿…?» va al agente sin perder el paso. La
  respuesta termina una sola vez con el recordatorio («↩️ ¿Seguimos con el pedido…», «↩️ Para
  el pedido me faltan…» o «↩️ ¿Confirmo el pedido…»); si el mensaje siguiente tampoco sigue
  el pedido, el paso se suelta. Si trae datos antes o después de la pregunta («Ana Pérez,
  ¿cuánto tarda?», «¿cuánto tarda? Ana Pérez»), los datos ganan: se toman y la pregunta no
  se responde en ese turno.

Por paso:

| Paso | Qué cuenta | Ejemplos |
|---|---|---|
| Oferta (`pedido`) | **Datos** solo si trae un correo o un nombre con marca («soy…», «me llamo…», «a nombre de…», «el nombre es…», «cambia el nombre a/por…») o escrito como nombre propio (mayúscula en cada palabra), también detrás de un «sí» o antes de la primera coma. **Acepta** una afirmación o una frase de compra. Lo demás sigue el resto de las reglas | «Ana Pérez, ana@correo.com» → confirmación; «Sí, a nombre de Ana Pérez», «Sí, soy Ana», «Sí, Ana Pérez», «Ana Pérez, quiero hacer el pedido» → toman el nombre y piden el correo (turno privado); «sí», «sí, adelante», «está bien», «quiero comprarla» → pide los datos; «chao» → rechazo; «cuéntame de la empresa» o «ana pérez» → no son datos |
| Datos (`datos_pedido`) | El correo del mensaje y el **nombre que se pidió**: el que trae marca, el ya dado o, si falta, el mensaje mismo (sin las preguntas) si no es una afirmación ni una muletilla (sin cifras, símbolos ni palabras como «ok», «gracias», «pedido», «olvídalo», «chao») | «Ana Pérez», «cambia el nombre a Ana Pérez» → «¿A qué correo…?»; «ana@correo.com» → la confirmación; «ok gracias» → el formato de ejemplo; «chao», «gracias, adiós» → rechazo |
| Confirmación (`confirmar_pedido`) | Un correo o un nombre con marca **corrigen** (aunque el mensaje diga «sí» o pida el cambio). Un nombre suelto o tras «sí,» corrige **solo escrito como nombre propio**; tras «no» también en minúsculas («no, carla ruiz»). Vale lo último tras «es» o «:» («no es Ana es Carla Ruiz», «corrige el nombre: Carla Ruiz»). **Solo un «sí» limpio crea el pedido**: una afirmación o frase de compra sin nombre nuevo y sin «pero», «aunque», «cambia…», «corrig…» ni «mal». Un «sí» con reparos, un «no» solo, «está mal», un cambio de los datos sin el dato o un «no» seguido de algo que no es cancelar ni un nombre preguntan qué corregir y **no borran los datos**. Cancelan un «no» con palabras de cancelación (ahora, después, olvídalo, cancela, déjalo, mejor, no quiero, gracias, pensar, ya no, por ahora, chao, adiós), «cancela todo» y los rechazos y adioses de arriba | «no, es otro@correo.com», «quiero cambiar el correo a carla@gmail.com», «sí, pero a nombre de Marta Gómez», «sí, pero cambia el nombre a Carla Ruiz», «sí, Carla Ruiz», «no, Carla Ruiz», «no, carla ruiz», «Marta Gómez» → confirmación con el dato nuevo; «no es Ana, es Carla», «no, es carla» → Carla; «sí, pero…», «sí, pero cambia el correo», «el correo está mal», «quiero cambiar el nombre», «no», «no, espera», «déjalo» → «¿Qué dato corrijo?…»; «olvídalo», «no, olvídalo», «no, gracias», «ahora no», «chao» → rechazo; «Sí, confírmalo», «sí», «ok» → el pedido |

Tras «no», el nombre puede venir en minúsculas: «no, carla ruiz» corrige a «Carla Ruiz» y no
se lee como rechazo. Las muletillas de los bordes se quitan («Juan,
juan@x.co, gracias» → Juan) y las partículas van en minúscula («María de la Cruz»). Tras
crear el pedido, «quiero comprarla» responde el número que ya existe; tras un rechazo,
«hacer el pedido» en el mensaje siguiente vuelve a pedir los datos (después, el borrador del
folio ya se cerró y hay que cotizar de nuevo).

**Precios vencidos**: el pedido toma los precios de la cotización. Si al «sí» final la
vigencia del vendedor de alguna línea ya pasó (`quotation.draft.expired_lines`), no se llama
a Odoo: «Los precios de <líneas> ya vencieron, así que no puedo confirmar el pedido con esa
cotización. Pídeme cotizarlos de nuevo y hacemos el pedido.» (`render.order_expired`); el
paso se cierra y los datos se borran.

**Si Odoo falla**: «No pude crear el pedido en este momento… responde **sí** en unos minutos y
lo intento de nuevo, o **ahora no** para dejarlo.». Los datos quedan en la confirmación y el
«sí» reintenta con el mismo folio.

## Recibo del pedido

Pedido del owner (2026-11-26): «si la orden se confirma, se debe guardar en el odoo y se debe
enviar un correo, además de que se envíe una copia o recibo al cliente (whatsapp o web)».

- **Odoo guarda el pedido** confirmado (ver [Qué queda en Odoo](#qué-queda-en-odoo)).
- **Odoo envía el correo** con el PDF del pedido al confirmarlo, **cuando haya SMTP** (ver
  [Correo saliente](#correo-saliente)).
- **El chat entrega el recibo**, haya o no SMTP: la respuesta al «sí» trae el número del
  pedido, el folio, la fecha, las líneas, el total con el IVA («Total por confirmar con el
  vendedor» si ninguna línea tiene precio; si solo algunas, el total de las que lo tienen
  con «sin <producto> (precio por confirmar)») y el correo de la confirmación, y el **PDF del
  pedido que generó Odoo** (el mismo del correo, que lee `OdooOrders.order_pdf(folio)`).
- **El correo del recibo es el del cliente del pedido en Odoo** (`OrderResult.email`): el
  que se escribió, salvo en un reintento sobre un pedido del folio que ya estaba confirmado,
  donde es el del cliente de ese pedido (si la persona corrigió el correo después, el recibo
  no promete el nuevo).
- **El PDF es el primero del pedido creado por quien creó el pedido**: `order_pdf` busca el
  `sale.order` del folio en `sale`, toma su `create_uid` (el usuario técnico `carvajal-bot`)
  y lee el `ir.attachment` PDF de ese pedido creado por ese usuario con el `id` más bajo
  (`order="id asc"`), que es el que Odoo generó al confirmar. Ni un documento interno que el
  equipo suba al chatter ni otro PDF posterior salen como recibo.

| Canal | Cómo llega el PDF |
|---|---|
| Web | Enlace «📄 Descarga el recibo de tu pedido S…» a `GET /pedidos/{folio}.pdf`, en la respuesta, en el historial y en un turno reemitido (mismo `client_turn_id`: llega como un solo «token» y `apps/api/views/stream.py` le aplica también `pdf_offer` y `receipt_offer`). Abre con la **cookie del visitante** dueño de la cotización |
| WhatsApp con `MEDIA_BASE_URL` | «📄 Te envío el recibo de tu pedido S… en PDF.» y `ADJUNTAR_PDF: <url firmada>`: OpenClaw lo envía como documento, igual que el PDF de la cotización |
| WhatsApp con solo `PUBLIC_BASE_URL` | «📄 Recibo de tu pedido S… en PDF. Ábrelo aquí:» y el enlace firmado |
| Sin URL pública | La línea del recibo se quita: no se promete un PDF que no llega |

La ruta (`apps/api/views/pedido_pdf.py`) sirve el PDF solo al dueño de la cotización del folio:

- **Cookie** del chat web, o **enlace firmado** `?t=<vence>.<firma>` (HMAC con
  `SESSION_SECRET`) con un **dominio de firma propio** (`quote_links.ORDER_DOMAIN`): un enlace
  del PDF de la cotización no abre el pedido, ni al revés.
- El enlace firmado **vence a los 7 días** (`MAX_LINK_DAYS`).
- Sin prueba válida, con la firma vencida o con un folio sin pedido confirmado: **404** «El
  pedido no existe o el enlace venció.», sin revelar si el folio existe.
- Sin `ODOO_*` o con Odoo sin responder: **503** «Servicio no disponible.»; el enlace se puede
  abrir después.
- Responde `inline; filename="pedido-<folio>.pdf"`, `Cache-Control: private, no-store` y
  `X-Robots-Tag: noindex`; los logs llevan el folio y el tamaño, nunca datos del cliente.

## Qué queda en Odoo

| Registro | Contenido |
|---|---|
| `res.partner` | Contacto **creado por el asistente** (`ref = asistente-carvajal`, `PARTNER_REF`) con el mismo correo **y** el mismo nombre (se reutiliza); si no, un contacto nuevo con ese `ref` (`es_CO`, con la ciudad) |
| `sale.order` | Pedido confirmado; `client_order_ref` = folio, `origin` = canal y folio |
| `crm.lead` | Oportunidad «Pedido de la cotización #folio», creada después de confirmar, ligada al pedido y marcada como ganada |
| Chatter del pedido | Nota con el canal y el folio (HTML, `body_is_html=True`) y el correo de confirmación |
| `product.product` | Un producto por SKU (catálogo de Colombia) y el servicio `ENVIO-CARVAJAL`; un SKU archivado se reactiva, no se duplica |

El pedido es **idempotente por folio** (`client_order_ref`, sin contar los cancelados):

- Si ya está confirmado (`sale`), se devuelve el existente y no se crea nada; si le falta la
  oportunidad (la respuesta de Odoo se perdió tras confirmar), se crea entonces.
- Si quedó en borrador porque un intento falló antes de confirmarlo, el reintento le pone el
  cliente actual y lo confirma; nunca se anuncia como confirmado un pedido sin confirmar.
- La oportunidad se crea después de confirmar: un fallo previo no deja oportunidades
  huérfanas, y el reintento sobre un pedido sin oportunidad la crea una vez. Si el CRM, la
  nota o «ganada» fallan, el pedido sigue.
- Si dos turnos confirman el mismo borrador a la vez, el 422 que Odoo devuelve al segundo no
  es un fallo cuando el pedido ya quedó en `sale` (`OdooOrders._confirm`).

El cliente se busca con `=ilike` y el correo escapado (`%` y `_` literales), **solo entre los
contactos que creó el asistente** (`ref = asistente-carvajal`), y se reutiliza si además
coincide el nombre. Solo el correo no basta: quien escribe el correo de otra persona no le
pone pedidos en su ficha. Y una ficha de un cliente real del equipo (sin ese `ref`) nunca se
reutiliza, aunque coincidan correo y nombre: su dirección, NIT o teléfono saldrían en el PDF
del pedido de quien los escribió. Los contactos que el asistente creó antes de este cambio no
llevan el `ref` y tampoco se reutilizan. Odoo admite contactos repetidos; el equipo los
fusiona si hace falta.

## Datos personales

- El nombre y el correo solo viajan a Odoo. Ninguna respuesta de los pasos del pedido va a
  Jev, y un mensaje en el que las reglas leen un nombre o un correo no va al modelo.
- Todos los turnos que deciden las reglas en `datos_pedido` y `confirmar_pedido` (también el
  «sí» que crea el pedido, el rechazo y «¿Qué dato corrijo?») y la respuesta a la oferta que
  trae datos llevan `Reply.private`: se guardan y se monitorean (LangSmith) sin datos, la
  pregunta como «🛒 (datos para el pedido)» y la respuesta con el correo enmascarado
  (`a***@dominio`) y el nombre como «(tu nombre)». La persona ve el texto completo en el
  turno, pero el historial que llega a DeepSeek en los turnos siguientes y el resumen de
  memoria salen de lo guardado.
- En voz, la confirmación lee el correo para que quien lo dictó lo compruebe («…con el correo
  ana punto perez arroba gmail punto com. ¿Lo confirmo?», `order.spoken_address`). La versión
  hablada guardada (`flow.spoken`) no lo conserva: `ChatService._without_buyer` cambia el
  correo leído por «el correo que escribiste», además de enmascarar el escrito y el nombre.
- El borrador de la conversación (base local) sí guarda los datos mientras se piden: se
  vacían al crear el pedido o al rechazarlo, y se conservan tras un fallo para reintentar.
- Los logs llevan el folio, el número de pedido y, ante un error de Odoo, el código HTTP y el
  tipo; nunca el nombre, el correo ni el mensaje de Odoo.
- Lo que las reglas no leen como dato va al agente como cualquier mensaje: una pregunta sin
  datos fuera de los «¿…?», o en la oferta un nombre en minúsculas. Si trae un nombre, el
  modelo lo ve. En la oferta, «Ana Pérez, quiero hacer el pedido» toma el nombre de antes de
  la coma y pide el correo, como turno privado.
- El recibo en PDF lo descarga solo el dueño de la cotización (cookie o enlace firmado); el
  PDF viene de Odoo en cada descarga y no se guarda en la app.

**Riesgo residual (pendiente)**:

- **No se verifica que el correo sea del cliente.** Cualquiera puede escribir un correo ajeno
  y, al confirmar, Odoo le envía a esa persona la confirmación del pedido. Lo mitigan el paso
  de confirmación, el cupo diario (`DAILY_REQUESTS_LIMIT`) y la allowlist de WhatsApp; el chat
  web solo tiene el cupo.
- **El reenvío de un turno** con el mismo `client_turn_id` (reintento del webhook de WhatsApp,
  doble envío en la web) muestra lo guardado: «(tu nombre)» y el correo enmascarado, no los
  datos que vio la persona. No filtra nada.
- **Dos «sí» cruzados** antes de que exista el pedido pueden crear dos pedidos del mismo folio:
  no hay bloqueo por folio. Si el otro turno ya confirmó, el 422 de Odoo se tolera.

## Configuración de la app

Variables de entorno (en la VPS, en `/opt/carvajal/.env`):

| Variable | Valor |
|---|---|
| `ODOO_URL` | `http://odoo:8069` (alias en la red de Docker de la app) |
| `ODOO_DB` | `carvajal` |
| `ODOO_API_KEY` | API key del usuario técnico `carvajal-bot` |
| `ODOO_TIMEOUT_S` | `20` (opcional) |

Sin las tres primeras el asistente no ofrece el pedido y el flujo sigue como antes.

## Odoo en la VPS

Odoo 19 corre en su propio proyecto de compose, `carvajal-odoo`, en `~/carvajal-odoo` del
usuario `deploy`, separado del Odoo 13 del host y del Odoo 15 de Maidensoft. La plantilla
versionada está en [`deploy/odoo/`](../deploy/odoo/).

- Publicado solo en `127.0.0.1:8169`. La app lo alcanza en `http://odoo:8069` porque el
  servicio se une a la red `carvajal-prod_default` con el alias `odoo`.
- Base `carvajal` (`dbfilter = ^carvajal$`, sin gestor de bases), con los módulos
  `sale_management`, `crm`, `sale_crm` y `contacts`, en español de Colombia, con COP y sin
  impuestos por defecto (los precios llegan calculados por la app).
- La API es JSON-2: `POST /json/2/<modelo>/<método>` con `Authorization: bearer <api key>`
  y `X-Odoo-Database: carvajal`.

### Abrir la interfaz

```bash
ssh -N -L 8169:127.0.0.1:8169 deploy@144.217.80.136 -p 2422
# luego http://localhost:8169  (usuario admin; clave en ~/carvajal-odoo/.admin-password)
```

### Correo saliente

Odoo envía la confirmación al confirmar el pedido, con la plantilla
`sale.mail_template_sale_confirmation` y el PDF del pedido adjunto; el mensaje queda siempre
en el chatter del pedido. **Sin servidor SMTP el correo no sale**: el `mail.mail` queda en
estado de excepción y no se reintenta solo. Por eso el chat dice «La confirmación del pedido
llega a …» y no «Te enviamos». Para activarlo:

1. Ajustes → Técnico → Servidores de correo saliente → nuevo (p. ej. Gmail con contraseña de
   aplicación, `smtp.gmail.com:587`, STARTTLS) y «Probar conexión».
2. Ajustar el correo de la compañía (hoy `ventas@example.com`) al remitente real.
3. Los correos que ya fallaron se reenvían desde Odoo: Ajustes → Técnico → Correos →
   «Reintentar».

## Catálogo

```bash
ssh -N -L 8169:127.0.0.1:8169 deploy@144.217.80.136 -p 2422 &
ODOO_URL=http://127.0.0.1:8169 ODOO_DB=carvajal ODOO_API_KEY=… \
    uv run python scripts/odoo_catalogo.py
```

Crea o actualiza un producto por referencia de Colombia (`default_code` = SKU, precio por
caja) en la categoría «Empaques Carvajal»; un producto archivado se reactiva en lugar de
duplicarse. Con `--imagenes` sube además la foto del catálogo
(`data/catalog/empaques/imagenes/`) a los productos que aún no tienen una. Es idempotente.
Se ven en Ventas → Productos → Productos. Un SKU que llegue en un pedido y no
esté en Odoo se crea en ese momento.

## Probar en local contra el Odoo de la VPS

La app de `make up` corre en Docker y llega al host por `host.docker.internal`; el túnel debe
escuchar en la IP del puente de Docker (`172.17.0.1`), no en loopback:

```bash
ssh -N -L 172.17.0.1:8169:127.0.0.1:8169 deploy@144.217.80.136 -p 2422 &
# en .env local:
#   ODOO_URL=http://host.docker.internal:8169
#   ODOO_DB=carvajal
#   ODOO_API_KEY=<la de /opt/carvajal/.env de la VPS>
make up PERFILES=full
```

Cotiza algo, confírmalo, acepta el pedido y confirma los datos: aparece en Odoo → Ventas →
Pedidos (abre la UI con el túnel de arriba en `localhost:8169`) y el enlace del recibo de la
respuesta descarga su PDF. Con `make api` (app nativa)
basta el túnel en loopback y `ODOO_URL=http://127.0.0.1:8169`.

## En producción

Ya están `ODOO_*` en `/opt/carvajal/.env`; el despliegue de `main` las pasa a `app` y `mcp`
(`compose.prod.yml`). Al fusionar, el pedido queda activo en la web y en WhatsApp.
