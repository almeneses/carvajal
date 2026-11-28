"""Productos pendientes en una cotización con varios productos (prueba del 2026-11-27).

«500 vasos de 9 oz y 300 tapas para esos vasos en Bogotá» sin la temperatura del vaso: el
vaso no se cotizaba ni se guardaba, la tapa entraba sola en el turno siguiente («agrega 200
contenedores») y «eso es todo» registraba una cotización solo con tapas.
"""

from datetime import UTC, datetime
from typing import Any

from tests.test_flujos_cotizacion import make_quote

from apps.api.chat.service import _apply_flow
from carvajal.quotation.draft import QuoteDraft, line_label
from carvajal.quotation.flow import Pass, Reply, after_turn, before_turn

AHORA = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)

VASO_PENDIENTE = {
    "family": "vaso",
    "missing": ["temperatura"],
    "quantity": 500,
    "capacity_oz": 9,
    "city": "Bogotá",
}
CONTENEDOR_PENDIENTE = {"family": "contenedor", "missing": ["temperatura"], "quantity": 200}
PREGUNTA_VASO = "¿Los vasos son para bebida caliente o fría?"


def _cup(capacity: float = 9.0) -> dict[str, Any]:
    spec = {
        "sku": "V9",
        "nombre": f"Vaso de papel {capacity:g} oz",
        "unidades": 500,
        "subtotal": 60000,
    }
    quote = make_quote(spec, city="Bogotá")
    quote["primary"]["capacity_oz"] = capacity
    return quote


def _lid(name: str = "Tapa para vaso de papel 9 oz", origin: str = "") -> dict[str, Any]:
    spec = {"sku": "T9", "nombre": name, "unidades": 300, "subtotal": 30000, "familia": "tapa"}
    quote = make_quote(spec, city="Bogotá")
    quote["reference_origin"] = origin
    return quote


def _container() -> dict[str, Any]:
    spec = {
        "sku": "C1",
        "nombre": "Contenedor bisagra",
        "unidades": 200,
        "subtotal": 50000,
        "familia": "contenedor",
    }
    return make_quote(spec, city="Bogotá")


def _primer_turno() -> QuoteDraft:
    """«500 vasos de 9 oz y 300 tapas para esos vasos en Bogotá»: el vaso pide temperatura."""

    outcome = after_turn(
        QuoteDraft(),
        [_lid()],
        "500 vasos de 9 oz y 300 tapas para esos vasos en Bogotá",
        now=AHORA,
        asks=True,
        pending=[VASO_PENDIENTE],
    )
    return outcome.draft


def test_el_vaso_sin_temperatura_queda_pendiente_y_la_tapa_lo_espera() -> None:
    # Act
    draft = _primer_turno()

    # Assert
    assert draft.lines == ()
    assert [item["family"] for item in draft.pending] == ["vaso"]
    assert draft.pending[0]["label"] == "500 vasos de 9 oz"
    assert [line_label(item) for item in draft.held] == ["Tapa para vaso de papel 9 oz"]
    assert draft.active


def test_otro_producto_no_borra_el_vaso_pendiente_ni_deja_entrar_la_tapa_sola() -> None:
    # Arrange
    draft = _primer_turno()

    # Act: «agrega 200 contenedores»; el modelo vuelve a cotizar la tapa (origen ``hilo``).
    outcome = after_turn(
        draft,
        [_lid(origin="hilo")],
        "agrega 200 contenedores",
        now=AHORA,
        asks=True,
        pending=[CONTENEDOR_PENDIENTE],
    )

    # Assert
    assert outcome.draft.lines == ()
    assert [item["family"] for item in outcome.draft.pending] == ["vaso", "contenedor"]
    assert len(outcome.draft.held) == 1
    assert outcome.message is not None
    assert "**500 vasos de 9 oz** son para bebida caliente o fría" in outcome.message.text
    assert "contenedores" not in outcome.message.text  # ese lo pregunta el agente


def test_al_cotizar_el_vaso_entran_el_vaso_y_su_tapa_medida_contra_el() -> None:
    # Arrange
    draft = _primer_turno()

    # Act: «caliente»: el agente cotiza el vaso.
    outcome = after_turn(draft, [_cup()], "caliente", now=AHORA)

    # Assert
    labels = [line_label(line) for line in outcome.draft.lines]
    assert labels == ["Vaso de papel 9 oz", "Tapa para vaso de papel 9 oz"]
    assert outcome.draft.pending == ()
    assert outcome.draft.held == ()


