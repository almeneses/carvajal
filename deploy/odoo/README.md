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

- [`carvajal_sale_report`](addons/carvajal_sale_report/): el PDF del pedido (el del correo de
  confirmación y el recibo del chat) con el formato de la cotización del asistente.

Para actualizar un addon en la VPS: copia su carpeta a `~/carvajal-odoo/addons/` y corre

```bash
docker compose run --rm web odoo -c /etc/odoo/odoo.conf -d carvajal \
    -u carvajal_sale_report --stop-after-init   # -i la primera vez
docker compose restart web
```

Para volver al PDF de Odoo, desinstala el addon desde Aplicaciones (o con
`--uninstall`); el formato de papel del reporte vuelve al de la compañía al reinstalar `sale`.

El servicio `web` se une a la red `carvajal-prod_default` con el alias `odoo`, así que la
app llega a `http://odoo:8069`. Guía completa: [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md).
