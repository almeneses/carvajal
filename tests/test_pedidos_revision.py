"""Revisión de la rama de cobertura del pedido (spec 056, 2026-11-27).

Fija lo que corrigió la revisión de los hallazgos confirmados sobre el flujo puro y el
servicio (el resto vive junto a su módulo: ``test_pedidos_web``, ``test_pedidos_aviso``,
``test_pedidos_recibo``, ``test_pedidos_seguimiento``, ``test_deploy_packaging`` y
``test_prompts``):

- #2/#14(3): en la oferta un nombre sin marca lleva nombre y apellido; ciudades y siglas no
  son nombres, y la lista de ciudades cubre el directorio de distribuidores;
- #3/#14(1): cualquier rechazo sin dato cierra la confirmación;
- #4/#14(2): sin CRM, el borrador no guarda el nombre ni el correo;
- #10: el correo del ejemplo (``ana@example.com``, RFC 2606) es un dato que falta y nunca
  llega a ``OrderPlacer.place``.
"""

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import stored_messages
from tests.test_pedidos_odoo import OWNER, FakeOrders, _line, _registered, _say, _service
from tests.test_pedidos_odoo_flujo import _stored_draft

from carvajal.adapters.storage import tables
from carvajal.quotation import flow, render
from carvajal.quotation.draft import Awaiting, QuoteDraft
from carvajal.quotation.flow import ORDER_AWAITING
from carvajal.quotation.order import CO_CITIES, Buyer, name_in, plausible_name

NOW = datetime.fromisoformat("2026-11-27T10:00:00-05:00")
FOLIO = "COT-1"
PROVIDERS = Path(__file__).resolve().parents[1] / "data" / "commercial" / "proveedores.csv"


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


def _draft(awaiting: str | None, buyer: Buyer | None = None) -> QuoteDraft:
    return QuoteDraft(
        lines=(_line(),),
        state="registrada",
        folio=FOLIO,
        awaiting=cast("Awaiting", awaiting),
        buyer=buyer.to_json() if buyer else None,
    )


def _before(draft: QuoteDraft, message: str, *, orders: bool = True) -> flow.Reply | flow.Pass:
    return flow.before_turn(draft, message, orders=orders, now=NOW)


# --- #2/#14(3): nombres -------------------------------------------------------------------


@pytest.mark.parametrize("word", ["Ana", "Listo", "Carvajal"])
def test_en_la_oferta_una_palabra_suelta_no_es_un_nombre(word: str) -> None:
    # Act
    decision = _before(_draft("pedido"), word)

    # Assert
    assert decision.draft.buyer is None


@pytest.mark.parametrize(
    ("message", "name"),
    [("Ana Pérez", "Ana Pérez"), ("ANA PÉREZ", "ANA PÉREZ"), ("me llamo Ana", "Ana")],
)
def test_en_la_oferta_nombre_y_apellido_o_la_marca_dan_el_nombre(message: str, name: str) -> None:
    # Act
    decision = _before(_draft("pedido"), message)

    # Assert
    assert Buyer.from_json(decision.draft.buyer).name == name


@pytest.mark.parametrize(
    "value", ["PDF", "SAS", "NIT", "IVA", "Bogotá", "Santa Marta", "San Andrés", "Ana SAS"]
)
def test_una_ciudad_o_una_sigla_no_es_un_nombre_propio(value: str) -> None:
    # Act / Assert
    assert name_in(value, strict=True) is None
    assert not plausible_name(value)


def test_la_lista_de_ciudades_cubre_el_directorio_de_distribuidores() -> None:
    # Arrange
    with PROVIDERS.open(encoding="utf-8") as handle:
        cities = {row["city"].strip() for row in csv.DictReader(handle) if row["city"].strip()}

    # Act
    missing = sorted(city for city in cities if plausible_name(city))

    # Assert: una ciudad nueva en el directorio se añade a ``CO_CITIES``.
    assert cities
    assert missing == [], missing
    assert len(CO_CITIES) >= len(cities)


