"""Regresiones del paso del pedido tras el folio (spec 056, revisión del commit b893c28).

Una prueba por comportamiento corregido: muletillas y rechazos que no son nombres, el correo
dictado por voz, preguntas a mitad del pedido (el paso se conserva y se recuerda una vez),
otro producto que sale del pedido, Jev fuera de los pasos del pedido y la privacidad del
nombre y el correo en lo que se guarda, se monitorea y vuelve al modelo. Las puras usan
``before_turn``/``after_turn``/``merge_buyer``; las de servicio, ``ChatService`` real sobre
SQLite con el runner ``tools`` guionado.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs
from tests.test_chat_service_tools import FakeTracer, final, scripted_events, stored_messages, texts
from tests.test_pedidos_odoo import OWNER, FakeOrders, _line, _registered, _say, _service

from apps.api.chat.service import ORDER_DATA_PLACEHOLDER, ChatService
from carvajal.adapters.storage import tables
from carvajal.quotation import flow, render
from carvajal.quotation.draft import Awaiting, QuoteDraft
from carvajal.quotation.flow import ORDER_AWAITING
from carvajal.quotation.order import (
    Buyer,
    declines_order,
    has_buyer_data,
    merge_buyer,
    spoken_email,
    wants_purchase,
)

# Distintos del ejemplo de ``render.ask_buyer`` («Ana Pérez, ana@correo.com»): así la
# búsqueda de los datos en lo guardado no tropieza con el texto fijo del asistente.
NAME = "Lucía Restrepo"
EMAIL = "lucia.r@empresa.co"
MASKED = "l…@empresa.co"
NOW = datetime.fromisoformat("2026-11-26T10:00:00-05:00")
QUESTION = "¿Cuánto tarda la entrega?"


def _draft(awaiting: str, buyer: Buyer | None = None) -> QuoteDraft:
    """Cotización registrada con el pedido en el paso ``awaiting``."""

    return QuoteDraft(
        lines=(_line(),),
        state="registrada",
        folio="COT-1",
        awaiting=cast("Awaiting", awaiting),
        buyer=buyer.to_json() if buyer else None,
    )


def _before(draft: QuoteDraft, message: str, judge: Any = None) -> flow.Reply | flow.Pass:
    return flow.before_turn(draft, message, orders=True, now=NOW, judge=judge)


class RecordingJudge:
    """Jev seguro de sí mismo: si lo llaman, decide «confirmar». Anota cada llamada."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]:
        self.calls.append((awaiting, reply))
        return "confirmar", 0.99


# --- nombres: muletillas, rechazos y partículas -----------------------------------------


@pytest.mark.parametrize(
    "message", ["ok gracias", "mejor no", "dale", "listo", "gracias", "quiero el pedido"]
)
def test_una_muletilla_o_un_rechazo_no_es_un_nombre(message: str) -> None:
    # Act
    buyer = merge_buyer(Buyer(), message)

    # Assert
    assert buyer == Buyer()
    assert not has_buyer_data(message)


def test_una_muletilla_no_pisa_el_nombre_ya_dado() -> None:
    # Arrange
    given = Buyer(NAME)

    # Act
    after = merge_buyer(given, "ok gracias")

    # Assert
    assert after == given


def test_las_muletillas_de_los_bordes_no_entran_al_nombre() -> None:
    # Act
    buyer = merge_buyer(Buyer(), "Juan, juan@x.co, gracias")

    # Assert
    assert buyer == Buyer("Juan", "juan@x.co")


@pytest.mark.parametrize("message", ["María de la Cruz", "maría de la cruz"])
def test_las_particulas_del_apellido_van_en_minuscula(message: str) -> None:
    # Act
    buyer = merge_buyer(Buyer(), message)

    # Assert
    assert buyer.name == "María de la Cruz"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Marta", NAME),  # una palabra suelta no pisa el nombre real
        ("me llamo Marta", "Marta"),
        ("mi nombre es Marta", "Marta"),
        ("a nombre de Marta Gómez", "Marta Gómez"),
        ("Marta, marta@correo.com", "Marta"),  # con un correo nuevo, el nombre también cambia
    ],
)
def test_el_nombre_previo_solo_se_reemplaza_con_marca_o_correo(message: str, expected: str) -> None:
    # Act
    buyer = merge_buyer(Buyer(NAME, EMAIL), message)

    # Assert
    assert buyer.name == expected


