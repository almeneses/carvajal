"""Pruebas de contrato de ``comparar_referencias`` (spec 045, T058).

Cubren ``contracts/agent-tools-045.md`` *comparar_referencias*: precio unitario con
``Decimal`` (CO desde la presentación de la oferta publicada, MX desde el precio por caja),
veredicto ``mas_economica`` solo con país, moneda y ``tax_status`` iguales, diferencias sin
veredicto cuando falta precio, ambigüedad → ``needs_input``, ``empty`` con menos de dos
referencias resueltas, y fuentes ``ficha:``/``precio:``/``guia:`` cuyo ``excerpt`` contiene
las cifras devueltas. Sin red: catálogo y contrato comercial versionados del repositorio.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fakes.catalogo import book_sin_proyecciones, catalog_sin_proyecciones

from carvajal.agent.toolagent.tools import comparar_referencias as mod
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.application.ports import ScoredChunk
from carvajal.commercial.book import CommercialBook
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

# Datos reales sin los precios proyectados (2026-11-26): la herramienta se prueba con el
# catálogo de antes; la proyección se prueba en ``test_catalog_projection``.
CATALOG = catalog_sin_proyecciones()
BOOK = book_sin_proyecciones()
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
EDEXA_BOGOTA = "CO-SEDE-9F23E89A16"  # sede de EDEXA que publica los dos contenedores


# --- Dobles --------------------------------------------------------------------------------


class _NoEmbedder:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("comparar_referencias no consulta el índice.")


class _NoStore:
    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("comparar_referencias no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        raise AssertionError("comparar_referencias no consulta el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("comparar_referencias no borra.")


def _deps(book: CommercialBook = BOOK) -> ToolDeps:
    return ToolDeps(
        vector_store=_NoStore(),
        embedder=_NoEmbedder(),
        settings=Settings(_env_file=None),
        catalog=CATALOG,
        book=book,
        now=NOW,
    )


def _run(
    referencias: list[str], pais: str | None = None, *, book: CommercialBook = BOOK
) -> tuple[_Draft, _Sources]:
    sources = _Sources(())
    args = mod.CompararReferenciasArgs.model_validate({"referencias": referencias, "pais": pais})
    draft = mod.run(args, _deps(book), ToolContext(question="¿Qué diferencia hay?"), sources)
    return draft, sources


def _row(draft: _Draft, sku: str) -> dict[str, Any]:
    return next(row for row in draft.data["comparacion"]["filas"] if row["sku"] == sku)


def _source(sources: _Sources, number: int) -> Mapping[str, Any]:
    return next(entry for entry in sources.entries if entry["n"] == number)


def _book_with(sku: str, **changes: Any) -> CommercialBook:
    """Contrato real con la oferta de ``sku`` modificada (sin tocar los archivos)."""

    offers = tuple(
        offer.model_copy(update=changes) if offer.sku == sku else offer for offer in BOOK.offers
    )
    return replace(BOOK, offers=offers)


# --- Caso positivo: ambas con precio de EDEXA ----------------------------------------------


def test_compara_dos_contenedores_con_precio_unitario_decimal_y_veredicto() -> None:
    # Arrange
    referencias = ["CO-CTT-08", "CO-CTT-16"]

    # Act
    draft, _ = _run(referencias, "CO")

    # Assert
    assert draft.status == "ok"
    comparacion = draft.data["comparacion"]
    assert [row["sku"] for row in comparacion["filas"]] == referencias
    ocho, dieciseis = _row(draft, "CO-CTT-08"), _row(draft, "CO-CTT-16")
    # Precio unitario = importe ÷ unidades de la presentación publicada, con Decimal.
    assert Decimal(ocho["precio_unitario"]["valor"]) == Decimal("17642") / Decimal("20")
    assert Decimal(dieciseis["precio_unitario"]["valor"]) == Decimal("20793") / Decimal("20")
    assert ocho["precio_unitario"]["moneda"] == "COP"
    assert ocho["precio_unitario"]["iva"] == "incluido"
    assert comparacion["mas_economica"] == "CO-CTT-08"
    assert comparacion["aviso"] is None
    assert any(line.startswith("Capacidad") for line in comparacion["diferencias"])
    assert any(line.startswith("Precio por unidad") for line in comparacion["diferencias"])
    # Sin totales ni recomendación de uso: comparar no es cotizar.
    assert "total" not in str(draft.data)
    assert draft.quotes is None and not draft.cards


def test_cada_cifra_del_precio_esta_en_el_excerpt_de_su_fuente() -> None:
    # Act
    draft, sources = _run(["CO-CTT-08", "CO-CTT-16"], "CO")

    # Assert
    ocho = _row(draft, "CO-CTT-08")
    precio = _source(sources, ocho["precio_unitario"]["n"])
    assert precio["chunk_id"] == f"precio:CO-CTT-08:{EDEXA_BOGOTA}"
    assert precio["source_type"] == "tercero"
    assert precio["url"].startswith("https://www.tiendaedexa.com/")
    for cifra in ("17642", "COP", "paquete de 20", ocho["precio_unitario"]["valor"], "EDEXA"):
        assert cifra in precio["excerpt"]
    # Sin la ciudad de la sede: no respalda una ciudad que la persona no dijo (FR-013).
    assert "Bogotá" not in precio["excerpt"] and "Bogotá" not in str(draft.data)
    ficha = _source(sources, ocho["n"])
    assert ficha["chunk_id"] == "ficha:CO-CTT-08:CO"
    assert ficha["layer"] == "core"
    for cifra in ("CO-CTT-08", "EPS", "8 oz", "paquete de 20 unidades", "2026-11-18"):
        assert cifra in ficha["excerpt"]
    # La ficha dice «(paquete x 20)» y no declara caja: no se afirma una caja de 20.
    assert "unidades por caja" not in ficha["excerpt"]
    assert sorted(sources.ids) == sorted({entry["n"] for entry in sources.entries})


def test_una_ficha_paquete_x_n_no_inventa_unidades_por_caja() -> None:
    # Arrange: CO-CTT-08 es «(paquete x 20)» sin units_per_pack; ficha_producto no declara caja.
    from carvajal.agent.toolagent.tools import ficha_producto

    ficha_sources = _Sources(())
    ficha = ficha_producto.run(
        ficha_producto.FichaProductoArgs.model_validate({"sku": "CO-CTT-08"}),
        _deps(),
        ToolContext(),
        ficha_sources,
    )

    # Act
    draft, sources = _run(["CO-CTT-08", "CO-CTT-16"], "CO")

    # Assert: comparar y ficha coinciden en lo que la ficha permite afirmar.
    ocho = _row(draft, "CO-CTT-08")
    assert ficha.data["ficha"]["unidades_por_caja"] is None
    assert ocho["unidades_por_caja"] is None
    assert ocho["presentacion_nombre"] == {"presentacion": "paquete", "unidades": 20}
    excerpt = _source(sources, ocho["n"])["excerpt"]
    assert "unidades por caja" not in excerpt
    assert "paquete de 20 unidades según el nombre" in excerpt
    assert not any(
        line.startswith("Unidades por caja") for line in draft.data["comparacion"]["diferencias"]
    )


def test_comparar_tras_la_ficha_no_ensucia_la_fuente_oficial_compartida() -> None:
    # Arrange: el turno pide la ficha y luego compara; las dos citan ficha:CO-CTT-08:CO.
    from carvajal.agent.toolagent.tools import ficha_producto

    sources = _Sources(())
    ficha_producto.run(
        ficha_producto.FichaProductoArgs.model_validate({"sku": "CO-CTT-08"}),
        _deps(),
        ToolContext(),
        sources,
    )
    args = mod.CompararReferenciasArgs.model_validate({"referencias": ["CO-CTT-08", "CO-CTT-16"]})

    # Act
    mod.run(args, _deps(), ToolContext(), sources)

    # Assert
    shared = next(e for e in sources.entries if e["chunk_id"] == "ficha:CO-CTT-08:CO")
    assert "unidades por caja" not in shared["excerpt"]


def test_las_fichas_con_caja_declarada_conservan_sus_unidades_por_caja() -> None:
    # Arrange: una ficha con paquete y paquetes por caja sí declara la caja.
    reference = next(
        item for item in CATALOG.references if item.units_per_pack and item.packs_per_box
    )
    other = next(
        item
        for item in CATALOG.references
        if item.units_per_pack
        and item.packs_per_box
        and item.sku != reference.sku
        and item.country == reference.country
    )

    # Act
    draft, sources = _run([reference.sku, other.sku], reference.country)

    # Assert
    row = _row(draft, reference.sku)
    assert row["unidades_por_caja"] == reference.units_per_box
    assert f"{reference.units_per_box} unidades por caja" in _source(sources, row["n"])["excerpt"]


def test_la_guia_de_materiales_va_rotulada_y_con_su_fuente() -> None:
    # Act
    draft, sources = _run(["CO-CTT-08", "CO-CTT-16"], "CO")

    # Assert: las fichas CO no declaran temperatura; el dato es de la guía (EPS).
    ocho = _row(draft, "CO-CTT-08")
    assert ocho["microondas"] is False
    temperatura = ocho["temperatura_guia"]
    assert (temperatura["min_c"], temperatura["max_c"]) == (-20, 60)
    assert "guía de materiales" in temperatura["rotulo"]
    guia = _source(sources, temperatura["n"])
    assert guia["chunk_id"] == "guia:EPS"
    assert "-20" in guia["excerpt"] and "60" in guia["excerpt"]
    # La guía se registra una vez aunque las dos referencias sean de EPS.
    assert sum(entry["chunk_id"] == "guia:EPS" for entry in sources.entries) == 1


# --- Sin precio: diferencias sin veredicto -------------------------------------------------


def test_vasos_sin_precio_dan_diferencias_sin_veredicto() -> None:
    # Act: por SKU; el nombre también coincide con los vasos térmicos copiados de México.
    draft, sources = _run(["CO-VTER-08", "CO-VTER-10"], "CO")

    # Assert
    assert draft.status == "ok"
    comparacion = draft.data["comparacion"]
    assert [row["sku"] for row in comparacion["filas"]] == ["CO-VTER-08", "CO-VTER-10"]
    assert all(row["precio_unitario"] is None for row in comparacion["filas"])
    assert comparacion["mas_economica"] is None
    assert "precio" in comparacion["aviso"]
    diferencias = " | ".join(comparacion["diferencias"])
    assert "2160" in diferencias and "960" in diferencias
    assert "Precio por unidad" not in diferencias
    assert not any(entry["chunk_id"].startswith("precio:") for entry in sources.entries)


def test_material_sin_regla_en_la_guia_no_inventa_microondas_ni_temperatura() -> None:
    # Act: un vaso de papel (la guía no cubre papel) frente a uno de EPS.
    draft, sources = _run(["CO-VBIO-16", "CO-VTER-08"])

    # Assert
    papel = _row(draft, "CO-VBIO-16")
    assert papel["microondas"] is None
    assert papel["temperatura_guia"] is None
    assert not any(entry["chunk_id"] == "guia:PAPEL" for entry in sources.entries)


# --- Sin veredicto por país, moneda o impuestos --------------------------------------------


def test_distinto_tax_status_no_da_veredicto() -> None:
    # Arrange: la misma oferta de EDEXA declarada sin IVA.
    book = _book_with("CO-CTT-16", tax_status="no_incluido")

    # Act
    draft, _ = _run(["CO-CTT-08", "CO-CTT-16"], "CO", book=book)

    # Assert
    comparacion = draft.data["comparacion"]
    assert _row(draft, "CO-CTT-16")["precio_unitario"]["iva"] == "no_incluido"
    assert comparacion["mas_economica"] is None
    assert "IVA" in comparacion["aviso"]
    assert not any(line.startswith("Precio por unidad") for line in comparacion["diferencias"])


def test_co_frente_a_mx_no_compara_precios() -> None:
    # Act: sin país, un contenedor de Colombia y un vaso de la tienda de México.
    draft, _ = _run(["CO-CTT-08", "2864108000"])

    # Assert
    assert draft.status == "ok"
    comparacion = draft.data["comparacion"]
    mx = _row(draft, "2864108000")
    assert mx["precio_unitario"]["moneda"] == "MXN"
    assert _row(draft, "CO-CTT-08")["precio_unitario"]["moneda"] == "COP"
    assert comparacion["mas_economica"] is None
    assert "país" in comparacion["aviso"]
    assert not any(line.startswith("Precio por unidad") for line in comparacion["diferencias"])


def test_dos_referencias_de_mexico_con_precio_por_caja() -> None:
    # Act
    draft, sources = _run(["2864108000", "2864108880"], "MX")

    # Assert: MX = precio por caja ÷ unidades por caja.
    barato, caro = _row(draft, "2864108000"), _row(draft, "2864108880")
    assert Decimal(barato["precio_unitario"]["valor"]) == Decimal("482.00") / Decimal("1000")
    assert Decimal(caro["precio_unitario"]["valor"]) == Decimal("603.00") / Decimal("1000")
    assert draft.data["comparacion"]["mas_economica"] == "2864108000"
    precio = _source(sources, barato["precio_unitario"]["n"])
    assert precio["chunk_id"] == "precio:2864108000:MX"
    assert precio["source_type"] == "oficial"
    for cifra in ("482.00", "MXN", "1000", barato["precio_unitario"]["valor"]):
        assert cifra in precio["excerpt"]


def test_con_varias_ofertas_usa_la_de_menor_precio_unitario() -> None:
    # Arrange: una segunda oferta, más cara, del mismo contenedor.
    [base] = [offer for offer in BOOK.offers if offer.sku == "CO-CTT-08"]
    cara = base.model_copy(update={"offer_id": "CO-OFERTA-PRUEBA", "amount": Decimal("99999")})
    book = replace(BOOK, offers=(*BOOK.offers, cara))

    # Act
    draft, _ = _run(["CO-CTT-08", "CO-CTT-16"], "CO", book=book)

    # Assert
    precio = _row(draft, "CO-CTT-08")["precio_unitario"]
    assert Decimal(precio["valor"]) == Decimal("17642") / Decimal("20")
    assert precio["ofertas"] == 2


# --- Ambigüedad y referencias no resueltas -------------------------------------------------


def test_una_referencia_ambigua_pide_elegir_sin_registrar_fuentes() -> None:
    # Act: «contenedor térmico 16 oz» nombra dos fichas de Colombia.
    draft, sources = _run(["contenedor térmico 16 oz", "CO-CTT-08"], "CO")

    # Assert
    assert draft.status == "needs_input"
    assert draft.missing == ["referencia"]
    [ambigua] = draft.data["candidatas"]
    assert ambigua["posicion"] == 1
    assert {item["sku"] for item in ambigua["opciones"]} >= {"CO-CTER-16", "CO-CTT-16"}
    assert not sources.entries and not sources.ids
    # El texto que escribió el modelo no vuelve en los datos (no respalda sus cifras).
    assert "contenedor térmico 16 oz" not in str(draft.data)


def test_menos_de_dos_resueltas_da_empty_con_las_no_encontradas() -> None:
    # Act
    draft, sources = _run(["CO-CTT-08", "xyzzy plutonio"], "CO")

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_encontradas"] == [2]
    assert [item["sku"] for item in draft.data["encontradas"]] == ["CO-CTT-08"]
    assert not sources.entries
    assert "xyzzy" not in str(draft.data)


def test_dos_textos_de_la_misma_referencia_no_son_una_comparacion() -> None:
    # Act
    draft, _ = _run(["CO-CTT-08", "co ctt 08"], "CO")

    # Assert
    assert draft.status == "empty"
    assert draft.data["misma_referencia"] == ["CO-CTT-08"]


def test_el_pais_acota_la_busqueda() -> None:
    # Act: un SKU que no existe entre las referencias de Colombia.
    draft, _ = _run(["CO-CTT-08", "9999999999"], "CO")

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_encontradas"] == [2]


# --- Esquema, descripción y línea de estado ------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"referencias": ["CO-CTT-08"]},
        {"referencias": ["a", "b", "c", "d", "e"]},
        {"referencias": ["CO-CTT-08", "   "]},
        {"referencias": ["CO-CTT-08", "x" * 121]},
        {"referencias": ["CO-CTT-08", "CO-CTT-16"], "pais": "AR"},
    ],
)
def test_el_esquema_rechaza_entradas_fuera_de_contrato(payload: dict[str, Any]) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        mod.CompararReferenciasArgs.model_validate(payload)


def test_el_esquema_limpia_espacios_y_no_tiene_campos_de_identidad() -> None:
    # Act
    args = mod.CompararReferenciasArgs.model_validate({"referencias": ["  CO-CTT-08 ", "b"]})
    schema = mod.CompararReferenciasArgs.model_json_schema()

    # Assert
    assert args.referencias == ["CO-CTT-08", "b"]
    assert args.pais is None
    assert set(schema["properties"]) == {"referencias", "pais"}
    assert all(field.get("description") for field in schema["properties"].values())


def test_la_descripcion_sigue_el_patron_sin_casos_concretos() -> None:
    # Arrange
    texto = mod.DESCRIPTION

    # Assert
    assert len(texto) <= 300
    assert "Úsala para" in texto and "No la uses para" in texto
    assert "buscar_producto" in texto and "cotizar" in texto
    assert not re.search(r"\d", texto)
    assert not re.search(r"\bCO-[A-Z0-9-]+", texto)


def test_la_linea_de_estado_no_interpola_valores() -> None:
    # Act
    texto = mod.status({"referencias": ["CO-CTT-08", "CO-CTT-16"], "pais": "CO"})

    # Assert
    assert texto == "Estoy comparando las referencias que me pediste…"
