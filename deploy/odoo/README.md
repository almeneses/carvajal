# Odoo 19 de Carvajal (spec 056)

Plantilla del proyecto de compose `carvajal-odoo` que corre en la VPS en `~/carvajal-odoo`
(usuario `deploy`). Sin secretos: en el servidor `.env` y `config/odoo.conf` llevan las
claves reales (chmod 600).

```bash
cp env.example .env                         # y pon una clave de Postgres
cp config/odoo.conf.example config/odoo.conf # admin_passwd y db_password
mkdir -p addons
docker compose up -d
docker compose run --rm web odoo -c /etc/odoo/odoo.conf -d carvajal \
    -i sale_management,crm,sale_crm,contacts --stop-after-init
```

El servicio `web` se une a la red `carvajal-prod_default` con el alias `odoo`, así que la
app llega a `http://odoo:8069`. Guía completa: [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md).