def test_en_datos_pedido_ok_gracias_no_cambia_el_nombre() -> None:
    # Arrange
    draft = _draft("datos_pedido", Buyer(NAME))

    # Act
    decision = _before(draft, "ok gracias")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.draft.awaiting == "datos_pedido"
    assert Buyer.from_json(decision.draft.buyer) == Buyer(NAME)
    assert decision.message == render.buyer_missing(NAME, None)


# --- rechazos -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "no quiero hacer el pedido",
        "no, mejor no",
        "mejor no",
        "ya no",
        "todavía no, gracias",
        "no quiero el pedido",
    ],
)
@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_los_rechazos_cierran_cualquier_paso_del_pedido(message: str, awaiting: str) -> None:
    # Arrange
    buyer = Buyer(NAME, EMAIL) if awaiting == "confirmar_pedido" else None
    draft = _draft(awaiting, buyer)

    # Act
    decision = _before(draft, message)

    # Assert
    assert declines_order(message)
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert decision.message == render.order_declined("COT-1")
    assert decision.draft.awaiting is None
    assert decision.draft.buyer is None
    assert decision.draft.state == "registrada" and decision.draft.folio == "COT-1"


@pytest.mark.parametrize(
    "message", ["no quiero hacer el pedido", "no quiero comprarla", "no hagamos el pedido"]
)
def test_la_compra_negada_no_es_intencion_de_compra(message: str) -> None:
    assert wants_purchase(message) is False


# --- correo dictado por voz -----------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ana punto perez arroba gmail punto com", "ana.perez@gmail.com"),
        ("mi correo es ana punto perez arroba gmail punto com", "mi correo es ana.perez@gmail.com"),
        ("ana guion bajo perez arroba correo punto com punto co", "ana_perez@correo.com.co"),
        ("ana guion perez arroba gmail punto com", "ana-perez@gmail.com"),
        ("nos vemos en el punto de venta", "nos vemos en el punto de venta"),  # sin «arroba»
        ("ana@correo.com", "ana@correo.com"),  # ya escrito
    ],
)
def test_el_correo_dictado_se_escribe_como_correo(message: str, expected: str) -> None:
    assert spoken_email(message) == expected


def test_el_correo_dictado_completa_los_datos() -> None:
    # Arrange
    draft = _draft("datos_pedido", Buyer(NAME))

    # Act
    decision = _before(draft, "ana punto perez arroba gmail punto com")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.draft.awaiting == "confirmar_pedido"
    assert Buyer.from_json(decision.draft.buyer) == Buyer(NAME, "ana.perez@gmail.com")
    assert decision.message == render.confirm_buyer(NAME, "ana.perez@gmail.com")


# --- pasos del pedido: confirmación, preguntas y otro producto ----------------------------


def test_la_oferta_respondida_con_los_datos_pide_confirmarlos() -> None:
    # Act
    decision = _before(_draft("pedido"), f"{NAME}, {EMAIL}")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None  # todavía no: falta el «sí» de la confirmación
    assert decision.private
    assert decision.draft.awaiting == "confirmar_pedido"
    assert decision.message == render.confirm_buyer(NAME, EMAIL)


def test_un_correo_nuevo_en_la_confirmacion_la_corrige() -> None:
    # Arrange
    draft = _draft("confirmar_pedido", Buyer(NAME, EMAIL))

    # Act
    decision = _before(draft, "mejor a lucia.restrepo@otra.co")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert decision.draft.awaiting == "confirmar_pedido"
    assert decision.message == render.confirm_buyer(NAME, "lucia.restrepo@otra.co")


def test_el_si_de_la_confirmacion_pide_el_pedido_con_los_datos() -> None:
    # Arrange
    draft = _draft("confirmar_pedido", Buyer(NAME, EMAIL))

    # Act
    decision = _before(draft, "Sí, confírmalo")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order == Buyer(NAME, EMAIL)


@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_una_pregunta_a_mitad_del_pedido_va_al_agente_sin_perder_el_paso(awaiting: str) -> None:
    # Arrange
    buyer = Buyer(NAME, EMAIL) if awaiting == "confirmar_pedido" else None
    draft = _draft(awaiting, buyer)

    # Act
    decision = _before(draft, QUESTION)
    reminded = flow.after_turn(decision.draft, [], QUESTION, now=NOW)
    released = flow.after_turn(reminded.draft, [], "¿Y hacen envíos a Cali?", now=NOW)

    # Assert
    assert isinstance(decision, flow.Pass)
    assert decision.draft.awaiting == awaiting
    assert Buyer.from_json(decision.draft.buyer) == (buyer or Buyer())
    assert reminded.message == render.order_reminder(awaiting)
    assert reminded.draft.awaiting == awaiting
    assert released.message is None
    assert released.draft == QuoteDraft()


