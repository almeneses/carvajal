"""Carga y criterio de la compuerta de afirmaciones (spec 046, T031; contrato §3).

``build_claims_query`` arma la evidencia mínima con el mismo criterio que la compuerta
determinista y la respuesta sin datos de la persona; la pregunta no viaja. ``judgeable``
reproduce la tabla *Cuándo se juzga*.
"""

import json
from typing import Any

import pytest

from carvajal.agent.gatekeeper.claims import (
    PERSON_EXTRACT,
    build_claims_query,
    draft_extract,
    judgeable,
    only_questions,
    said_before,
    said_by_person,
)
from carvajal.agent.gatekeeper.privacy import FOLIO_MARK
from carvajal.agent.memory.sensitive import REDACTED
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.draft import QuoteDraft

QUESTION = "Cotízame 500 vasos de 9 oz para café en Cali, mi celular es 3001234567"
PRODUCT = (
    "buscar_producto",
    {
        "status": "ok",
        "message": "Se muestran 2 opciones para vaso en Cali.",
        "aviso": "La búsqueda por nombre «x» no encontró nada.",
        "opciones": [{"n": 1, "sku": "VC-9", "nombre": "Vaso cartón 9 oz", "por_caja": 1000}],
    },
)
CORPUS = (
    "buscar_corpus",
    {
        "status": "ok",
        "fragmentos": [
            {"n": 3, "texto": "Carvajal fue fundada en Cali en 1904."},
            {"n": 4, "texto": "Tiene presencia en varios países."},
        ],
    },
)


def _evidence(query: Any) -> dict[str, str]:
    return dict(query.evidence)


def test_la_evidencia_usa_los_datos_sin_eco_ni_declaraciones_ni_memoria_ni_errores() -> None:
    # Arrange
    payloads = [
        PRODUCT,
        ("sin_consulta", {"status": "ok", "motivo": "pedir_dato", "dato": "ciudad"}),
        ("listar_memoria", {"status": "ok", "memoria": {"nombre": "Ana"}}),
        ("buscar_sedes", {"status": "error", "message": "falló"}),
    ]

    # Act
    query = build_claims_query("El vaso VC-9 viene por 1000 [1].", QUESTION, payloads)

    # Assert
    evidence = _evidence(query)
    assert set(evidence) == {PERSON_EXTRACT, "buscar_producto"}
    data = json.loads(evidence["buscar_producto"])
    assert "message" not in data and "aviso" not in data
    assert data["opciones"][0]["sku"] == "VC-9"


def test_del_corpus_solo_los_fragmentos_citados() -> None:
    cited = build_claims_query("Se fundó en 1904 [3].", "¿Cuándo se fundó?", [CORPUS])
    uncited = build_claims_query("Se fundó en 1904.", "¿Cuándo se fundó?", [CORPUS])

    assert _evidence(cited)["buscar_corpus"] == "[3] Carvajal fue fundada en Cali en 1904."
    assert "[4]" in _evidence(uncited)["buscar_corpus"]


def test_el_tope_se_reparte_con_puntos_suspensivos() -> None:
    # Arrange
    long_corpus = ("buscar_corpus", {"status": "ok", "fragmentos": [{"n": 1, "texto": "a" * 5000}]})
    settings = Settings(_env_file=None, jev_max_evidence_chars=1000)

    # Act
    query = build_claims_query("Texto [1].", "¿algo?", [long_corpus, PRODUCT], settings=settings)

    # Assert
    total = sum(len(text) for _, text in query.evidence)
    assert total <= 1000
    assert _evidence(query)["buscar_corpus"].endswith("…")
    assert not _evidence(query)["buscar_producto"].endswith("…")


def test_la_pregunta_no_viaja_y_si_lo_dicho_por_la_persona() -> None:
    # Act
    query = build_claims_query("El vaso VC-9 [1].", QUESTION, [PRODUCT])

    # Assert
    said = _evidence(query)[PERSON_EXTRACT]
    assert said == "cantidad: 500; familia: vaso; capacidad: 9 oz; uso: bebida_caliente; " + (
        "temperatura: caliente; ciudad: Cali"
    )
    everything = json.dumps([query.answer, *query.evidence], ensure_ascii=False)
    assert QUESTION not in everything
    assert "3001234567" not in everything


def test_lo_dicho_en_mensajes_anteriores_viaja_tras_lo_del_turno() -> None:
    # Arrange: pedido del dato que repite lo dicho dos turnos antes (evidencia §10.3).
    earlier = ["Cotízame 1000 vasos de 14 oz en Cali", "para llevar"]
    answer = "Falta la temperatura. Con eso te cotizo los 1000 vasos de 14 oz en Cali."

    # Act
    query = build_claims_query(answer, "¿cuánto sale?", [PRODUCT], earlier_questions=earlier)

    # Assert
    said = _evidence(query)[PERSON_EXTRACT]
    assert said == "en mensajes anteriores: cantidad: 1000; familia: vaso; capacidad: 14 oz; " + (
        "ciudad: Cali"
    )
    assert "Cotízame" not in json.dumps(query.evidence, ensure_ascii=False)


def test_said_before_solo_lleva_el_ultimo_valor_de_cada_campo() -> None:
    """Un valor corregido no respalda nada: tras «mejor 800» el 500 ya no viaja."""

    # Act
    found = said_before(
        ["500 vasos en Cali", "mejor 800 vasos en Cali"], current={"ciudad": "Cali"}
    )

    # Assert
    assert found == {"cantidad": "800", "familia": "vaso"}


