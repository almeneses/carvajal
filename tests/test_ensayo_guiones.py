"""Guiones del ensayo general (tests/e2e/guiones.py) contra el servicio del chat, sin red.

El ensayo no importa la app y copia sus textos; aquí se corren sus pasos contra
``ChatService`` con el agente y Odoo simulados, con y sin pedidos (spec 055), para que un
cambio de plantilla rompa esta prueba y no el ensayo en producción.
"""

from collections.abc import Iterator
from itertools import count
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.e2e.guiones import (
    ACEPTAR_PEDIDO,
    CONFIRMAR_DATOS,
    CONFIRMAR_PEDIDO,
    GUIONES,
    OFERTA_PEDIDO,
    PEDIDO_FALLIDO,
    PedidosApagados,
    Respuesta,
    correo_de_prueba,
    ejecutar,
    ejecutar_pedido,
    formato,
    inferir_awaiting,
    nombre_de_prueba,
    registra,
)
from tests.test_pedidos_odoo import FakeOrders, _service

from apps.api.chat.service import ChatService
from apps.mcp.bridge import to_whatsapp
from carvajal.adapters.crm import OdooError, OrderResult
from carvajal.adapters.storage import tables
from carvajal.quotation import render
from carvajal.quotation.order import Buyer, OrderRequest, has_buyer_data, merge_buyer
from carvajal.quotation.render import ORDER_OFFER

GUION_REGISTRO = next(guion for guion in GUIONES if guion.designado)
_DUENOS = count()


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


class CanalServicio:
    """Un canal del ensayo sobre ``ChatService``: web tal cual o WhatsApp (``*x*``, sin flujo).

    En WhatsApp el estado se deduce del texto, como en el ensayo; solo el turno del agente
    simulado (que no trae el texto del carrito) usa el ``awaiting`` del servicio.
    """

    def __init__(self, service: ChatService, nombre: str = "web") -> None:
        self.service = service
        self.nombre = nombre
        self.dueno = ""
        self.registros: list[str] = []

    def reiniciar(self) -> None:
        prefijo = "wa_" if self.nombre == "whatsapp" else "visitante-"
        self.dueno = f"{prefijo}ensayo-{next(_DUENOS)}"

    def enviar(self, mensaje: str) -> Respuesta:
        eventos = {evento.name: evento.data for evento in self.service.stream(self.dueno, mensaje)}
        fijo = eventos.get("fallback")
        texto = (fijo or {}).get("text", "")
        cierre = eventos.get("done") or {}
        awaiting = (cierre.get("flow") or {}).get("awaiting")
        if self.nombre == "whatsapp":
            plano = texto.replace("**", "*")
            return Respuesta(texto=plano, awaiting=inferir_awaiting(plano) if fijo else awaiting)
        return Respuesta(
            texto=texto,
            awaiting=awaiting,
            folio=cierre.get("quote_id"),
            ruta=(eventos.get("trace") or {}).get("route") or cierre.get("route"),
        )

    def tras_registro(self, respuesta: Respuesta, folio: str) -> None:
        self.registros.append(folio)


