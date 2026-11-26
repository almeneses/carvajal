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
    GUIONES,
    OFERTA_PEDIDO,
    PedidosApagados,
    Respuesta,
    correo_de_prueba,
    ejecutar,
    ejecutar_pedido,
    inferir_awaiting,
    nombre_de_prueba,
    registra,
)
from tests.test_pedidos_odoo import FakeOrders, _service

from apps.api.chat.service import ChatService
from carvajal.adapters.storage import tables
from carvajal.quotation.order import Buyer, merge_buyer
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

    # Assert
    assert pedido.numero == "S00001"
    assert pedido.folio == ensayo.registros[0]
    assert _mensajes(pedido.transcripcion)[-4:] == [
        "Sí, quiero hacer el pedido",
        nombre,
        correo,
        "quiero comprarla",
    ]
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


def test_si_odoo_falla_el_escenario_del_pedido_falla(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders(fail=True))
    ensayo = CanalServicio(service)

    # Act / Assert
    with pytest.raises(AssertionError, match="Pedido "):
        ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com")


@pytest.mark.parametrize(
    ("run_id", "nombre"),
    [
        ("20261126103000", "Ensayo Cacgbbcgbadaaa"),
        ("corrida-7", "Ensayo Corridah"),
        ("", "Ensayo Prueba"),
    ],
)
def test_el_comprador_de_prueba_lo_lee_el_flujo(run_id: str, nombre: str) -> None:
    # Arrange
    correo = correo_de_prueba(run_id)

    # Act
    comprador = merge_buyer(merge_buyer(Buyer(), nombre_de_prueba(run_id)), correo)

    # Assert
    assert comprador == Buyer(nombre, correo)
    assert correo.startswith("e2e-") and correo.endswith("@example.com")


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
