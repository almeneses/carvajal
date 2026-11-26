"""Odoo 19 por la API JSON-2 (spec 055): pedidos de venta, oportunidades y catálogo.

Cada llamada es ``POST {url}/json/2/{modelo}/{método}`` con la API key del usuario técnico
(``Authorization: bearer``) y la base en ``X-Odoo-Database``; el cuerpo lleva los
argumentos por nombre (``ids``, ``vals_list``, ``domain``, ``context``…) y la respuesta es el
valor que devuelve el método.

El pedido es idempotente por folio: ``client_order_ref`` guarda el folio de la cotización y
un segundo intento devuelve el pedido que ya existe. Al confirmar el pedido Odoo envía al
cliente el correo de confirmación con la plantilla de ventas; si no hay servidor de correo
configurado, el correo queda en la cola de Odoo y se ve en el chatter del pedido.

Los datos del cliente (nombre y correo) solo viajan a Odoo; los logs llevan el folio y el
número de pedido, nunca el nombre ni el correo.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from carvajal.quotation.order import SHIPPING_CODE, OrderLine, OrderRequest

if TYPE_CHECKING:
    from carvajal.catalog.models import Reference
    from carvajal.infrastructure.settings import Settings

__all__ = ["OdooClient", "OdooError", "OdooOrders", "OrderResult", "build_orders"]

logger = structlog.get_logger(__name__)

# Contexto con el que ``action_confirm`` envía al cliente el correo de confirmación.
CONFIRM_CONTEXT: Final[dict[str, Any]] = {"send_email": True}
CATEGORY_NAME: Final = "Empaques Carvajal"
NO_TAXES: Final = [[6, 0, []]]  # los precios llegan calculados por el motor de la app


class OdooError(RuntimeError):
    """Odoo no respondió o rechazó la llamada; el mensaje no incluye datos del cliente."""


@dataclass(frozen=True)
class OrderResult:
    """Pedido de venta en Odoo: número visible (``S00012``) e identificadores."""

    name: str
    order_id: int
    lead_id: int | None = None
    existing: bool = False


class OdooClient:
    """Cliente mínimo de la API JSON-2 de Odoo."""

    def __init__(
        self,
        url: str,
        database: str,
        api_key: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._database = database
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"bearer {api_key}",
                "X-Odoo-Database": database,
                "Content-Type": "application/json",
                "User-Agent": "carvajal-asistente",
            },
        )

    def call(self, model: str, method: str, **arguments: Any) -> Any:
        try:
            response = self._client.post(f"/json/2/{model}/{method}", json=arguments)
        except httpx.HTTPError as error:
            raise OdooError(f"Odoo no responde ({type(error).__name__}).") from error
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("message") or body.get("name") or "")[:200]
            except ValueError:
                detail = response.text[:200]
            raise OdooError(f"Odoo rechazó {model}.{method} ({response.status_code}): {detail}")
        try:
            return response.json()
        except ValueError as error:
            raise OdooError(f"Respuesta inválida de {model}.{method}.") from error

    def search_read(
        self,
        model: str,
        domain: Sequence[Any],
        fields: Sequence[str],
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"domain": list(domain), "fields": list(fields)}
        if limit is not None:
            arguments["limit"] = limit
        found = self.call(model, "search_read", **arguments)
        return list(found or [])

    def create(self, model: str, values: Sequence[Mapping[str, Any]]) -> list[int]:
        created = self.call(model, "create", vals_list=[dict(item) for item in values])
        return [int(item) for item in (created if isinstance(created, list) else [created])]

    def write(self, model: str, ids: Sequence[int], values: Mapping[str, Any]) -> None:
        self.call(model, "write", ids=list(ids), vals=dict(values))

    def close(self) -> None:
        self._client.close()


def _money(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0


def _html(lines: Iterable[str]) -> str:
    from html import escape

    return "".join(f"<p>{escape(line)}</p>" for line in lines if line)


class OdooOrders:
    """Pedidos de venta y catálogo de Carvajal en una base de Odoo."""

    def __init__(self, client: OdooClient) -> None:
        self._odoo = client
        self._category_id: int | None = None

    # --- pedidos ---------------------------------------------------------------------

    def place(self, request: OrderRequest) -> OrderResult:
        """Crea (o recupera) el pedido de ``request.folio`` y lo confirma; envía el correo."""

        existing = self._odoo.search_read(
            "sale.order",
            [["client_order_ref", "=", request.folio]],
            ["id", "name", "opportunity_id"],
            limit=1,
        )
        if existing:
            found = existing[0]
            lead = found.get("opportunity_id")
            logger.info("pedido.existente", folio=request.folio, pedido=found["name"])
            return OrderResult(
                name=str(found["name"]),
                order_id=int(found["id"]),
                lead_id=int(lead[0]) if isinstance(lead, list) and lead else None,
                existing=True,
            )
        partner_id = self._partner(request)
        products = self._products(request.lines)
        lead_id = self._lead(request, partner_id)
        order_values: dict[str, Any] = {
            "partner_id": partner_id,
            "client_order_ref": request.folio,
            "origin": f"Asistente Carvajal ({request.channel}) · cotización #{request.folio}",
            "note": _html(request.notes),
            "order_line": [
                [
                    0,
                    0,
                    {
                        "product_id": products[line.code],
                        "name": line.description,
                        "product_uom_qty": float(line.quantity),
                        "price_unit": _money(line.price_unit),
                    },
                ]
                for line in request.lines
            ],
        }
        if lead_id is not None:
            order_values["opportunity_id"] = lead_id
        (order_id,) = self._odoo.create("sale.order", [order_values])
        self._odoo.call("sale.order", "action_confirm", ids=[order_id], context=CONFIRM_CONTEXT)
        self._note(
            "sale.order",
            order_id,
            [
                f"Pedido confirmado por el cliente en {request.channel} a partir de la "
                f"cotización #{request.folio} del asistente de Carvajal.",
                *request.notes,
            ],
        )
        if lead_id is not None:
            self._won(lead_id)
        (order,) = self._odoo.search_read("sale.order", [["id", "=", order_id]], ["name"])
        logger.info("pedido.creado", folio=request.folio, pedido=order["name"])
        return OrderResult(name=str(order["name"]), order_id=order_id, lead_id=lead_id)

    def _partner(self, request: OrderRequest) -> int:
        email = request.buyer.email or ""
        found = self._odoo.search_read("res.partner", [["email", "=ilike", email]], ["id"], limit=1)
        if found:
            return int(found[0]["id"])
        values: dict[str, Any] = {
            "name": request.buyer.name,
            "email": email,
            "lang": "es_CO",  # el correo de confirmación sale en español
            "comment": _html([f"Cliente creado por el asistente de Carvajal ({request.channel})."]),
        }
        if request.city:
            values["city"] = request.city
        (partner_id,) = self._odoo.create("res.partner", [values])
        return partner_id

    def _lead(self, request: OrderRequest, partner_id: int) -> int | None:
        """Oportunidad del CRM ganada con el pedido; si el CRM falla, el pedido sigue."""

        try:
            (lead_id,) = self._odoo.create(
                "crm.lead",
                [
                    {
                        "name": f"Pedido de la cotización #{request.folio}",
                        "type": "opportunity",
                        "partner_id": partner_id,
                        "contact_name": request.buyer.name,
                        "email_from": request.buyer.email,
                        "expected_revenue": _money(request.total),
                        "description": _html([f"Canal: {request.channel}.", *request.notes]),
                    }
                ],
            )
        except OdooError as error:
            logger.warning("pedido.oportunidad_fallida", folio=request.folio, error=str(error))
            return None
        return lead_id

    def _won(self, lead_id: int) -> None:
        try:
            self._odoo.call("crm.lead", "action_set_won", ids=[lead_id])
        except OdooError as error:
            logger.warning("pedido.oportunidad_sin_ganar", error=str(error))

    def _note(self, model: str, record_id: int, lines: Sequence[str]) -> None:
        try:
            self._odoo.call(
                model,
                "message_post",
                ids=[record_id],
                body=_html(lines),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        except OdooError as error:  # la traza es un extra: el pedido ya existe
            logger.warning("pedido.nota_fallida", error=str(error))

    def _products(self, lines: Sequence[OrderLine]) -> dict[str, int]:
        """ID de ``product.product`` por SKU; crea los que el catálogo de Odoo no tenga."""

        codes = sorted({line.code for line in lines})
        found = self._odoo.search_read(
            "product.product", [["default_code", "in", codes]], ["id", "default_code"]
        )
        products = {str(item["default_code"]): int(item["id"]) for item in found}
        missing = [line for line in lines if line.code not in products]
        seen: set[str] = set()
        values = []
        for line in missing:
            if line.code in seen:
                continue
            seen.add(line.code)
            values.append(self._product_values(line))
        if values:
            created = self._odoo.create("product.product", values)
            codes_created = [str(item["default_code"]) for item in values]
            products.update(dict(zip(codes_created, created, strict=True)))
        return products

    def _product_values(self, line: OrderLine) -> dict[str, Any]:
        values: dict[str, Any] = {
            "name": line.name,
            "default_code": line.code,
            "list_price": _money(line.price_unit),
            "type": "service" if line.service else "consu",
            "sale_ok": True,
            "purchase_ok": False,
            "taxes_id": NO_TAXES,
        }
        if not line.service:
            values["categ_id"] = self._category()
        return values

    # --- catálogo --------------------------------------------------------------------

    def _category(self) -> int:
        if self._category_id is None:
            found = self._odoo.search_read(
                "product.category", [["name", "=", CATEGORY_NAME]], ["id"], limit=1
            )
            if found:
                self._category_id = int(found[0]["id"])
            else:
                (self._category_id,) = self._odoo.create(
                    "product.category", [{"name": CATEGORY_NAME}]
                )
        return self._category_id

    def sync_catalog(self, references: Sequence["Reference"]) -> dict[str, int]:
        """Crea o actualiza un producto por referencia (``default_code`` = SKU)."""

        category = self._category()
        codes = [reference.sku for reference in references]
        found = self._odoo.search_read(
            "product.template",
            [["default_code", "in", codes]],
            ["id", "default_code", "name", "list_price", "description_sale"],
        )
        current = {str(item["default_code"]): item for item in found}
        created = updated = unchanged = 0
        pending: list[dict[str, Any]] = []
        for reference in references:
            values = _reference_values(reference, category)
            existing = current.get(reference.sku)
            if existing is None:
                pending.append(values)
                continue
            changed = {
                key: values[key]
                for key in ("name", "list_price", "description_sale")
                if existing.get(key) != values[key]
            }
            if changed:
                self._odoo.write("product.template", [int(existing["id"])], changed)
                updated += 1
            else:
                unchanged += 1
        for start in range(0, len(pending), 50):
            created += len(self._odoo.create("product.template", pending[start : start + 50]))
        shipping = self._odoo.search_read(
            "product.product", [["default_code", "=", SHIPPING_CODE]], ["id"], limit=1
        )
        if not shipping:
            self._odoo.create(
                "product.product",
                [
                    self._product_values(
                        OrderLine(SHIPPING_CODE, "Envío", "Envío", Decimal(1), Decimal(0), True)
                    )
                ],
            )
        return {"creados": created, "actualizados": updated, "sin_cambios": unchanged}


def _reference_values(reference: "Reference", category: int) -> dict[str, Any]:
    details = [
        reference.family,
        reference.material,
        f"{reference.units_per_box} unidades por caja",
    ]
    if reference.capacity_oz:
        details.append(f"{reference.capacity_oz:g} oz")
    if reference.brand:
        details.append(f"marca {reference.brand}")
    if reference.price is not None and reference.tax_included is not None:
        details.append("IVA incluido" if reference.tax_included else "IVA no incluido")
    return {
        "name": reference.name,
        "default_code": reference.sku,
        "list_price": float(reference.price) if reference.price is not None else 0.0,
        "type": "consu",
        "sale_ok": True,
        "purchase_ok": False,
        "categ_id": category,
        "taxes_id": NO_TAXES,
        "description_sale": " · ".join(details),
    }


def build_orders(settings: "Settings") -> OdooOrders | None:
    """Pedidos en Odoo si ``ODOO_URL``, ``ODOO_DB`` y ``ODOO_API_KEY`` están definidos."""

    if not settings.odoo_configured:
        return None
    client = OdooClient(
        settings.odoo_url,
        settings.odoo_db,
        settings.odoo_api_key.get_secret_value(),
        timeout=settings.odoo_timeout_s,
    )
    return OdooOrders(client)
