"""De la foto a la cotización en el servicio de chat (spec 053, US1-US4).

Servicio real sobre SQLite con un runner del modo ``tools`` guionado: la elección («la 2»)
llega al agente resuelta a su referencia, una elección fuera de rango responde sin el agente,
la cotización queda en el borrador (spec 054) y se registra al cerrarla con un «sí», y la
miniatura de la foto queda con la pregunta.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_chat_service_tools import (
    FakeToolRunner,
    assistant_rows,
    build_service,
    commercial_final,
    final,
    named,
    quote_payload,
    scripted_events,
    stored_messages,
    stored_quotes,
)

from apps.api.chat.service import ChatService
from apps.api.chat.streaming import ChatEvent
from carvajal.adapters.storage import tables
from carvajal.food.profile import FoodProfile

OWNER = "dueno-foto-cotiza"
SOPA = FoodProfile(0.98, "sopas", 0.95, None, "liquido", 0.9, "caliente", 0.95, 2.0, 0.8)
THUMB = "data:image/jpeg;base64,/9j/AAAA"


@pytest.fixture()
def engine() -> Iterator[Any]:
    """SQLite en memoria con el esquema real (como ``tests/test_chat_service_tools.py``)."""

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


def _photo_turn(service: ChatService, owner: str = OWNER) -> list[dict[str, Any]]:
    """Turno de foto (sin LLM) y sus opciones guardadas: recomendada + alternativas."""

    events = list(
        service.photo_stream(owner, "", analyze=lambda: SOPA, charge=lambda: None, thumbnail=THUMB)
    )
    assert named(events, "done")
    return list(_last_result(service, owner)["options"])


def _last_result(service: ChatService, owner: str) -> dict[str, Any]:
    message = list(service.history(owner))[-1]
    return next(s["result"] for s in message.sources or [] if s["result_type"] == "commercial")


def _send(service: ChatService, text: str, owner: str = OWNER) -> list[ChatEvent]:
    return list(service.stream(owner, text))


def test_la_foto_guarda_la_miniatura_con_la_pregunta_y_numera_las_opciones(engine: Any) -> None:
    # Arrange
    service = build_service(engine, FakeToolRunner(scripted_events()))

    # Act
    options = _photo_turn(service)

    # Assert
    question = next(row for row in stored_messages(engine) if row["role"] == "user")
    assert question["sources"] == [{"result_type": "photo", "thumbnail": THUMB}]
    assert [option["choice"] for option in options] == ["recomendada", 1, 2, 3, 4]
    assert _last_result(service, OWNER)["origin"] == "foto"


def test_la_eleccion_llega_al_agente_resuelta_a_su_referencia(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)
    options = _photo_turn(service)

    # Act
    _send(service, "vamos con la 2, son 500 para Cali")

    # Assert: el agente lee la referencia; el hilo guarda lo que escribió la persona.
    chosen = options[2]
    assert runner.calls[-1]["question"] == (
        "vamos con la 2, son 500 para Cali\n\n(Elegí la alternativa 2 de la foto: "
        f"{chosen['name']}, ref. {chosen['sku']}. Cotízala.)"
    )
    assert list(service.history(OWNER))[-2].content == "vamos con la 2, son 500 para Cali"


def test_dame_esa_elige_la_recomendada(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)
    options = _photo_turn(service)

    # Act
    _send(service, "dame esa")

    # Assert
    assert f"Elegí la recomendada de la foto: {options[0]['name']}" in runner.calls[-1]["question"]


def test_una_alternativa_que_no_existe_responde_sin_el_agente(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)
    options = _photo_turn(service)

    # Act
    events = _send(service, "la 9")

    # Assert
    assert runner.calls == []
    text = named(events, "fallback").data["text"]
    assert text.startswith("No tengo la alternativa 9.")
    assert f"**Alternativa 4**: {options[4]['name']}" in text
    assert named(events, "trace").data["route"] == "eleccion"


def test_otra_lista_de_productos_reemplaza_la_de_la_foto(engine: Any) -> None:
    # Arrange: después de la foto, el agente mostró otra lista (``buscar_producto``).
    listed = final(products={"request": {}, "options": [{"sku": "X-1", "name": "Vaso"}]})
    runner = FakeToolRunner(scripted_events(listed))
    service = build_service(engine, runner)
    _photo_turn(service)
    _send(service, "muéstrame vasos")

    # Act
    _send(service, "la 2")

    # Assert: «la 2» ya no se resuelve contra la foto.
    assert runner.calls[-1]["question"] == "la 2"


def test_si_sin_propuesta_pendiente_lo_atiende_el_agente(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)

    # Act
    _send(service, "sí")

    # Assert
    assert runner.calls[-1]["question"] == "sí" and stored_quotes(engine) == []


def test_un_si_con_cambios_no_registra_la_propuesta(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    events = _send(service, "sí pero mejor 800")

    # Assert: otra cantidad para la línea, por reglas (ensayo del 2026-11-26); nada se
    # registra (FR-012) y el agente no vuelve a cotizar.
    assert runner.calls[-1]["question"] == "cotiza 50 vasos en Bogotá"
    assert "Actualicé" in "".join(
        str(event.data.get("text", "")) for event in events if event.name in {"token", "fallback"}
    )
    assert stored_quotes(engine) == []


def test_la_linea_vencida_se_recalcula_en_lugar_de_registrarse(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: la vigencia comercial de la línea ya pasó y el motor no la puede recotizar.
    from carvajal.quotation import flow

    monkeypatch.setattr(flow, "requote", lambda *args, **kwargs: None)
    quote = quote_payload()
    quote["commercial"] = {**quote["commercial"], "valid_until": "2020-01-31"}
    stale = final(products={"quote": quote, "quotes": [quote]}, quotes=(quote,))
    runner = FakeToolRunner(scripted_events(stale))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    _send(service, "No, confírmala")

    # Assert: el agente recotiza; nada se registra.
    assert runner.calls[-1]["question"] == "Vuelve a cotizar Vaso rígido: su precio venció."
    assert stored_quotes(engine) == []


def test_los_botones_llevan_del_carrito_al_resumen_y_la_registrada_celebra(engine: Any) -> None:
    # Arrange
    service = build_service(engine, FakeToolRunner(scripted_events(commercial_final())))

    # Act
    added = _send(service, "cotiza 50 vasos en Bogotá")
    summary = _send(service, "Eso es todo")
    confirmed = _send(service, "Sí, confirma la cotización")

    # Assert: cada turno ofrece los botones de su pregunta; nada se registra antes del «sí».
    html = named(added, "products").data["html"]
    assert 'data-flujo="algo_mas"' in html and 'data-pregunta="Eso es todo"' in html
    assert "¡Cotización lista!" not in html
    resumen = named(summary, "fallback").data["text"]
    assert resumen.startswith("🧾 **Resumen de tu cotización**")
    assert 'data-pregunta="Sí, confirma la cotización"' in named(summary, "products").data["html"]
    folio = named(confirmed, "done").data["quote_id"]
    registered = named(confirmed, "products").data["html"]
    assert "¡Cotización lista!" in registered and f"#{folio}" in registered
    assert f"✅ **Cotización #{folio} registrada**" in named(confirmed, "fallback").data["text"]
    assert len(assistant_rows(engine)) == 3


def test_con_una_sola_opcion_la_primera_es_esa(engine: Any) -> None:
    # Arrange: un turno de foto con solo la recomendada.
    runner = FakeToolRunner(scripted_events())
    service = build_service(engine, runner)
    options = _photo_turn(service)
    message = list(service.history(OWNER))[-1]
    single = {"result_type": "commercial", "result": {"origin": "foto", "options": options[:1]}}
    with engine.begin() as connection:
        connection.execute(
            tables.messages.update()
            .where(tables.messages.c.id == message.id)
            .values(sources=[single])
        )

    # Act
    _send(service, "la primera")

    # Assert
    assert f"Elegí la recomendada de la foto: {options[0]['name']}" in runner.calls[-1]["question"]


def test_un_segundo_si_no_vuelve_a_cotizar_ni_emite_otro_folio(engine: Any) -> None:
    # Arrange
    runner = FakeToolRunner(scripted_events(commercial_final()))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")
    folio = named(_send(service, "No, confírmala"), "done").data["quote_id"]

    # Act
    again = _send(service, "sí")

    # Assert
    assert len(runner.calls) == 1
    assert named(again, "fallback").data["text"] == (
        f"Esa cotización ya quedó registrada con el folio **#{folio}**. ¿Quieres cotizar algo más?"
    )
    assert [row["id"] for row in stored_quotes(engine)] == [folio]


def test_la_vigencia_se_compara_con_el_dia_de_bogota(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: 21:00 en Bogotá (ya es el día siguiente en UTC); la propuesta vale hasta hoy.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import apps.api.chat.service as service_module

    night = datetime(2026, 11, 25, 21, 0, tzinfo=ZoneInfo("America/Bogota"))
    monkeypatch.setattr(service_module, "local_now", lambda: night)
    quote = quote_payload()
    quote["commercial"] = {**quote["commercial"], "valid_until": "2026-11-25"}
    proposal = final(products={"quote": quote, "quotes": [quote]}, quotes=(quote,))
    runner = FakeToolRunner(scripted_events(proposal))
    service = build_service(engine, runner)
    _send(service, "cotiza 50 vasos en Bogotá")

    # Act
    confirmed = _send(service, "No, confírmala")

    # Assert: sigue vigente; se registra sin recalcular.
    assert len(runner.calls) == 1
    assert named(confirmed, "done").data["quote_id"]
