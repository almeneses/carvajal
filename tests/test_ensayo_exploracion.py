"""Exploración de escenarios (tests/e2e/test_exploracion.py), sin red: alertas y guiones.

Los guiones del pedido (spec 056) se corren contra ``ChatService`` con el agente y Odoo
simulados: los de rechazo nunca crean el pedido y el de ``ENSAYO_PEDIDO`` crea uno solo.
"""

from collections.abc import Iterator
from itertools import pairwise
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.e2e.guiones import OFERTA_PEDIDO, PEDIDO_CREADO
from tests.e2e.test_exploracion import (
    ESCENARIO_PEDIDO_CREADO,
    ESCENARIOS,
    ESCENARIOS_PEDIDO,
    alertas,
    comprador,
    con_comprador,
)
from tests.test_chat_service_tools import (
    FakeEmbedder,
    FakeLLM,
    FakeToolRunner,
    FakeTracer,
    commercial_final,
    scripted_events,
    tools_settings,
)
from tests.test_pedidos_odoo import FakeOrders

from apps.api.chat.service import ChatService
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.agent.followup import is_confirmation, resolve_more
from carvajal.quotation import render
from carvajal.quotation.order import Buyer, find_email


def test_marca_las_respuestas_genericas_y_las_citas_sin_fuente() -> None:
    # Arrange
    turnos = [
        {"msg": "Medellín", "resp": "Para seguir necesito un dato más. ¿Me cuentas…?"},
        {"msg": "sedes", "resp": "Dos sedes [1][2].\n\n*Fuentes:*\n[1] Sede A — https://x"},
    ]

    # Act
    encontradas = alertas("sedes", "wa", turnos)

    # Assert
    assert any("respuesta genérica" in a for a in encontradas)
    assert any("citas sin fuente ['2']" in a for a in encontradas)


def test_marca_la_ayuda_ajena_y_lo_agregado_tras_eso_es_todo() -> None:
    assert alertas("fuera_de_alcance", "web", [{"msg": "tarea", "resp": "Sí, te ayudo."}])
    assert alertas("x", "web", [{"msg": "eso es todo", "resp": "✅ Agregué 2.000 platos"}])


def test_una_respuesta_buena_no_tiene_alertas() -> None:
    turnos = [{"msg": "¿Dónde compro?", "resp": "En Pereira [1].\n\n*Fuentes:*\n[1] Sede — u"}]
    assert alertas("sedes", "wa", turnos) == []


def test_ningun_guion_confirma_una_cotizacion() -> None:
    # Solo «¿La confirmo?» (``confirmar``) registra un folio, y a esa pregunta se llega
    # únicamente tras un cierre («eso es todo», «no nada más», «…, confírmala»). Con las
    # mismas reglas del flujo: ningún guion afirma después de un cierre, ni cierra
    # confirmando, así la exploración no deja folios ni PDF en producción.
    for nombre, pasos in ESCENARIOS:
        textos = [mensaje for mensaje, _voz in pasos]
        for texto in textos:
            assert resolve_more(texto) != "close_confirm", (nombre, texto)
        for anterior, actual in pairwise(textos):
            cierra = resolve_more(anterior) in ("close", "close_confirm")
            assert not (cierra and is_confirmation(actual)), (nombre, anterior, actual)
    # La guarda no es vacía: el par que registraría un folio sí se detecta.
    assert resolve_more("no, nada más") == "close"
    assert is_confirmation("sí")


# --- Guiones del pedido (spec 056) --------------------------------------------------------


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


class AgenteQueCotizaUnaVez(FakeToolRunner):
    """El agente simulado cotiza en el primer turno y luego solo responde (sin cotizar).

    Así una pregunta a mitad del pedido («¿cuánto tarda el envío?») no agrega un producto
    al borrador, como haría el agente real al responderla.
    """

    def __init__(self) -> None:
        super().__init__(scripted_events(commercial_final()))

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        if self.calls:
            self.events = scripted_events()
        yield from super().stream(*args, **kwargs)


def _servicio(engine: Any, orders: FakeOrders) -> ChatService:
    return ChatService(
        build_session_factory(engine),
        FakeEmbedder(),
        FakeLLM(),
        tool_agent=AgenteQueCotizaUnaVez(),
        tracer=FakeTracer(),
        settings=tools_settings(),
        orders=orders,
    )


