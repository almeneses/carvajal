"""Pedido en Odoo a partir de una cotización registrada (spec 055).

Cubre lo puro (datos de quien compra, intención de compra y líneas del pedido), el adaptador
JSON-2 contra un transporte simulado y el flujo completo en el servicio del chat: folio →
«¿Hacemos el pedido?» → nombre y correo → pedido confirmado, sin pasar por el modelo.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import (
    FakeEmbedder,
    FakeLLM,
    FakeToolRunner,
    FakeTracer,
    commercial_final,
    named,
    scripted_events,
    stored_quotes,
    tools_settings,
)

from apps.api.chat.service import ChatService
from carvajal.adapters.crm.odoo import CONFIRM_CONTEXT, OdooClient, OdooOrders, OrderResult
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.quotation.order import (
    SHIPPING_CODE,
    Buyer,
    OrderLine,
    OrderRequest,
    declines_order,
    merge_buyer,
    order_lines,
    wants_purchase,
)
from carvajal.quotation.render import ORDER_OFFER

OWNER = "dueno-pedidos"

# --- puro ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "name", "email"),
    [
        ("Ana Pérez, ana@correo.com", "Ana Pérez", "ana@correo.com"),
        (
            "mi nombre es juan gómez y mi correo es JUAN.G@Empresa.co",
            "Juan Gómez",
            "juan.g@empresa.co",
        ),
        ("soy Marta", "Marta", None),
        ("ventas@restaurante.com.co", None, "ventas@restaurante.com.co"),
        ("12345", None, None),
    ],
)
def test_los_datos_de_quien_compra_salen_del_texto(
    message: str, name: str | None, email: str | None
) -> None:
    # Act
    buyer = merge_buyer(Buyer(), message)

    # Assert
    assert (buyer.name, buyer.email) == (name, email)


def test_los_datos_se_completan_en_dos_mensajes() -> None:
    # Arrange
    first = merge_buyer(Buyer(), "Ana Pérez")

    # Act
    second = merge_buyer(first, "ana@correo.com")

    # Assert
    assert second == Buyer("Ana Pérez", "ana@correo.com")
    assert second.complete


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("quiero comprarla", True),
        ("hagamos el pedido", True),
        ("Sí, quiero hacer el pedido", True),
        ("quiero comprar 500 vasos", False),  # otra cotización
        ("¿puedo comprarla con tarjeta?", False),  # una pregunta va al agente
        ("gracias", False),
    ],
)
def test_la_intencion_de_compra(message: str, expected: bool) -> None:
    assert wants_purchase(message) is expected


def test_ahora_no_rechaza_el_pedido() -> None:
    assert declines_order("Ahora no")
    assert not declines_order("Ana Pérez, ana@correo.com")


def _line(sku: str = "V-12", *, shipping: dict[str, Any] | None = None) -> dict[str, Any]:
    commercial: dict[str, Any] = {"currency": "COP"}
    if shipping is not None:
        commercial["shipping"] = shipping
    return {
        "kind": "cotizacion",
        "city": "Bogotá",
        "primary": {
            "sku": sku,
            "name": "Vaso 12 oz",
            "seller": "Distribuidora Uno",
            "currency": "COP",
            "provider": {"id": "uno"},
            "tax": {"status": "incluido", "rate": "0.19"},
            "presentation": {
                "basis": "caja",
                "units": 50,
                "units_requested": 100,
                "count": 2,
                "units_bought": 100,
                "price_per_presentation": "25000",
                "subtotal": "50000",
            },
        },
        "commercial": commercial,
        "delivery": {"requested": shipping is not None},
    }


def test_las_lineas_del_pedido_usan_los_importes_del_motor() -> None:
    # Arrange
    shipping = {"requested": True, "status": "confirmed", "cost": "8000"}

    # Act
    lines, notes = order_lines([_line(shipping=shipping)])

    # Assert
    product, envio = lines
    assert (product.code, product.quantity, product.price_unit) == (
        "V-12",
        Decimal(2),
        Decimal(25000),
    )
    assert "2 cajas de 50 u. (100 unidades)" in product.description
    assert (envio.code, envio.price_unit, envio.service) == (SHIPPING_CODE, Decimal(8000), True)
    assert any("IVA" in note for note in notes)


def test_un_envio_sin_costo_queda_como_nota() -> None:
    # Act
    lines, notes = order_lines([_line(shipping={"requested": True, "status": "pending"})])

    # Assert
    assert [line.code for line in lines] == ["V-12"]
    assert "Envío por confirmar con el vendedor." in notes


# --- adaptador JSON-2 -----------------------------------------------------------------------


class FakeOdoo:
    """Odoo en memoria: responde lo justo y anota cada llamada."""

    def __init__(self, *, existing: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.existing = existing
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        _, _, model, method = request.url.path.strip("/").split("/")
        body = json.loads(request.content)
        self.calls.append((model, method, body))
        self.headers.append(request.headers)
        return httpx.Response(200, json=self._answer(model, method, body))

    def _answer(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if method == "create":
            return [100 + index for index, _ in enumerate(body["vals_list"])]
        if method == "search_read":
            if model == "sale.order":
                if self.existing or body["domain"] == [["id", "=", 100]]:
                    return [{"id": 100, "name": "S00007", "opportunity_id": [100, "Pedido"]}]
                return []
            if model == "product.product":
                return [{"id": 5, "default_code": "V-12"}]
            return []
        return True

    def methods(self) -> list[str]:
        return [f"{model}.{method}" for model, method, _ in self.calls]


def _orders(fake: FakeOdoo) -> OdooOrders:
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )
    return OdooOrders(client)


def _request() -> OrderRequest:
    line = OrderLine("V-12", "Vaso 12 oz", "[V-12] Vaso 12 oz", Decimal(2), Decimal(25000))
    return OrderRequest(
        folio="COT-1",
        buyer=Buyer("Ana Pérez", "ana@correo.com"),
        channel="chat web",
        city="Bogotá",
        lines=(line,),
        total=Decimal(50000),
        notes=("Impuestos: IVA incl.",),
    )


def test_el_pedido_se_crea_confirma_y_deja_traza() -> None:
    # Arrange
    fake = FakeOdoo()

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result == OrderResult(name="S00007", order_id=100, lead_id=100)
    assert fake.methods() == [
        "sale.order.search_read",
        "res.partner.search_read",
        "res.partner.create",
        "product.product.search_read",
        "crm.lead.create",
        "sale.order.create",
        "sale.order.action_confirm",
        "sale.order.message_post",
        "crm.lead.action_set_won",
        "sale.order.search_read",
    ]
    order = next(
        body for model, method, body in fake.calls if method == "create" and model == "sale.order"
    )
    (values,) = order["vals_list"]
    assert values["client_order_ref"] == "COT-1"
    assert values["order_line"] == [
        [
            0,
            0,
            {
                "product_id": 5,
                "name": "[V-12] Vaso 12 oz",
                "product_uom_qty": 2.0,
                "price_unit": 25000.0,
            },
        ]
    ]
    confirm = next(body for _, method, body in fake.calls if method == "action_confirm")
    assert confirm == {"ids": [100], "context": CONFIRM_CONTEXT}
    assert fake.headers[0]["authorization"] == "bearer clave"
    assert fake.headers[0]["x-odoo-database"] == "carvajal"


def test_el_mismo_folio_no_crea_otro_pedido() -> None:
    # Arrange
    fake = FakeOdoo(existing=True)

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.existing and result.name == "S00007"
    assert fake.methods() == ["sale.order.search_read"]


# --- servicio del chat ------------------------------------------------------------------


class FakeOrders:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[OrderRequest] = []
        self.fail = fail

    def place(self, request: OrderRequest) -> OrderResult:
        from carvajal.adapters.crm import OdooError

        self.requests.append(request)
        if self.fail:
            raise OdooError("Odoo no responde (ConnectError).")
        return OrderResult(name="S00001", order_id=1, lead_id=2)


class FakeJudge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]:
        self.calls.append((awaiting, reply))
        return "otro", 0.2


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


def _service(engine: Any, orders: Any, judge: Any = None) -> tuple[ChatService, FakeToolRunner]:
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        tool_agent=runner,
        tracer=FakeTracer(),
        settings=tools_settings(),
        orders=orders,
        reply_judge=judge,
    )
    return service, runner


def _say(service: ChatService, message: str, owner: str = OWNER) -> str:
    return named(list(service.stream(owner, message)), "fallback").data["text"]


def _registered(service: ChatService, owner: str = OWNER) -> str:
    list(service.stream(owner, "cotiza 50 vasos en Bogotá"))
    _say(service, "no", owner)
    return _say(service, "sí", owner)


def test_el_folio_ofrece_el_pedido_y_lo_crea_con_nombre_y_correo(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    judge = FakeJudge()
    service, runner = _service(engine, orders, judge)

    # Act
    registered = _registered(service)
    accepted = _say(service, "Sí, quiero hacer el pedido")
    only_name = _say(service, "Ana Pérez")
    placed = _say(service, "ana@correo.com")
    again = _say(service, "quiero comprarla")

    # Assert
    assert ORDER_OFFER in registered
    assert "**nombre**" in accepted and "**correo**" in accepted
    assert "¿A qué **correo**" in only_name
    assert "**Pedido S00001 confirmado**" in placed
    assert "ana@correo.com" in placed
    assert "S00001" in again and "ya quedó creado" in again
    (request,) = orders.requests
    (row,) = stored_quotes(engine)
    assert request.folio == row["id"]
    assert request.buyer == Buyer("Ana Pérez", "ana@correo.com")
    assert request.channel == "chat web"
    assert [line.code for line in request.lines]
    assert len(runner.calls) == 1  # solo la cotización pasó por el modelo
    assert all(awaiting != "datos_pedido" for awaiting, _ in judge.calls)


def test_whatsapp_marca_el_canal_del_pedido(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _registered(service, "wa_abc")

    # Act
    _say(service, "hagamos el pedido", "wa_abc")
    _say(service, "Ana Pérez, ana@correo.com", "wa_abc")

    # Assert
    (request,) = orders.requests
    assert request.channel == "WhatsApp"


def test_si_odoo_falla_se_puede_reintentar(engine: Any) -> None:
    # Arrange
    orders = FakeOrders(fail=True)
    service, _ = _service(engine, orders)
    _registered(service)
    _say(service, "sí")

    # Act
    failed = _say(service, "Ana Pérez, ana@correo.com")
    orders.fail = False
    _say(service, "hacer el pedido")
    placed = _say(service, "Ana Pérez, ana@correo.com")

    # Assert
    assert failed.startswith("No pude crear el pedido")
    assert "**Pedido S00001 confirmado**" in placed
    assert len(orders.requests) == 2


def test_ahora_no_deja_la_cotizacion_sin_pedido(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _registered(service)

    # Act
    declined = _say(service, "Ahora no")

    # Assert
    assert "queda registrada" in declined
    assert orders.requests == []


def test_sin_crm_el_folio_no_ofrece_el_pedido(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)

    # Act
    registered = _registered(service)

    # Assert
    assert ORDER_OFFER not in registered


def test_las_fotos_solo_se_suben_a_los_productos_sin_foto() -> None:
    # Arrange: dos SKU con foto en el catálogo; en Odoo solo el segundo no tiene.
    writes: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/write"):
            writes.append(body)
            return httpx.Response(200, json=True)
        domain = body["domain"]
        if ["image_1920", "=", False] in domain:
            return httpx.Response(200, json=[{"id": 2, "default_code": "B"}])
        return httpx.Response(
            200, json=[{"id": 1, "default_code": "A"}, {"id": 2, "default_code": "B"}]
        )

    client = OdooClient("http://odoo", "carvajal", "k", transport=httpx.MockTransport(handler))

    # Act
    report = OdooOrders(client).sync_images({"A": b"foto-a", "B": b"foto-b"})

    # Assert
    assert report == {"fotos_subidas": 1, "fotos_existentes": 1}
    assert writes == [{"ids": [2], "vals": {"image_1920": "Zm90by1i"}}]