def test_la_tapa_que_esperaba_y_no_le_sirve_al_vaso_no_entra() -> None:
    # Arrange: la tapa en espera es de 3,5 a 7 oz; el vaso resulta de 9 oz.
    draft = after_turn(
        QuoteDraft(),
        [_lid("Tapa traslúcida para vaso rígido 3,5 a 7 oz Wau", origin="hilo")],
        "500 vasos de 9 oz y 300 tapas para esos vasos",
        now=AHORA,
        asks=True,
        pending=[VASO_PENDIENTE],
    ).draft

    # Act
    outcome = after_turn(draft, [_cup()], "caliente", now=AHORA)

    # Assert
    assert [line_label(line) for line in outcome.draft.lines] == ["Vaso de papel 9 oz"]
    assert outcome.message is not None
    assert "No agregué **Tapa traslúcida" in outcome.message.text


def test_eso_es_todo_con_un_pendiente_pregunta_antes_de_cerrar() -> None:
    # Arrange: el contenedor ya entró; el vaso sigue esperando la temperatura.
    draft = _primer_turno()
    draft = after_turn(draft, [_container()], "agrega 200 contenedores", now=AHORA).draft
    assert draft.awaiting == "algo_mas"

    # Act
    reply = before_turn(draft, "eso es todo", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert not reply.register
    assert reply.draft.awaiting == "algo_mas"
    assert "Antes de cerrar" in reply.message.text
    assert "**500 vasos de 9 oz**" in reply.message.text


def test_el_segundo_eso_es_todo_cierra_sin_el_pendiente_y_lo_dice() -> None:
    # Arrange
    draft = _primer_turno()
    draft = after_turn(draft, [_container()], "agrega 200 contenedores", now=AHORA).draft
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply)

    # Act
    reply = before_turn(asked.draft, "eso es todo", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert not reply.register
    assert reply.draft.awaiting == "confirmar"
    assert reply.draft.pending == () and reply.draft.held == ()
    assert [line_label(line) for line in reply.draft.lines] == ["Contenedor bisagra"]
    assert reply.message.text.startswith("Cerré sin **500 vasos de 9 oz** ni las tapas")


def test_eso_es_todo_y_confirmala_con_un_pendiente_tampoco_registra() -> None:
    # Arrange
    draft = _primer_turno()
    draft = after_turn(draft, [_container()], "agrega 200 contenedores", now=AHORA).draft

    # Act
    reply = before_turn(draft, "eso es todo, confírmala", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert not reply.register
    assert "Antes de cerrar" in reply.message.text


def test_eso_es_todo_sin_carrito_y_con_pendientes_pregunta_en_vez_de_ir_al_agente() -> None:
    # Arrange: tras «agrega 200 contenedores» sin cotización, nada en el carrito.
    draft = _primer_turno()
    draft = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    ).draft
    assert draft.awaiting is None

    # Act
    reply = before_turn(draft, "eso es todo", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert "**500 vasos de 9 oz** son para bebida caliente o fría" in reply.message.text
    assert "**200 contenedores**" in reply.message.text


def test_la_respuesta_al_pendiente_va_al_agente() -> None:
    # Arrange
    draft = _primer_turno()
    draft = after_turn(draft, [_container()], "agrega 200 contenedores", now=AHORA).draft
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply)

    # Act
    result = before_turn(asked.draft, "para bebida caliente", now=AHORA)

    # Assert
    assert isinstance(result, Pass)
    assert result.draft.pending  # se resuelve cuando el agente cotice el vaso


def test_un_solo_producto_sin_su_dato_no_queda_pendiente() -> None:
    # Arrange: «cotiza 20 vasos calientes» sin ciudad: el agente ya pregunta la ciudad.
    ciudad = {"family": "vaso", "missing": ["ciudad"], "quantity": 20}

    # Act
    outcome = after_turn(
        QuoteDraft(), [], "cotiza 20 vasos calientes", now=AHORA, asks=True, pending=[ciudad]
    )

    # Assert
    assert outcome.draft.pending == ()
    assert outcome.message is None


def test_el_borrador_con_pendientes_se_guarda_y_se_recupera() -> None:
    # Arrange
    draft = _primer_turno()

    # Act
    again = QuoteDraft.from_json(draft.to_json())

    # Assert
    assert again.pending == draft.pending
    assert again.held == draft.held


def test_cancelar_todo_borra_los_pendientes() -> None:
    # Arrange
    draft = _primer_turno()

    # Act
    reply = before_turn(draft, "cancela todo", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert reply.draft.pending == () and reply.draft.held == ()


def test_el_servicio_pasa_los_pendientes_al_borrador_y_no_los_guarda_en_el_turno() -> None:
    # Arrange: lo que deja el agente tras el primer turno.
    products = {"quotes": [_lid()], "quote": _lid(), "pending_quotes": [VASO_PENDIENTE]}

    # Act
    result, draft, _suffix = _apply_flow(
        QuoteDraft(),
        products,
        "500 vasos de 9 oz y 300 tapas para esos vasos en Bogotá",
        answer=PREGUNTA_VASO,
    )

    # Assert
    assert [item["family"] for item in draft.pending] == ["vaso"]
    assert len(draft.held) == 1 and draft.lines == ()
    assert result is not None and "pending_quotes" not in result
