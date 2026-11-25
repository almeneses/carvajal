"""Flujos de cotización (spec 054): reglas, borrador, plantillas y guiones multiturno.

Sin tokens: el agente se simula con las cotizaciones de cada guion de
``data/eval/flujos_cotizacion_054.json`` y la recotización con un doble. Cubre los ocho flujos
por texto y por voz (transcripciones), el rechazo con insulto, la salida sin compra, las
preguntas fuera del flujo, el cambio de producto y la segunda capa con Jev.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.followup import (
    is_cancel_all,
    is_confirmation,
    is_leaving,
    is_price_insult,
    is_resume,
    is_too_expensive,
    parse_quantities,
    resolve_more,
    resolve_quantity_choice,
    resolve_remove,
    resolve_switch,
    wants_switch,
)
from carvajal.numbers import spoken_quantities, words_to_number
from carvajal.quotation import render
from carvajal.quotation.draft import QuoteDraft, add_line, draft_quote_payload, totals
from carvajal.quotation.flow import Pass, Reply, after_turn, before_turn
from carvajal.speech import normalize_transcript

GUIONES = Path(__file__).resolve().parents[1] / "data" / "eval" / "flujos_cotizacion_054.json"
AHORA = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)


# --- Dobles ----------------------------------------------------------------------------------


def make_quote(spec: Mapping[str, Any], *, city: str = "Cali") -> dict[str, Any]:
    """Cotización con la forma del motor a partir de un renglón del guion."""

    units = int(spec["unidades"])
    subtotal = int(spec["subtotal"])
    alternatives = []
    for index in range(1, int(spec.get("alternativas", 0)) + 1):
        delta = -1000 * index if spec.get("alternativas_mas_baratas") else 1000 * index
        alternatives.append(
            {
                "sku": f"{spec['sku']}-A{index}",
                "name": f"{spec['nombre']} alternativa {index}",
                "presentation": {
                    "subtotal": str(subtotal + delta),
                    "unit_price": str(round((subtotal + delta) / units, 2)),
                    "units_requested": units,
                },
            }
        )
    return {
        "kind": "cotizacion",
        "city": city,
        "request": {"quantity": units, "family": spec.get("familia", "vaso")},
        "primary": {
            "sku": spec["sku"],
            "name": spec["nombre"],
            "family": spec.get("familia", "vaso"),
            "currency": "COP",
            "seller": "Distribuidora del Valle",
            "units_requested": units,
            "tax": {"status": "incluido", "rate": "0.19"},
            "presentation": {
                "basis": "paquete",
                "units": 50,
                "count": max(units // 50, 1),
                "units_requested": units,
                "subtotal": str(subtotal),
                "unit_price": str(round(subtotal / units, 2)),
            },
        },
        "commercial": {
            "subtotal": str(subtotal),
            "currency": "COP",
            "shipping": {"requested": False, "status": "not_requested"},
            "tax": {"status": "incluido", "rate": "0.19"},
            "valid_until": None,
        },
        "delivery": {"requested": False},
        "alternatives": alternatives,
        "total": str(subtotal),
    }


def fake_requote(
    base: Mapping[str, Any], *, sku: str | None = None, quantity: int | None = None, **_: Any
) -> dict[str, Any] | None:
    """Recotiza con lo que ya trae la cotización base: su alternativa o su cantidad."""

    primary = base["primary"]
    if sku and sku != primary["sku"]:
        found = next((alt for alt in base.get("alternatives") or [] if alt["sku"] == sku), None)
        if found is None:
            return None
        spec = {
            "sku": sku,
            "nombre": found["name"],
            "unidades": quantity or found["presentation"]["units_requested"],
            "subtotal": int(found["presentation"]["subtotal"]),
        }
    else:
        units = quantity or primary["presentation"]["units_requested"]
        spec = {
            "sku": primary["sku"],
            "nombre": primary["name"],
            "unidades": units,
            "subtotal": int(
                int(primary["presentation"]["subtotal"])
                * units
                / max(1, primary["presentation"]["units_requested"])
            ),
        }
    return make_quote(spec, city=str(base.get("city") or "Cali"))


class FakeJudge:
    """Jev simulado: devuelve una clase fija y registra lo que se le envió."""

    def __init__(self, choice: str, confidence: float = 0.9) -> None:
        self.choice = choice
        self.confidence = confidence
        self.calls: list[dict[str, str]] = []

    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]:
        self.calls.append({"awaiting": awaiting, "reply": reply})
        return self.choice, self.confidence


class BrokenJudge:
    def classify_reply(self, *, awaiting: str, reply: str) -> tuple[str, float]:
        raise RuntimeError("Jev caído")


# --- Guiones multiturno ----------------------------------------------------------------------


def _conversations() -> list[dict[str, Any]]:
    return json.loads(GUIONES.read_text(encoding="utf-8"))["conversaciones"]


def _run_turn(
    draft: QuoteDraft, turn: Mapping[str, Any], now: datetime
) -> tuple[QuoteDraft, str, bool, bool, str | None]:
    """Un turno: ``(borrador, texto, fue al agente, registró, prompt)``."""

    decision = before_turn(draft, turn["persona"], requote_fn=fake_requote, now=now)
    if isinstance(decision, Reply):
        return decision.draft, decision.message.text, False, decision.register, None
    quotes = [make_quote(spec) for spec in turn.get("agente") or []]
    question = decision.prompt or turn["persona"]
    outcome = after_turn(decision.draft, quotes, question, now=now)
    text = outcome.message.text if outcome.message else ""
    return outcome.draft, text, True, False, decision.prompt


@pytest.mark.parametrize("guion", _conversations(), ids=lambda guion: guion["id"])
def test_guion_de_cotizacion(guion: dict[str, Any]) -> None:
    # Arrange
    draft = QuoteDraft()
    now = AHORA

    for number, turn in enumerate(guion["turnos"], start=1):
        # Act
        draft, text, to_agent, registered, prompt = _run_turn(draft, turn, now)
        now += timedelta(minutes=1)

        # Assert
        expected = turn["espera"]
        where = f"{guion['id']} turno {number}: {turn['persona']!r}"
        if "al_agente" in expected:
            assert to_agent is expected["al_agente"], where
        if "awaiting" in expected:
            assert draft.awaiting == expected["awaiting"], (where, draft.awaiting, text)
        if "texto" in expected:
            assert expected["texto"] in text, (where, text)
        if "registra" in expected:
            assert registered is expected["registra"], (where, text)
        if "prompt" in expected:
            assert prompt == expected["prompt"], where


def test_el_conjunto_cubre_los_ocho_flujos_por_texto_y_por_voz() -> None:
    conversations = _conversations()
    assert {item["flujo"] for item in conversations} == set(range(1, 9))
    assert {item["canal"] for item in conversations} == {"texto", "voz"}


# --- Reglas para respuestas cortas -------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("100, 500 o 1000 vasos", (100, 500, 1000)),
        ("cien, quinientos y mil", (100, 500, 1000)),
        ("2 mil vasos", (2000,)),
        ("dos mil quinientos platos", (2500,)),
        ("ciento veinte vasos", (120,)),
        ("treinta y cinco", (35,)),
        ("12 oz, 500 unidades", (500,)),
        ("a $5.000 cada paquete, 300", (300,)),
        ("una caja", ()),
    ],
)
def test_las_cantidades_se_leen_en_cifras_y_en_palabras(
    texto: str, esperado: tuple[int, ...]
) -> None:
    assert parse_quantities(texto) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("no", "close"),
        ("No, eso es todo", "close"),
        ("nada más", "close"),
        ("así está bien", "close"),
        ("no, confírmala", "close_confirm"),
        ("genera el PDF", "close_confirm"),
        ("PDF", "close_confirm"),
        ("sí", "add"),
        ("sí, 500 vasos", "add"),
        ("también tapas", "add"),
        ("agregar otro producto", "add"),
        ("¿cuánto llevo?", None),
    ],
)
def test_algo_mas_se_resuelve_sin_el_modelo(texto: str, esperado: str | None) -> None:
    assert resolve_more(texto) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("cancélalos", "cancel"),
        ("Cancelar", "cancel"),
        ("la 1", "cancel"),
        ("consérvalos", "keep"),
        ("agrégalos también", "keep"),
        ("no, déjalos", "keep"),
        ("2", "keep"),
        ("¿qué me recomiendas?", None),
    ],
)
def test_cancelar_o_conservar_el_producto_anterior(texto: str, esperado: str | None) -> None:
    assert resolve_switch(texto) == esperado


def test_detectores_de_salida_retomar_cancelar_y_precio() -> None:
    assert is_leaving("Listo, gracias, lo voy a pensar")
    assert is_leaving("solo estaba mirando")
    assert not is_leaving("¿lo pienso o lo compro?")
    assert is_resume("retomar") and is_resume("sigamos con la cotización")
    assert is_cancel_all("cancela todo") and is_cancel_all("empecemos de nuevo")
    assert is_too_expensive("está caro") and is_too_expensive("muy costoso")
    assert not is_too_expensive("¿cuál es la menos cara?")
    assert wants_switch("No, mejor vasos") and not wants_switch("sí, vasos")


@pytest.mark.parametrize(
    "texto",
    [
        "esta muy caro, muy ladrones",
        "Está caro, ladrones",
        "está carísimo, rateros",
        "caro ladrones",
    ],
)
def test_el_rechazo_con_insulto_se_reconoce(texto: str) -> None:
    assert is_price_insult(texto)


@pytest.mark.parametrize(
    "texto", ["está caro", "¿son ladrones los que venden?", "ladrón de bicicletas"]
)
def test_sin_precio_e_insulto_juntos_no_es_rechazo(texto: str) -> None:
    assert not is_price_insult(texto)


def test_confirmar_acepta_pedir_el_pdf_a_la_vez() -> None:
    assert is_confirmation("sí, mándame el PDF")
    assert is_confirmation("dale, envíamela en PDF")
    assert not is_confirmation("sí pero mejor 800")


def test_eleccion_de_cantidad_del_comparativo() -> None:
    quantities = (100, 500, 1000)
    assert resolve_quantity_choice("con mil", quantities) == 1000
    assert resolve_quantity_choice("la de 500", quantities) == 500
    assert resolve_quantity_choice("la segunda", quantities) == 500
    assert resolve_quantity_choice("la más grande", quantities) == 1000
    assert resolve_quantity_choice("con 700", quantities) is None


def test_quitar_una_linea_por_nombre_o_numero() -> None:
    labels = ("Plato pulpa 9″", "Vaso térmico 12 oz")
    assert resolve_remove("quita los vasos", labels) == 1
    assert resolve_remove("borra la 1", labels) == 0
    assert resolve_remove("elimina la última", labels) == 1
    assert resolve_remove("quita eso", labels) is None


# --- Voz: números dichos con palabras ------------------------------------------------------


def test_las_cantidades_dictadas_pasan_a_cifras_solo_antes_de_un_producto() -> None:
    assert spoken_quantities("cotízame trescientos vasos de doce onzas") == (
        "cotízame 300 vasos de 12 onzas"
    )
    assert spoken_quantities("dos mil quinientos platos") == "2.500 platos"
    assert spoken_quantities("el dos de diciembre") == "el dos de diciembre"
    assert spoken_quantities("un vaso") == "un vaso"
    assert words_to_number("mil quinientos") == 1500
    assert normalize_transcript("quinientos vasos y 1,800 tapas") == "500 vasos y 1.800 tapas"


# --- Borrador y plantillas -------------------------------------------------------------------


def _cart() -> QuoteDraft:
    draft = QuoteDraft()
    draft, _ = add_line(
        draft,
        make_quote({"sku": "PL9", "nombre": "Plato pulpa 9″", "unidades": 300, "subtotal": 75000}),
    )
    draft, _ = add_line(
        draft,
        make_quote(
            {"sku": "VT12", "nombre": "Vaso térmico 12 oz", "unidades": 500, "subtotal": 206000}
        ),
    )
    return draft


def test_el_total_parcial_suma_los_subtotales_y_el_iva_se_declara() -> None:
    result = totals(_cart().lines)
    assert str(result.products) == "281000"
    assert result.total is not None and str(result.total) == "281000"
    assert result.tax == "IVA incl. (19 %)"


def test_el_mismo_producto_actualiza_la_linea_en_lugar_de_duplicarla() -> None:
    draft = _cart()
    draft, action = add_line(
        draft,
        make_quote({"sku": "PL9", "nombre": "Plato pulpa 9″", "unidades": 800, "subtotal": 200000}),
    )
    assert action == "sumada" and len(draft.lines) == 2
    assert str(totals(draft.lines).products) == "406000"


def test_la_cotizacion_registrada_lleva_todas_las_lineas_y_el_total() -> None:
    payload = draft_quote_payload(_cart())
    assert payload["kind"] == "cotizacion" and len(payload["lines"]) == 2
    assert payload["total"] == "281000"
    assert payload["primary"]["sku"] == "PL9"  # lectores de una línea siguen funcionando


def test_las_plantillas_son_cortas_y_cierran_con_una_pregunta() -> None:
    draft = _cart().touch(AHORA, state="carrito", awaiting="algo_mas")
    quote = make_quote(
        {
            "sku": "J1",
            "nombre": "Portacomida J1",
            "unidades": 200,
            "subtotal": 58000,
            "alternativas": 4,
        }
    )
    messages = [
        render.options(quote),
        render.added(draft, [draft.lines[-1]]),
        render.summary(draft),
        render.switch_question("los platos"),
        render.cheaper_offer({"name": "Vaso", "subtotal": "1000", "currency": "COP", "units": 10}),
    ]
    for message in messages:
        lines = message.text.splitlines()
        assert len(lines) <= 14, message.text
        assert lines[-1].endswith(("?", "**.", "o lo nuevo", "lo nuevo")) or "?" in lines[-1]
        assert len(message.spoken.split()) <= 45, message.spoken
        assert "**" not in message.spoken and "🛒" not in message.spoken


def test_la_recomendada_y_cuatro_alternativas_numeradas() -> None:
    quote = make_quote(
        {
            "sku": "J1",
            "nombre": "Portacomida J1",
            "unidades": 200,
            "subtotal": 58000,
            "alternativas": 4,
        }
    )
    text = render.options(quote).text
    assert "⭐ **Recomendada: Portacomida J1** · $58.000" in text
    assert all(f"\n{index}. Portacomida J1 alternativa {index}" in text for index in range(1, 5))
    assert text.endswith("¿Cuál te cotizo? Di **esa** o un número.")


def test_la_broma_del_rechazo_es_la_pedida_y_la_voz_no_lee_el_emoji() -> None:
    message = render.joke()
    assert message.text == "Arrancado, vaya cómprele a su madre entonces 😂"
    assert message.spoken == "Arrancado, vaya cómprele a su madre entonces"


# --- Fuera del flujo, cambio de producto y Jev -----------------------------------------------


def _with_options() -> QuoteDraft:
    quote = make_quote(
        {
            "sku": "J1",
            "nombre": "Portacomida J1",
            "unidades": 200,
            "subtotal": 58000,
            "alternativas": 2,
        }
    )
    return after_turn(QuoteDraft(), [quote], "200 portacomidas en Cali", now=AHORA).draft


def test_una_pregunta_de_otro_tema_recibe_el_recordatorio_dos_veces_como_mucho() -> None:
    draft = _with_options()
    reminders = []
    for _ in range(3):
        decision = before_turn(draft, "¿Quién fundó Carvajal?", requote_fn=fake_requote, now=AHORA)
        assert isinstance(decision, Pass) and decision.prompt is None
        outcome = after_turn(decision.draft, [], "¿Quién fundó Carvajal?", now=AHORA)
        reminders.append(outcome.message)
        draft = outcome.draft
    assert reminders[0] is not None and reminders[0].text.startswith("↩️")
    assert reminders[1] is not None and reminders[2] is None
    assert draft.awaiting == "eleccion"  # la cotización sigue esperando la elección


def test_el_borrador_vencido_no_se_retoma() -> None:
    draft = _cart().touch(AHORA - timedelta(days=8), state="pausada", awaiting=None)
    decision = before_turn(draft, "retomar", requote_fn=fake_requote, now=AHORA)
    assert isinstance(decision, Reply)
    assert decision.message.text == render.empty_cart().text


def test_jev_decide_cuando_las_reglas_no_y_solo_ve_la_respuesta() -> None:
    draft = _cart().touch(AHORA, state="carrito", awaiting="algo_mas")
    judge = FakeJudge("cerrar")
    decision = before_turn(draft, "de una, ciérrala ya mismo", judge=judge, now=AHORA)
    assert isinstance(decision, Reply) and decision.draft.awaiting == "confirmar"
    assert judge.calls == [{"awaiting": "algo_mas", "reply": "de una, ciérrala ya mismo"}]


def test_jev_con_poca_confianza_o_caido_deja_pasar_al_agente() -> None:
    draft = _cart().touch(AHORA, state="carrito", awaiting="algo_mas")
    low = before_turn(draft, "de una, ciérrala ya mismo", judge=FakeJudge("cerrar", 0.4), now=AHORA)
    broken = before_turn(draft, "de una, ciérrala ya mismo", judge=BrokenJudge(), now=AHORA)
    assert isinstance(low, Pass) and isinstance(broken, Pass)


def test_jev_no_se_consulta_sin_pregunta_pendiente_ni_con_preguntas() -> None:
    judge = FakeJudge("confirmar")
    before_turn(QuoteDraft(), "de una", judge=judge, now=AHORA)
    before_turn(_with_options(), "¿y el de cartón aguanta microondas?", judge=judge, now=AHORA)
    assert judge.calls == []


def test_ninguna_cotizacion_se_registra_sin_el_si() -> None:
    draft = _cart().touch(AHORA, state="carrito", awaiting="algo_mas")
    for text in ("sí", "también tapas", "¿cuánto llevo?", "lo voy a pensar", "cancela todo"):
        decision = before_turn(draft, text, requote_fn=fake_requote, now=AHORA)
        assert not (isinstance(decision, Reply) and decision.register), text
