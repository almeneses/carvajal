"""Pruebas del nodo ``commercial``: secuencia fija y prompt acotado (spec 036, T019-T020)."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from carvajal.agent.commercial_flow import (
    COMMERCIAL_INSTRUCTIONS,
    commercial_sources,
    run_commercial,
)
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.catalog.loader import load_catalog
from carvajal.infrastructure.settings import Settings

FIXTURES = load_catalog(Path(__file__).parent / "fixtures" / "catalog")
HOY = date(2026, 11, 1)


def _pedido(**datos: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict.fromkeys(COMMERCIAL_KEYS)
    base["prefers_renewable"] = False
    base.update(datos)
    return base


def _correr(**datos: Any) -> dict[str, Any]:
    return run_commercial(_pedido(**datos), catalog=FIXTURES, today=HOY)


def test_bebida_caliente_en_mexico_calcula_cajas_y_total() -> None:
    resultado = _correr(
        use="bebida_caliente",
        temperature="caliente",
        family="vaso",
        capacity_oz=12.0,
        quantity=5000,
        country="MX",
    )

    [opcion] = resultado["options"]
    assert opcion["sku"] == "FX-VASO-PAPEL-12"
    assert (opcion["boxes"], opcion["surplus_units"]) == (5, 0)
    assert opcion["total"] == "7250.00 MXN"
    assert "no confirma existencias" in opcion["price_notice"]
    assert resultado["channel"]["type"] == "tienda_en_linea"
    assert resultado["pending"] == []


def test_lo_caliente_descarta_el_pet_con_su_motivo() -> None:
    resultado = _correr(use="bebida_caliente", temperature="caliente", family="vaso")

    assert "FX-VASO-PET-16" in {item["sku"] for item in resultado["discarded"]}
    assert all(opcion["material"] != "PET" for opcion in resultado["options"])


def test_sin_pais_no_muestra_precio_y_lo_pide() -> None:
    resultado = _correr(use="bebida_caliente", temperature="caliente", family="vaso", quantity=10)

    assert resultado["options"]
    assert all("total" not in opcion for opcion in resultado["options"])
    assert any(pendiente.startswith("país") for pendiente in resultado["pending"])


def test_sin_cantidad_muestra_el_precio_por_caja() -> None:
    resultado = _correr(use="bebida_caliente", temperature="caliente", family="vaso", country="MX")

    assert resultado["options"][0]["price_per_box"] == "1450.00 MXN"
    assert any(pendiente.startswith("cantidad") for pendiente in resultado["pending"])


def test_colombia_remite_a_los_distribuidores_de_la_ciudad() -> None:
    resultado = _correr(use="bebida_fria", temperature="fria", country="CO", city="Medellín")

    canal = resultado["channel"]
    assert canal["type"] == "distribuidores"
    assert {item["name"] for item in canal["distributors"]} == {
        "Distribuidora Ejemplo Dos",
        "Distribuidora Ejemplo Tres",
    }
    assert all("price_per_box" not in opcion for opcion in resultado["options"])


def test_una_ciudad_sin_distribuidores_lista_las_que_si_tienen() -> None:
    resultado = _correr(use="bebida_fria", temperature="fria", country="CO", city="Tuluá")

    assert resultado["channel"]["distributors"] == []
    assert resultado["channel"]["cities_with_distributors"] == ["Bogotá", "Medellín"]


def test_colombia_sin_ciudad_la_pide() -> None:
    resultado = _correr(use="bebida_fria", temperature="fria", country="CO")

    assert any(pendiente.startswith("ciudad") for pendiente in resultado["pending"])


def test_sin_referencias_del_pais_avisa_que_falta_catalogo() -> None:
    solo_mexico = replace(
        FIXTURES, references=tuple(ref for ref in FIXTURES.references if ref.country == "MX")
    )

    resultado = run_commercial(
        _pedido(use="bebida_fria", temperature="fria", country="CO"),
        catalog=solo_mexico,
        today=HOY,
    )

    assert "CO" not in solo_mexico.countries
    assert "todavía no tiene referencias de CO" in resultado["catalog_note"]


def test_ninguna_opcion_valida_no_ofrece_alternativas() -> None:
    resultado = _correr(
        use="sopas", temperature="caliente", microwave=True, family="contenedor", country="MX"
    )

    assert resultado["options"] == []
    assert resultado["discarded_total"] == 1
    assert commercial_sources(resultado) == []


def test_preferencia_renovable_informa_si_hay_opciones_renovables() -> None:
    resultado = _correr(
        use="comida_caliente",
        temperature="caliente",
        family="contenedor",
        prefers_renewable=True,
    )

    assert resultado["renewable_available"] is False


def test_cada_opcion_cita_su_ficha_con_fecha() -> None:
    resultado = _correr(use="bebida_caliente", temperature="caliente", family="vaso")

    [cita] = commercial_sources(resultado)
    assert cita["url"] == "https://ejemplo.test/mx/vaso-papel-12"
    assert "2026-10-23" in cita["excerpt"]


class _SinRecuperacion:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("La ruta comercial no consulta Qdrant.")

    def upsert(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("La ruta comercial no indexa.")

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("La ruta comercial no consulta Qdrant.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("La ruta comercial no borra.")


def test_el_grafo_enruta_a_commercial_con_el_catalogo_real() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings()),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {
            "question": "Necesito 5.000 vasos de 12 oz para café en México",
            "owner_key": "dueño",
            "thread_id": "hilo-comercial",
        },
        config={"configurable": {"thread_id": "hilo-comercial"}},
    )

    assert estado["route"] == "commercial"
    assert estado["tool_name"] == "catalogo_empaques"
    opciones = estado["tool_result"]["options"]
    assert opciones and all(opcion["boxes"] == 5 for opcion in opciones)
    assert len(estado["sources"]) == len(opciones)
    assert COMMERCIAL_INSTRUCTIONS in estado["prompt"]
    assert opciones[0]["total"] in estado["prompt"]


def test_el_nodo_commercial_usa_el_catalogo_y_la_fecha_inyectados() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(
            vector_store=falso,
            embedder=falso,
            settings=Settings(),
            catalog=FIXTURES,
            today=HOY,
        ),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {
            "question": "Necesito 5.000 vasos de 12 oz para café en México",
            "owner_key": "dueño",
            "thread_id": "hilo-inyectado",
        },
        config={"configurable": {"thread_id": "hilo-inyectado"}},
    )

    [opcion] = estado["tool_result"]["options"]
    assert opcion["sku"] == "FX-VASO-PAPEL-12"
    assert opcion["total"] == "7250.00 MXN"


def test_la_aclaracion_comercial_no_ejecuta_la_secuencia() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings()),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {"question": "busco vasos de 16 oz", "owner_key": "dueño", "thread_id": "hilo-2"},
        config={"configurable": {"thread_id": "hilo-2"}},
    )

    assert estado["route"] == "direct"
    assert estado["tool_result"] == {}
    assert "caliente, frío o a temperatura ambiente" in estado["prompt"]
