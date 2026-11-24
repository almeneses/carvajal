"""Pruebas de ``ciudades_con_cobertura`` (spec 045, T062; contrato agent-tools-045).

Datos reales del repositorio: el directorio de distribuidores de Colombia tiene 29 sedes en
10 ciudades y la única sede con ofertas publicadas es la de EDEXA en Bogotá. Sin red ni LLM.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.tools import ciudades_con_cobertura as mod
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.application.ports import ScoredChunk
from carvajal.commercial.book import CommercialBook
from carvajal.commercial.loader import load_commercial_book
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings
from carvajal.quotation import Dataset, load_dataset

HOY = date(2026, 11, 24)
DIRECTORIO_URL = "https://colombia.carvajalempaques.com/distribuidores"
EDEXA_BOGOTA = "CO-SEDE-9F23E89A16"
SEDES_POR_CIUDAD = {
    "Barranquilla": 2,
    "Bogotá": 7,
    "Bucaramanga": 3,
    "Cali": 4,
    "Manizales": 2,
    "Medellín": 6,
    "Neiva": 1,
    "Pasto": 1,
    "Pereira": 2,
    "Villavicencio": 1,
}
AVISO = "una sede no confirma existencias"


# --- Dobles --------------------------------------------------------------------------------


class _NoEmbedder:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("ciudades_con_cobertura no consulta el índice.")


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
        raise AssertionError("ciudades_con_cobertura no consulta el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("Las herramientas no borran.")


def _deps(**extra: Any) -> ToolDeps:
    return ToolDeps(
        vector_store=_NoStore(),
        embedder=_NoEmbedder(),
        settings=Settings(_env_file=None),
        now=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
        **extra,
    )


def _run(
    args: dict[str, Any], deps: ToolDeps | None = None, existing: Sequence[dict[str, Any]] = ()
) -> tuple[_Draft, _Sources]:
    sources = _Sources(existing)
    draft = mod.run(
        mod.CiudadesConCoberturaArgs.model_validate(args),
        deps or _deps(),
        ToolContext(question="¿En qué ciudades tienen distribuidores?"),
        sources,
    )
    return draft, sources


def _new_entries(sources: _Sources) -> list[dict[str, Any]]:
    return [entry for entry in sources.entries if entry["n"] in sources.ids]


def _dataset_without_stock(sku: str) -> Dataset:
    """Dataset real con las ofertas de ``sku`` marcadas «no disponible» (sin precio cotizable)."""

    real = load_dataset(HOY.isoformat())
    offers = tuple(
        replace(offer, stock_status="no_disponible") if offer.sku == sku else offer
        for offer in real.offers
    )
    return Dataset(products=real.products, offers=offers, providers=real.providers)


# --- Sin referencia: el directorio completo ------------------------------------------------


def test_sin_referencia_devuelve_las_10_ciudades_y_29_sedes_del_directorio() -> None:
    # Arrange
    args: dict[str, Any] = {}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    ciudades = draft.data["ciudades"]
    assert {item["ciudad"]: item["sedes"] for item in ciudades} == SEDES_POR_CIUDAD
    assert [item["ciudad"] for item in ciudades] == list(SEDES_POR_CIUDAD)  # orden sin tildes
    assert draft.data["total_ciudades"] == 10
    assert draft.data["total_sedes"] == 29
    assert draft.data["aviso"] == AVISO
    assert "con_precio" not in draft.data and "con_sede" not in draft.data
    assert draft.missing == []


def test_sin_referencia_cita_el_directorio_oficial_con_cada_ciudad_y_cifra() -> None:
    # Arrange
    args: dict[str, Any] = {}

    # Act
    draft, sources = _run(args)

    # Assert
    [entry] = _new_entries(sources)
    assert entry["chunk_id"] == "directorio:CO"
    assert entry["url"] == DIRECTORIO_URL
    assert entry["source_type"] == "oficial"
    assert entry["layer"] == "core"
    assert draft.data["n"] == entry["n"]
    excerpt = entry["excerpt"]
    for city, count in SEDES_POR_CIUDAD.items():
        assert f"{city}: {count}" in excerpt
    assert "29 sedes" in excerpt and "10 ciudades" in excerpt


def test_reutiliza_el_numero_si_el_directorio_ya_estaba_citado() -> None:
    # Arrange
    _, first = _run({})

    # Act
    draft, sources = _run({}, existing=first.entries)

    # Assert
    assert len(sources.entries) == 1
    assert draft.data["n"] == first.entries[0]["n"]


def test_directorio_vacio_es_empty_sin_fuentes() -> None:
    # Arrange
    book = CommercialBook(products={}, sellers={}, sites={}, offers=())

    # Act
    draft, sources = _run({}, _deps(book=book))

    # Assert
    assert draft.status == "empty"
    assert sources.ids == []


# --- Con referencia: precio frente a sede --------------------------------------------------


def test_con_sku_separa_precio_y_sede_y_cita_el_precio() -> None:
    # Arrange
    args = {"sku": "CO-PCOM-J1"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["referencia"] == {
        "sku": "CO-PCOM-J1",
        "nombre": "Portacomidas espumado J1 Wau (paca x 200)",
    }
    assert draft.data["con_precio"] == ["Bogotá"]
    assert draft.data["con_sede"] == ["Bogotá"]
    assert draft.data["aviso"] == AVISO
    assert len(draft.data["ciudades"]) == 10
    chunk_ids = [entry["chunk_id"] for entry in _new_entries(sources)]
    assert chunk_ids == ["directorio:CO", f"precio:CO-PCOM-J1:{EDEXA_BOGOTA}"]
    price = _new_entries(sources)[1]
    assert "Bogotá" in price["excerpt"] and "102185" in price["excerpt"]
    assert price["source_type"] == "tercero"  # EDEXA es un distribuidor
    assert draft.data["fuentes_precio"] == [price["n"]]


def test_sin_existencias_cotizables_hay_sede_vinculada_pero_no_precio() -> None:
    # Arrange
    deps = _deps(dataset=_dataset_without_stock("CO-PCOM-J1"))

    # Act
    draft, sources = _run({"sku": "CO-PCOM-J1"}, deps)

    # Assert
    assert draft.status == "ok"
    assert draft.data["con_precio"] == []
    assert draft.data["con_sede"] == ["Bogotá"]
    assert draft.data["fuentes_precio"] == []
    assert [entry["chunk_id"] for entry in _new_entries(sources)] == ["directorio:CO"]


def test_referencia_sin_precio_ni_sede_sigue_dando_el_directorio() -> None:
    # Arrange
    args = {"sku": "CO-VTER-08"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["con_precio"] == []
    assert draft.data["con_sede"] == []
    assert len(draft.data["ciudades"]) == 10
    assert "sin precio publicado" in draft.message
    assert [entry["chunk_id"] for entry in _new_entries(sources)] == ["directorio:CO"]


def test_nombre_unico_resuelve_la_referencia() -> None:
    # Arrange
    args = {"nombre": "portacomidas J2"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["referencia"]["sku"] == "CO-PCOM-J2"
    assert draft.data["con_precio"] == ["Bogotá"]


def test_sku_escrito_como_nombre_se_busca_por_nombre() -> None:
    # Arrange
    args = {"sku": "J2"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["referencia"]["sku"] == "CO-PCOM-J2"


# --- Referencias que no se pueden fijar ----------------------------------------------------


def test_nombre_ambiguo_pide_elegir_entre_candidatas_sin_fuentes() -> None:
    # Arrange
    args = {"nombre": "J1"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert draft.missing == ["referencia"]
    skus = {item["sku"] for item in draft.data["candidatas"]}
    assert skus == {"CO-PCOM-J1", "7704918105071"}
    assert sources.ids == []
    assert "ciudades" not in draft.data


def test_nombre_generico_limita_las_candidatas_y_declara_el_total() -> None:
    # Arrange
    args = {"nombre": "vaso"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert len(draft.data["candidatas"]) == mod.MAX_CANDIDATES
    assert draft.data["total_candidatas"] > mod.MAX_CANDIDATES


def test_sku_y_nombre_de_referencias_distintas_es_conflicto() -> None:
    # Arrange
    args = {"sku": "CO-PCOM-J1", "nombre": "portacomidas J2"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert draft.data["conflicto_sku_nombre"] is True
    assert {item["sku"] for item in draft.data["candidatas"]} == {"CO-PCOM-J1", "CO-PCOM-J2"}
    assert sources.ids == []


def test_referencia_inexistente_es_empty_sin_fuentes() -> None:
    # Arrange
    args = {"sku": "CO-NOEXISTE-99"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert sources.ids == []
    assert "ciudades" not in draft.data


@pytest.mark.parametrize("sku", ["CO-NOEXISTE-1", "CO-PCOM-P9", "9999999999"])
def test_un_sku_inexistente_con_forma_de_sku_no_ofrece_candidatas_ajenas(sku: str) -> None:
    # Act: «CO-NOEXISTE-1» coincidía en parte por «co» y «1» con bandejas sin relación.
    draft, sources = _run({"sku": sku})

    # Assert: como ficha_producto, el SKU es exacto o no es.
    assert draft.status == "empty", draft.data
    assert draft.data == {"referencia_encontrada": False}
    assert sources.ids == []


def test_referencia_de_mexico_es_empty_porque_el_directorio_es_de_colombia() -> None:
    # Arrange
    args = {"sku": "2864108000"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["pais_referencia"] == "MX"
    assert "Colombia" in draft.message
    assert sources.ids == []


def test_falla_del_contrato_es_error_declarado() -> None:
    # Arrange: un libro sin referencias de Colombia no se puede adaptar al motor.
    book = load_commercial_book()
    empty_products = CommercialBook(
        products={}, sellers=book.sellers, sites=book.sites, offers=(), evidence=()
    )

    # Act
    draft, sources = _run({"sku": "CO-PCOM-J1"}, _deps(book=empty_products))

    # Assert
    assert draft.status == "error"
    assert sources.ids == []


# --- Esquema, descripción y línea de estado ------------------------------------------------


def test_esquema_sin_ciudad_ni_identidad_y_con_descripciones() -> None:
    # Arrange
    schema = mod.CiudadesConCoberturaArgs.model_json_schema()

    # Act
    fields = schema["properties"]

    # Assert
    assert set(fields) == {"sku", "nombre"}
    assert all(field.get("description") for field in fields.values())


def test_esquema_trata_texto_vacio_como_ausente_e_ignora_la_ciudad() -> None:
    # Arrange: la herramienta no pide ciudad (eso es ``buscar_sedes``); si el modelo la
    # manda, se ignora en vez de fallar.
    raw = {"sku": "   ", "nombre": "", "ciudad": "Cali"}

    # Act
    args = mod.CiudadesConCoberturaArgs.model_validate(raw)

    # Assert
    assert args.sku is None and args.nombre is None
    assert "ciudad" not in args.model_dump()


def test_esquema_rechaza_textos_demasiado_largos() -> None:
    # Arrange
    raw = {"nombre": "x" * 200}

    # Act / Assert
    with pytest.raises(ValidationError):
        mod.CiudadesConCoberturaArgs.model_validate(raw)


def test_descripcion_sigue_el_patron_sin_cifras_ni_ciudades() -> None:
    # Arrange
    description = mod.DESCRIPTION

    # Act
    digits = [char for char in description if char.isdigit()]

    # Assert
    assert len(description) <= 300
    assert "Úsala para" in description and "No la uses para" in description
    assert "buscar_sedes" in description
    assert digits == []
    assert not any(city in description for city in SEDES_POR_CIUDAD)


def test_linea_de_estado_no_interpola_valores() -> None:
    # Arrange
    args = {"sku": "CO-PCOM-J1", "nombre": "Portacomidas J1"}

    # Act
    text = mod.status(args)

    # Assert
    assert text == "Estoy revisando en qué ciudades hay distribuidores…"
