# Odoo 19 de Carvajal (spec 056)

Plantilla del proyecto de compose `carvajal-odoo` que corre en la VPS en `~/carvajal-odoo`
(usuario `deploy`). Sin secretos: en el servidor `.env` y `config/odoo.conf` llevan las
claves reales (chmod 600).

```bash
cp env.example .env                         # y pon una clave de Postgres
cp config/odoo.conf.example config/odoo.conf # admin_passwd y db_password
mkdir -p addons                             # con los addons de deploy/odoo/addons
docker compose up -d
docker compose run --rm web odoo -c /etc/odoo/odoo.conf -d carvajal \
    -i sale_management,crm,sale_crm,contacts,carvajal_sale_report --stop-after-init
```

## Addons

Los addons propios viven en [`addons/`](addons/) de este repositorio. El compose monta
`~/carvajal-odoo/addons` en `/mnt/extra-addons`, que es el `addons_path` de Odoo.
**Fusionar en `main` no los despliega**: `deploy.yml` solo despliega la app, así que cada
cambio de un addon se copia a la VPS a mano.

- [`carvajal_sale_report`](addons/carvajal_sale_report/): el PDF del pedido (el del correo de
  confirmación, «Imprimir» y el recibo del chat) con el formato de la cotización del asistente.
  Instalado en producción el 2026-11-27.

### Copiar e instalar (o actualizar) un addon

Desde la raíz del repositorio, en `main` y al día:

```bash
# 1. Copiar la carpeta del addon a la VPS (--delete quita los archivos que ya no están).
rsync -a --delete -e "ssh -p 2422" deploy/odoo/addons/carvajal_sale_report \
    deploy@144.217.80.136:carvajal-odoo/addons/

# 2. En la VPS: instalar (-i, la primera vez) o actualizar (-u) el módulo en la base.
ssh -p 2422 deploy@144.217.80.136
cd ~/carvajal-odoo
docker compose run --rm web odoo -c /etc/odoo/odoo.conf -d carvajal \
    -u carvajal_sale_report --stop-after-init --no-http

# 3. Reiniciar web: Odoo solo sirve los estáticos (fuente, logo) de las carpetas que vio
#    al arrancar, así que un addon nuevo o con estáticos nuevos lo necesita.
docker compose restart web
```

El reinicio deja Odoo fuera unos segundos; `/health` de la app puede decir
`odoo: unreachable` hasta 60 s (su caché guarda también los fallos) y luego vuelve a `ok`.

**`report.url` es obligatorio para los PDF**: wkhtmltopdf corre dentro del contenedor y baja
el CSS, la fuente y el logo de esa URL. `web.base.url` es `http://localhost:8169` (el túnel),
que dentro del contenedor no responde, y sin `report.url` el PDF sale sin estilos. En la VPS
quedó `report.url = http://localhost:8069` (2026-11-27). En un Odoo nuevo:

```bash
echo 'env["ir.config_parameter"].sudo().set_param("report.url", "http://localhost:8069"); env.cr.commit()' \
  | docker compose exec -T web odoo shell -c /etc/odoo/odoo.conf -d carvajal --no-http
```

**Comprobar** sin crear adjuntos ni enviar correos: generar el PDF del último pedido
confirmado y revisar que diga «Pedido de venta» y traiga DM Sans (`pdffonts`):

```bash
printf '%s\n' 'import base64' \
  'o = env["sale.order"].search([("state", "=", "sale")], order="id desc", limit=1)' \
  'pdf, _ = env["ir.actions.report"]._render_qweb_pdf("sale.action_report_saleorder", o.ids)' \
  'open("/tmp/pedido.pdf", "wb").write(pdf)' \
  | docker compose exec -T web odoo shell -c /etc/odoo/odoo.conf -d carvajal --no-http
docker compose cp web:/tmp/pedido.pdf .
```

Los pedidos ya confirmados conservan el PDF con que se enviaron: el recibo del chat lee el
adjunto que Odoo guardó al confirmar. El formato nuevo aplica a los pedidos siguientes.

**Volver al PDF de Odoo**: desinstalar el addon desde Aplicaciones, o

```bash
docker compose run --rm web odoo module uninstall -c /etc/odoo/odoo.conf -d carvajal \
    carvajal_sale_report
docker compose restart web
```

Al desinstalarlo se borra también su formato de papel: el reporte vuelve al documento y al
papel de la compañía.

El servicio `web` se une a la red `carvajal-prod_default` con el alias `odoo`, así que la
app llega a `http://odoo:8069`. Guía completa: [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md).
