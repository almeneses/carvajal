"""Aviso al equipo de ventas por cada pedido nuevo en Odoo (spec 056, hueco 41).

El recibo promete «Un asesor te contacta para coordinar el pago y la entrega». Al confirmar
el pedido, el adaptador programa en el ``sale.order`` una actividad «Por hacer»
(``mail.mail_activity_data_todo``) para el usuario de ventas de ``ODOO_SALES_LOGIN``. Si la
actividad o el usuario fallan, el pedido sigue; si el login no existe, no hay actividad
(nunca en el usuario técnico). Odoo simulado con ``httpx.MockTransport``.
"""

from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs
from tests.test_pedidos_odoo import ODOO_SECRET, FakeOdoo, _request

from carvajal.adapters.crm.odoo import (
    ACTIVITY_SUMMARY,
    SALES_USER_RETRY_S,
    OdooClient,
    OdooOrders,
    OrderResult,
    build_orders,
)
from carvajal.infrastructure.settings import Settings

SALES_USER = 7


class SalesOdoo(FakeOdoo):
    """:class:`FakeOdoo` con usuarios: ``users`` es lo que devuelve la búsqueda por login."""

    def __init__(self, *, users: list[dict[str, Any]] | None = None, **values: Any) -> None:
        super().__init__(**values)
        self.users = [{"id": SALES_USER}] if users is None else users

    def _answer(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if (model, method) == ("res.users", "search_read"):
            return self.users
        return super()._answer(model, method, body)


def _orders(fake: FakeOdoo, login: str = "vendedora") -> OdooOrders:
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )
    return OdooOrders(client, sales_login=login)


def test_el_pedido_nuevo_programa_la_actividad_para_el_vendedor() -> None:
    # Arrange
    fake = SalesOdoo()

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result == OrderResult(name="S00007", order_id=100, lead_id=300, email="ana@correo.com")
    [users] = fake.bodies("res.users", "search_read")
    assert users["domain"] == [["login", "=", "vendedora"]]
    [activity] = fake.bodies("sale.order", "activity_schedule")
    assert activity["ids"] == [100]
    assert activity["act_type_xmlid"] == "mail.mail_activity_data_todo"
    assert activity["summary"] == ACTIVITY_SUMMARY == "Coordinar pago y entrega del pedido"
    assert activity["user_id"] == SALES_USER
    assert "S00007" in activity["note"]
    assert "#COT-1" in activity["note"] and "chat web" in activity["note"]
    # La nota es para el equipo: ni el nombre ni el correo de quien compra (están en el pedido).
    assert "Ana" not in activity["note"] and "correo.com" not in activity["note"]


def test_la_actividad_va_despues_de_confirmar() -> None:
    # Arrange
    fake = SalesOdoo()

    # Act
    _orders(fake).place(_request())

    # Assert
    methods = fake.methods()
    assert methods.index("sale.order.action_confirm") < methods.index(
        "sale.order.activity_schedule"
    )


def test_sin_el_login_no_hay_actividad_en_el_usuario_tecnico_y_el_pedido_sigue() -> None:
    # Arrange: el login de ventas no existe en Odoo (p. ej. el admin entra con un correo).
    fake = SalesOdoo(users=[])

    # Act
    with capture_logs() as logs:
        result = _orders(fake).place(_request())

    # Assert: sin actividad (caería en el bot de la API key) y un error sin datos.
    assert result.name == "S00007"
    assert fake.bodies("sale.order", "activity_schedule") == []
    [missing] = [entry for entry in logs if entry["event"] == "pedido.vendedor_no_encontrado"]
    assert missing["log_level"] == "error"
    assert missing["folio"] == "COT-1"
    for secret in ("Ana", "ana@correo.com", "vendedora"):
        assert secret not in repr(missing), secret


