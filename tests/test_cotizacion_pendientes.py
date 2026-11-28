"""Productos pendientes en una cotización con varios productos (prueba del 2026-11-27).

«500 vasos de 9 oz y 300 tapas para esos vasos en Bogotá» sin la temperatura del vaso: el
vaso no se cotizaba ni se guardaba, la tapa entraba sola en el turno siguiente («agrega 200
contenedores») y «eso es todo» registraba una cotización solo con tapas.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.test_flujos_cotizacion import fake_requote, make_quote

from apps.api.chat.service import _apply_flow
from carvajal.agent.followup import edited_line_number
from carvajal.quotation import render
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


# --- Fallos menores de la misma prueba -------------------------------------------------------


def test_cambia_la_2_con_un_solo_producto_muestra_el_carrito() -> None:
    # Arrange: un producto en el carrito con «¿Algo más?».
    draft = after_turn(QuoteDraft(), [_container()], "200 contenedores", now=AHORA).draft

    # Act
    reply = before_turn(draft, "cambia la 2", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert reply.message.text.startswith("Tu cotización no tiene un producto 2: tiene 1 producto.")
    assert "1. Contenedor bisagra" in reply.message.text
    assert reply.draft.awaiting == "algo_mas"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("cambia la 2", 2),
        ("quita el producto 3", 3),
        ("quiero cambiar la 2 a 800", 2),
        ("borra la línea 4", 4),
        ("cambia el vaso por el de 9 oz", None),
        ("cambia la 9 oz por la de 12", None),
        ("cambia la cantidad a 2 mil", None),
        ("agrega 2 cajas", None),
    ],
)
def test_el_numero_de_linea_de_un_cambio(texto: str, esperado: int | None) -> None:
    # Act / Assert
    assert edited_line_number(texto) == esperado


def test_las_opciones_con_precio_de_referencia_lo_dicen() -> None:
    # Arrange: «cotiza 2000 cubiertos en Bogotá»: precio del catálogo convertido.
    spec = {
        "sku": "CUB",
        "nombre": "Tenedores de plástico PS",
        "unidades": 2000,
        "subtotal": 152434,
        "familia": "cubierto",
        "alternativas": 1,
    }
    quote = make_quote(spec, city="Bogotá")
    quote["primary"]["price_kind"] = "convertido"
    quote["alternatives"][0]["price_kind"] = "convertido"

    # Act
    message = render.options(quote)

    # Assert
    assert f"📍 {render.REFERENCE_PRICE}" in message.text
    assert "alternativa 1 · $153.434 (precio de referencia)" in message.text
    assert "precio de referencia del catálogo" in message.spoken


def test_las_opciones_con_precio_publicado_no_dicen_referencia() -> None:
    # Arrange
    spec = {"sku": "V", "nombre": "Vaso", "unidades": 20, "subtotal": 3751, "alternativas": 1}
    quote = make_quote(spec, city="Bogotá")
    quote["primary"]["price_kind"] = "publicado"

    # Act
    message = render.options(quote)

    # Assert
    assert "referencia" not in message.text.lower()


# --- Revisión de la PR #296 ------------------------------------------------------------------


def test_un_producto_repetido_en_el_turno_queda_una_sola_vez() -> None:
    # Act: el modelo repite ``cotizar`` del vaso en el mismo turno.
    draft = after_turn(
        QuoteDraft(),
        [_lid()],
        "500 vasos de 9 oz y 300 tapas para esos vasos",
        now=AHORA,
        asks=True,
        pending=[VASO_PENDIENTE, {**VASO_PENDIENTE, "quantity": 600}],
    ).draft

    # Assert
    assert [item["label"] for item in draft.pending] == ["600 vasos de 9 oz"]


def test_si_el_turno_trae_otra_tapa_no_entra_tambien_la_que_esperaba() -> None:
    # Arrange: espera la tapa T9; el turno del vaso trae otra tapa (otro SKU) que le sirve.
    draft = _primer_turno()
    otra = _lid("Tapa para vaso de papel 9 oz blanca")
    otra["primary"]["sku"] = "T9B"

    # Act
    outcome = after_turn(draft, [_cup(), otra], "caliente", now=AHORA)

    # Assert
    tapas = [line for line in outcome.draft.lines if line_label(line).startswith("Tapa")]
    assert [line_label(line) for line in tapas] == ["Tapa para vaso de papel 9 oz blanca"]
    assert outcome.draft.held == ()


def test_el_recordatorio_sigue_tras_guardar_y_leer_el_borrador() -> None:
    # Arrange: el servicio guarda el borrador en la base entre turnos.
    draft = QuoteDraft.from_json(_primer_turno().to_json())

    # Act
    outcome = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    )

    # Assert
    assert outcome.message is not None
    assert "**500 vasos de 9 oz**" in outcome.message.text


def test_eso_es_todo_con_pendientes_limpia_lo_ultimo_mostrado() -> None:
    # Arrange: un borrador con pendientes y opciones a la vista, sin pregunta del flujo.
    draft = _primer_turno()
    draft = after_turn(draft, [_container()], "agrega 200 contenedores", now=AHORA).draft
    draft = replace(draft, awaiting=None, options=(_container(),), current={"family": "vaso"})
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply)

    # Act
    reply = before_turn(asked.draft, "eso es todo", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert asked.draft.options == () and asked.draft.current is None
    assert reply.draft.awaiting == "confirmar" and reply.draft.choices == ()


def test_liberar_las_tapas_no_vuelve_a_preguntar_lo_ya_preguntado() -> None:
    # Arrange: vaso y contenedor pendientes, tapa en espera; «eso es todo» ya preguntó.
    draft = _primer_turno()
    draft = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    ).draft
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply) and asked.draft.pending_asked

    # Act: se cotiza el vaso (salen las tapas); el contenedor sigue pendiente.
    outcome = after_turn(asked.draft, [_cup()], "caliente", now=AHORA)

    # Assert
    assert outcome.draft.held == ()
    assert [item["family"] for item in outcome.draft.pending] == ["contenedor"]
    assert outcome.draft.pending_asked


def test_cambia_la_2_a_800_con_dos_productos_cambia_la_cantidad() -> None:
    # Arrange
    draft = after_turn(QuoteDraft(), [_container(), _cup()], "contenedores y vasos", now=AHORA)
    assert len(draft.draft.lines) == 2

    # Act
    reply = before_turn(draft.draft, "cambia la 2 a 800", now=AHORA, requote_fn=fake_requote)

    # Assert
    assert isinstance(reply, Reply)
    assert "no tiene un producto" not in reply.message.text
    assert "800" in reply.message.text


# --- Segunda corrida de la prueba contra producción ------------------------------------------


def test_cambia_la_2_sin_carrito_y_con_pendientes_pide_lo_que_falta() -> None:
    # Arrange: vaso y contenedor esperando su dato, nada en el carrito.
    draft = _primer_turno()
    draft = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    ).draft

    # Act
    reply = before_turn(draft, "cambia la 2", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert reply.message.text.startswith("Tu cotización todavía no tiene un producto 2")
    assert "**500 vasos de 9 oz** son para bebida caliente o fría" in reply.message.text


def test_si_suelto_tras_antes_de_cerrar_vuelve_a_pedir_el_dato() -> None:
    # Arrange
    draft = _primer_turno()
    draft = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    ).draft
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply)

    # Act
    reply = before_turn(asked.draft, "sí", now=AHORA)

    # Assert
    assert isinstance(reply, Reply)
    assert not reply.register
    assert reply.message.text.startswith("Perfecto. Dime si los **500 vasos de 9 oz**")
    assert "¿Qué producto agregamos?" not in reply.message.text
    assert reply.draft.pending == asked.draft.pending


def test_la_tapa_de_otra_linea_no_entra_mientras_se_eligen_los_vasos() -> None:
    # Arrange: «cotiza 20 vasos…» deja opciones de vasos Wau («¿Cuál te cotizo?»).
    vasos = _cup()
    vasos["primary"]["name"] = "Vaso rígido traslúcido 9 oz Wau"
    vasos["alternatives"] = [
        {"sku": "V14", "name": "Vaso rígido traslúcido 14 oz Wau", "capacity_oz": 14.0}
    ]
    draft = after_turn(QuoteDraft(), [vasos], "cotiza 20 vasos", now=AHORA).draft
    assert draft.awaiting == "eleccion"
    tapa = _lid("Tapa viajera 9 a 12 oz para vaso Bioform")
    tapa["request"]["capacity_oz"] = 9.0

    # Act: «agrega 100 tapas».
    outcome = after_turn(draft, [tapa], "agrega 100 tapas", now=AHORA)

    # Assert
    assert outcome.draft.lines == ()
    assert outcome.message is not None
    assert outcome.message.text.startswith("No agregué **Tapa viajera 9 a 12 oz")


@pytest.mark.parametrize("texto", ["listo", "ok", "dale"])
def test_listo_tras_antes_de_cerrar_no_vuelve_a_pedir_el_dato(texto: str) -> None:
    # Arrange: revisión de la PR #297: «listo» u «ok» no se toman como un «sí» que repite
    # la pregunta (podrían querer cerrar).
    draft = _primer_turno()
    draft = after_turn(
        draft, [], "agrega 200 contenedores", now=AHORA, asks=True, pending=[CONTENEDOR_PENDIENTE]
    ).draft
    asked = before_turn(draft, "eso es todo", now=AHORA)
    assert isinstance(asked, Reply)

    # Act
    reply = before_turn(asked.draft, texto, now=AHORA)

    # Assert
    assert not (isinstance(reply, Reply) and reply.message.text.startswith("Perfecto. Dime"))
