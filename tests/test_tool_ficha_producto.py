"""Contrato de ``ficha_producto`` (spec 045, T057; contracts/agent-tools-045.md).

Sin red ni LLM: el catálogo y el libro comercial versionados del repositorio. Cada cifra,
fecha, ciudad y nombre que devuelve la herramienta debe estar en el ``excerpt`` de una
fuente registrada (research R11), porque la compuerta de evidencia los busca ahí.
"""

import re
from datetime import UTC, datetime
from functools import cache
from typing import Any

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.tools import ficha_producto as mod
from carvajal.agent.toolagent.tools._base import (
    ToolContext,
    ToolDeps,
    _Sources,
    content_data,
    format_tool_content,
)
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.commercial.book import CommercialBook
from carvajal.commercial.loader import load_commercial_book
from carvajal.infrastructure.settings import Settings

ROTULO = "según la guía de materiales, no la ficha"


@cache
def _catalog() -> Catalog:
    return load_catalog()


@cache
def _book() -> CommercialBook:
    return load_commercial_book()


def _deps() -> ToolDeps:
    return ToolDeps(
        vector_store=None,  # ty: ignore[invalid-argument-type]
        embedder=None,  # ty: ignore[invalid-argument-type]
        settings=Settings(_env_file=None),
        catalog=_catalog(),
        book=_book(),
        now=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
    )


def _run(args: dict[str, Any], existing: list[dict[str, Any]] | None = None):
    sources = _Sources(existing or [])
    draft = mod.run(
        mod.FichaProductoArgs.model_validate(args),
        _deps(),
        ToolContext(question="¿Qué trae esa referencia?"),
        sources,
    )
    return draft, sources


def _excerpts(sources: _Sources) -> str:
    return "\n".join(str(entry["excerpt"]) for entry in sources.entries)


def _values(
    value: Any, skip: frozenset[str] = frozenset({"n", "rotulo", "tipo_fuente"})
) -> list[str]:
    """Hojas de texto y número de los datos, salvo citas, rótulo y tipo de fuente."""

    if isinstance(value, dict):
        return [item for key, child in value.items() if key not in skip for item in _values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _values(child)]
    if isinstance(value, bool) or value is None:
        return []
    return [str(value)]


# --- Esquema, descripción y línea de estado ------------------------------------------------


def test_exige_sku_o_nombre() -> None:
    # Arrange
    vacios = [{}, {"sku": "  "}, {"nombre": ""}, {"pais": "CO"}]

    # Act / Assert
    for args in vacios:
        with pytest.raises(ValidationError):
            mod.FichaProductoArgs.model_validate(args)


def test_esquema_acota_pais_y_largo_del_nombre() -> None:
    # Arrange
    largo = "x" * 121

    # Act / Assert
    with pytest.raises(ValidationError):
        mod.FichaProductoArgs.model_validate({"nombre": largo})
    with pytest.raises(ValidationError):
        mod.FichaProductoArgs.model_validate({"sku": "CO-VTER-04", "pais": "PE"})
    schema = mod.FichaProductoArgs.model_json_schema()
    assert set(schema["properties"]) == {"sku", "nombre", "pais"}
    assert all(prop.get("description") for prop in schema["properties"].values())


def test_descripcion_sigue_el_patron_sin_casos_concretos() -> None:
    # Arrange
    texto = mod.DESCRIPTION

    # Act
    cifras = re.findall(r"\d", texto)

    # Assert
    assert len(texto) <= 300
    assert "Úsala para" in texto
    assert "No la uses para" in texto
    assert "buscar_producto" in texto
    assert "cotizar" in texto
    assert not cifras


def test_linea_de_estado_no_lleva_valores() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "nombre": "Vaso Gold de 14 oz", "pais": "CO"}

    # Act
    texto = mod.status(args)

    # Assert
    assert texto == "Estoy revisando la ficha del producto…"
    assert "CO-VTER" not in texto
    assert "Gold" not in texto
    assert not re.search(r"\d", texto)


# --- Ficha por SKU exacto ------------------------------------------------------------------


def test_sku_exacto_devuelve_la_ficha_co_sin_temperatura_y_con_la_guia_rotulada() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    ficha = draft.data["ficha"]
    assert ficha["sku"] == "CO-VTER-04"
    assert ficha["nombre"] == "Vaso térmico 4 oz Wau"
    assert ficha["familia"] == "vaso"
    assert ficha["material"] == "EPS"
    assert ficha["marca"] == "Wau"
    assert ficha["capacidad_oz"] == 4
    assert ficha["unidades_por_paquete"] == 20
    assert ficha["paquetes_por_caja"] == 50
    assert ficha["unidades_por_caja"] == 1000
    assert ficha["pais"] == "CO"
    assert "bebida_caliente" in ficha["usos"]
    # Las fichas CO no declaran temperatura: solo la guía, rotulada como tal.
    assert ficha["temperatura_ficha"] is None
    guia = ficha["guia_material"]
    assert guia["rotulo"] == ROTULO
    assert guia["microondas"] is False
    assert guia["temperatura"] == {"min_c": -20, "max_c": 60}
    # Sin oferta publicada no hay precios ni se estiman.
    assert ficha["precios"] == []
    assert "total" not in str(draft.data)