def test_said_before_el_turno_reemplaza_la_cantidad_del_mismo_producto() -> None:
    # Act: el turno dice «800» sin familia; continúa los vasos de antes.
    found = said_before(["500 vasos en Cali"], current={"cantidad": "800"})

    # Assert
    assert found == {"familia": "vaso", "ciudad": "Cali"}


def test_said_before_conserva_la_cantidad_de_cada_producto() -> None:
    # Act: «300 tapas» no corrige los 500 vasos: son dos productos.
    found = said_before(["500 vasos en Cali", "300 tapas"])

    # Assert
    assert found == {"cantidad": "500 / 300", "familia": "vaso / tapa", "ciudad": "Cali"}


def test_draft_extract_trae_lineas_y_referencias_por_elegir_sin_folio() -> None:
    # Arrange
    line = {
        "primary": {
            "name": "Vaso PP 9 oz",
            "presentation": {"units_requested": 500, "subtotal": "52000.00"},
            "provider": {"city": "Cali", "phone": "6025551234"},
        },
        "folio": "CV-7F3A9",
    }
    draft = QuoteDraft(
        lines=(line,),
        choices=({"sku": "T1", "name": "Tapa 9 oz"}, {"sku": "T2", "name": "Tapa Gold"}),
        choice_request={"quantity": 300, "city": "Cali"},
    )

    # Act
    text = draft_extract(draft)

    # Assert
    assert text.splitlines() == [
        "1. Vaso PP 9 oz · 500 und · Cali · subtotal 52000 COP",
        "referencias por elegir: Tapa 9 oz, Tapa Gold; cantidad: 300; ciudad: Cali",
    ]
    assert "CV-7F3A9" not in text and "555" not in text


def test_un_borrador_cancelado_o_ausente_no_respalda_nada() -> None:
    line = {"primary": {"name": "Vaso PP 9 oz"}}

    assert draft_extract(QuoteDraft(lines=(line,), state="cancelada")) == ""
    assert draft_extract(None) == ""
    assert draft_extract(QuoteDraft()) == ""


def test_sin_valores_estructurados_no_hay_extracto_de_la_persona() -> None:
    query = build_claims_query("Se fundó en 1904 [3].", "¿Quién fundó Carvajal?", [CORPUS])

    assert PERSON_EXTRACT not in _evidence(query)
    assert said_by_person("¿Quién fundó Carvajal?") == {}


def test_el_indicador_y_el_periodo_son_dichos_por_la_persona() -> None:
    said = said_by_person("¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?")

    assert said == {"indicador": "ingresos_consolidados", "periodo": "2024"}


def test_la_respuesta_conserva_el_telefono_del_directorio_y_redacta_el_de_la_persona() -> None:
    # Arrange
    answer = "Llama a la sede al (601) 555 1234; también te escribo al 300 123 4567."

    # Act
    query = build_claims_query(answer, QUESTION, [PRODUCT])

    # Assert
    assert "(601) 555 1234" in query.answer
    assert "300 123 4567" not in query.answer
    assert REDACTED in query.answer


def test_los_folios_viajan_como_marcador_en_respuesta_y_evidencia() -> None:
    # Arrange
    saved = (
        "consultar_cotizacion",
        {"status": "ok", "cotizaciones": [{"folio": "CV-7F3A9", "total": "120000 COP"}]},
    )

    # Act
    query = build_claims_query(
        "Tu cotización CV-7F3A9 suma 120000 COP [1].", "¿y mi cotización?", [saved]
    )

    # Assert
    assert "CV-7F3A9" not in query.answer
    assert FOLIO_MARK in query.answer
    assert "CV-7F3A9" not in _evidence(query)["consultar_cotizacion"]
    assert FOLIO_MARK in _evidence(query)["consultar_cotizacion"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"shortcut": True, "source": "reglas"}, (False, "atajo")),
        ({"shortcut": True, "source": "jev"}, (False, "directa")),
        ({"outcome": "abstain"}, (False, "abstencion")),
        ({"outcome": "replace"}, (False, "reemplazo")),
        ({"results": [{"tool": "listar_memoria"}]}, (False, "memoria")),
        ({"results": [{"tool": "buscar_producto"}]}, (True, "")),
    ],
)
def test_judgeable_reproduce_la_tabla_del_contrato(
    kwargs: dict[str, Any], expected: tuple[bool, str]
) -> None:
    assert judgeable("El vaso VC-9 viene por 1000.", **kwargs) == expected


def test_judgeable_solo_preguntas_y_demasiado_larga() -> None:
    assert judgeable("¿En qué ciudad? ¿Cuántas unidades?") == (False, "solo_preguntas")
    assert judgeable("a" * 8001) == (False, "demasiado_larga")
    assert judgeable("El vaso cuesta $500, ¿te lo cotizo?") == (True, "")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿En qué ciudad lo necesitas?", True),
        ("¿En qué ciudad? ¿Y cuántas unidades?", True),
        ("Tengo dos opciones. ¿Cuál prefieres?", False),
        ("", False),
        ("¿Cuál prefieres: la de 9 oz o la de 12 oz?", True),
        ("Te lo cotizo?", True),
        # Afirmación y pregunta en la misma oración: se juzga.
        ("El vaso cuesta $500, ¿te lo cotizo?", False),
        (
            "Carvajal fue fundada en 1904 [1] y es la más usada para sopas, "
            "¿te ayudo con algo más?",
            False,
        ),
        ("El vaso cuesta $500, te lo cotizo?", False),
    ],
)
def test_only_questions(text: str, expected: bool) -> None:
    assert only_questions(text) is expected