def test_marcado_el_nombre_puede_ser_cualquiera() -> None:
    # Act: «a nombre de Cali Restrepo» lo dice con su marca (no es una ciudad suelta).
    decision = _before(
        _draft("confirmar_pedido", Buyer("Ana Pérez", "ana@gmail.com")), "a nombre de Cali Restrepo"
    )

    # Assert
    assert Buyer.from_json(decision.draft.buyer).name == "Cali Restrepo"


# --- #3/#14(1): rechazos en la confirmación ------------------------------------------------


@pytest.mark.parametrize("message", ["no sé", "no, mañana", "no, tranquilo", "No"])
def test_un_no_sin_rechazo_en_la_confirmacion_pregunta_que_corregir(message: str) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", Buyer("Ana Pérez", "ana@gmail.com")), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.message == render.which_correction()
    assert Buyer.from_json(decision.draft.buyer) == Buyer("Ana Pérez", "ana@gmail.com")


# --- #4/#14(2): sin CRM, sin datos ---------------------------------------------------------


@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_sin_crm_el_pedido_a_medias_no_guarda_datos(awaiting: str) -> None:
    # Arrange
    draft = _draft(awaiting, Buyer("Ana Pérez", "ana@gmail.com"))

    # Act: cualquier turno (un «sí», una pregunta) sin CRM.
    replies = [_before(draft, message, orders=False) for message in ("sí", "¿y el envío?")]

    # Assert
    for decision in replies:
        assert decision.draft.buyer is None
        assert decision.draft.awaiting not in ORDER_AWAITING


def test_sin_crm_tras_el_reinicio_la_pregunta_no_guarda_datos(engine: Any) -> None:
    # Arrange: confirmación pendiente con CRM; la app reinicia sin ``ODOO_*``.
    first, _ = _service(engine, FakeOrders())
    _registered(first)
    _say(first, "sí")
    _say(first, "Ana Pérez, ana@gmail.com")
    service, _ = _service(engine, None)

    # Act
    list(service.stream(OWNER, "¿cuánto tarda la entrega?"))

    # Assert
    draft = _stored_draft(engine)
    assert draft.buyer is None and draft.awaiting not in ORDER_AWAITING


# --- #10: el correo del ejemplo ------------------------------------------------------------


def test_el_ejemplo_usa_un_dominio_reservado() -> None:
    # Assert: RFC 2606; ``correo.com`` tiene MX y recibiría el pedido de otra persona.
    assert render.EXAMPLE_EMAIL == "ana@example.com"
    assert render.EXAMPLE_EMAIL in render.ask_buyer().text
    assert render.EXAMPLE_EMAIL in render.buyer_missing(None, None).text


@pytest.mark.parametrize("awaiting", ["pedido", "datos_pedido", "confirmar_pedido"])
def test_el_correo_del_ejemplo_es_un_dato_que_falta(awaiting: str) -> None:
    # Arrange
    buyer = Buyer("Ana Pérez", "ana@gmail.com") if awaiting == "confirmar_pedido" else None

    # Act
    decision = _before(_draft(awaiting, buyer), f"Ana Pérez, {render.EXAMPLE_EMAIL}")

    # Assert: nunca queda como correo del comprador.
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    stored = Buyer.from_json(decision.draft.buyer)
    assert stored.email != render.EXAMPLE_EMAIL
    if awaiting != "confirmar_pedido":
        assert decision.message == render.buyer_missing("Ana Pérez", None)


def test_el_ejemplo_copiado_nunca_llega_al_crm(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    _registered(service)
    _say(service, "sí")

    # Act: copia el ejemplo tal cual y confirma.
    asked = _say(service, f"Ana Pérez, {render.EXAMPLE_EMAIL}")
    _say(service, "sí")
    _say(service, "sí")

    # Assert
    assert asked == render.buyer_missing("Ana Pérez", None).text
    assert orders.requests == []
    assert "Pedido S00001" not in repr(stored_messages(engine))