class OrdersQueFallaUnaVez(FakeOrders):
    """Odoo que falla al primer pedido y responde al reintento (como un corte breve)."""

    def place(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise OdooError("Odoo no responde (ConnectError).")
        return OrderResult(name="S00001", order_id=1, lead_id=2)


def _mensajes(transcripcion: Any) -> list[str]:
    return [paso.mensaje for paso, _ in transcripcion.turnos]


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_el_guion_de_registro_rechaza_el_pedido_antes_del_segundo_si(
    engine: Any, canal: str
) -> None:
    # Arrange: Odoo configurado: el folio ofrece el pedido.
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service, canal)

    # Act
    transcripcion = ejecutar(ensayo, GUION_REGISTRO, registrar=True)

    # Assert
    mensajes = _mensajes(transcripcion)
    assert mensajes[-3:] == ["sí", "ahora no", "sí"]
    _, registro = transcripcion.turnos[-3]
    assert OFERTA_PEDIDO in registro.texto
    assert registro.awaiting == "pedido"
    assert len(ensayo.registros) == 1
    assert orders.requests == []


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_sin_odoo_el_guion_de_registro_sigue_como_en_la_spec_054(engine: Any, canal: str) -> None:
    # Arrange
    service, _ = _service(engine, None)
    ensayo = CanalServicio(service, canal)

    # Act
    transcripcion = ejecutar(ensayo, GUION_REGISTRO, registrar=True)

    # Assert: sin oferta, «ahora no» se salta y el segundo «sí» repite el folio.
    mensajes = _mensajes(transcripcion)
    assert "ahora no" not in mensajes
    assert mensajes[-2:] == ["sí", "sí"]
    assert OFERTA_PEDIDO not in transcripcion.turnos[-2][1].texto


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_el_pedido_del_ensayo_se_crea_con_el_comprador_del_run(engine: Any, canal: str) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    ensayo = CanalServicio(service, canal)
    nombre = nombre_de_prueba("20261126103000")
    correo = correo_de_prueba(f"20261126103000-{canal}")

    # Act
    pedido = ejecutar_pedido(ensayo, nombre=nombre, correo=correo)

    # Assert: los datos se confirman antes del pedido y «Sí, confírmalo» lo crea.
    assert pedido.numero == "S00001"
    assert pedido.folio == ensayo.registros[0]
    assert not pedido.reintentado
    assert _mensajes(pedido.transcripcion)[-5:] == [
        ACEPTAR_PEDIDO,
        nombre,
        correo,
        CONFIRMAR_PEDIDO,
        "quiero comprarla",
    ]
    _, confirmacion = pedido.transcripcion.turnos[-3]
    assert confirmacion.awaiting == "confirmar_pedido"
    assert formato(f"- Correo: **{correo}**", canal) in confirmacion.texto
    _, creado = pedido.transcripcion.turnos[-2]
    assert formato(f"La confirmación del pedido llega a **{correo}**", canal) in creado.texto
    (request,) = orders.requests
    assert request.folio == pedido.folio
    assert request.buyer == Buyer(nombre, correo)
    assert request.channel == ("WhatsApp" if canal == "whatsapp" else "chat web")
    assert len(runner.calls) == 1  # solo la cotización pasó por el modelo


def test_sin_odoo_el_escenario_del_pedido_se_salta_tras_el_folio(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)
    ensayo = CanalServicio(service)

    # Act / Assert
    with pytest.raises(PedidosApagados, match="ODOO_URL"):
        ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com")
    assert len(ensayo.registros) == 1


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_si_odoo_falla_una_vez_el_si_reintenta_con_el_mismo_folio(engine: Any, canal: str) -> None:
    # Arrange
    orders = OrdersQueFallaUnaVez()
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service, canal)

    # Act
    pedido = ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com", pausa=0)

    # Assert: el fallo deja el pedido por confirmar y el «sí» lo crea sin otro folio.
    assert pedido.reintentado
    assert pedido.numero == "S00001"
    assert _mensajes(pedido.transcripcion)[-3:] == [CONFIRMAR_PEDIDO, "sí", "quiero comprarla"]
    _, fallido = pedido.transcripcion.turnos[-3]
    assert PEDIDO_FALLIDO in fallido.texto
    assert fallido.awaiting == "confirmar_pedido"
    assert [request.folio for request in orders.requests] == [pedido.folio, pedido.folio]
    assert orders.requests[1].buyer == Buyer("Ensayo Prueba", "e2e-x@example.com")
    assert len(ensayo.registros) == 1


def test_si_odoo_sigue_fallando_el_escenario_del_pedido_falla(engine: Any) -> None:
    # Arrange
    orders = FakeOrders(fail=True)
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service)

    # Act / Assert: un solo reintento; el segundo fallo hace fallar el escenario.
    with pytest.raises(AssertionError, match="ni al reintentar"):
        ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com", pausa=0)
    assert len(orders.requests) == 2
    assert len({request.folio for request in orders.requests}) == 1