def _conversar(service: Any, dueno: str, pasos: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """Los pasos del guion contra ``ChatService`` (agente y Odoo simulados), como la web.

    El recibo se enlaza como en la vista del stream (``render.receipt_offer``).
    """

    valores = comprador("20261126103000", "web")
    turnos: list[dict[str, Any]] = []
    for plantilla, voz in pasos:
        mensaje = con_comprador(plantilla, valores)
        eventos = {e.name: e.data for e in service.stream(dueno, mensaje, spoken=voz)}
        cierre = eventos.get("done") or {}
        turnos.append(
            {
                "msg": mensaje,
                "resp": render.receipt_offer(
                    (eventos.get("fallback") or {}).get("text", ""),
                    lambda folio: f"/pedidos/{folio}.pdf",
                ),
                "datos": "{" in plantilla,
                "meta": {
                    "ruta": cierre.get("route"),
                    "espera": (cierre.get("flow") or {}).get("awaiting"),
                },
            }
        )
    return turnos


@pytest.mark.parametrize(
    ("nombre", "pasos"), ESCENARIOS_PEDIDO, ids=[e[0] for e in ESCENARIOS_PEDIDO]
)
def test_los_guiones_de_rechazo_llegan_a_la_oferta_y_no_crean_el_pedido(
    engine: Any, nombre: str, pasos: list[tuple[str, bool]]
) -> None:
    # Arrange
    orders = FakeOrders()
    service = _servicio(engine, orders)

    # Act
    turnos = _conversar(service, f"visitante-{nombre}", pasos)

    # Assert: el folio ofrece el pedido, ningún paso lo crea y el guion no deja alertas.
    folio = next(t for t in turnos if OFERTA_PEDIDO in t["resp"])
    assert "Cotización #" in folio["resp"], folio
    assert orders.requests == []
    assert not any(PEDIDO_CREADO.search(t["resp"]) for t in turnos)
    assert turnos[-1]["meta"]["espera"] not in {"pedido", "datos_pedido", "confirmar_pedido"}
    assert alertas(nombre, "web", turnos) == []


def test_el_guion_que_crea_el_pedido_crea_uno_solo(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service = _servicio(engine, orders)
    nombre, pasos = ESCENARIO_PEDIDO_CREADO

    # Act
    turnos = _conversar(service, "visitante-pedido-creado", pasos)

    # Assert: uno solo, con el comprador del run; el estado y «cancela» no lo tocan.
    (request,) = orders.requests
    valores = comprador("20261126103000", "web")
    assert request.buyer == Buyer(valores["{nombre}"], valores["{correo}"])
    assert [t["msg"] for t in turnos][-3:] == [
        "¿cómo va mi pedido?",
        "cancela el pedido",
        "mándame el recibo",
    ]
    assert "asesor" in turnos[-3]["resp"]
    assert "no se cancela desde el chat" in turnos[-2]["resp"]
    assert alertas(nombre, "web", turnos) == []


def test_el_correo_dictado_del_guion_lo_lee_el_flujo() -> None:
    # Arrange
    valores = comprador("20261126103000", "wa")

    # Act
    leido = find_email(valores["{correo_voz}"])

    # Assert
    assert leido == valores["{correo}"]
    assert "@" not in valores["{correo_voz}"]
    assert valores["{correo}"].endswith("@example.com")


def test_las_alertas_del_pedido() -> None:
    # Arrange
    pendiente = {
        "msg": "Ana",
        "resp": "Gracias",
        "meta": {"ruta": "pedido", "espera": "datos_pedido"},
    }
    turnos = [
        pendiente,
        {"msg": "¿envío?", "resp": "Lo siento, no puedo tomar pedidos.", "meta": {}},
        {"msg": "ana@x.co", "resp": "x", "datos": True, "meta": {"ruta": "agent"}},
        {
            "msg": "sí",
            "resp": "🧾 **Pedido S00001 confirmado**\n📄 Te envío el recibo del pedido en PDF.",
        },
    ]

    # Act
    encontradas = alertas("pedido_chao_en_datos", "web", turnos)

    # Assert
    assert any("no toma pedidos" in a for a in encontradas)
    assert any("ruta 'agent'" in a for a in encontradas)
    assert any("promete el recibo" in a for a in encontradas)
    assert any("CREÓ UN PEDIDO REAL" in a for a in encontradas)
    assert alertas("pedido_creado", "web", turnos[-1:]) == [
        "web · pedido_creado · turno 1 («sí»): promete el recibo sin enlace ni adjunto"
    ]
