"""Odoo 19 por la API JSON-2 (spec 056): pedidos de venta, oportunidades y catálogo.

Cada llamada es ``POST {url}/json/2/{modelo}/{método}`` con la API key del usuario técnico
(``Authorization: bearer``) y la base en ``X-Odoo-Database``; el cuerpo lleva los
argumentos por nombre (``ids``, ``vals_list``, ``domain``, ``context``…) y la respuesta es el
valor que devuelve el método.

El pedido es idempotente por folio: ``client_order_ref`` guarda el folio de la cotización.
Un segundo intento devuelve el pedido confirmado que ya existe; si el primero se quedó a
medias (creado en borrador, sin confirmar), el reintento le pone el cliente actual y lo
confirma. Al confirmar, Odoo envía al cliente el correo de confirmación con la plantilla de
ventas y el PDF, y lo deja en el chatter; sin servidor SMTP el correo no sale (queda en
excepción y se reintenta desde Odoo cuando haya servidor).

Al confirmar un pedido nuevo, el adaptador avisa al equipo con una actividad «Coordinar pago y
entrega del pedido» en el pedido (``mail.activity``, tipo «Por hacer») para el usuario de ventas
de ``ODOO_SALES_LOGIN``; si la actividad falla, el pedido sigue. :class:`OdooProbe` comprueba
para ``/health`` que Odoo responde, con plazo corto y caché.

El cliente se reutiliza solo si coinciden el correo (sin comodines) y el nombre: quien
escribe el correo de otra persona no le asigna pedidos a su ficha. Los datos del cliente
solo viajan a Odoo; los logs llevan el folio, el número de pedido y el tipo de error, nunca
el nombre, el correo ni el mensaje de Odoo.
"""

import base64
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from carvajal.quotation.order import SHIPPING_CODE, OrderLine, OrderRequest

if TYPE_CHECKING:
    from carvajal.catalog.models import Reference
    from carvajal.infrastructure.settings import Settings

__all__ = [
    "ACTIVITY_SUMMARY",
    "OdooClient",
    "OdooError",
    "OdooOrders",
    "OdooProbe",
    "OrderResult",
    "build_orders",
]

logger = structlog.get_logger(__name__)

# Contexto con el que ``action_confirm`` envía al cliente el correo de confirmación.
CONFIRM_CONTEXT: Final[dict[str, Any]] = {"send_email": True}
CATEGORY_NAME: Final = "Empaques Carvajal"
# Marca de los contactos que crea el asistente: solo esos se reutilizan (``ref``).
PARTNER_REF: Final = "asistente-carvajal"
NO_TAXES: Final = [[6, 0, []]]  # los precios llegan calculados por el motor de la app
# Aviso al equipo de ventas en cada pedido nuevo: la actividad «Por hacer» del chatter.
ACTIVITY_TYPE: Final = "mail.mail_activity_data_todo"
ACTIVITY_SUMMARY: Final = "Coordinar pago y entrega del pedido"
# Sonda de ``/health``: plazo corto y caché, para no colgar ni saturar la sonda pública.
PROBE_TIMEOUT_S: Final = 3.0
PROBE_TTL_S: Final = 60.0


class OdooError(RuntimeError):
    """Odoo no respondió o rechazó la llamada.

    ``status`` es el código HTTP (``None`` si no hubo respuesta). El mensaje puede traer el
    texto de Odoo: no se registra en los logs.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class OrderResult:
    """Pedido de venta en Odoo: número visible (``S00012``) e identificadores."""

    name: str
    order_id: int
    lead_id: int | None = None
    existing: bool = False
    # Correo del cliente del pedido: el que sale en el recibo. En un reintento sobre un
    # pedido ya confirmado puede no ser el que se escribió ahora.
    email: str | None = None


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
            raise OdooError(
                f"Odoo rechazó {model}.{method} ({response.status_code}): {detail}",
                response.status_code,
            )
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


def _like_literal(value: str) -> str:
    """``value`` para ``=ilike`` sin comodines: ``%`` y ``_`` de un correo son literales."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _same_name(left: str | None, right: str | None) -> bool:
    return " ".join((left or "").casefold().split()) == " ".join((right or "").casefold().split())