@pytest.mark.parametrize(
    ("run_id", "nombre"),
    [
        ("20261126103000", "Ensayo Cacgbbcgbadaaa"),
        ("corrida-7", "Ensayo Corridah"),
        ("", "Ensayo Prueba"),
        # Cortos: sin «prueba», el flujo los leería como muletilla o partícula.
        ("ok", "Ensayo Okprueba"),
        ("si", "Ensayo Siprueba"),
        ("ya", "Ensayo Yaprueba"),
        ("es", "Ensayo Esprueba"),
        ("34", "Ensayo Deprueba"),
        ("mejor", "Ensayo Mejorprueba"),
    ],
)
def test_el_comprador_de_prueba_lo_lee_el_flujo(run_id: str, nombre: str) -> None:
    # Arrange: el correo del canal, como en test_web.py y test_whatsapp.py.
    correo = correo_de_prueba(f"{run_id}-web")

    # Act
    comprador = merge_buyer(merge_buyer(Buyer(), nombre_de_prueba(run_id)), correo)

    # Assert
    assert nombre_de_prueba(run_id) == nombre
    assert comprador == Buyer(nombre, correo)
    assert has_buyer_data(nombre) and has_buyer_data(correo)
    assert correo.startswith("e2e-") and correo.endswith("-web@example.com")


@pytest.mark.parametrize(
    ("politica", "designado", "pedido", "esperado"),
    [
        ("todas", False, True, True),
        ("una", True, False, True),
        ("una", True, True, False),
        ("una", False, False, False),
        ("ninguna", True, False, False),
    ],
)
def test_con_pedido_el_folio_unico_lo_registra_el_pedido(
    politica: str, designado: bool, pedido: bool, esperado: bool
) -> None:
    assert registra(politica, designado, pedido=pedido) is esperado


def test_la_oferta_del_ensayo_es_la_de_la_app() -> None:
    assert OFERTA_PEDIDO in ORDER_OFFER
    assert inferir_awaiting(ORDER_OFFER) == "pedido"
    assert CONFIRMAR_DATOS in render.confirm_buyer("Ana", "ana@correo.com").text
    assert PEDIDO_FALLIDO in render.order_failed().text


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
@pytest.mark.parametrize(
    ("mensaje", "esperado"),
    [
        (render.order_offer(render.Message("✅ **Cotización #abc registrada**", "")), "pedido"),
        (render.ask_buyer(), "datos_pedido"),
        (render.buyer_missing("Ana", None), "datos_pedido"),
        (render.buyer_missing(None, "ana@correo.com"), "datos_pedido"),
        (render.buyer_missing(None, None), "datos_pedido"),
        (render.confirm_buyer("Ensayo Prueba", "e2e-x@example.com"), "confirmar_pedido"),
        (render.order_failed(), "confirmar_pedido"),
        (render.order_reminder("pedido"), "pedido"),
        (render.order_reminder("datos_pedido"), "datos_pedido"),
        (render.order_reminder("confirmar_pedido"), "confirmar_pedido"),
        (render.order_placed("S00001", "e2e-x@example.com", "abc"), None),
        (render.already_ordered("S00001"), None),
        (render.order_declined("abc"), None),
    ],
)
def test_whatsapp_deduce_cada_paso_del_pedido_del_texto(
    mensaje: render.Message | None, esperado: str | None, canal: str
) -> None:
    # Arrange: WhatsApp recibe la plantilla con la negrita de un solo «*» (bridge.to_whatsapp).
    assert mensaje is not None
    texto = to_whatsapp(mensaje.text) if canal == "whatsapp" else mensaje.text

    # Act
    awaiting = inferir_awaiting(texto)

    # Assert
    assert awaiting == esperado
