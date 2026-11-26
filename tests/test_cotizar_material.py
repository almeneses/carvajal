"""El material que pide la persona filtra la cotización («vasos de papel» no cotiza PP).

Reporte del 2026-11-25 por WhatsApp: «Me puedes cotizar 1000 vasos de papel para bebida
caliente en Bogotá» devolvía el vaso rígido de PP de 9 oz de EDEXA, porque el motor no
filtraba por material y ponía primero el precio publicado.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from carvajal.agent.commercial_intent import material_for
from carvajal.agent.toolagent.tools import ToolContext, run_tool
from carvajal.catalog.loader import load_catalog
from carvajal.quotation import QuoteRequest, quote, quote_request

HOY = date(2026, 11, 25)
AHORA = datetime(2026, 11, 25, 9, 0, tzinfo=ZoneInfo("America/Bogota"))
REPORTE = "Me puedes cotizar 1000 vasos de papel para bebida caliente en Bogotá"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("vasos de papel", "papel"),
        ("vasos de cartón", "papel"),
        ("vaso de icopor", "icopor"),
        ("vasos de poliestireno expandido", "icopor"),
        ("contenedor de pulpa", "pulpa"),
        ("vasos plásticos PET", "pet"),
        ("vasos plásticos", "plastico"),
        ("vaso PP", "pp"),
        ("vasos para café", None),
        ("sin plástico por favor", None),
        ("papel o icopor", None),
    ],
)
def test_el_material_se_lee_de_la_pregunta(texto: str, esperado: str | None) -> None:
    assert material_for(texto) == esperado


def test_el_reporte_cotiza_un_vaso_de_papel_y_alternativas_de_papel() -> None:
    # Act
    resultado = quote(REPORTE, today=HOY, now=AHORA)

    # Assert
    assert resultado["kind"] == "cotizacion"
    assert resultado["primary"]["material"] == "PAPEL"
    assert {item["material"] for item in resultado["alternatives"]} <= {"PAPEL"}


def test_quote_request_con_material_nunca_cotiza_otro() -> None:
    for material, permitidos in (("papel", {"PAPEL"}), ("plastico", {"PS", "PP", "PET"})):
        resultado = quote_request(
            QuoteRequest(
                family="vaso",
                use="bebida_caliente" if material == "papel" else "bebida_fria",
                material=material,
                quantity=1000,
                city="Bogotá",
                delivery=False,
            ),
            today=HOY,
            now=AHORA,
        )
        assert resultado["kind"] == "cotizacion", material
        materiales = {resultado["primary"]["material"]} | {
            item["material"] for item in resultado["alternatives"]
        }
        assert materiales <= permitidos, material


def test_material_sin_referencias_aptas_no_cotiza_otro_material() -> None:
    # Arrange: la familia «cubierto» no tiene ninguna referencia de papel.
    assert not any(
        ref.family == "cubierto" and ref.material == "PAPEL" for ref in load_catalog().references
    )

    # Act
    resultado = quote_request(
        QuoteRequest(family="cubierto", material="papel", quantity=100, city="Bogotá"),
        today=HOY,
        now=AHORA,
    )

    # Assert
    assert resultado["kind"] == "sin_resultados"
    assert resultado["reason"] == "material_sin_resultados"
    assert "papel" in resultado["reply"]
    assert "primary" not in resultado


def test_sin_material_dicho_no_se_filtra() -> None:
    resultado = quote_request(
        QuoteRequest(family="vaso", use="bebida_caliente", quantity=1000, city="Bogotá"),
        today=HOY,
        now=AHORA,
    )

    assert resultado["kind"] == "cotizacion"
    assert "material" not in resultado["request"]
    materiales = {resultado["primary"]["material"]} | {
        item["material"] for item in resultado["alternatives"]
    }
    assert len(materiales) > 1  # sin material dicho se ofrecen varios


def test_la_herramienta_toma_el_material_de_la_pregunta_si_el_modelo_lo_omite() -> None:
    # Act: el modelo llama a cotizar sin ``material``, como en el reporte.
    result = run_tool(
        "cotizar",
        {
            "family": "vaso",
            "use": "bebida_caliente",
            "temperature": "caliente",
            "quantity": 1000,
            "city": "Bogotá",
        },
        _deps(),
        ToolContext(question=REPORTE),
    )

    # Assert
    assert result.status == "ok"
    assert result.quote is not None
    assert result.quote["primary"]["material"] == "PAPEL"
    assert result.context_update.get("material") == "papel"


def test_una_referencia_nombrada_no_se_filtra_por_el_material_de_la_pregunta() -> None:
    # Act: «el vaso rígido de 9 oz, no el de papel» nombra una referencia de PP.
    result = run_tool(
        "cotizar",
        {"sku": "7704918100199", "quantity": 100, "city": "Bogotá"},
        _deps(),
        ToolContext(question="cotiza el vaso rígido 7704918100199, no el de papel, en Bogotá"),
    )

    # Assert
    assert result.status == "ok"
    assert result.quote is not None
    assert result.quote["primary"]["sku"] == "7704918100199"


def _deps():
    from tests.test_tool_agent_tools import _deps as deps

    return deps()