def _log_error(event: str, error: OdooError, **fields: Any) -> None:
    logger.warning(event, status=error.status, error_type=type(error).__name__, **fields)


class OdooOrders:
    """Pedidos de venta y catálogo de Carvajal en una base de Odoo."""

    def __init__(self, client: OdooClient, *, sales_login: str = "") -> None:
        self._odoo = client
        self._category_id: int | None = None
        # ``res.users.login`` de quien recibe la actividad del pedido; vacío, sin actividad.
        self._sales_login = sales_login.strip()
        self._sales_user: int | None = None

    # --- pedidos ---------------------------------------------------------------------

    def place(self, request: OrderRequest) -> OrderResult:
        """Crea (o recupera) el pedido de ``request.folio`` y lo confirma; envía el correo."""

        existing = self._odoo.search_read(
            "sale.order",
            [["client_order_ref", "=", request.folio], ["state", "!=", "cancel"]],
            ["id", "name", "state", "partner_id", "opportunity_id"],
            limit=1,
        )
        if existing and existing[0].get("state") == "sale":
            found = existing[0]
            order_id = int(found["id"])
            lead_id = _many2one(found.get("opportunity_id"))
            if lead_id is None:  # confirmado, pero la respuesta se perdió antes del CRM
                partner = _many2one(found.get("partner_id"))
                if partner is not None:
                    lead_id = self._lead(request, partner, order_id)
                # El aviso va después de la oportunidad: tampoco llegó a programarse.
                self._notify_sales(order_id, str(found["name"]), request)
            logger.info("pedido.existente", folio=request.folio, pedido=found["name"])
            return OrderResult(
                name=str(found["name"]),
                order_id=order_id,
                lead_id=lead_id,
                existing=True,
                email=self._partner_email(_many2one(found.get("partner_id"))),
            )
        partner_id = self._partner(request)
        if existing:  # quedó en borrador: el cliente de ahora y a confirmar
            order_id = int(existing[0]["id"])
            lead_id = _many2one(existing[0].get("opportunity_id"))
            self._odoo.write("sale.order", [order_id], {"partner_id": partner_id})
            logger.info("pedido.retomado", folio=request.folio, pedido=existing[0]["name"])
        else:
            order_id = self._create_order(request, partner_id)
            lead_id = None
        self._confirm(order_id)
        self._note(
            "sale.order",
            order_id,
            [
                f"Pedido confirmado por el cliente en {request.channel} a partir de la "
                f"cotización #{request.folio} del asistente de Carvajal.",
                *request.notes,
            ],
        )
        if lead_id is None:
            lead_id = self._lead(request, partner_id, order_id)
        (order,) = self._odoo.search_read("sale.order", [["id", "=", order_id]], ["name"])
        self._notify_sales(order_id, str(order["name"]), request)
        logger.info("pedido.creado", folio=request.folio, pedido=order["name"])
        return OrderResult(
            name=str(order["name"]),
            order_id=order_id,
            lead_id=lead_id,
            email=request.buyer.email,
        )

    def _notify_sales(self, order_id: int, name: str, request: OrderRequest) -> None:
        """Actividad «Coordinar pago y entrega del pedido» para el usuario de ventas.

        Es lo que cumple el «un asesor te contacta» del recibo: el pedido aparece en
        «Mis actividades» del vendedor. Solo en el pedido que se confirma ahora o en el
        reintento de uno confirmado cuya respuesta se perdió antes del CRM (sin
        oportunidad: tampoco llegó a programarse). Si el login no existe, la actividad queda en
        el usuario técnico; si Odoo la rechaza, el pedido sigue. Los logs no llevan datos de
        quien compra.
        """

        if not self._sales_login:
            return
        values: dict[str, Any] = {
            "act_type_xmlid": ACTIVITY_TYPE,
            "summary": ACTIVITY_SUMMARY,
            "note": _html(
                [
                    f"Pedido {name} de la cotización #{request.folio}, confirmado por el "
                    f"cliente en {request.channel}.",
                    "Contactar al cliente para coordinar el pago y la entrega.",
                ]
            ),
        }
        user_id: int | None = None
        try:
            user_id = self._sales_user_id()
            if user_id is not None:
                values["user_id"] = user_id
            self._odoo.call("sale.order", "activity_schedule", ids=[order_id], **values)
        except OdooError as error:  # el aviso es un extra: el pedido ya está confirmado
            _log_error("pedido.aviso_fallido", error, folio=request.folio)
            return
        logger.info("pedido.aviso_programado", folio=request.folio, asignado=user_id is not None)

    def _sales_user_id(self) -> int | None:
        """``res.users`` de ``ODOO_SALES_LOGIN`` (en caché); ``None`` si no existe."""

        if self._sales_user is None:
            found = self._odoo.search_read(
                "res.users", [["login", "=", self._sales_login]], ["id"], limit=1
            )
            if not found:
                logger.warning("pedido.vendedor_no_encontrado")
                return None
            self._sales_user = int(found[0]["id"])
        return self._sales_user

    def _partner_email(self, partner_id: int | None) -> str | None:
        if partner_id is None:
            return None
        found = self._odoo.search_read("res.partner", [["id", "=", partner_id]], ["email"])
        email = found[0].get("email") if found else None
        return str(email) if email else None

    def order_name(self, folio: str) -> str | None:
        """Número del pedido confirmado de ``folio`` («S00042»), o ``None`` si no hay."""

        found = self._odoo.search_read(
            "sale.order",
            [["client_order_ref", "=", folio], ["state", "=", "sale"]],
            ["name"],
            limit=1,
        )
        name = found[0].get("name") if found else None
        return str(name) if name else None

    def order_pdf(self, folio: str) -> bytes | None:
        """PDF del pedido confirmado de ``folio`` (el que Odoo adjuntó al correo), o ``None``."""

        found = self._odoo.search_read(
            "sale.order",
            [["client_order_ref", "=", folio], ["state", "=", "sale"]],
            ["id", "create_uid"],
            limit=1,
        )
        if not found:
            return None
        # El PDF que generó el bot al confirmar (el del correo): ni un documento interno que
        # el equipo subió al chatter ni otro PDF posterior.
        author = _many2one(found[0].get("create_uid"))
        domain: list[Any] = [
            ["res_model", "=", "sale.order"],
            ["res_id", "=", int(found[0]["id"])],
            ["mimetype", "=", "application/pdf"],
        ]
        if author is not None:
            domain.append(["create_uid", "=", author])
        attachments = self._odoo.call(
            "ir.attachment",
            "search_read",
            domain=domain,
            fields=["datas"],
            order="id asc",
            limit=1,
        )
        if not attachments or not attachments[0].get("datas"):
            return None
        return base64.b64decode(attachments[0]["datas"])

    def _confirm(self, order_id: int) -> None:
        """``action_confirm`` con el correo; un 422 de un pedido ya confirmado no es fallo.

        Dos «sí» casi simultáneos sobre el mismo folio: el segundo encuentra el pedido en
        borrador, pero cuando llama a confirmar el primero ya lo hizo y Odoo responde
        ``UserError`` (422). Si el pedido quedó confirmado, el turno sigue como éxito.
        """

        try:
            self._odoo.call("sale.order", "action_confirm", ids=[order_id], context=CONFIRM_CONTEXT)
        except OdooError as error:
            if error.status != 422:
                raise
            state = self._odoo.search_read("sale.order", [["id", "=", order_id]], ["state"])
            if not state or state[0].get("state") != "sale":
                raise
            logger.info("pedido.confirmado_por_otro_turno", order_id=order_id)

    def _create_order(self, request: OrderRequest, partner_id: int) -> int:
        products = self._products(request.lines)
        (order_id,) = self._odoo.create(
            "sale.order",
            [
                {
                    "partner_id": partner_id,
                    "client_order_ref": request.folio,
                    "origin": (
                        f"Asistente Carvajal ({request.channel}) · cotización #{request.folio}"
                    ),
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
            ],
        )
        return order_id

    def _partner(self, request: OrderRequest) -> int:
        """Contacto creado por el asistente con el mismo correo y el mismo nombre, o uno nuevo.

        Solo el correo no basta: cualquiera puede escribir el de otra persona. Y solo se
        reutilizan contactos del asistente (``ref``): la ficha de un cliente real del equipo
        tiene dirección, NIT o teléfono, que saldrían en el PDF del pedido de quien escribió
        su nombre y su correo. Odoo admite contactos repetidos; el equipo los fusiona.
        """

        email = request.buyer.email or ""
        found = self._odoo.search_read(
            "res.partner",
            [["email", "=ilike", _like_literal(email)], ["ref", "=", PARTNER_REF]],
            ["id", "name"],
            limit=20,
        )
        for item in found:
            if _same_name(str(item.get("name") or ""), request.buyer.name):
                return int(item["id"])
        values: dict[str, Any] = {
            "name": request.buyer.name,
            "email": email,
            "lang": "es_CO",  # el correo de confirmación sale en español
            "ref": PARTNER_REF,
            "comment": _html([f"Cliente creado por el asistente de Carvajal ({request.channel})."]),
        }
        if request.city:
            values["city"] = request.city
        (partner_id,) = self._odoo.create("res.partner", [values])
        return partner_id

    def _lead(self, request: OrderRequest, partner_id: int, order_id: int) -> int | None:
        """Oportunidad del CRM ganada y ligada al pedido; si el CRM falla, el pedido sigue.

        Se crea después de confirmar: un intento que falla antes no deja oportunidades
        huérfanas, y el reintento sobre un pedido sin oportunidad la crea una sola vez.
        """

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
            self._odoo.write("sale.order", [order_id], {"opportunity_id": lead_id})
        except OdooError as error:
            _log_error("pedido.oportunidad_fallida", error, folio=request.folio)
            return None
        self._won(lead_id)
        return lead_id

    def _won(self, lead_id: int) -> None:
        try:
            self._odoo.call("crm.lead", "action_set_won", ids=[lead_id])
        except OdooError as error:
            _log_error("pedido.oportunidad_sin_ganar", error)

    def _note(self, model: str, record_id: int, lines: Sequence[str]) -> None:
        try:
            self._odoo.call(
                model,
                "message_post",
                ids=[record_id],
                body=_html(lines),
                body_is_html=True,  # un str se escapa: las etiquetas saldrían literales
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        except OdooError as error:  # la traza es un extra: el pedido ya existe
            _log_error("pedido.nota_fallida", error)

    def _products(self, lines: Sequence[OrderLine]) -> dict[str, int]:
        """ID de ``product.product`` por SKU; crea los que el catálogo de Odoo no tenga."""

        codes = sorted({line.code for line in lines})
        found = self._odoo.search_read(
            "product.product",
            [["default_code", "in", codes], ["active", "in", [True, False]]],
            ["id", "default_code", "active"],
        )
        products = {str(item["default_code"]): int(item["id"]) for item in found}
        archived = [int(item["id"]) for item in found if item.get("active") is False]
        if archived:  # un SKU archivado se reactiva en lugar de duplicarse
            self._odoo.write("product.product", archived, {"active": True})
        missing = [line for line in lines if line.code not in products]
        seen: set[str] = set()
        values = []
        for line in missing:
            if line.code in seen:
                continue
            seen.add(line.code)
            values.append(self._product_values(line))
        if values:
            self._odoo.create("product.product", values)
            # Se vuelven a leer por SKU: el pedido nunca depende del orden de los IDs creados.
            again = self._odoo.search_read(
                "product.product",
                [["default_code", "in", [item["default_code"] for item in values]]],
                ["id", "default_code"],
            )
            products.update({str(item["default_code"]): int(item["id"]) for item in again})
            missing_codes = [line.code for line in lines if line.code not in products]
            if missing_codes:
                raise OdooError(f"Odoo no devolvió {len(missing_codes)} productos creados.")
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
            [["default_code", "in", codes], ["active", "in", [True, False]]],
            ["id", "default_code", "name", "list_price", "description_sale", "active"],
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
                for key in ("name", "list_price", "description_sale", "active")
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

    def sync_images(self, images: Mapping[str, bytes]) -> dict[str, int]:
        """Foto principal (``image_1920``) de cada SKU que todavía no tenga una en Odoo."""

        if not images:
            return {"fotos_subidas": 0, "fotos_existentes": 0}
        codes = sorted(images)
        found = self._odoo.search_read(
            "product.template", [["default_code", "in", codes]], ["id", "default_code"]
        )
        missing = self._odoo.search_read(
            "product.template",
            [["default_code", "in", codes], ["image_1920", "=", False]],
            ["id", "default_code"],
        )
        for item in missing:
            encoded = base64.b64encode(images[str(item["default_code"])]).decode("ascii")
            self._odoo.write("product.template", [int(item["id"])], {"image_1920": encoded})
        return {"fotos_subidas": len(missing), "fotos_existentes": len(found) - len(missing)}


def _many2one(value: Any) -> int | None:
    return int(value[0]) if isinstance(value, list) and value else None


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
        "active": True,
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
    return OdooOrders(client, sales_login=settings.odoo_sales_login)


class OdooProbe:
    """Bloque ``odoo`` de ``/health``: configurado y, si lo está, si Odoo responde.

    La sonda es ``sale.order.search_count`` sin resultados por JSON-2: comprueba la red, la
    base, la API key y el acceso a ventas sin leer datos. Plazo corto
    (:data:`PROBE_TIMEOUT_S`) y caché (:data:`PROBE_TTL_S`, también para un fallo): un Odoo
    caído no frena ``/health`` más de una vez por minuto. Sin secretos ni URL: ``reason`` es
    ``auth`` (401/403), ``unreachable`` (sin respuesta) o ``error``.
    """

    def __init__(
        self,
        settings: "Settings",
        *,
        transport: httpx.BaseTransport | None = None,
        ttl_s: float = PROBE_TTL_S,
        timeout_s: float = PROBE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[float, dict[str, object]] | None = None

    def status(self) -> dict[str, object]:
        """``{"configured", "status"}`` y, configurado, ``reachable`` (y ``reason`` si falla)."""

        settings = self._settings
        if not settings.odoo_configured:
            return {"configured": False, "status": "off"}
        with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._cached[0] < self._ttl_s:
                return dict(self._cached[1])
            block = self._probe()
            self._cached = (now, block)
            return dict(block)

    def _probe(self) -> dict[str, object]:
        client = OdooClient(
            self._settings.odoo_url,
            self._settings.odoo_db,
            self._settings.odoo_api_key.get_secret_value(),
            timeout=min(self._timeout_s, self._settings.odoo_timeout_s),
            transport=self._transport,
        )
        try:
            client.call("sale.order", "search_count", domain=[["id", "=", 0]])
        except OdooError as error:
            if error.status in {401, 403}:
                reason = "auth"
            elif error.status is None:
                reason = "unreachable"
            else:
                reason = "error"
            logger.warning("odoo.health_failed", status=error.status, reason=reason)
            return {
                "configured": True,
                "status": "unreachable",
                "reachable": False,
                "reason": reason,
            }
        finally:
            client.close()
        return {"configured": True, "status": "ok", "reachable": True}