def test_el_login_que_no_existe_se_cachea_y_se_vuelve_a_buscar_mas_tarde() -> None:
    # Arrange
    now = [0.0]
    fake = SalesOdoo(users=[])
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )
    orders = OdooOrders(client, sales_login="vendedora", clock=lambda: now[0])

    # Act
    orders.place(_request())
    orders.place(_request())
    searched_twice = len(fake.bodies("res.users", "search_read"))
    fake.users = [{"id": SALES_USER}]
    now[0] = SALES_USER_RETRY_S + 1
    orders.place(_request())

    # Assert: una búsqueda para los dos primeros pedidos; tras el plazo, la actividad sale.
    assert searched_twice == 1
    assert len(fake.bodies("res.users", "search_read")) == 2
    [activity] = fake.bodies("sale.order", "activity_schedule")
    assert activity["user_id"] == SALES_USER


def test_si_odoo_rechaza_la_actividad_el_pedido_sigue_y_el_log_no_lleva_datos() -> None:
    # Arrange
    fake = SalesOdoo(fail={"sale.order.activity_schedule": 403})

    # Act
    with capture_logs() as logs:
        result = _orders(fake).place(_request())

    # Assert
    assert result.name == "S00007"
    [failed] = [entry for entry in logs if entry["event"] == "pedido.aviso_fallido"]
    assert failed["status"] == 403 and failed["folio"] == "COT-1"
    rendered = repr(logs)
    for secret in (ODOO_SECRET, "Ana", "ana@correo.com"):
        assert secret not in rendered, secret


def test_si_falla_la_busqueda_del_vendedor_el_pedido_sigue() -> None:
    # Arrange
    fake = SalesOdoo(fail={"res.users.search_read": 500})

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.name == "S00007"
    assert fake.bodies("sale.order", "activity_schedule") == []


def test_sin_login_de_ventas_no_hay_actividad() -> None:
    # Arrange
    fake = SalesOdoo()

    # Act
    _orders(fake, login="  ").place(_request())

    # Assert
    assert "res.users.search_read" not in fake.methods()
    assert "sale.order.activity_schedule" not in fake.methods()


def test_el_vendedor_se_busca_una_vez() -> None:
    # Arrange
    fake = SalesOdoo()
    orders = _orders(fake)

    # Act
    orders.place(_request())
    orders.place(_request())

    # Assert
    assert len(fake.bodies("res.users", "search_read")) == 1
    assert len(fake.bodies("sale.order", "activity_schedule")) == 2


def test_un_reintento_sobre_un_pedido_completo_no_repite_la_actividad() -> None:
    # Arrange: el pedido ya está confirmado y con su oportunidad (el primer intento terminó).
    fake = SalesOdoo(order_state="sale", opportunity=301)

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.existing
    assert "sale.order.activity_schedule" not in fake.methods()


def test_un_reintento_sin_oportunidad_programa_la_actividad_que_falto() -> None:
    # Arrange: confirmado, pero la respuesta se perdió antes del CRM (y del aviso).
    fake = SalesOdoo(order_state="sale")

    # Act
    result = _orders(fake).place(_request())

    # Assert
    assert result.existing
    [activity] = fake.bodies("sale.order", "activity_schedule")
    assert activity["ids"] == [90]
    assert "S00003" in activity["note"]


def test_un_pedido_en_borrador_retomado_programa_la_actividad() -> None:
    # Arrange
    fake = SalesOdoo(order_state="draft")

    # Act
    _orders(fake).place(_request())

    # Assert
    [activity] = fake.bodies("sale.order", "activity_schedule")
    assert activity["ids"] == [90]


def test_build_orders_usa_odoo_sales_login(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.delenv("ODOO_SALES_LOGIN", raising=False)
    base: dict[str, Any] = {"odoo_url": "http://odoo:8069", "odoo_db": "c", "odoo_api_key": "k"}

    # Act
    default = build_orders(Settings(**base))
    custom = build_orders(Settings(**base, odoo_sales_login="ventas@carvajal.com"))

    # Assert
    assert default is not None and default._sales_login == "admin"
    assert custom is not None and custom._sales_login == "ventas@carvajal.com"


def test_el_adaptador_sin_login_conserva_las_llamadas_de_siempre() -> None:
    # Arrange: ``OdooOrders(client)`` sin ``sales_login`` (pruebas y usos anteriores).
    fake = SalesOdoo()
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )

    # Act
    OdooOrders(client).place(_request())

    # Assert
    assert "sale.order.activity_schedule" not in fake.methods()
