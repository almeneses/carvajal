"""Cobertura del pedido en Odoo (spec 056) que faltaba tras la auditoría de la PR #279.

Una sección por hueco verificado: precios vencidos al confirmar (FR-032), caminos del
adaptador JSON-2 (carrera 422, productos y categoría nuevos, oportunidad que faltó,
``sync_catalog`` y errores del transporte), el servicio contra el adaptador real, las reglas
generales a mitad del pedido (FR-031), botones y rechazos (FR-003 y FR-004), «olvida todo»
con datos en el borrador, retomar tras «ahora no», la guarda de ``_place_order``, la voz del
pedido, palabras sueltas que no son nombres, las variantes del total del recibo (FR-026),
``send_email`` (FR-015) y la privacidad fuera del camino feliz (FR-020).

Los cuatro comportamientos que la auditoría dejó en ``xfail`` (rechazo en la confirmación,
datos sin CRM tras un reinicio y ciudades o siglas como nombre en la oferta y en la
confirmación) se corrigieron en la revisión de la rama de cobertura (2026-11-27): sus pruebas
ya son normales.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs
from tests.test_chat_service_tools import (
    FakeTracer,
    final,
    named,
    scripted_events,
    stored_messages,
    stored_quotes,
)
from tests.test_pedidos_odoo import (
    OWNER,
    FakeOdoo,
    FakeOrders,
    _line,
    _orders,
    _registered,
    _request,
    _say,
    _service,
)
from tests.test_pedidos_odoo_flujo import (
    EMAIL,
    MASKED,
    NAME,
    _all_rows,
    _ask,
    _stored_draft,
)

from apps.api.chat import service as service_module
from apps.api.chat.service import ORDER_DATA_PLACEHOLDER, ChatService
from carvajal.adapters.crm.odoo import (
    CATEGORY_NAME,
    OdooClient,
    OdooError,
    OdooOrders,
    OrderResult,
)
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import SqlChatRepository
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.drafts import SqlDraftRepository
from carvajal.agent.followup import is_leaving
from carvajal.catalog.models import Reference
from carvajal.quotation import flow, render
from carvajal.quotation.draft import DRAFT_DAYS, Awaiting, QuoteDraft
from carvajal.quotation.flow import ORDER_AWAITING
from carvajal.quotation.order import (
    SHIPPING_CODE,
    Buyer,
    OrderLine,
    OrderRequest,
    declines_order,
    spoken_address,
)

NOW = datetime.fromisoformat("2026-11-26T10:00:00-05:00")
FOLIO = "COT-1"
STALE = "2020-01-01"  # una vigencia que ya pasó
# SKU de ``quote_payload`` (el «Vaso rígido» de ``commercial_final``).
QUOTED_SKU = "7704918100199"


@pytest.fixture()
def engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        motor.dispose()


def _draft(awaiting: str | None, buyer: Buyer | None = None, **changes: Any) -> QuoteDraft:
    """Cotización registrada con el pedido en el paso ``awaiting``."""

    return QuoteDraft(
        lines=(_line(),),
        state="registrada",
        folio=FOLIO,
        awaiting=cast("Awaiting", awaiting),
        buyer=buyer.to_json() if buyer else None,
        **changes,
    )


def _before(draft: QuoteDraft, message: str) -> flow.Reply | flow.Pass:
    return flow.before_turn(draft, message, orders=True, now=NOW)


def _edit_draft(engine: Any, change: Callable[[QuoteDraft], QuoteDraft]) -> None:
    """Cambia el borrador guardado del hilo activo (como si pasara el tiempo)."""

    with build_session_factory(engine)() as session:
        conversation = SqlChatRepository(session).active(OWNER)
        assert conversation is not None
        drafts = SqlDraftRepository(session)
        drafts.save(conversation.id, OWNER, change(drafts.get(conversation.id)))
        session.commit()


def _until_confirmation(service: ChatService) -> None:
    """Folio → «sí» → nombre y correo: el borrador queda en ``confirmar_pedido``."""

    _registered(service)
    _say(service, "sí")
    _say(service, f"{NAME}, {EMAIL}")


def _with_stale_prices(draft: QuoteDraft) -> QuoteDraft:
    lines = tuple(
        {**line, "commercial": {**line["commercial"], "valid_until": STALE}} for line in draft.lines
    )
    return replace(draft, lines=lines)


def _fallback(events: list[Any]) -> str:
    return str(named(events, "fallback").data["text"])


# --- #17: precios vencidos al confirmar el pedido (FR-032) ------------------------------


def test_con_precios_vencidos_el_si_no_llama_a_odoo_y_borra_los_datos(engine: Any) -> None:
    # Arrange: la persona vuelve días después, con la vigencia del vendedor ya pasada.
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _until_confirmation(service)
    _edit_draft(engine, _with_stale_prices)

    # Act
    answer = _say(service, "sí")
    stored = _stored_draft(engine)
    again = _say(service, "sí")

    # Assert
    assert answer == render.order_expired(["Vaso rígido"]).text
    assert orders.requests == []
    assert stored.awaiting is None and stored.buyer is None and stored.order is None
    assert stored.state == "registrada" and stored.folio  # la cotización sigue registrada
    assert "Pedido" not in again and "confirmado" not in again
    assert orders.requests == []  # un «sí» posterior tampoco llama a Odoo
    rows = _all_rows(engine)
    assert NAME not in rows and EMAIL not in rows


def test_los_precios_vencidos_se_guardan_sin_los_datos_del_turno(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    _until_confirmation(service)
    _edit_draft(engine, _with_stale_prices)

    # Act
    with capture_logs() as logs:
        _say(service, "sí")

    # Assert
    contents = [str(row["content"]) for row in stored_messages(engine)]
    assert contents[-2] == ORDER_DATA_PLACEHOLDER  # el «sí» que confirma los datos
    assert contents[-1] == render.order_expired(["Vaso rígido"]).text
    (expired,) = [log for log in logs if log["event"] == "pedido.precios_vencidos"]
    assert expired["lineas"] == 1
    assert NAME not in repr(logs) and EMAIL not in repr(logs)


# --- #19: caminos del adaptador JSON-2 sin cubrir ----------------------------------------


class RaceOdoo(FakeOdoo):
    """Odoo en el que otro turno confirmó el pedido justo antes: ``action_confirm`` da 422.

    ``state_after`` es el estado que lee el adaptador tras el 422.
    """

    def __init__(self, state_after: str, **kwargs: Any) -> None:
        super().__init__(fail={"sale.order.action_confirm": 422}, **kwargs)
        self.state_after = state_after

    def _orders(self, domain: list[Any]) -> list[dict[str, Any]]:
        found = super()._orders(domain)
        if domain and domain[0][0] == "id":
            return [{**item, "state": self.state_after} for item in found]
        return found


def test_un_422_de_un_pedido_ya_confirmado_por_otro_turno_es_exito() -> None:
    # Arrange: dos «sí» casi simultáneos; el otro confirmó primero.
    fake = RaceOdoo("sale", order_state="draft")

    # Act
    with capture_logs() as logs:
        result = _orders(fake).place(_request())

    # Assert
    assert result.name == "S00003" and result.order_id == 90
    assert "sale.order.create" not in fake.methods()
    methods = fake.methods()
    confirmed = methods.index("sale.order.action_confirm")
    assert methods[confirmed + 1] == "sale.order.search_read"  # relee el estado
    assert fake.bodies("sale.order", "message_post")  # la traza sigue
    assert any(log["event"] == "pedido.confirmado_por_otro_turno" for log in logs)


def test_un_422_con_el_pedido_sin_confirmar_es_un_fallo() -> None:
    # Arrange: Odoo rechazó la confirmación y el pedido sigue en borrador.
    fake = RaceOdoo("draft")

    # Act / Assert
    with pytest.raises(OdooError) as raised:
        _orders(fake).place(_request())
    assert raised.value.status == 422
    assert "crm.lead.create" not in fake.methods()
    assert "sale.order.message_post" not in fake.methods()


def test_otro_error_al_confirmar_no_relee_el_estado() -> None:
    # Arrange
    fake = FakeOdoo(fail={"sale.order.action_confirm": 500})

    # Act / Assert
    with pytest.raises(OdooError) as raised:
        _orders(fake).place(_request())
    assert raised.value.status == 500
    lookups = fake.bodies("sale.order", "search_read")
    assert len(lookups) == 1  # solo la búsqueda por folio, antes de crear


class CatalogOdoo(FakeOdoo):
    """Odoo sin los productos ni la categoría del pedido: el adaptador los crea.

    ``created`` es lo que devuelve la relectura por SKU tras crear (``None``: los creados);
    ``category`` dice si ya existe la categoría «Empaques Carvajal».
    """

    def __init__(
        self, *, created: list[dict[str, Any]] | None = None, category: bool = False
    ) -> None:
        super().__init__(products=[])
        self.created = created
        self.category = category

    def _answer(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if (model, method) == ("product.category", "search_read"):
            return [{"id": 1}] if self.category else []
        if (model, method) == ("product.product", "search_read"):
            if ["active", "in", [True, False]] in body["domain"]:
                return []  # la búsqueda inicial: Odoo no tiene ningún SKU del pedido
            if self.created is not None:
                return self.created
            codes = body["domain"][0][2]
            return [{"id": 500 + index, "default_code": code} for index, code in enumerate(codes)]
        return super()._answer(model, method, body)


def _shipping_request() -> OrderRequest:
    lines = (
        OrderLine("V-99", "Vaso 9 oz", "[V-99] Vaso 9 oz", Decimal(2), Decimal(25000)),
        OrderLine(SHIPPING_CODE, "Envío", "Envío a Bogotá", Decimal(1), Decimal(8000), True),
    )
    return replace(_request(), lines=lines, total=Decimal(58000))


def test_los_productos_que_odoo_no_tiene_se_crean_y_se_releen_por_sku() -> None:
    # Arrange
    fake = CatalogOdoo()

    # Act
    _orders(fake).place(_shipping_request())

    # Assert
    (created,) = fake.bodies("product.product", "create")
    product, shipping = created["vals_list"]
    assert (product["default_code"], product["type"], product["list_price"]) == (
        "V-99",
        "consu",
        25000.0,
    )
    assert product["categ_id"] == 900  # la categoría recién creada
    assert product["taxes_id"] == [[6, 0, []]]
    assert (shipping["default_code"], shipping["type"]) == (SHIPPING_CODE, "service")
    assert "categ_id" not in shipping  # el envío es un servicio, sin categoría
    (category,) = fake.bodies("product.category", "create")
    assert category["vals_list"] == [{"name": CATEGORY_NAME}]
    _, reread = fake.bodies("product.product", "search_read")
    assert reread["domain"] == [["default_code", "in", ["V-99", SHIPPING_CODE]]]
    (order,) = fake.bodies("sale.order", "create")
    ids = [line[2]["product_id"] for line in order["vals_list"][0]["order_line"]]
    assert ids == [500, 501]  # los IDs de la relectura, por SKU


def test_si_odoo_no_devuelve_los_productos_creados_el_pedido_no_se_crea() -> None:
    # Arrange: la relectura no trae el SKU recién creado.
    fake = CatalogOdoo(created=[])

    # Act / Assert
    with pytest.raises(OdooError, match="Odoo no devolvió 2 productos creados"):
        _orders(fake).place(_shipping_request())
    assert "sale.order.create" not in fake.methods()


def test_la_categoria_existente_se_reutiliza_y_se_busca_una_vez() -> None:
    # Arrange
    fake = CatalogOdoo(category=True)
    orders = _orders(fake)

    # Act
    orders.place(_shipping_request())
    orders.place(replace(_shipping_request(), folio="COT-2"))

    # Assert
    assert "product.category.create" not in fake.methods()
    assert len(fake.bodies("product.category", "search_read")) == 1  # en caché
    first, second = fake.bodies("product.product", "create")
    assert first["vals_list"][0]["categ_id"] == second["vals_list"][0]["categ_id"] == 1


def test_un_pedido_confirmado_sin_oportunidad_la_crea_al_repetir() -> None:
    # Arrange: el pedido quedó confirmado, pero la respuesta se perdió antes del CRM.
    fake = FakeOdoo(order_state="sale", opportunity=None)

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.existing and result.lead_id == 300
    assert "sale.order.create" not in fake.methods()
    assert "sale.order.action_confirm" not in fake.methods()
    (lead,) = fake.bodies("crm.lead", "create")
    assert lead["vals_list"][0]["partner_id"] == 1  # el cliente del pedido existente
    assert fake.bodies("sale.order", "write") == [{"ids": [90], "vals": {"opportunity_id": 300}}]
    assert fake.bodies("crm.lead", "action_set_won") == [{"ids": [300]}]


def _reference(sku: str, *, price: str | None = "25000", name: str = "Vaso 12 oz") -> Reference:
    return Reference(
        source_url="https://carvajalempaques.com/vaso",
        captured_at=date(2026, 11, 1),
        sku=sku,
        name=name,
        family="vaso",
        use="bebidas",
        capacity_oz=12,
        material="PS",
        brand="Wau",
        units_per_box=1000,
        price=Decimal(price) if price is not None else None,
        currency="COP" if price is not None else None,
        tax_included=True if price is not None else None,
        country="CO",
    )


class SyncOdoo:
    """Odoo en memoria para ``sync_catalog``: plantillas por SKU y el servicio de envío."""

    def __init__(self) -> None:
        self.templates: dict[str, dict[str, Any]] = {}
        self.shipping = False
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._next = 700

    def __call__(self, request: httpx.Request) -> httpx.Response:
        _, _, model, method = request.url.path.strip("/").split("/")
        body = json.loads(request.content)
        self.calls.append((model, method, body))
        return httpx.Response(200, json=self._answer(model, method, body))

    def _answer(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if model == "product.category":
            return [{"id": 1}] if method == "search_read" else [1]
        if model == "product.template" and method == "search_read":
            codes = body["domain"][0][2]
            return [dict(item) for code, item in self.templates.items() if code in codes]
        if model == "product.template" and method == "create":
            ids = []
            for values in body["vals_list"]:
                self._next += 1
                self.templates[values["default_code"]] = {"id": self._next, **values}
                ids.append(self._next)
            return ids
        if model == "product.template" and method == "write":
            for item in self.templates.values():
                if item["id"] in body["ids"]:
                    item.update(body["vals"])
            return True
        if model == "product.product" and method == "search_read":
            return [{"id": 1}] if self.shipping else []
        if model == "product.product" and method == "create":
            self.shipping = True
            return [800]
        return True

    def writes(self) -> list[str]:
        return [f"{m}.{name}" for m, name, _ in self.calls if name in {"create", "write"}]

    def bodies(self, model: str, method: str) -> list[dict[str, Any]]:
        return [body for m, name, body in self.calls if (m, name) == (model, method)]


def _sync_orders(fake: SyncOdoo) -> OdooOrders:
    client = OdooClient("http://odoo:8069", "carvajal", "k", transport=httpx.MockTransport(fake))
    return OdooOrders(client)


def test_sync_catalog_da_de_alta_cada_referencia_y_el_envio() -> None:
    # Arrange
    fake = SyncOdoo()
    references = [_reference("V-12"), _reference("V-16", price=None, name="Vaso 16 oz")]

    # Act
    report = _sync_orders(fake).sync_catalog(references)

    # Assert
    assert report == {"creados": 2, "actualizados": 0, "sin_cambios": 0}
    (created,) = fake.bodies("product.template", "create")
    priced, unpriced = created["vals_list"]
    assert (priced["default_code"], priced["list_price"], priced["categ_id"]) == ("V-12", 25000, 1)
    assert priced["description_sale"].endswith("IVA incluido")
    assert unpriced["list_price"] == 0.0 and "IVA" not in unpriced["description_sale"]
    (shipping,) = fake.bodies("product.product", "create")
    assert shipping["vals_list"][0]["default_code"] == SHIPPING_CODE
    assert shipping["vals_list"][0]["type"] == "service"


def test_sync_catalog_una_segunda_pasada_no_escribe_nada() -> None:
    # Arrange
    fake = SyncOdoo()
    orders = _sync_orders(fake)
    references = [_reference("V-12"), _reference("V-16")]
    orders.sync_catalog(references)
    fake.calls.clear()

    # Act
    report = orders.sync_catalog(references)

    # Assert
    assert report == {"creados": 0, "actualizados": 0, "sin_cambios": 2}
    assert fake.writes() == []


def test_sync_catalog_reactiva_un_archivado_y_actualiza_el_precio_sin_duplicar() -> None:
    # Arrange
    fake = SyncOdoo()
    orders = _sync_orders(fake)
    orders.sync_catalog([_reference("V-12")])
    fake.templates["V-12"]["active"] = False  # alguien lo archivó en Odoo
    fake.calls.clear()

    # Act
    report = orders.sync_catalog([_reference("V-12", price="27000")])

    # Assert
    assert report == {"creados": 0, "actualizados": 1, "sin_cambios": 0}
    (write,) = fake.bodies("product.template", "write")
    assert write["vals"] == {"list_price": 27000.0, "active": True}
    assert "product.template.create" not in fake.writes()


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> OdooClient:
    return OdooClient("http://odoo:8069", "carvajal", "k", transport=httpx.MockTransport(handler))


def test_sin_red_odoo_error_sin_codigo_y_el_pedido_no_sigue() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin ruta", request=request)

    # Act / Assert
    with pytest.raises(OdooError, match=r"Odoo no responde \(ConnectError\)") as raised:
        OdooOrders(_client(handler)).place(_request())
    assert raised.value.status is None


def test_una_respuesta_que_no_es_json_es_un_odoo_error() -> None:
    # Arrange: un proxy delante de Odoo responde una página HTML con 200.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>mantenimiento</html>")

    # Act / Assert
    with pytest.raises(OdooError, match=r"Respuesta inválida de sale\.order\.search_read"):
        OdooOrders(_client(handler)).place(_request())


def test_un_rechazo_sin_json_conserva_el_codigo() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    # Act / Assert
    with pytest.raises(OdooError, match="Bad Gateway") as raised:
        _client(handler).call("sale.order", "search_read", domain=[], fields=["id"])
    assert raised.value.status == 502


# --- #20: el servicio contra el adaptador real ------------------------------------------


def test_el_recibo_dice_el_correo_del_pedido_que_ya_existia_en_odoo(engine: Any) -> None:
    # Arrange: el folio ya tiene un pedido confirmado con otro correo de cliente.
    fake = FakeOdoo(
        order_state="sale",
        opportunity=301,
        partners=[{"id": 1, "name": "Contacto anterior", "email": "compras@restaurante.co"}],
    )
    service, _ = _service(engine, _orders(fake))
    _until_confirmation(service)

    # Act
    placed = _say(service, "sí")

    # Assert
    assert "**Pedido S00003 confirmado**" in placed
    assert "La confirmación del pedido llega a compras@restaurante.co" in placed
    assert EMAIL not in placed
    assert "sale.order.create" not in fake.methods()
    (lookup, *_) = fake.bodies("sale.order", "search_read")
    (row,) = stored_quotes(engine)
    assert lookup["domain"][0] == ["client_order_ref", "=", row["id"]]


def test_el_pedido_del_servicio_llega_entero_al_adaptador(engine: Any) -> None:
    # Arrange
    fake = FakeOdoo(products=[{"id": 5, "default_code": QUOTED_SKU, "active": True}])
    service, _ = _service(engine, _orders(fake))
    _until_confirmation(service)

    # Act
    placed = _say(service, "sí")

    # Assert
    assert "**Pedido S00007 confirmado**" in placed
    assert f"llega a {EMAIL}." in placed
    (row,) = stored_quotes(engine)
    (order,) = fake.bodies("sale.order", "create")
    (values,) = order["vals_list"]
    assert values["client_order_ref"] == row["id"]
    assert values["origin"] == f"Asistente Carvajal (chat web) · cotización #{row['id']}"
    ((_, _, line),) = values["order_line"]
    assert line["product_id"] == 5
    assert line["product_uom_qty"] == 1.0 and line["price_unit"] == 3751.0
    (partner,) = fake.bodies("res.partner", "create")
    assert partner["vals_list"][0]["name"] == NAME
    assert partner["vals_list"][0]["email"] == EMAIL
    assert partner["vals_list"][0]["city"] == "Bogotá"
    assert fake.bodies("sale.order", "action_confirm")[0]["context"] == {"send_email": True}


# --- #21: reglas generales a mitad del pedido (FR-031) ----------------------------------

GENERAL_RULES = ["Está muy caro, muy ladrones", "retomar"]


def _buyer_for(awaiting: str) -> Buyer | None:
    return Buyer(NAME, EMAIL) if awaiting == "confirmar_pedido" else None


@pytest.mark.parametrize("message", GENERAL_RULES)
@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_el_insulto_y_retomar_los_deciden_las_reglas_generales(awaiting: str, message: str) -> None:
    # Arrange: lo mismo que fuera del pedido (cotización registrada sin paso pendiente).
    outside = _before(_draft(None), message)

    # Act
    decision = _before(_draft(awaiting, _buyer_for(awaiting)), message)

    # Assert
    assert isinstance(decision, flow.Reply) and isinstance(outside, flow.Reply)
    assert decision.order is None
    assert decision.route == outside.route != "pedido"
    assert decision.message == outside.message
    # El borrador del pedido no sobrevive: ni el paso ni los datos de quien compra.
    assert decision.draft.awaiting not in ORDER_AWAITING
    assert decision.draft.buyer is None and decision.draft.folio is None


@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_un_cambio_del_carrito_a_mitad_del_pedido_va_al_agente(awaiting: str) -> None:
    # Act
    decision = _before(_draft(awaiting, _buyer_for(awaiting)), "quiero cambiar la 1 a 800")

    # Assert: no lo lee el paso (no es un nombre) y no crea el pedido.
    assert isinstance(decision, flow.Pass)
    assert Buyer.from_json(decision.draft.buyer) == (_buyer_for(awaiting) or Buyer())


@pytest.mark.parametrize("message", GENERAL_RULES)
def test_tras_las_reglas_generales_un_si_ya_no_crea_el_pedido(engine: Any, message: str) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _until_confirmation(service)
    runner.events = scripted_events(final())  # si llega al agente, responde sin cotizar

    # Act
    _ask(service, message)
    _ask(service, "sí")

    # Assert: el borrador del pedido se fue con la regla general.
    assert orders.requests == []
    assert _stored_draft(engine).order is None


def test_un_cambio_del_carrito_en_la_confirmacion_lo_responde_el_agente_y_recuerda_el_paso(
    engine: Any,
) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _until_confirmation(service)
    runner.events = scripted_events(final())
    calls = len(runner.calls)

    # Act
    answer = _ask(service, "quiero cambiar la 1 a 800")

    # Assert: no crea el pedido; el «sí» que siga contesta el recordatorio, que lo pregunta.
    reminder = render.order_reminder("confirmar_pedido")
    assert reminder is not None
    assert len(runner.calls) == calls + 1
    assert answer.endswith(reminder.text)
    assert orders.requests == []
    assert _stored_draft(engine).awaiting == "confirmar_pedido"


def test_el_insulto_en_la_confirmacion_no_deja_los_datos_en_la_base(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    _until_confirmation(service)

    # Act
    answer = _ask(service, "Está muy caro, muy ladrones")

    # Assert
    assert answer == "Arrancado, vaya cómprele a su madre entonces 😂"
    rows = _all_rows(engine)
    assert NAME not in rows and EMAIL not in rows


# --- #22: botones del pedido (FR-004) y rechazos de FR-003 ------------------------------


def test_los_botones_del_pedido_llegan_en_el_done_y_su_frase_hace_lo_que_dicen(
    engine: Any,
) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    list(service.stream(OWNER, "cotiza 50 vasos en Bogotá"))
    _say(service, "no")

    # Act
    offer = named(list(service.stream(OWNER, "sí")), "done").data["flow"]
    accept, _ = offer["actions"]
    asked = named(list(service.stream(OWNER, accept["say"])), "done").data["flow"]
    confirming = list(service.stream(OWNER, f"{NAME}, {EMAIL}"))
    confirm_flow = named(confirming, "done").data["flow"]
    confirm, _ = confirm_flow["actions"]
    placed = list(service.stream(OWNER, confirm["say"]))

    # Assert
    assert offer["awaiting"] == "pedido"
    assert offer["actions"] == [
        {"label": "Hacer el pedido", "say": "Sí, quiero hacer el pedido"},
        {"label": "Ahora no", "say": "Ahora no"},
    ]
    assert asked["awaiting"] == "datos_pedido"
    assert confirm_flow["awaiting"] == "confirmar_pedido"
    assert confirm_flow["actions"] == [
        {"label": "Confirmar pedido", "say": "Sí, confírmalo"},
        {"label": "Ahora no", "say": "Ahora no"},
    ]
    assert "**Pedido S00001 confirmado**" in _fallback(placed)
    assert len(orders.requests) == 1
    assert named(placed, "done").data["flow"]["actions"] == []


@pytest.mark.parametrize("step", ["pedido", "confirmar_pedido"])
def test_el_boton_ahora_no_cierra_el_pedido_sin_llamar_a_odoo(engine: Any, step: str) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    if step == "pedido":
        events = list(service.stream(OWNER, "cotiza 50 vasos en Bogotá"))
        _say(service, "no")
        events = list(service.stream(OWNER, "sí"))
    else:
        _registered(service)
        _say(service, "sí")
        events = list(service.stream(OWNER, f"{NAME}, {EMAIL}"))
    (decline,) = [
        action
        for action in named(events, "done").data["flow"]["actions"]
        if action["label"] == "Ahora no"
    ]

    # Act
    answer = list(service.stream(OWNER, decline["say"]))

    # Assert
    assert _fallback(answer).startswith("Listo, tu cotización #")
    assert "queda registrada sin pedido" in _fallback(answer)
    assert named(answer, "done").data["flow"]["awaiting"] is None
    assert orders.requests == []
    assert _stored_draft(engine).buyer is None


# Las frases de FR-003 y de US4 (escenario 1) que no estaban en la parametrización.
DECLINES = [
    "Ahora no",
    "cancela",
    "cancela todo",
    "cancela el pedido",
    "olvídalo",
    "cancélalo",
    "no, olvídalo",
    "lo pienso",
    "lo voy a pensar",
    "después",
    "bye",
    "chau",
    "adiós",
    "nos vemos",
]


@pytest.mark.parametrize("message", DECLINES)
@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_cada_rechazo_de_fr003_cierra_el_pedido_en_cualquier_paso(
    awaiting: str, message: str
) -> None:
    # Act
    decision = _before(_draft(awaiting, _buyer_for(awaiting)), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert decision.message == render.order_declined(FOLIO)
    assert decision.draft.awaiting is None and decision.draft.buyer is None
    assert decision.draft.state == "registrada" and decision.draft.folio == FOLIO


@pytest.mark.parametrize("awaiting", ["pedido", "datos_pedido"])
def test_solo_estaba_mirando_cierra_la_oferta_y_los_datos(awaiting: str) -> None:
    # Act
    decision = _before(_draft(awaiting), "solo estaba mirando")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.message == render.order_declined(FOLIO)


@pytest.mark.parametrize(
    "message", ["solo estaba mirando", "lo voy a pensar", "mejor no", "no me interesa"]
)
def test_solo_estaba_mirando_cierra_tambien_la_confirmacion(message: str) -> None:
    # Arrange: un rechazo sin dato ni corrección (US4, escenario 1).
    assert declines_order(message) or is_leaving(message)

    # Act
    decision = _before(_draft("confirmar_pedido", Buyer(NAME, EMAIL)), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert decision.message == render.order_declined(FOLIO)
    assert decision.draft.buyer is None


# --- #23: «olvida todo» con un pedido a medias ------------------------------------------


@pytest.mark.parametrize(
    "steps", [("sí", NAME), ("sí", f"{NAME}, {EMAIL}")], ids=["datos", "confirmar"]
)
def test_olvida_todo_a_mitad_del_pedido_borra_los_datos_del_borrador(
    engine: Any, steps: tuple[str, ...]
) -> None:
    # Arrange: en datos_pedido (solo el nombre) o en confirmar_pedido (nombre y correo).
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _registered(service)
    for message in steps:
        _say(service, message)
    assert _stored_draft(engine).buyer is not None
    runner.events = scripted_events(final())

    # Act
    _ask(service, "olvida todo lo que sabes de mí")

    # Assert
    draft = _stored_draft(engine)
    assert draft.state != "registrada"  # cancelado: el turno del agente sigue desde cero
    assert draft.folio is None and draft.buyer is None
    assert draft.awaiting not in ORDER_AWAITING
    rows = _all_rows(engine)
    assert NAME not in rows and EMAIL not in rows
    assert orders.requests == []


def test_olvida_todo_con_el_pedido_pendiente_cancela_el_borrador() -> None:
    # Act
    decision = _before(
        _draft("confirmar_pedido", Buyer(NAME, EMAIL)), "olvida todo lo que sabes de mí"
    )

    # Assert: lo atiende el grafo (borra la memoria); el borrador se va con los datos.
    assert isinstance(decision, flow.Pass)
    assert decision.draft.state == "cancelada"
    assert decision.draft.buyer is None and decision.draft.folio is None


# --- #25: retomar tras «ahora no» y el pedido ya creado ---------------------------------


def test_tras_ahora_no_hacer_el_pedido_vuelve_a_pedir_los_datos() -> None:
    # Arrange
    declined = _before(_draft("pedido"), "Ahora no")

    # Act
    retaken = _before(declined.draft, "hacer el pedido")

    # Assert
    assert isinstance(retaken, flow.Reply)
    assert retaken.message == render.ask_buyer()
    assert retaken.draft.awaiting == "datos_pedido"
    assert retaken.draft.buyer is None


def test_tras_ahora_no_el_pedido_se_retoma_y_se_crea_una_sola_vez(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _registered(service)
    _say(service, "Ahora no")

    # Act
    asked = _say(service, "hacer el pedido")
    confirming = _say(service, f"{NAME}, {EMAIL}")
    placed = _say(service, "sí")

    # Assert
    assert asked == render.ask_buyer().text
    assert confirming == render.confirm_buyer(NAME, EMAIL).text
    assert "**Pedido S00001 confirmado**" in placed
    (request,) = orders.requests
    assert request.buyer == Buyer(NAME, EMAIL)


@pytest.mark.parametrize("message", ["sí", "hacer el pedido", "Sí, quiero hacer el pedido"])
def test_con_el_pedido_creado_un_si_o_hacer_el_pedido_no_crean_otro(message: str) -> None:
    # Act
    decision = _before(_draft(None, order="S00001"), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert decision.message == render.already_ordered("S00001")
    assert decision.draft.order == "S00001"


@pytest.mark.parametrize("message", ["sí", "hacer el pedido"])
def test_con_el_pedido_creado_el_servicio_no_vuelve_a_llamar_a_odoo(
    engine: Any, message: str
) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _until_confirmation(service)
    _say(service, "sí")

    # Act
    again = _say(service, message)

    # Assert
    assert again == render.already_ordered("S00001").text
    assert len(orders.requests) == 1


# --- #26: guarda de _place_order, CRM que no se construye y borradores viejos -----------


def test_si_el_crm_no_se_construye_el_folio_sigue_sin_oferta(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: ``orders=None`` arma el CRM de forma perezosa desde la configuración.
    def broken(settings: Any) -> Any:
        raise ValueError("ODOO_URL inválida")

    monkeypatch.setattr(service_module, "build_orders", broken)
    service, _ = _service(engine, None)

    # Act
    with capture_logs() as logs:
        registered = _registered(service)
        again = service.pending_order_step(OWNER)

    # Assert
    (row,) = stored_quotes(engine)
    assert registered.startswith(f"✅ **Cotización #{row['id']} registrada**")
    assert render.ORDER_OFFER not in registered
    assert again is None
    failures = [log for log in logs if log["event"] == "pedido.crm_no_construido"]
    assert len(failures) == 1  # se intenta una sola vez por servicio
    assert failures[0]["error_type"] == "ValueError"


@pytest.mark.parametrize(
    ("buyer", "folio"),
    [(Buyer(NAME, None), FOLIO), (Buyer(NAME, EMAIL), None)],
    ids=["sin-correo", "sin-folio"],
)
def test_sin_correo_o_sin_folio_place_order_no_llama_a_odoo(
    engine: Any, buyer: Buyer, folio: str | None
) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    draft = replace(_draft("confirmar_pedido", buyer), folio=folio)

    # Act
    message, after = service._place_order(OWNER, draft, buyer)

    # Assert
    assert message == render.order_failed()
    assert after.awaiting == "confirmar_pedido"
    assert orders.requests == []


def test_sin_crm_place_order_responde_el_fallo_sin_llamar_a_nadie(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)

    # Act
    message, after = service._place_order(
        OWNER, _draft("confirmar_pedido", Buyer(NAME, EMAIL)), Buyer(NAME, EMAIL)
    )

    # Assert: sin CRM no hay reintento que prometer ni datos que guardar.
    assert message == render.already_registered(FOLIO)
    assert after.order is None
    assert after.buyer is None and after.awaiting is None


def test_un_borrador_del_pedido_caducado_no_crea_el_pedido(engine: Any) -> None:
    # Arrange: la confirmación quedó pendiente hace más de DRAFT_DAYS días.
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _until_confirmation(service)
    old = (datetime.now(UTC) - timedelta(days=DRAFT_DAYS + 1)).isoformat()
    _edit_draft(engine, lambda draft: replace(draft, updated_at=old))
    runner.events = scripted_events(final())

    # Act
    step = service.pending_order_step(OWNER)
    _ask(service, "sí")

    # Assert
    assert step is None
    assert orders.requests == []
    draft = _stored_draft(engine)
    assert draft.buyer is None and draft.awaiting not in ORDER_AWAITING
    rows = _all_rows(engine)
    assert NAME not in rows and EMAIL not in rows


def _restarted_without_crm(engine: Any) -> tuple[ChatService, Any]:
    """Confirmación pendiente y, después, un reinicio de la app sin ``ODOO_*``."""

    first, _ = _service(engine, FakeOrders())
    _until_confirmation(first)
    return _service(engine, None)


def test_sin_crm_tras_un_reinicio_el_si_no_promete_un_reintento(engine: Any) -> None:
    # Arrange
    service, runner = _restarted_without_crm(engine)

    # Act
    answer = _say(service, "sí")

    # Assert: no promete el reintento de order_failed ni pasa por el modelo.
    assert answer != render.order_failed().text
    assert "ya quedó registrada" in answer
    assert len(runner.calls) == 0


def test_sin_crm_tras_un_reinicio_el_si_borra_los_datos_del_borrador(engine: Any) -> None:
    # Arrange
    service, _ = _restarted_without_crm(engine)

    # Act
    _say(service, "sí")

    # Assert
    draft = _stored_draft(engine)
    assert draft.buyer is None and draft.awaiting not in ORDER_AWAITING


# --- #27: la voz del pedido --------------------------------------------------------------


def test_la_confirmacion_hablada_lee_el_correo_y_no_se_guarda(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    _registered(service)
    _say(service, "sí")

    # Act
    events = list(service.stream(OWNER, f"{NAME}, {EMAIL}"))

    # Assert: FR-005, la voz lee el correo («lucia punto r arroba…») en el turno…
    spoken = render.confirm_buyer(NAME, EMAIL).spoken
    assert spoken_address(EMAIL) in spoken
    assert named(events, "done").data["flow"]["spoken"] == spoken
    # …pero ni ella ni los datos quedan en los mensajes guardados (el borrador sí los
    # conserva mientras se piden: FR-007).
    stored = repr(stored_messages(engine))
    assert spoken_address(EMAIL) not in stored
    assert NAME not in stored and EMAIL not in stored
    assert "el correo que escribiste" in stored


def test_la_nota_de_voz_del_recibo_por_whatsapp_lleva_el_adjunto(engine: Any) -> None:
    # Arrange
    from tests.test_mcp_pedidos import CHAT, STEPS, NamedOrders, _attached, _say_all, _whatsapp

    from apps.mcp.bridge import ATTACH_PDF_PREFIX
    from carvajal.speech import TEXT_HEADER

    orders = NamedOrders()
    bridge, _, _ = _whatsapp(engine, orders)
    _say_all(bridge, STEPS["confirmar_pedido"])

    # Act: el «sí» final dictado por nota de voz.
    reply = bridge.respond("sí", CHAT, voice=True)
    said = reply.voice_message()

    # Assert: la voz no lee el enlace; el recibo viaja por escrito tras TEXTO.
    spoken, header, written = said.partition(TEXT_HEADER)
    assert header == TEXT_HEADER
    assert ATTACH_PDF_PREFIX not in spoken
    (url,) = _attached(written)
    assert "/pedidos/" in url
    assert "*Pedido S00001 confirmado*" in written
    assert len(orders.requests) == 1


# --- #29: palabras sueltas que no son nombres -------------------------------------------

NOT_NAMES = ["Bogotá", "Cali", "PDF", "SAS", "Santa Marta", "NIT"]


@pytest.mark.parametrize("word", NOT_NAMES)
def test_una_ciudad_o_una_sigla_no_pisa_el_nombre_ya_dado_en_los_datos(word: str) -> None:
    # Act
    decision = _before(_draft("datos_pedido", Buyer(NAME)), word)

    # Assert
    assert Buyer.from_json(decision.draft.buyer) == Buyer(NAME)


@pytest.mark.parametrize("word", NOT_NAMES)
def test_en_la_oferta_una_ciudad_o_una_sigla_no_es_un_nombre(word: str) -> None:
    # Act
    decision = _before(_draft("pedido"), word)

    # Assert
    assert Buyer.from_json(decision.draft.buyer).name is None


@pytest.mark.parametrize("word", NOT_NAMES)
def test_en_la_confirmacion_una_ciudad_o_una_sigla_no_cambia_el_nombre(word: str) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", Buyer(NAME, EMAIL)), word)

    # Assert
    assert Buyer.from_json(decision.draft.buyer) == Buyer(NAME, EMAIL)


# --- #30: variantes del total del recibo (FR-026) y send_email (FR-015) -----------------


def _unpriced(name: str) -> dict[str, Any]:
    line = _line(name)
    presentation = dict(line["primary"]["presentation"])
    presentation.pop("subtotal")
    presentation.pop("price_per_presentation")
    return {**line, "primary": {**line["primary"], "name": name, "presentation": presentation}}


def test_el_recibo_sin_ningun_precio_deja_el_total_por_confirmar() -> None:
    # Arrange
    draft = QuoteDraft(lines=(_unpriced("Tapa 12 oz"),), state="registrada", folio=FOLIO)

    # Act
    receipt = render.order_placed("S00001", EMAIL, FOLIO, draft).text

    # Assert
    assert "Total por confirmar con el vendedor" in receipt
    assert "Total **" not in receipt


def test_el_recibo_con_una_linea_sin_precio_la_nombra_en_el_total() -> None:
    # Arrange
    draft = QuoteDraft(lines=(_line(), _unpriced("Tapa 12 oz")), state="registrada", folio=FOLIO)

    # Act
    receipt = render.order_placed("S00001", EMAIL, FOLIO, draft).text

    # Assert
    (total,) = [line for line in receipt.splitlines() if line.startswith("Total")]
    assert total.startswith("Total **$50.000**")
    assert " sin Tapa 12 oz (precio por confirmar)" in total
    assert "Total por confirmar" not in receipt


def test_el_pedido_se_confirma_pidiendo_a_odoo_el_correo_de_ventas() -> None:
    # Arrange
    fake = FakeOdoo()

    # Act
    _orders(fake).place(_request())

    # Assert: el literal, no la constante: quitar ``send_email`` rompe esta prueba.
    (confirm,) = fake.bodies("sale.order", "action_confirm")
    assert confirm == {"ids": [100], "context": {"send_email": True}}


# --- #31: privacidad fuera del camino feliz (FR-020) ------------------------------------

OTHER_EMAIL = "lucia.restrepo@otra.co"
PRIVACY_SEQUENCES: dict[str, tuple[str, ...]] = {
    "corrige-el-correo": ("sí", f"{NAME}, {EMAIL}", f"mejor a {OTHER_EMAIL}", "sí"),
    "que-dato-corrijo": ("sí", f"{NAME}, {EMAIL}", "el correo está mal", "sí"),
    "rechazo-en-la-confirmacion": ("sí", f"{NAME}, {EMAIL}", "no, olvídalo"),
    "rechazo-con-el-nombre-dado": ("sí", NAME, "Ahora no"),
    "oferta-con-los-datos": (f"{NAME}, {EMAIL}", "sí"),
    "falla-odoo-y-reintenta": ("sí", f"{NAME}, {EMAIL}", "sí", "sí"),
}


class FlakyOrders(FakeOrders):
    """Falla el primer intento y crea el pedido en el siguiente."""

    def place(self, request: OrderRequest) -> OrderResult:
        self.fail = not self.requests
        return super().place(request)


@pytest.mark.parametrize("sequence", list(PRIVACY_SEQUENCES))
def test_ningun_camino_del_pedido_guarda_monitorea_ni_devuelve_los_datos(
    engine: Any, sequence: str
) -> None:
    # Arrange
    tracer = FakeTracer()
    orders = FlakyOrders() if sequence == "falla-odoo-y-reintenta" else FakeOrders()
    service, runner = _service(engine, orders, tracer=tracer)
    _registered(service)

    # Act
    with capture_logs() as logs:
        shown = [_say(service, message) for message in PRIVACY_SEQUENCES[sequence]]
    runner.events = scripted_events(final())
    _ask(service, "¿Qué otros productos tienen?")

    # Assert: la persona vio sus datos en algún turno…
    assert any(NAME in answer for answer in shown)
    # …pero no quedan en la base, ni en el monitoreo, ni en la semilla del modelo, ni en logs.
    secrets = (NAME, EMAIL, OTHER_EMAIL, spoken_address(EMAIL))
    stored = _all_rows(engine)
    traced = repr(tracer.calls) + repr([(t.metadata, t.outputs) for t in tracer.turns])
    seed = repr(runner.calls[-1]["seed_turns"])
    for secret in secrets:
        assert secret not in stored, secret
        assert secret not in traced, secret
        assert secret not in seed, secret
        assert secret not in repr(logs), secret
    if sequence == "falla-odoo-y-reintenta":
        first, second = orders.requests
        assert first.folio == second.folio
        assert "**Pedido S00001 confirmado**" in shown[-1]
    if sequence.startswith("corrige"):
        (request,) = orders.requests
        assert request.buyer == Buyer(NAME, OTHER_EMAIL)
        assert "l…@otra.co" in stored


def test_el_correo_enmascarado_es_el_mismo_en_la_base_y_en_la_semilla(engine: Any) -> None:
    # Arrange
    service, runner = _service(engine, FakeOrders())
    _registered(service)
    _say(service, f"{NAME}, {EMAIL}")
    runner.events = scripted_events(final())

    # Act
    _ask(service, "¿Cuánto tarda la entrega?")

    # Assert
    assert MASKED in _all_rows(engine)
    assert MASKED in repr(runner.calls[-1]["seed_turns"])


# --- #24: las variables ODOO_* del entorno no encienden el CRM en las pruebas -------------


def test_un_env_local_con_odoo_no_enciende_el_crm_en_las_pruebas(tmp_path: Any) -> None:
    # Arrange: un ``.env`` como el del servidor, con las tres variables del CRM.
    from carvajal.infrastructure.settings import Settings

    env = tmp_path / ".env"
    env.write_text("ODOO_URL=http://odoo:8069\nODOO_DB=carvajal\nODOO_API_KEY=clave\n")

    # Act
    settings = Settings(_env_file=env)  # type: ignore[call-arg]

    # Assert: el conftest las deja vacías en el entorno, que gana al ``.env``.
    assert not settings.odoo_configured
    assert settings.odoo_sales_login == Settings.model_fields["odoo_sales_login"].default


def test_el_servicio_sin_orders_no_arma_el_crm_en_las_pruebas(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)

    # Act
    orders = service._orders()

    # Assert
    assert orders is None


def test_una_conexion_real_al_odoo_de_produccion_se_bloquea() -> None:
    # Act / Assert: ``JevNetworkBlocked`` es un ``AssertionError`` (el conftest se importa
    # con otro nombre de módulo, así que se compara por la clase base y el mensaje).
    blocked = "Conexión a odoo bloqueada"
    with httpx.Client() as client, pytest.raises(AssertionError, match=blocked):
        client.post("http://odoo:8069/json/2/sale.order/search_read", json={})
