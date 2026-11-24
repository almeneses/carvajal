"""Pruebas de contrato de ``convertir_presentacion`` (spec 045, T060; contrato 10).

La herramienta convierte una cantidad en cajas, paquetes o pacas de una referencia a
unidades con las funciones públicas del motor de cotización (``declared_sizes``,
``conversion`` y ``published_presentations``), sin duplicarlas. Si la ficha y el contrato no
declaran la presentación o se contradicen, devuelve ``empty`` con ``no_convertible``: nunca
supone cuántas unidades trae una presentación.

Sin red ni LLM: catálogo versionado y read model comercial del repositorio, más un
``Dataset`` construido a mano para el caso de tamaños contradictorios.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.tools import convertir_presentacion as mod
from carvajal.agent.toolagent.tools._base import (
    ToolContext,
    ToolDeps,
    _Draft,
    _Sources,
    content_data,
    format_tool_content,
)
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import load_catalog
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings
from carvajal.quotation import Dataset, QuoteRequest, load_dataset, quote_request
from carvajal.quotation.engine import conversion

HOY = date(2026, 11, 24)
CATALOG = load_catalog()
DATASET = load_dataset(HOY.isoformat())
BY_SKU = {reference.sku: reference for reference in CATALOG.references}
MX_CON_PAQUETE = next(
    reference
    for reference in CATALOG.references
    if reference.country == "MX" and reference.units_per_pack and reference.packs_per_box
)
MX_SIN_PAQUETE = next(
    reference
    for reference in CATALOG.references
    if reference.country == "MX" and not reference.units_per_pack
)


# --- Dobles --------------------------------------------------------------------------------


class _NoEmbedder:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("convertir_presentacion no consulta el índice.")


class _NoStore:
    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("Las herramientas no indexan.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        raise AssertionError("convertir_presentacion no consulta el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("Las herramientas no borran.")


def _deps(dataset: Dataset | None = DATASET) -> ToolDeps:
    return ToolDeps(
        vector_store=_NoStore(),
        embedder=_NoEmbedder(),
        settings=Settings(_env_file=None),
        catalog=CATALOG,
        today=HOY,
        dataset=dataset,
    )


def _run(args: dict[str, Any], *, dataset: Dataset | None = DATASET) -> tuple[_Draft, _Sources]:
    sources = _Sources(())
    draft = mod.run(
        mod.ConvertirPresentacionArgs.model_validate(args),
        _deps(dataset),
        ToolContext(question="¿Cuántas unidades son?"),
        sources,
    )
    return draft, sources


def _entry(sources: _Sources, number: int) -> dict[str, Any]:
    return next(entry for entry in sources.entries if entry["n"] == number)


# --- Esquema y descripción -----------------------------------------------------------------


def test_la_descripcion_sigue_el_patron_y_no_nombra_casos_concretos() -> None:
    # Arrange
    texto = mod.DESCRIPTION
    esquema = json.dumps(mod.ConvertirPresentacionArgs.model_json_schema(), ensure_ascii=False)

    # Act
    cifras = re.findall(r"\d", texto)
    skus = re.findall(r"\bCO-[A-Z0-9-]+", texto + esquema)
    codigos = re.findall(r"\b[A-Z]{1,3}\d{1,3}\b", texto + esquema)

    # Assert: patrón «Úsala para… No la uses para… (usa X)», ≤ 300 caracteres (R10).
    assert len(texto) <= 300
    assert "Úsala para" in texto
    assert "No la uses para" in texto
    assert "(usa cotizar)" in texto
    assert not cifras
    assert not skus
    assert not codigos


def test_el_esquema_no_tiene_campos_de_identidad_y_describe_en_espanol() -> None:
    # Arrange
    campos = mod.ConvertirPresentacionArgs.model_fields

    # Act
    nombres = set(campos)

    # Assert
    assert nombres == {"sku", "nombre", "cantidad", "unidad"}
    assert all(campo.description for campo in campos.values())


@pytest.mark.parametrize(
    ("escrita", "esperada"),
    [("cajas", "caja"), ("Paquetes", "paquete"), ("pacas", "paca"), ("unidades", "unidad")],
)
def test_la_unidad_acepta_el_plural(escrita: str, esperada: str) -> None:
    # Arrange
    datos = {"sku": "CO-VTER-04", "cantidad": 3, "unidad": escrita}

    # Act
    args = mod.ConvertirPresentacionArgs.model_validate(datos)

    # Assert
    assert args.unidad == esperada


@pytest.mark.parametrize(
    "datos",
    [
        {"cantidad": 3, "unidad": "caja"},  # sin referencia
        {"sku": "  ", "nombre": "", "cantidad": 3, "unidad": "caja"},  # referencia vacía
        {"sku": "CO-VTER-04", "cantidad": 0, "unidad": "caja"},
        {"sku": "CO-VTER-04", "cantidad": 1_000_001, "unidad": "caja"},
        {"sku": "CO-VTER-04", "cantidad": 3, "unidad": "bulto"},
    ],
)
def test_entradas_invalidas_se_rechazan(datos: dict[str, Any]) -> None:
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        mod.ConvertirPresentacionArgs.model_validate(datos)


def test_la_linea_de_estado_no_interpola_valores() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "nombre": "vaso", "cantidad": 3, "unidad": "caja"}

    # Act
    linea = mod.status(args)

    # Assert
    assert linea == "Estoy calculando cuántas unidades son…"


# --- Conversiones con certeza --------------------------------------------------------------


def test_tres_cajas_del_vaso_termico_cuatro_onzas_son_tres_mil_unidades() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, sources = _run(args)

    # Assert: la ficha declara paquete de 20 y caja de 1000; no hay oferta publicada.
    assert draft.status == "ok"
    conv = draft.data["conversion"]
    assert conv["unidades"] == 3000
    assert conv["unidades_por_presentacion"] == 1000
    assert conv["origen"] == "catalogo"
    assert conv["texto"] == "3 cajas \u00d7 1.000 = 3.000 unidades"
    ficha = _entry(sources, conv["n"])
    assert ficha["chunk_id"] == "ficha:CO-VTER-04:CO"
    assert "1000 unidades por caja" in ficha["excerpt"]
    assert ficha["url"] == BY_SKU["CO-VTER-04"].source_url
    assert sources.ids == [conv["n"]]


def test_la_paca_del_nombre_de_la_ficha_convierte() -> None:
    # Arrange: «Portacomidas espumado P1 Wau (paca x 200)», sin paquete en la ficha.
    args = {"nombre": "portacomidas P1", "cantidad": 2, "unidad": "pacas"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    conv = draft.data["conversion"]
    assert draft.data["sku"] == "CO-PCOM-P1"
    assert conv["unidades"] == 400
    assert conv["origen"] == "catalogo"
    assert "paca x 200" in _entry(sources, conv["n"])["excerpt"]


def test_paquete_declarado_por_ficha_y_contrato_cita_ambas_fuentes() -> None:
    # Arrange: «(paquete x 20)» en el nombre y oferta de EDEXA por paquete de 20.
    args = {"sku": "CO-CTT-08", "cantidad": 3, "unidad": "paquete"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    conv = draft.data["conversion"]
    assert conv["unidades"] == 60
    assert conv["origen"] == "ambos"
    citadas = [_entry(sources, n) for n in conv["fuentes"]]
    precios = [entry for entry in citadas if entry["chunk_id"].startswith("precio:")]
    assert citadas[0]["chunk_id"] == "ficha:CO-CTT-08:CO"
    assert precios
    assert all("paquete de 20 unidades" in entry["excerpt"] for entry in precios)
    assert all(entry["source_type"] in {"oficial", "tercero"} for entry in precios)


def test_mexico_convierte_con_la_ficha_del_catalogo() -> None:
    # Arrange: el read model comercial es solo de Colombia; MX usa la ficha.
    ref = MX_CON_PAQUETE
    args = {"sku": ref.sku, "cantidad": 2, "unidad": "caja"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    conv = draft.data["conversion"]
    assert conv["unidades"] == 2 * ref.units_per_box
    assert conv["origen"] == "catalogo"
    assert draft.data["pais"] == "MX"
    ficha = _entry(sources, conv["n"])
    assert ficha["chunk_id"] == f"ficha:{ref.sku}:MX"
    assert f"{ref.units_per_box} unidades por caja" in ficha["excerpt"]
    assert not [entry for entry in sources.entries if entry["chunk_id"].startswith("precio:")]


def test_mexico_sin_paquete_declarado_no_supone_la_caja() -> None:
    # Arrange: la ficha conoce una sola presentación; como el motor, no se supone que es caja.
    args = {"sku": MX_SIN_PAQUETE.sku, "cantidad": 2, "unidad": "caja"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_convertible"] is True


# --- Sin certeza: no_convertible -----------------------------------------------------------


def test_portacomidas_j1_en_cajas_no_es_convertible() -> None:
    # Arrange: el nombre dice «paca x 200» y la oferta del contrato está rotulada «caja de
    # 200»; ``declared_sizes`` devuelve vacío a propósito.
    args = {"sku": "CO-PCOM-J1", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_convertible"] is True
    assert draft.data["motivo"] == "no_declarado"
    assert draft.data["presentaciones_publicadas"] == ["paca de 200"]
    assert "conversion" not in draft.data
    assert "paca x 200" in _entry(sources, draft.data["n"])["excerpt"]
    # La oferta «caja de 200» no se cita como si respaldara una caja.
    assert not [entry for entry in sources.entries if entry["chunk_id"].startswith("precio:")]


def test_tamanos_contradictorios_no_son_convertibles() -> None:
    # Arrange: ficha con caja de 1000 y una oferta del contrato con caja de 500.
    vaso = DATASET.product("CO-VTER-04")
    assert vaso is not None
    [oferta] = DATASET.offers_for("CO-PCOM-J1")
    contradictoria = replace(oferta, sku="CO-VTER-04", basis="caja", units_in_basis=500)
    dataset = Dataset(products=(vaso,), offers=(contradictoria,), providers=())
    args = {"sku": "CO-VTER-04", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, _ = _run(args, dataset=dataset)

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_convertible"] is True
    assert draft.data["motivo"] == "contradictorio"
    assert "conversion" not in draft.data
    assert "caja de 1000" not in draft.data["presentaciones_declaradas"]


def test_presentacion_no_declarada_lista_las_declaradas() -> None:
    # Arrange: el vaso térmico de 4 oz no declara paca.
    args = {"sku": "CO-VTER-04", "cantidad": 3, "unidad": "paca"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["no_convertible"] is True
    assert draft.data["presentaciones_declaradas"] == ["caja de 1000", "paquete de 20"]
    excerpt = _entry(sources, draft.data["n"])["excerpt"]
    assert "1000 unidades por caja" in excerpt
    assert "20 unidades por paquete" in excerpt


def test_la_cantidad_en_unidades_pide_la_presentacion() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "cantidad": 3000, "unidad": "unidad"}

    # Act
    draft, _ = _run(args)

    # Assert: no hay nada que convertir y la herramienta no divide por su cuenta.
    assert draft.status == "needs_input"
    assert draft.missing == ["unidad"]
    assert "conversion" not in draft.data


# --- Resolución de la referencia -----------------------------------------------------------


def test_nombre_ambiguo_devuelve_candidatas() -> None:
    # Arrange
    args = {"nombre": "vaso térmico", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert draft.missing == ["referencia"]
    candidatas = draft.data["candidatas"]
    assert 1 < len(candidatas) <= mod.MAX_TOOL_OPTIONS
    assert all({"sku", "nombre", "pais"} <= set(item) for item in candidatas)
    assert sources.ids == []


def test_referencia_inexistente_es_empty() -> None:
    # Arrange
    args = {"nombre": "zzzz inexistente", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data == {"referencia_encontrada": False}
    assert sources.ids == []


def test_sku_y_nombre_que_no_coinciden_preguntan_cual() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "nombre": "portacomidas J1", "cantidad": 3, "unidad": "caja"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "needs_input"
    skus = {item["sku"] for item in draft.data["candidatas"]}
    assert {"CO-VTER-04", "CO-PCOM-J1"} <= skus


# --- Paridad con el motor de cotización ----------------------------------------------------


@pytest.mark.parametrize(
    ("sku", "unidad"),
    [
        ("CO-VTER-04", "caja"),
        ("CO-VTER-04", "paquete"),
        ("CO-VTER-04", "paca"),
        ("CO-PCOM-J1", "caja"),
        ("CO-PCOM-J1", "paca"),
        ("CO-CTT-08", "paquete"),
        ("CO-CTT-08", "caja"),
        ("CO-BESP-1", "paca"),
    ],
)
def test_paridad_con_la_conversion_del_motor(sku: str, unidad: str) -> None:
    # Arrange
    producto = DATASET.product(sku)
    assert producto is not None

    # Act
    draft, _ = _run({"sku": sku, "cantidad": 7, "unidad": unidad})
    esperado = conversion(producto, 7, unidad, DATASET)

    # Assert
    if esperado is None:
        assert draft.status == "empty"
    else:
        assert draft.status == "ok"
        assert draft.data["conversion"]["unidades"] == esperado.units
        assert draft.data["conversion"]["texto"] == esperado.as_dict()["text"]


def test_paridad_con_la_cotizacion_del_motor() -> None:
    # Arrange: el mismo pedido que cotizaría ``cotizar`` en una ciudad con oferta.
    pedido = QuoteRequest(sku="CO-CTT-08", quantity=3, unit="paquete", city="Bogotá")

    # Act
    cotizado = quote_request(pedido, dataset=DATASET, today=HOY, catalog=CATALOG)
    draft, _ = _run({"sku": "CO-CTT-08", "cantidad": 3, "unidad": "paquete"})

    # Assert
    motor = cotizado["primary"]["conversion"]
    assert draft.data["conversion"]["unidades"] == motor["units"]
    assert draft.data["conversion"]["texto"] == motor["text"]


# --- Formato para el modelo ----------------------------------------------------------------


def test_cada_cifra_del_resultado_tiene_fuente_y_se_serializa() -> None:
    # Arrange
    draft, sources = _run({"sku": "CO-CTT-08", "cantidad": 3, "unidad": "paquete"})
    payload = {"status": draft.status, "message": draft.message, **draft.data}

    # Act
    contenido = format_tool_content(mod.NAME, payload)
    datos = content_data(contenido)

    # Assert: el tamaño de la presentación aparece en el excerpt de cada fuente citada.
    assert datos is not None
    assert datos["conversion"]["unidades"] == 60
    for numero in datos["conversion"]["fuentes"]:
        assert "20" in _entry(sources, numero)["excerpt"]