def test_si_el_agente_cotiza_otra_cosa_el_pedido_pendiente_se_reinicia() -> None:
    # Arrange
    draft = _draft("datos_pedido")
    decision = _before(draft, QUESTION)
    quote = {**_line("V-16"), "commercial": {"currency": "COP"}}

    # Act
    outcome = flow.after_turn(decision.draft, [quote], QUESTION, now=NOW)

    # Assert
    assert outcome.draft.folio is None
    assert outcome.draft.awaiting not in ORDER_AWAITING
    assert outcome.message is not None and "↩️" not in outcome.message.text


def test_otro_producto_en_un_paso_del_pedido_sale_del_pedido_aunque_el_agente_no_cotice() -> None:
    # Arrange
    draft = _draft("datos_pedido")

    # Act
    decision = _before(draft, "quiero 500 vasos")
    outcome = flow.after_turn(decision.draft, [], "quiero 500 vasos", now=NOW)

    # Assert
    assert isinstance(decision, flow.Pass)
    assert decision.draft.awaiting not in ORDER_AWAITING
    assert outcome.message is None or "↩️" not in outcome.message.text


@pytest.mark.parametrize(
    "message", ["Sí, adelante", "sí señor", "hágale", "cuéntame de la empresa"]
)
def test_en_la_oferta_una_frase_que_no_es_nombre_no_se_toma_como_nombre(message: str) -> None:
    # Act
    decision = _before(_draft("pedido"), message)

    # Assert
    assert Buyer.from_json(decision.draft.buyer).name is None


def test_en_la_confirmacion_escribir_solo_el_nombre_correcto_lo_corrige() -> None:
    # Arrange
    draft = _draft("confirmar_pedido", Buyer("Adelante", EMAIL))

    # Act
    decision = _before(draft, "Marta Gómez")

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.message == render.confirm_buyer("Marta Gómez", EMAIL)


# --- Jev ------------------------------------------------------------------------------


@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
@pytest.mark.parametrize(
    "message",
    ["pues bueno", "ok gracias", "gracias por todo", "cuéntame de la empresa", "hágale pues"],
)
def test_jev_nunca_clasifica_un_paso_del_pedido(awaiting: str, message: str) -> None:
    # Arrange
    buyer = Buyer(NAME, EMAIL) if awaiting == "confirmar_pedido" else None
    judge = RecordingJudge()

    # Act
    _before(_draft(awaiting, buyer), message, judge=judge)

    # Assert
    assert judge.calls == []


def test_fuera_del_pedido_jev_si_clasifica() -> None:
    # Arrange: control de la prueba anterior; el mismo Jev sí se consulta en «¿Algo más?».
    draft = QuoteDraft(lines=(_line(),), state="carrito", awaiting="algo_mas")
    judge = RecordingJudge()

    # Act
    _before(draft, "pues bueno", judge=judge)

    # Assert
    assert judge.calls == [("algo_mas", "pues bueno")]


# --- servicio del chat ------------------------------------------------------------------


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


def _ask(service: ChatService, message: str) -> str:
    """Texto del turno, salga del flujo (``fallback``) o del agente (``token``)."""

    events = list(service.stream(OWNER, message))
    fallback = [event for event in events if event.name == "fallback"]
    return str(fallback[0].data["text"]) if fallback else texts(events)


def _stored_draft(engine: Any) -> QuoteDraft:
    with engine.connect() as connection:
        payload = connection.execute(select(tables.quote_drafts.c.payload)).scalar_one()
    return QuoteDraft.from_json(payload)


def _all_rows(engine: Any) -> str:
    """Todas las filas de todas las tablas, como texto."""

    with engine.connect() as connection:
        return repr(
            [
                [dict(row) for row in connection.execute(select(table)).mappings()]
                for table in tables.metadata.sorted_tables
            ]
        )


_STEPS: dict[str, tuple[str, ...]] = {
    "pedido": (),
    "datos_pedido": ("sí",),
    "confirmar_pedido": ("sí", f"{NAME}, {EMAIL}"),
}


