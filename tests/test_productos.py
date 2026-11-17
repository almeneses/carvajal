"""Tarjetas de la ruta comercial: parcial ``_productos.html`` (spec 036, T028-T030)."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from structlog.testing import capture_logs

from apps.api.chat.agent import GraphAgentRunner
from apps.api.chat.products import has_cards, render_products, safe_render_products
from carvajal.agent.commercial_flow import custom_need_result, run_commercial
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.catalog.loader import load_catalog
from carvajal.infrastructure.settings import Settings

FIXTURES = load_catalog(Path(__file__).parent / "fixtures" / "catalog")


def _resultado(**datos: Any) -> dict[str, Any]:
    pedido: dict[str, Any] = dict.fromkeys(COMMERCIAL_KEYS)
    pedido["prefers_renewable"] = False
    pedido.update(datos)
    return run_commercial(pedido, catalog=FIXTURES, today=date(2026, 11, 1))


def test_la_tarjeta_con_precio_muestra_total_fecha_aviso_y_ficha() -> None:
    html = render_products(
        _resultado(
            use="bebida_caliente",
            temperature="caliente",
            family="vaso",
            capacity_oz=12.0,
            quantity=5000,
            country="MX",
        )
    )

    assert "Vaso papel 12 oz bebida caliente" in html
    assert "<code>FX-VASO-PAPEL-12</code>" in html
    assert "<dd>PAPEL</dd>" in html
    assert "<dd>12 oz</dd>" in html
    assert "<dd>1000</dd>" in html  # unidades por caja
    assert "<dd>5</dd>" in html  # cajas
    assert "<strong>7250.00 MXN</strong>" in html
    assert "en total" in html
    assert "no confirma existencias" in html
    assert 'href="https://ejemplo.test/mx/vaso-papel-12"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "capturada el 2026-10-23" in html


def test_sin_cantidad_el_precio_es_por_caja() -> None:
    html = render_products(
        _resultado(use="bebida_caliente", temperature="caliente", family="vaso", country="MX")
    )

    assert "<strong>1450.00 MXN</strong>" in html
    assert "por caja" in html


def test_la_tarjeta_sin_precio_de_colombia_no_estima_y_lista_distribuidores() -> None:
    html = render_products(
        _resultado(use="bebida_fria", temperature="fria", country="CO", city="Medellín")
    )

    assert "<code>FX-VASO-PS-12</code>" in html
    assert "MXN" not in html
    assert "no publica precios" in html
    assert "Distribuidores en Medellín" in html
    assert "Distribuidora Ejemplo Tres" in html
    assert "Carrera 4 # 5-6" in html


def test_sin_pais_la_tarjeta_pide_el_pais_para_cotizar() -> None:
    html = render_products(_resultado(use="bebida_caliente", temperature="caliente", family="vaso"))

    assert "indica el país" in html
    assert "MXN" not in html


def test_una_ciudad_sin_distribuidores_muestra_las_que_si_tienen() -> None:
    html = render_products(
        _resultado(use="bebida_fria", temperature="fria", country="CO", city="Tuluá")
    )

    assert "No hay distribuidores publicados en Tuluá" in html
    assert "Bogotá, Medellín" in html


def test_la_necesidad_a_la_medida_muestra_solo_canales() -> None:
    html = render_products(custom_need_result(catalog=FIXTURES))

    assert 'class="producto"' not in html
    assert 'href="https://ejemplo.test/"' in html
    assert "directorio de distribuidores" in html


def test_el_texto_del_catalogo_se_escapa() -> None:
    resultado = _resultado(use="bebida_caliente", temperature="caliente", family="vaso")
    resultado["options"][0]["name"] = '<script>alert("x")</script>'

    html = render_products(resultado)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_sin_referencias_del_pais_la_nota_es_para_el_cliente() -> None:
    resultado = _resultado(use="bebida_fria", temperature="fria", country="CO", city="Medellín")
    resultado["options"] = []
    resultado["catalog_note"] = "instrucción interna para el LLM"

    html = render_products(resultado)

    assert "instrucción interna" not in html
    assert "El catálogo de Colombia" in html


def test_un_resultado_vacio_no_genera_tarjetas() -> None:
    assert not has_cards({"options": [], "channel": {}, "discarded": [{"sku": "x"}]})
    assert has_cards({"options": [], "contacts": [{"url": "https://ejemplo.test/"}]})


def test_un_parcial_roto_no_tumba_la_respuesta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las tarjetas son opcionales: si el parcial falla, no se emiten y queda el log."""

    resultado = _resultado(use="bebida_caliente", temperature="caliente", country="MX")

    def roto(_: dict[str, Any]) -> str:
        raise RuntimeError("plantilla rota")

    monkeypatch.setattr("apps.api.chat.products.render_products", roto)

    with capture_logs() as logs:
        html = safe_render_products(resultado)

    assert html == ""
    [evento] = [log for log in logs if log["event"] == "chat.products_render_failed"]
    assert "plantilla rota" in evento["error"]


class _SinRecuperacion:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("No se consulta Qdrant.")

    def upsert(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("No se indexa.")

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("No se consulta Qdrant.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("No se borra.")


def _runner() -> GraphAgentRunner:
    falso = _SinRecuperacion()
    settings = Settings()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=settings),
        checkpointer=InMemorySaver(),
    )
    return GraphAgentRunner(grafo, settings)


def test_el_turno_comercial_lleva_el_resultado_para_las_tarjetas() -> None:
    turno = _runner().prepare(
        "dueño", "hilo-tarjetas", "Necesito 5.000 vasos de 12 oz para café en México", []
    )

    assert turno.route == "commercial"
    assert turno.products["options"]
    assert has_cards(turno.products)


def test_un_turno_no_comercial_no_lleva_tarjetas() -> None:
    turno = _runner().prepare("dueño", "hilo-saludo", "hola", [])

    assert turno.products == {}
