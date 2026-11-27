{  # noqa: B018 (un manifiesto de Odoo es un diccionario suelto)
    "name": "Carvajal: PDF del pedido",
    "summary": "El PDF del pedido de venta con el formato de la cotización del asistente.",
    "description": """
El reporte «Cotización / Pedido» (``sale.action_report_saleorder``) sale con la marca de
Carvajal Empaques y el mismo formato que el PDF de la cotización del asistente: logo, número
del pedido y folio de la cotización, tabla de productos con foto, barra del total y pie con
la página. Es el PDF que Odoo adjunta al correo de confirmación y el que el chat entrega como
recibo. La factura pro forma no cambia.
""",
    "version": "19.0.1.0.0",
    "category": "Sales",
    "author": "Carvajal (asistente comercial)",
    "license": "LGPL-3",
    "depends": ["sale"],
    "data": [
        "data/report_paperformat.xml",
        "views/report_saleorder.xml",
    ],
    "assets": {
        "web.report_assets_common": [
            "carvajal_sale_report/static/src/scss/report_saleorder.scss",
        ],
    },
    "installable": True,
    "application": False,
}