@pytest.mark.parametrize("awaiting", sorted(ORDER_AWAITING))
def test_una_pregunta_en_el_pedido_la_responde_el_agente_y_recuerda_el_paso_una_vez(
    engine: Any, awaiting: str
) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _registered(service)
    for message in _STEPS[awaiting]:
        _say(service, message)
    runner.events = scripted_events(final())  # el agente responde sin cotizar

    # Act
    answer = _ask(service, QUESTION)
    kept = _stored_draft(engine)
    second = _ask(service, "¿Y hacen envíos a Cali?")

    # Assert
    reminder = render.order_reminder(awaiting)
    assert reminder is not None
    assert len(runner.calls) == 3  # cotización y las dos preguntas
    assert answer.endswith(reminder.text)
    assert answer.count("↩️") == 1
    assert kept.awaiting == awaiting and kept.folio
    assert "↩️" not in second
    assert _stored_draft(engine).awaiting is None
    assert orders.requests == []


def test_tras_la_pregunta_el_pedido_sigue_hasta_crearse(engine: Any) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _registered(service)
    _say(service, f"{NAME}, {EMAIL}")
    runner.events = scripted_events(final())
    _ask(service, QUESTION)

    # Act
    placed = _say(service, "sí")

    # Assert
    assert "**Pedido S00001 confirmado**" in placed
    (request,) = orders.requests
    assert request.buyer == Buyer(NAME, EMAIL)


def test_otro_producto_en_datos_pedido_sale_del_pedido_y_lo_cotiza_el_agente(
    engine: Any,
) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    _registered(service)
    _say(service, "sí")

    # Act
    answer = _ask(service, "quiero 500 vasos")

    # Assert
    assert len(runner.calls) == 2
    assert "↩️" not in answer
    draft = _stored_draft(engine)
    assert draft.awaiting not in ORDER_AWAITING
    assert draft.folio is None and draft.state != "registrada"
    assert orders.requests == []


def test_la_oferta_respondida_con_los_datos_no_pasa_por_el_agente_ni_por_jev(
    engine: Any,
) -> None:
    # Arrange
    judge = RecordingJudge()
    service, runner = _service(engine, FakeOrders(), judge)
    _registered(service)
    before = len(judge.calls)

    # Act
    answer = _say(service, f"{NAME}, {EMAIL}")

    # Assert
    assert answer == render.confirm_buyer(NAME, EMAIL).text
    assert len(runner.calls) == 1  # solo la cotización
    assert len(judge.calls) == before
    assert _stored_draft(engine).awaiting == "confirmar_pedido"


def test_jev_no_se_consulta_en_ningun_paso_del_pedido_en_el_servicio(engine: Any) -> None:
    # Arrange
    judge = RecordingJudge()
    orders = FakeOrders()
    service, _ = _service(engine, orders, judge)
    _registered(service)

    # Act: respuestas cortas que Jev clasificaría en cualquier otro paso.
    for message in ("ok gracias", "ok gracias", f"{NAME}, {EMAIL}", "pues bueno", "Sí, dale"):
        _say(service, message)

    # Assert
    assert all(awaiting not in ORDER_AWAITING for awaiting, _ in judge.calls)
    (request,) = orders.requests
    assert request.buyer == Buyer(NAME, EMAIL)


def test_el_nombre_y_el_correo_no_se_guardan_ni_se_monitorean_ni_vuelven_al_modelo(
    engine: Any,
) -> None:
    # Arrange
    tracer = FakeTracer()
    orders = FakeOrders()
    service, runner = _service(engine, orders, tracer=tracer)
    _registered(service)
    _say(service, "sí")

    # Act
    with capture_logs() as logs:
        only_name = _say(service, NAME)
        confirming = _say(service, EMAIL)
        placed = _say(service, "sí")
    runner.events = scripted_events(final())
    _ask(service, "¿Qué otros productos tienen?")

    # Assert: la persona ve sus datos en el turno…
    assert NAME in only_name
    assert NAME in confirming and EMAIL in confirming
    assert EMAIL in placed
    (request,) = orders.requests
    assert request.buyer == Buyer(NAME, EMAIL)
    # …pero no quedan en la base (mensajes, borrador ni cotizaciones)…
    rows = stored_messages(engine)
    contents = [str(row["content"]) for row in rows]
    # El nombre, el correo y el «sí» que los confirma: todo el paso se guarda sin datos.
    assert contents.count(ORDER_DATA_PLACEHOLDER) == 3
    assert any(MASKED in content for content in contents)
    stored = _all_rows(engine)
    assert NAME not in stored and EMAIL not in stored
    # …ni en el monitoreo…
    traced = repr(tracer.calls) + repr([(t.metadata, t.outputs) for t in tracer.turns])
    assert NAME not in traced and EMAIL not in traced
    # …ni en la semilla del siguiente turno del modelo…
    seed = repr(runner.calls[-1]["seed_turns"])
    assert MASKED in seed
    assert NAME not in seed and EMAIL not in seed
    # …ni en los logs.
    assert NAME not in repr(logs) and EMAIL not in repr(logs)