def test_registra_las_fuentes_ficha_y_guia_con_las_cifras_en_el_extracto() -> None:
    # Arrange
    args = {"sku": "co-vter-04"}  # sin distinguir mayúsculas

    # Act
    draft, sources = _run(args)

    # Assert
    ids = [entry["chunk_id"] for entry in sources.entries]
    assert ids == ["ficha:CO-VTER-04:CO", "guia:EPS"]
    ficha_fuente, guia_fuente = sources.entries
    assert ficha_fuente["url"] == "https://colombia.carvajalempaques.com/producto/vaso-termico/"
    assert ficha_fuente["source_type"] == "oficial"
    assert ficha_fuente["layer"] == "core"
    for cifra in ("4 oz", "EPS", "20 unidades por paquete", "50 paquetes por caja", "1000"):
        assert cifra in ficha_fuente["excerpt"]
    assert "2026-11-18" in ficha_fuente["excerpt"]
    assert "-20" in guia_fuente["excerpt"]
    assert "60" in guia_fuente["excerpt"]
    assert draft.data["ficha"]["n"] == ficha_fuente["n"]
    assert draft.data["ficha"]["guia_material"]["n"] == guia_fuente["n"]
    assert sources.ids == [1, 2]


def test_cada_dato_devuelto_esta_en_el_extracto_de_una_fuente() -> None:
    # Arrange
    skus = ["CO-VTER-04", "CO-CTT-08", "CO-PCOM-J1", "2864108000"]

    for sku in skus:
        # Act
        draft, sources = _run({"sku": sku})

        # Assert
        assert draft.status == "ok", sku
        extractos = _excerpts(sources)
        faltan = [valor for valor in _values(draft.data["ficha"]) if valor not in extractos]
        assert not faltan, (sku, faltan)


# --- Precios publicados --------------------------------------------------------------------


def test_ficha_mx_con_precio_inline_y_temperatura_de_la_ficha() -> None:
    # Arrange
    args = {"sku": "2864108000", "pais": "MX"}

    # Act
    draft, sources = _run(args)

    # Assert
    ficha = draft.data["ficha"]
    assert ficha["pais"] == "MX"
    assert ficha["temperatura_ficha"] == {"min_c": -20, "max_c": 60}
    [precio] = ficha["precios"]
    assert precio["importe"] == "482.00"
    assert precio["moneda"] == "MXN"
    assert precio["iva_incluido"] is True
    assert precio["vendedor"] == "Carvajal Empaques México"
    assert precio["tipo_fuente"] == "oficial"
    assert precio["presentacion"] == "caja"
    assert precio["unidades"] == 1000
    assert precio["fecha"] == "2026-11-18"
    fuente = next(e for e in sources.entries if e["chunk_id"].startswith("precio:"))
    assert fuente["chunk_id"].startswith("precio:2864108000:")
    assert fuente["n"] == precio["n"]
    assert "482.00 MXN" in fuente["excerpt"]
    assert fuente["source_type"] == "oficial"


def test_precio_co_de_un_distribuidor_con_su_sede_y_la_presentacion_del_nombre() -> None:
    # Arrange
    args = {"sku": "CO-CTT-08"}

    # Act
    draft, sources = _run(args)

    # Assert
    ficha = draft.data["ficha"]
    # «(paquete x 20)» en el nombre: la presentación sale del nombre, como en el motor, y
    # la ficha no dice si esas 20 unidades son una caja.
    assert ficha["unidades_por_paquete"] == 20
    assert ficha["unidades_por_caja"] is None
    assert ficha["presentacion_nombre"] == {"presentacion": "paquete", "unidades": 20}
    [precio] = ficha["precios"]
    assert precio == {
        "n": precio["n"],
        "presentacion": "paquete",
        "unidades": 20,
        "importe": "17642",
        "moneda": "COP",
        "iva_incluido": True,
        "vendedor": "EDEXA",
        "ciudad": "Bogotá",
        "fecha": "2026-11-18",
        "tipo_fuente": "tercero",
    }
    fuente = next(e for e in sources.entries if e["n"] == precio["n"])
    # Mismo ``chunk_id`` que ``cotizar``: una cita repetida reutiliza su número.
    assert fuente["chunk_id"] == "precio:CO-CTT-08:CO-SEDE-9F23E89A16"
    assert fuente["excerpt"].startswith("Precio 17642 COP por paquete de 20 · IVA incluido")
    assert "EDEXA" in fuente["excerpt"]
    assert "Bogotá" in fuente["excerpt"]
    assert fuente["source_type"] == "tercero"


