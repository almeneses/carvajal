"""CRM externo (spec 055): pedidos de venta y catálogo en Odoo."""

from carvajal.adapters.crm.odoo import (
    OdooClient,
    OdooError,
    OdooOrders,
    OrderResult,
    build_orders,
)

__all__ = ["OdooClient", "OdooError", "OdooOrders", "OrderResult", "build_orders"]
