# Pedidos en Odoo (spec 055)

Cuando la persona confirma una cotización y recibe el folio, el asistente ofrece hacer el
pedido. Si acepta, pide nombre y correo, se los muestra para que los confirme y, con un «sí»,
crea en Odoo un **pedido de venta confirmado** (cliente, líneas con los precios de la
cotización, envío y folio). Al confirmarlo, Odoo envía al cliente el correo de confirmación
(solo si hay servidor SMTP; ver [Correo saliente](#correo-saliente)). Funciona igual en el
chat web y en WhatsApp, porque los dos canales pasan por `ChatService`.

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
🧾 Pedido S00001 confirmado · cotización #A1B2C3
La confirmación del pedido llega a ana@correo.com…
```

## Cómo lee la conversación

Todo el paso del pedido lo deciden reglas (`quotation/order.py` y `quotation/flow.py`), sin
el modelo y sin Jev. Las preguntas pendientes son `pedido` (la oferta), `datos_pedido` y
`confirmar_pedido`.

- **Aceptar**: «sí», «quiero comprarla», «hagamos el pedido», «la compro». Con negación
  («no quiero hacer el pedido»), producto, cantidad o «?» no acepta. Si responde a la oferta
  directamente con nombre o correo, se toman como los datos.
- **Rechazar**, en cualquier paso: «ahora no», «no», «no, mejor no», «no quiero el pedido»,
  «ya no», «todavía no, gracias», «lo pienso», «cancelar», «olvídalo». La cotización queda
  registrada y los datos parciales se borran.
- **Nombre y correo**, en uno o dos mensajes; el que falte se pide. El correo se puede dictar
  («ana punto perez arroba gmail punto com» → `ana.perez@gmail.com`; «guion» y «guion bajo»
  dan `-` y `_`). No es un nombre lo que trae cifras o símbolos, más de seis palabras o una
  muletilla o palabra del pedido («ok», «gracias», «listo», «mejor», «quiero», «pedido»…);
  las muletillas de los bordes se quitan («Juan, juan@x.co, gracias» → Juan) y las
  partículas van en minúscula («María de la Cruz»). Un nombre ya dado solo cambia con un
  correo en el mismo mensaje o con «me llamo…», «mi nombre es…», «a nombre de…» o «soy…».
- **Confirmar**: «sí» crea el pedido. Un correo nuevo o un nombre con marca corrige el dato y
  vuelve a mostrar la confirmación.
- **Otro producto o cantidad** («quiero 500 vasos») no es el pedido: va al agente y, si
  cotiza, el borrador del pedido se reinicia (la cotización anterior queda sin pedido).
- **Una pregunta** con «?» y sin datos va al agente sin perder el paso. La respuesta termina
  una sola vez con el recordatorio («↩️ ¿Seguimos con el pedido…», «↩️ Para el pedido me
  faltan…» o «↩️ ¿Confirmo el pedido…»); si el mensaje siguiente tampoco sigue el pedido, el
  paso se suelta.
- **Si Odoo falla**: «No pude crear el pedido en este momento… responde **sí** en unos minutos
  y lo intento de nuevo, o **ahora no** para dejarlo.». Los datos quedan en la confirmación y
  el «sí» reintenta con el mismo folio.

## Qué queda en Odoo

| Registro | Contenido |
|---|---|
| `res.partner` | Cliente con el mismo correo **y** el mismo nombre (se reutiliza); si no, un contacto nuevo (`es_CO`, con la ciudad) |
| `sale.order` | Pedido confirmado; `client_order_ref` = folio, `origin` = canal y folio |
| `crm.lead` | Oportunidad «Pedido de la cotización #folio», creada después de confirmar, ligada al pedido y marcada como ganada |
| Chatter del pedido | Nota con el canal y el folio (HTML, `body_is_html=True`) y el correo de confirmación |
| `product.product` | Un producto por SKU (catálogo de Colombia) y el servicio `ENVIO-CARVAJAL`; un SKU archivado se reactiva, no se duplica |

El pedido es **idempotente por folio** (`client_order_ref`, sin contar los cancelados):

- Si ya está confirmado (`sale`), se devuelve el existente y no se crea nada.
- Si quedó en borrador porque un intento falló antes de confirmarlo, el reintento le pone el
  cliente actual y lo confirma; nunca se anuncia como confirmado un pedido sin confirmar.
- La oportunidad se crea después de confirmar: un fallo previo no deja oportunidades
  huérfanas, y el reintento sobre un pedido sin oportunidad la crea una vez. Si el CRM, la
  nota o «ganada» fallan, el pedido sigue.

El cliente se busca con `=ilike` y el correo escapado (`%` y `_` literales). Solo el correo no
basta para reutilizarlo: quien escribe el correo de otra persona no le pone pedidos en su
ficha. Odoo admite contactos con el mismo correo; el equipo los fusiona si hace falta.

## Datos personales

- El nombre y el correo solo viajan a Odoo. Ninguna respuesta de los pasos del pedido va a
  Jev, y un mensaje en el que las reglas leen un nombre o un correo no va al modelo.
- El turno con esos datos se guarda y se monitorea (LangSmith) sin ellos: la pregunta como
  «🛒 (datos para el pedido)» y la respuesta con el correo enmascarado (`a***@dominio`) y el
  nombre como «(tu nombre)». La persona ve el texto completo en el turno, pero el historial
  que llega a DeepSeek en los turnos siguientes y el resumen de memoria salen de lo guardado.
- El borrador de la conversación (base local) sí guarda los datos mientras se piden: se
  vacían al crear el pedido o al rechazarlo, y se conservan tras un fallo para reintentar.
- Los logs llevan el folio, el número de pedido y, ante un error de Odoo, el código HTTP y el
  tipo; nunca el nombre, el correo ni el mensaje de Odoo.
- Una pregunta con «?» va al agente como cualquier pregunta: si trae un nombre que las reglas
  no reconocen, el modelo lo ve.

**Riesgo residual (pendiente)**: no hay verificación del correo. Cualquiera puede escribir un
correo ajeno y, al confirmar, Odoo le envía a esa persona la confirmación del pedido. Lo
mitigan el paso de confirmación, el cupo diario (`DAILY_REQUESTS_LIMIT`) y la allowlist de
WhatsApp; el chat web solo tiene el cupo.

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
Pedidos (abre la UI con el túnel de arriba en `localhost:8169`). Con `make api` (app nativa)
basta el túnel en loopback y `ODOO_URL=http://127.0.0.1:8169`.

## En producción

Ya están `ODOO_*` en `/opt/carvajal/.env`; el despliegue de `main` las pasa a `app` y `mcp`
(`compose.prod.yml`). Al fusionar, el pedido queda activo en la web y en WhatsApp.
