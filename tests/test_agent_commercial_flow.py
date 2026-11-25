"""Pruebas del nodo ``commercial``: secuencia fija y prompt acotado (spec 036, T019-T020)."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from carvajal.agent.commercial_flow import (
    COMMERCIAL_INSTRUCTIONS,
    CUSTOM_INSTRUCTIONS,
    commercial_sources,
    custom_need_result,
    custom_sources,
    mixed_notice,
    run_commercial,
)
from carvajal.agent.commercial_intent import COMMERCIAL_KEYS
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import load_catalog
from carvajal.domain.entities import Chunk
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
    assert resultado["channel"]["cities_with_distributors"] == ["Bogotá", "Medellín"]


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


def test_el_precio_convertido_se_cita_con_su_procedencia_y_no_como_publicado() -> None:
    # Arrange: una opción con un precio convertido (``sistema``) y uno oficial.
    oferta = {"source_name": "Carvajal Empaques México", "captured_at": "2026-11-25"}
    resultado = {
        "options": [
            {
                "sku": "CO-MX-2864108000",
                "name": "Vasos térmicos 8oz",
                "material": "EPS",
                "units_per_box": 1000,
                "captured_at": "2026-11-25",
                "source_url": "https://ejemplo.test/mx/vaso-8",
                "offers": [
                    {**oferta, "source_type": "sistema", "source_url": "https://ejemplo.test/tasa"},
                    {**oferta, "source_type": "oficial", "source_url": "https://ejemplo.test/of"},
                ],
            }
        ]
    }

    # Act
    ficha, precio = commercial_sources(resultado)

    # Assert: el oficial ya lo cubre la ficha; el convertido se cita y dice que lo es.
    assert ficha["source_type"] == "oficial"
    assert precio["source_type"] == "sistema"
    assert precio["url"] == "https://ejemplo.test/tasa"
    assert "convertido desde México a COP, no publicado en Colombia" in precio["excerpt"]


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


def _grafo() -> Any:
    falso = _SinRecuperacion()
    return build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings()),
        checkpointer=InMemorySaver(),
    )


def _invocar(pregunta: str, hilo: str) -> dict[str, Any]:
    return _grafo().invoke(
        {"question": pregunta, "owner_key": "dueño", "thread_id": hilo},
        config={"configurable": {"thread_id": hilo}},
    )


def test_la_necesidad_a_la_medida_entrega_solo_canales_oficiales() -> None:
    resultado = custom_need_result(catalog=FIXTURES)

    assert resultado["options"] == []
    assert {contacto["country"] for contacto in resultado["contacts"]} == {"MX", "CO"}
    assert resultado["contacts"][0]["url"] == "https://ejemplo.test/"
    assert [cita["url"] for cita in custom_sources(resultado)] == [
        "https://ejemplo.test/",
        "https://ejemplo.test/co/directorio",
    ]


def test_la_tienda_mx_no_se_desvia_por_una_ficha_de_otro_dominio() -> None:
    intrusa = FIXTURES.references[0].model_copy(
        update={"source_url": "https://marketplace.test/ficha/vaso"}
    )
    catalogo = replace(FIXTURES, references=(intrusa, *FIXTURES.references))

    resultado = custom_need_result(catalog=catalogo)

    assert resultado["contacts"][0]["url"] == "https://ejemplo.test/"


def test_el_grafo_deriva_la_necesidad_a_la_medida_sin_referencias() -> None:
    estado = _invocar("quiero vasos personalizados con mi logo", "hilo-medida")

    assert estado["route"] == "commercial"
    assert estado["tool_result"]["custom_need"] is True
    assert estado["tool_result"]["options"] == []
    assert CUSTOM_INSTRUCTIONS in estado["prompt"]
    assert COMMERCIAL_INSTRUCTIONS not in estado["prompt"]


def test_el_grafo_avisa_la_parte_no_comercial_de_una_pregunta_mixta() -> None:
    estado = _invocar("Necesito vasos para café de 12 oz. ¿Quién fundó Carvajal?", "hilo-mixto")

    assert estado["intent"] == "commercial_mixed"
    assert estado["tool_result"]["options"]
    assert mixed_notice("Quién fundó Carvajal") in estado["prompt"]


class _Embedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 768


class _Documentos:
    def __init__(self, hits: Sequence[ScoredChunk]) -> None:
        self.hits = list(hits)
        self.calls = 0

    def upsert(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("El grafo no indexa.")

    def search(self, *args: Any, **kwargs: Any) -> Sequence[ScoredChunk]:
        self.calls += 1
        return self.hits

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("El grafo no borra.")


HIT = ScoredChunk(
    chunk=Chunk(
        id="historia:3",
        document_id="historia",
        text="Carvajal fue fundada en Cali en 1904.",
        position=3,
        section="historia",
        layer="core",
    ),
    score=0.91,
    metadata={
        "url": "https://www.carvajal.com/historia/",
        "title": "Historia",
        "source_type": "oficial",
        "content_hash": "abc",
    },
)


def test_el_turno_mixto_cita_la_parte_documental_y_la_comercial() -> None:
    documentos = _Documentos([HIT])
    grafo = build_agent(
        AgentDeps(
            vector_store=documentos,
            embedder=_Embedder(),
            settings=Settings(),
            catalog=FIXTURES,
            today=HOY,
        ),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {
            "question": "Necesito vasos para café de 12 oz. ¿Quién fundó Carvajal?",
            "owner_key": "dueño",
            "thread_id": "hilo-mixto-citas",
        },
        config={"configurable": {"thread_id": "hilo-mixto-citas"}},
    )

    assert estado["route"] == "commercial"
    assert estado["intent"] == "commercial_mixed"
    assert documentos.calls == 1
    assert any(source["slug"] == "historia" for source in estado["sources"])
    assert "Contexto documental para la parte no comercial" in estado["prompt"]
    assert "Carvajal fue fundada en Cali en 1904." in estado["prompt"]
    assert mixed_notice("Quién fundó Carvajal") in estado["prompt"]


def test_el_grafo_enriquece_el_turno_co_con_el_contrato_publicado() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings(), today=HOY),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {
            "question": "cotiza 300 vasos para bebida caliente en Bogotá",
            "owner_key": "dueño",
            "thread_id": "hilo-contrato",
        },
        config={"configurable": {"thread_id": "hilo-contrato"}},
    )

    resultado = estado["tool_result"]
    assert resultado["ficha"]["sku"] == "7704918100199"
    assert resultado["offers"][0]["seller"] == "EDEXA"
    assert any(sede["linked"] for sede in resultado["sites"])
    assert resultado["quote"]["kind"] == "cotizacion"
    assert resultado["quote"]["primary"]["presentation"]["units_bought"] == 300
    assert any(cita["slug"].startswith("sede-") for cita in estado["sources"])
    assert estado["commercial_context"]["city"] == "Bogotá"
    assert estado["commercial_context"]["quantity"] == 300


def test_el_contexto_comercial_del_hilo_actualiza_la_cantidad() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings(), today=HOY),
        checkpointer=InMemorySaver(),
    )
    primera = "cotiza 300 vasos para bebida caliente en Bogotá"
    grafo.invoke(
        {"question": primera, "owner_key": "dueño", "thread_id": "hilo-contexto"},
        config={"configurable": {"thread_id": "hilo-contexto"}},
    )

    estado = grafo.invoke(
        {
            "question": "mejor 100",
            "owner_key": "dueño",
            "thread_id": "hilo-contexto",
            "seed_turns": [{"question": primera, "answer": "ok", "incomplete": False}],
        },
        config={"configurable": {"thread_id": "hilo-contexto"}},
    )

    assert estado["route"] == "commercial"
    assert estado["commercial_context"]["quantity"] == 100
    assert estado["tool_result"]["quote"]["primary"]["presentation"]["units_requested"] == 100


def test_el_turno_co_conserva_las_lineas_pendientes() -> None:
    falso = _SinRecuperacion()
    grafo = build_agent(
        AgentDeps(vector_store=falso, embedder=falso, settings=Settings(), today=HOY),
        checkpointer=InMemorySaver(),
    )

    estado = grafo.invoke(
        {
            "question": "cotiza 20 vasos y 10 platos para comida caliente en Bogotá",
            "owner_key": "dueño",
            "thread_id": "hilo-lineas",
        },
        config={"configurable": {"thread_id": "hilo-lineas"}},
    )

    assert estado["tool_result"]["quote"]["pending_lines"][0]["family"] == "plato"
    assert estado["tool_result"]["clarifications"][0]["status"] == "pendiente"
