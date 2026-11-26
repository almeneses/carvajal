# Pedidos en Odoo (spec 055)

Cuando la persona confirma una cotización y recibe el folio, el asistente ofrece hacer el
pedido. Si acepta, pide nombre y correo y crea en Odoo un **pedido de venta confirmado**
(cliente, líneas con los precios de la cotización, envío y folio). Odoo le envía al cliente el
correo de confirmación. Funciona igual en el chat web y en WhatsApp, porque los dos canales
pasan por `ChatService`.

```text
✅ Cotización #A1B2C3 registrada            ← ya existía (spec 054)
🛒 ¿Quieres hacer el pedido? …              ← nuevo: botones «Hacer el pedido» / «Ahora no»
› Sí, quiero hacer el pedido
Para el pedido envíame tu nombre y tu correo…
› Ana Pérez, ana@correo.com
🧾 Pedido S00001 confirmado · cotización #A1B2C3
Te enviamos la confirmación a ana@correo.com…
```

## Qué queda en Odoo

| Registro | Contenido |
|---|---|
| `res.partner` | Cliente por correo (si ya existe, se reutiliza) |
| `crm.lead` | Oportunidad «Pedido de la cotización #folio», marcada como ganada |
| `sale.order` | Pedido confirmado; `client_order_ref` = folio, `origin` = canal y folio |
| Chatter del pedido | Nota con el canal y el folio, y el correo de confirmación |
| `product.product` | Un producto por SKU (catálogo de Colombia) y el servicio `ENVIO-CARVAJAL` |

El pedido es **idempotente por folio**: repetir «hacer el pedido» o reintentar el turno no
crea un segundo pedido. Si Odoo no responde, el chat lo dice y se puede reintentar.

## Datos personales

- El nombre y el correo solo viajan a Odoo. No se envían a Jev (la compuerta del flujo se
  apaga en ese paso) ni al modelo, y los logs solo llevan el folio y el número de pedido.
- Mientras se piden, el borrador de la conversación guarda los datos parciales; se borran al
  crear el pedido.

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
estado de excepción y no se reintenta solo. Para activarlo:

1. Ajustes → Técnico → Servidores de correo saliente → nuevo (p. ej. Gmail con contraseña de
   aplicación, `smtp.gmail.com:587`, STARTTLS) y «Probar conexión».
2. Ajustar el correo de la compañía (hoy `ventas@example.com`) al remitente real.
3. Los correos que ya fallaron se reenvían desde Ajustes → Técnico → Correos → «Reintentar».

## Catálogo

```bash
ssh -N -L 8169:127.0.0.1:8169 deploy@144.217.80.136 -p 2422 &
ODOO_URL=http://127.0.0.1:8169 ODOO_DB=carvajal ODOO_API_KEY=… \
    uv run python scripts/odoo_catalogo.py
```

Crea o actualiza un producto por referencia de Colombia (`default_code` = SKU, precio por
caja) en la categoría «Empaques Carvajal». Es idempotente. Un SKU que llegue en un pedido y no
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

Cotiza algo, confírmalo y acepta el pedido: aparece en Odoo → Ventas → Pedidos (abre la UI
con el túnel de arriba en `localhost:8169`). Con `make api` (app nativa) basta el túnel en
loopback y `ODOO_URL=http://127.0.0.1:8169`.

## En producción

Ya están `ODOO_*` en `/opt/carvajal/.env`; el despliegue de `main` las pasa a `app` y `mcp`
(`compose.prod.yml`). Al fusionar, el pedido queda activo en la web y en WhatsApp.