def test_paca_del_nombre_no_se_presenta_como_caja() -> None:
    # Arrange: el J1 dice «(paca x 200)» y la oferta del distribuidor la rotula «caja».
    args = {"sku": "CO-PCOM-J1"}

    # Act
    draft, _ = _run(args)

    # Assert
    ficha = draft.data["ficha"]
    assert ficha["presentacion_nombre"] == {"presentacion": "paca", "unidades": 200}
    assert ficha["unidades_por_caja"] is None
    assert ficha["unidades_por_paquete"] is None
    [precio] = ficha["precios"]
    assert precio["presentacion"] == "caja"  # lo que publica el vendedor, tal cual
    assert precio["unidades"] == 200


# --- Nombre, ambigüedad e inexistencia -----------------------------------------------------


def test_nombre_unico_resuelve_la_referencia() -> None:
    # Arrange
    args = {"nombre": "vaso termico 4 oz"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["ficha"]["sku"] == "CO-VTER-04"


def test_nombre_ambiguo_devuelve_candidatas_sin_fuentes() -> None:
    # Arrange
    args = {"nombre": "J1"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert draft.missing == ["referencia"]
    skus = {item["sku"] for item in draft.data["candidatas"]}
    assert skus == {"CO-PCOM-J1", "7704918105071"}
    assert all(set(item) == {"sku", "nombre"} for item in draft.data["candidatas"])
    assert draft.data["total_candidatas"] == 2
    assert "ficha" not in draft.data
    assert sources.entries == []


def test_candidatas_se_recortan_al_tope_de_opciones() -> None:
    # Arrange: «vaso térmico» nombra 16 referencias: los dos países y las copias de México.
    args = {"nombre": "vaso térmico"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert len(draft.data["candidatas"]) == mod.MAX_CANDIDATES
    assert draft.data["total_candidatas"] == 16


def test_el_pais_acota_las_candidatas() -> None:
    # Arrange
    args = {"nombre": "vaso térmico", "pais": "CO"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert all(item["sku"].startswith("CO-") for item in draft.data["candidatas"])


def test_referencia_inexistente_devuelve_empty_sin_eco_del_nombre() -> None:
    # Arrange
    args = {"nombre": "zzz inexistente"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data == {"referencia_encontrada": False}
    assert "zzz" not in draft.message
    assert sources.entries == []


def test_sku_inexacto_no_se_toma_por_otra_referencia() -> None:
    # Arrange: «CO-PCOM-P1S» no es el P1 ni ningún otro portacomidas.
    args = {"sku": "CO-PCOM-P1S"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["referencia_encontrada"] is False


def test_sku_de_otro_pais_no_aparece() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "pais": "MX"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert sources.entries == []


def test_sku_y_nombre_contradictorios_piden_elegir() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "nombre": "portacomidas J1"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "needs_input"
    assert draft.missing == ["referencia"]
    assert draft.data["conflicto_sku_nombre"] is True
    skus = [item["sku"] for item in draft.data["candidatas"]]
    assert skus[0] == "CO-VTER-04"
    assert "CO-PCOM-J1" in skus
    assert sources.entries == []


def test_sku_y_nombre_coherentes_dan_la_ficha() -> None:
    # Arrange
    args = {"sku": "CO-VTER-04", "nombre": "vaso térmico"}

    # Act
    draft, _ = _run(args)

    # Assert
    assert draft.status == "ok"
    assert draft.data["ficha"]["sku"] == "CO-VTER-04"


# --- Numeración y formato ------------------------------------------------------------------


def test_la_numeracion_continua_la_del_turno_y_reutiliza_fuentes_repetidas() -> None:
    # Arrange: una búsqueda previa del turno ya registró la guía del EPS.
    _, previas = _run({"sku": "CO-VTER-06"})

    # Act
    draft, sources = _run({"sku": "CO-VTER-04"}, existing=previas.entries)

    # Assert
    guia_previa = next(e for e in previas.entries if e["chunk_id"] == "guia:EPS")
    assert draft.data["ficha"]["guia_material"]["n"] == guia_previa["n"]
    assert draft.data["ficha"]["n"] == len(previas.entries) + 1
    assert sorted(sources.ids) == [guia_previa["n"], len(previas.entries) + 1]


def test_el_resultado_se_serializa_en_el_bloque_de_datos() -> None:
    # Arrange
    draft, _ = _run({"sku": "2864108000"})
    payload = {"status": draft.status, "message": draft.message, "missing": [], **draft.data}

    # Act
    data = content_data(format_tool_content("ficha_producto", payload))

    # Assert
    assert data is not None
    assert data["ficha"]["precios"][0]["importe"] == "482.00"
    assert data["status"] == "ok"