# --- cierre: bordes de la verificación final --------------------------------------------

CONFIRMED = Buyer("Ana Pérez", "ana@gmail.com")


@pytest.mark.parametrize(
    ("message", "name", "email"),
    [
        ("quiero cambiar el correo a carla@gmail.com", "Ana Pérez", "carla@gmail.com"),
        ("sí, pero cambia el nombre a Carla Ruiz", "Carla Ruiz", "ana@gmail.com"),
        ("no, carla ruiz", "Carla Ruiz", "ana@gmail.com"),
        ("no es Ana es Carla Ruiz", "Carla Ruiz", "ana@gmail.com"),
        ("corrige el nombre: Carla Ruiz", "Carla Ruiz", "ana@gmail.com"),
    ],
)
def test_en_la_confirmacion_las_correcciones_se_aplican_sin_salir_del_paso(
    message: str, name: str, email: str
) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", CONFIRMED), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None and decision.private
    assert decision.draft.awaiting == "confirmar_pedido"
    assert Buyer.from_json(decision.draft.buyer) == Buyer(name, email)


@pytest.mark.parametrize("message", ["sí, pero cambia el correo", "No", "el correo está mal"])
def test_en_la_confirmacion_un_cambio_sin_dato_pregunta_que_corregir(message: str) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", CONFIRMED), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert "¿Qué dato corrijo?" in decision.message.text
    assert Buyer.from_json(decision.draft.buyer) == CONFIRMED


@pytest.mark.parametrize("awaiting", ["pedido", "datos_pedido", "confirmar_pedido"])
@pytest.mark.parametrize("message", ["chao", "gracias, adiós"])
def test_despedirse_deja_la_cotizacion_sin_pedido(awaiting: str, message: str) -> None:
    # Arrange
    buyer = CONFIRMED if awaiting == "confirmar_pedido" else None

    # Act
    decision = _before(_draft(awaiting, buyer), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.draft.awaiting is None and decision.draft.buyer is None
    assert "sin pedido" in decision.message.text


def test_en_la_oferta_el_nombre_antes_de_la_compra_se_toma() -> None:
    # Act
    decision = _before(_draft("pedido"), "Ana Pérez, quiero hacer el pedido")

    # Assert
    assert isinstance(decision, flow.Reply) and decision.private
    assert Buyer.from_json(decision.draft.buyer).name == "Ana Pérez"


def test_el_correo_leido_en_voz_no_se_guarda() -> None:
    # Arrange
    from apps.api.chat.service import _without_buyer
    from carvajal.quotation import render

    message = render.confirm_buyer("Ana Pérez", "ana.perez22@gmail.com")

    # Act
    stored = _without_buyer(message.spoken, [Buyer("Ana Pérez", "ana.perez22@gmail.com")])

    # Assert
    assert "perez22" not in stored and "Ana Pérez" not in stored


@pytest.mark.parametrize("message", ["ok, chao", "sí, chao", "listo, gracias, adiós", "nos vemos"])
def test_un_adios_en_la_confirmacion_nunca_crea_el_pedido(message: str) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", CONFIRMED), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None


@pytest.mark.parametrize("message", ["no sé", "no, mañana", "no, tranquilo"])
def test_tras_un_no_una_palabra_suelta_no_cambia_el_nombre(message: str) -> None:
    # Act
    decision = _before(_draft("confirmar_pedido", CONFIRMED), message)

    # Assert
    assert isinstance(decision, flow.Reply)
    assert decision.order is None
    assert Buyer.from_json(decision.draft.buyer) == CONFIRMED
