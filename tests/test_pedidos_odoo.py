"""Pedido en Odoo a partir de una cotización registrada (spec 056).

Cubre lo puro (datos de quien compra, intención de compra y líneas del pedido), el adaptador
JSON-2 contra un transporte simulado y el flujo completo en el servicio del chat: folio →
«¿Hacemos el pedido?» → nombre y correo → «¿Lo confirmo?» → pedido confirmado, sin pasar por
el modelo. Las regresiones del flujo (muletillas, rechazos, preguntas a mitad del pedido,
Jev y privacidad) viven en ``tests/test_pedidos_odoo_flujo.py``.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs
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
from carvajal.adapters.crm.odoo import (
    CONFIRM_CONTEXT,
    PARTNER_REF,
    OdooClient,
    OdooOrders,
    OrderResult,
)
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.quotation.flow import ORDER_AWAITING
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
from carvajal.quotation.render import ORDER_OFFER, confirm_buyer, order_failed

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

# IDs que asigna el Odoo simulado al crear, por modelo: así se distingue qué id va a dónde.
_FIRST_ID = {"res.partner": 10, "sale.order": 100, "crm.lead": 300, "product.product": 500}
ODOO_SECRET = "detalle interno de Odoo"


class FakeOdoo:
    """Odoo en memoria: responde lo que el adaptador pide y anota cada llamada.

    ``order_state`` simula un pedido previo del mismo folio (``"sale"`` o ``"draft"``);
    ``partners`` son los contactos que devuelve la búsqueda por correo; ``products``, lo que
    devuelve la búsqueda de ``product.product`` (con ``active``); ``fail`` responde con ese
    código HTTP a ``modelo.método``.
    """

    def __init__(
        self,
        *,
        order_state: str | None = None,
        opportunity: int | None = None,
        partners: list[dict[str, Any]] | None = None,
        products: list[dict[str, Any]] | None = None,
        fail: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self.order_state = order_state
        self.opportunity = opportunity
        self.partners = partners or []
        self.products = (
            products
            if products is not None
            else [{"id": 5, "default_code": "V-12", "active": True}]
        )
        self.fail = fail or {}
        self._next = dict(_FIRST_ID)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        _, _, model, method = request.url.path.strip("/").split("/")
        body = json.loads(request.content)
        self.calls.append((model, method, body))
        self.headers.append(request.headers)
        status = self.fail.get(f"{model}.{method}")
        if status is not None:
            return httpx.Response(
                status, json={"name": "odoo.exceptions.AccessError", "message": ODOO_SECRET}
            )
        return httpx.Response(200, json=self._answer(model, method, body))

    def _answer(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if method == "create":
            first = self._next.get(model, 900)
            self._next[model] = first + len(body["vals_list"])
            return [first + index for index, _ in enumerate(body["vals_list"])]
        if method != "search_read":
            return True
        if model == "sale.order":
            return self._orders(body["domain"])
        if model == "res.partner":
            return self.partners
        if model == "product.product":
            return self.products
        if model == "product.category":
            return [{"id": 1}]
        return []

    def _orders(self, domain: list[Any]) -> list[dict[str, Any]]:
        if domain and domain[0][0] == "client_order_ref":
            if self.order_state is None:
                return []
            return [
                {
                    "id": 90,
                    "name": "S00003",
                    "state": self.order_state,
                    "partner_id": [1, "Contacto anterior"],
                    "opportunity_id": [self.opportunity, "Pedido"] if self.opportunity else False,
                }
            ]
        ((_, _, order_id),) = domain
        return [{"id": order_id, "name": "S00003" if order_id == 90 else "S00007"}]

    def methods(self) -> list[str]:
        return [f"{model}.{method}" for model, method, _ in self.calls]

    def bodies(self, model: str, method: str) -> list[dict[str, Any]]:
        return [body for m, name, body in self.calls if (m, name) == (model, method)]


def _orders(fake: FakeOdoo) -> OdooOrders:
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )
    return OdooOrders(client)


def _request(name: str = "Ana Pérez", email: str = "ana@correo.com") -> OrderRequest:
    line = OrderLine("V-12", "Vaso 12 oz", "[V-12] Vaso 12 oz", Decimal(2), Decimal(25000))
    return OrderRequest(
        folio="COT-1",
        buyer=Buyer(name, email),
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
    assert result == OrderResult(name="S00007", order_id=100, lead_id=300, email="ana@correo.com")
    assert fake.methods() == [
        "sale.order.search_read",
        "res.partner.search_read",
        "res.partner.create",
        "product.product.search_read",
        "sale.order.create",
        "sale.order.action_confirm",
        "sale.order.message_post",
        "crm.lead.create",
        "sale.order.write",
        "crm.lead.action_set_won",
        "sale.order.search_read",
    ]
    (lookup, _) = fake.bodies("sale.order", "search_read")
    assert lookup["domain"] == [["client_order_ref", "=", "COT-1"], ["state", "!=", "cancel"]]
    assert "state" in lookup["fields"]
    (order,) = fake.bodies("sale.order", "create")
    (values,) = order["vals_list"]
    assert values["client_order_ref"] == "COT-1"
    assert values["partner_id"] == 10
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
    (confirm,) = fake.bodies("sale.order", "action_confirm")
    assert confirm == {"ids": [100], "context": CONFIRM_CONTEXT}
    (link,) = fake.bodies("sale.order", "write")
    assert link == {"ids": [100], "vals": {"opportunity_id": 300}}
    assert fake.bodies("crm.lead", "action_set_won") == [{"ids": [300]}]
    assert fake.headers[0]["authorization"] == "bearer clave"
    assert fake.headers[0]["x-odoo-database"] == "carvajal"


def test_el_mismo_folio_no_crea_otro_pedido() -> None:
    # Arrange
    fake = FakeOdoo(order_state="sale", opportunity=301)

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert (result.name, result.order_id, result.lead_id, result.existing) == (
        "S00003",
        90,
        301,
        True,
    )
    # Solo lee: el pedido y el correo de su cliente (el del recibo); no crea ni confirma.
    assert fake.methods() == ["sale.order.search_read", "res.partner.search_read"]


def test_un_pedido_en_borrador_se_retoma_sin_crear_otro() -> None:
    # Arrange: un intento anterior creó el pedido y falló antes de confirmarlo.
    fake = FakeOdoo(order_state="draft")

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result == OrderResult(name="S00003", order_id=90, lead_id=300, email="ana@correo.com")
    assert not result.existing
    assert "sale.order.create" not in fake.methods()
    retake, link = fake.bodies("sale.order", "write")
    assert retake == {"ids": [90], "vals": {"partner_id": 10}}
    assert link == {"ids": [90], "vals": {"opportunity_id": 300}}
    assert fake.bodies("sale.order", "action_confirm") == [
        {"ids": [90], "context": CONFIRM_CONTEXT}
    ]
    methods = fake.methods()
    assert methods.index("sale.order.write") < methods.index("sale.order.action_confirm")
    assert fake.bodies("sale.order", "message_post")


def test_un_borrador_con_oportunidad_no_crea_otra() -> None:
    # Arrange
    fake = FakeOdoo(order_state="draft", opportunity=301)

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.lead_id == 301
    assert "crm.lead.create" not in fake.methods()
    assert "crm.lead.action_set_won" not in fake.methods()


def test_el_correo_se_busca_sin_comodines() -> None:
    # Arrange
    fake = FakeOdoo()

    # Act
    _orders(fake).place(_request(email="ana_p%z@correo.com"))

    # Assert
    (search,) = fake.bodies("res.partner", "search_read")
    assert search["domain"] == [
        ["email", "=ilike", "ana\\_p\\%z@correo.com"],
        ["ref", "=", PARTNER_REF],
    ]
    (partner,) = fake.bodies("res.partner", "create")
    assert partner["vals_list"][0]["email"] == "ana_p%z@correo.com"  # el correo real, sin escapar


def test_mismo_correo_con_otro_nombre_crea_un_contacto_nuevo() -> None:
    # Arrange: el correo ya es de otra persona en Odoo.
    fake = FakeOdoo(partners=[{"id": 7, "name": "Carlos Ruiz"}])

    # Act
    _orders(fake).place(_request())

    # Assert
    (partner,) = fake.bodies("res.partner", "create")
    assert partner["vals_list"][0]["name"] == "Ana Pérez"
    (order,) = fake.bodies("sale.order", "create")
    assert order["vals_list"][0]["partner_id"] == 10
    (lead,) = fake.bodies("crm.lead", "create")
    assert lead["vals_list"][0]["partner_id"] == 10


def test_mismo_correo_y_mismo_nombre_reutiliza_el_contacto() -> None:
    # Arrange: el mismo nombre con otra capitalización y otros espacios.
    fake = FakeOdoo(partners=[{"id": 8, "name": "Otra Persona"}, {"id": 7, "name": " ANA  pérez "}])

    # Act
    _orders(fake).place(_request())

    # Assert
    assert "res.partner.create" not in fake.methods()
    (order,) = fake.bodies("sale.order", "create")
    assert order["vals_list"][0]["partner_id"] == 7


def test_la_oportunidad_se_crea_tras_confirmar_y_se_liga_al_pedido() -> None:
    # Arrange
    fake = FakeOdoo()

    # Act
    _orders(fake).place(_request())

    # Assert
    methods = fake.methods()
    confirmed = methods.index("sale.order.action_confirm")
    created = methods.index("crm.lead.create")
    assert confirmed < created < methods.index("sale.order.write")
    (lead,) = fake.bodies("crm.lead", "create")
    assert lead["vals_list"][0]["name"] == "Pedido de la cotización #COT-1"
    assert "opportunity_id" not in fake.bodies("sale.order", "create")[0]["vals_list"][0]


def test_si_el_crm_falla_el_pedido_sigue_y_el_log_no_lleva_el_mensaje_de_odoo() -> None:
    # Arrange
    fake = FakeOdoo(fail={"crm.lead.create": 403})

    # Act
    with capture_logs() as logs:
        result = _orders(fake).place(_request())

    # Assert
    assert result == OrderResult(name="S00007", order_id=100, lead_id=None, email="ana@correo.com")
    assert "crm.lead.action_set_won" not in fake.methods()
    assert "sale.order.write" not in fake.methods()  # sin oportunidad que ligar
    (failed,) = [log for log in logs if log["event"] == "pedido.oportunidad_fallida"]
    assert failed["status"] == 403
    assert failed["error_type"] == "OdooError"
    for log in logs:
        assert ODOO_SECRET not in str(log)
        assert "ana@correo.com" not in str(log)
        assert "Ana Pérez" not in str(log)


def test_la_nota_del_pedido_se_publica_como_html() -> None:
    # Arrange
    fake = FakeOdoo()

    # Act
    _orders(fake).place(_request())

    # Assert
    (note,) = fake.bodies("sale.order", "message_post")
    assert note["ids"] == [100]
    assert note["body_is_html"] is True
    assert note["body"].startswith("<p>Pedido confirmado por el cliente en chat web")
    assert "<p>Impuestos: IVA incl.</p>" in note["body"]


def test_un_producto_archivado_se_reactiva_sin_duplicarse() -> None:
    # Arrange
    fake = FakeOdoo(products=[{"id": 5, "default_code": "V-12", "active": False}])

    # Act
    _orders(fake).place(_request())

    # Assert
    (search,) = fake.bodies("product.product", "search_read")
    assert ["active", "in", [True, False]] in search["domain"]
    assert fake.bodies("product.product", "write") == [{"ids": [5], "vals": {"active": True}}]
    assert "product.product.create" not in fake.methods()
    (order,) = fake.bodies("sale.order", "create")
    assert order["vals_list"][0]["order_line"][0][2]["product_id"] == 5


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


def _service(
    engine: Any, orders: Any, judge: Any = None, tracer: FakeTracer | None = None
) -> tuple[ChatService, FakeToolRunner]:
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        tool_agent=runner,
        tracer=tracer or FakeTracer(),
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
    confirming = _say(service, "ana@correo.com")
    pending = list(orders.requests)
    placed = _say(service, "Sí, confírmalo")
    again = _say(service, "quiero comprarla")

    # Assert
    assert ORDER_OFFER in registered
    assert "**nombre**" in accepted and "**correo**" in accepted
    assert "¿A qué **correo**" in only_name
    assert confirming == confirm_buyer("Ana Pérez", "ana@correo.com").text
    assert pending == []  # sin el «sí» de la confirmación no hay pedido
    assert "**Pedido S00001 confirmado**" in placed
    assert "La confirmación del pedido llega a **ana@correo.com**" in placed
    assert "Te enviamos" not in placed
    assert "S00001" in again and "ya quedó creado" in again
    (request,) = orders.requests
    (row,) = stored_quotes(engine)
    assert request.folio == row["id"]
    assert request.buyer == Buyer("Ana Pérez", "ana@correo.com")
    assert request.channel == "chat web"
    assert [line.code for line in request.lines]
    assert len(runner.calls) == 1  # solo la cotización pasó por el modelo
    assert all(awaiting not in ORDER_AWAITING for awaiting, _ in judge.calls)


def test_whatsapp_marca_el_canal_del_pedido(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _registered(service, "wa_abc")

    # Act
    _say(service, "hagamos el pedido", "wa_abc")
    _say(service, "Ana Pérez, ana@correo.com", "wa_abc")
    _say(service, "sí", "wa_abc")

    # Assert
    (request,) = orders.requests
    assert request.channel == "WhatsApp"


def test_si_odoo_falla_se_puede_reintentar(engine: Any) -> None:
    # Arrange
    orders = FakeOrders(fail=True)
    service, _ = _service(engine, orders)
    _registered(service)
    _say(service, "sí")
    _say(service, "Ana Pérez, ana@correo.com")

    # Act
    failed = _say(service, "sí")
    orders.fail = False
    placed = _say(service, "sí")

    # Assert
    assert failed == order_failed().text
    assert "**Pedido S00001 confirmado**" in placed
    first, second = orders.requests
    assert first.folio == second.folio
    assert first.buyer == second.buyer == Buyer("Ana Pérez", "ana@correo.com")


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
