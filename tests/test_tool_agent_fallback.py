"""Pruebas del respaldo determinista sin modelo de lenguaje (spec 044, T041, FR-009, R8).

Los resultados salen del camino de reglas con el catálogo real (``run_commercial``) o con
el catálogo de pruebas (``tests/fixtures/catalog``); nada usa red.
"""

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.commercial_flow import commercial_sources, run_commercial
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.agent.router import decide_route
from carvajal.agent.toolagent.fallback import (
    FALLBACK_NOTICE,
    RETRY_INVITATION,
    TIMEOUT_NOTE,
    render,
    timeout_note,
)
from carvajal.application.ask import Source
from carvajal.catalog.loader import load_catalog

FIXTURES = load_catalog(Path(__file__).parent / "fixtures" / "catalog")
HOY_FIXTURES = date(2026, 11, 1)
HOY_COTIZADOR = date(2026, 11, 27)


def _rules(question: str) -> dict[str, Any]:
    """Resultado comercial del camino de reglas sobre el catálogo real."""

    decision = decide_route(question, None)
    return run_commercial(
        decision.extra, today=HOY_COTIZADOR, question=question, questions=[question]
    )


def _pedido(**datos: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict.fromkeys(COMMERCIAL_KEYS)
    base["prefers_renewable"] = False
    base.update(datos)
    return base


def _fragmento(n: int, *, excerpt: str = "", title: str = "") -> Source:
    return Source(
        chunk_id=f"c{n}",
        slug=f"pagina-{n}",
        section="historia",
        layer="core",
        score=0.9,
        excerpt=excerpt or f"Extracto número {n} del corpus.",
        url=f"https://www.carvajal.com/{n}",
        title=title or f"Página {n}",
    )


# --- cotización -------------------------------------------------------------------------


def test_cotizacion_con_envio_sin_precio_declara_total_pendiente() -> None:
    # Arrange
    resultado = _rules("cotiza 300 vasos para bebida caliente en Bogotá con domicilio")
    coti = resultado["quote"]
    assert coti["kind"] == "cotizacion" and coti["total"] is None
    presentacion = coti["primary"]["presentation"]

    # Act
    texto = render(resultado, commercial_sources(resultado))

    # Assert
    assert texto.startswith(FALLBACK_NOTICE)
    assert "Cotización en Bogotá" in texto
    assert coti["primary"]["name"] in texto
    assert f"{presentacion['basis']} de {presentacion['units']} unidades" in texto
    assert "pides 300" in texto
    assert "Subtotal: 22.506 COP" in texto
    assert "IVA: incluido" in texto
    assert "Envío: por confirmar" in texto
    assert "Total pendiente" in texto
    assert RETRY_INVITATION not in texto


def test_cotizacion_con_total_lo_muestra_y_no_lo_declara_pendiente() -> None:
    # Arrange
    resultado = _rules("Necesito 1000 vasos para bebida caliente en Bogotá")
    assert resultado["quote"]["total"] == "75020"

    # Act
    texto = render(resultado)

    # Assert
    assert "Subtotal: 75.020 COP" in texto
    assert "Total: 75.020 COP" in texto
    assert "Total pendiente" not in texto
    assert "Envío: no aplica" in texto


def test_cotizacion_con_folio_lo_incluye() -> None:
    # Arrange
    resultado = _rules("Necesito 1000 vasos para bebida caliente en Bogotá")
    resultado["quote"] = {**resultado["quote"], "quote": {"id": "COT-1", "folio": "COT-1"}}

    # Act
    texto = render(resultado)

    # Assert
    assert "Folio #COT-1" in texto


# --- opciones ---------------------------------------------------------------------------


def test_solo_opciones_lista_hasta_tres_con_material() -> None:
    # Arrange
    resultado = run_commercial(
        _pedido(use="bebida_caliente", temperature="caliente", family="vaso", country="MX"),
        catalog=FIXTURES,
        today=HOY_FIXTURES,
    )
    opciones = resultado["options"]
    assert opciones and "quote" not in resultado and "sites" not in resultado

    # Act
    texto = render(resultado)

    # Assert
    assert texto.startswith(FALLBACK_NOTICE)
    assert "Opciones del catálogo:" in texto
    for opcion in opciones[:3]:
        assert opcion["name"] in texto
    assert "Papel" in texto
    assert "Cotización" not in texto
    assert "Dónde comprar" not in texto
    assert RETRY_INVITATION not in texto


def test_opciones_se_limitan_a_tres() -> None:
    # Arrange
    base = {"name": "Vaso", "material": "PAPEL"}
    resultado = {"options": [{**base, "name": f"Vaso {n}"} for n in range(1, 6)]}

    # Act
    texto = render(resultado)

    # Assert
    assert "Vaso 3" in texto
    assert "Vaso 4" not in texto


# --- sedes ------------------------------------------------------------------------------


def test_solo_sedes_lista_hasta_tres_con_contacto() -> None:
    # Arrange
    real = _rules("Necesito 500 vasos de 12 oz para tinto en Cali")
    sedes = real["sites"]
    assert len(sedes) > 3
    resultado = {"request": real["request"], "sites": sedes}

    # Act
    texto = render(resultado)

    # Assert
    assert texto.startswith(FALLBACK_NOTICE)
    assert "Dónde comprar en Cali:" in texto
    for sede in sedes[:3]:
        assert sede["name"] in texto
        assert sede["phone"] in texto
    assert sedes[3]["name"] not in texto
    assert "Opciones del catálogo" not in texto


def test_sedes_del_canal_de_distribuidores_cuando_no_hay_contrato() -> None:
    # Arrange
    resultado = {
        "channel": {
            "type": "distribuidores",
            "city": "Cali",
            "distributors": [{"name": "Sin teléfono", "city": "Cali", "address": "Calle 1"}],
        }
    }

    # Act
    texto = render(resultado)

    # Assert
    assert "Sin teléfono: Calle 1" in texto


# --- fragmentos -------------------------------------------------------------------------


def test_solo_fragmentos_cita_hasta_tres_con_titulo() -> None:
    # Arrange
    fuentes = [_fragmento(n) for n in range(1, 5)]

    # Act
    texto = render({}, fuentes)

    # Assert
    assert texto.startswith(FALLBACK_NOTICE)
    assert "[1] Página 1: «Extracto número 1 del corpus.»" in texto
    assert "[3] Página 3" in texto
    assert "[4]" not in texto
    assert RETRY_INVITATION not in texto


def test_fragmentos_conservan_el_numero_de_cita_y_omiten_los_vacios() -> None:
    # Arrange
    vacio = Source("c0", "sin-texto", "s", "core", 0.5, excerpt="   ")
    fuentes = [vacio, {**asdict(_fragmento(2)), "excerpt": "Texto\n  con   saltos."}]

    # Act
    texto = render(None, fuentes)

    # Assert
    assert "[1]" not in texto
    assert "[2] Página 2: «Texto con saltos.»" in texto


def test_fragmento_largo_se_recorta_sin_inventar() -> None:
    # Arrange
    largo = "palabra " * 200

    # Act
    texto = render(None, [_fragmento(1, excerpt=largo)])

    # Assert
    assert "…»" in texto
    assert len(texto) < 600


def test_fragmentos_del_catalogo_no_repiten_las_tarjetas() -> None:
    # Arrange
    resultado = run_commercial(
        _pedido(use="bebida_caliente", temperature="caliente", family="vaso", country="MX"),
        catalog=FIXTURES,
        today=HOY_FIXTURES,
    )

    # Act
    texto = render(resultado, commercial_sources(resultado))

    # Assert
    assert "Fragmentos" not in texto


# --- nada -------------------------------------------------------------------------------


@pytest.mark.parametrize("products", [None, {}, {"options": [], "sites": [], "quote": {}}])
def test_sin_resultados_muestra_aviso_e_invitacion(products: dict[str, Any] | None) -> None:
    # Act
    texto = render(products, [])

    # Assert
    assert texto == f"{FALLBACK_NOTICE}\n\n{RETRY_INVITATION}"


def test_aviso_fijo_de_la_spec() -> None:
    assert FALLBACK_NOTICE == "No pude redactar la respuesta; esto es lo que encontré."


def test_es_determinista() -> None:
    # Arrange
    resultado = _rules("cotiza 300 vasos para bebida caliente en Bogotá con domicilio")
    fuentes = commercial_sources(resultado)

    # Act / Assert
    assert render(resultado, fuentes) == render(resultado, fuentes)


# --- plazo vencido (R9, FR-010) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        (0, TIMEOUT_NOTE),
        (1, f"{TIMEOUT_NOTE} Quedó 1 consulta sin hacer."),
        (3, f"{TIMEOUT_NOTE} Quedaron 3 consultas sin hacer."),
    ],
)
def test_la_nota_del_plazo_declara_lo_que_falto(missing: int, expected: str) -> None:
    # Act / Assert
    assert timeout_note(missing) == expected


def test_la_nota_va_al_final_de_lo_obtenido() -> None:
    # Arrange
    fragmento = _fragmento(1, excerpt="Carvajal se fundó en 1904.", title="Historia")

    # Act
    texto = render({}, [fragmento], note=timeout_note(0))

    # Assert
    assert texto.startswith(FALLBACK_NOTICE)
    assert texto.endswith(f"\n\n{TIMEOUT_NOTE}")
    assert "[1] Historia" in texto


def test_sin_resultados_la_nota_no_sustituye_la_invitacion() -> None:
    # Act
    texto = render({}, [], note=timeout_note(0))

    # Assert
    assert texto == f"{FALLBACK_NOTICE}\n\n{RETRY_INVITATION}\n\n{TIMEOUT_NOTE}"
