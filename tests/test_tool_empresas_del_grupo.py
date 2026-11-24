"""``empresas_del_grupo`` (spec 045, T072; contrato *empresas_del_grupo*, research R11 y R12).

Lee la capa institucional curada real (``data/institucional/empresas.json`` vía
``carvajal.institutional``): cada unidad y marca devuelta registra su fuente
``empresa:{clave}`` con la cita literal de su página del núcleo; un alias citado («Propal»)
lleva a su empresa; un nombre que el núcleo no menciona («Alpina») da ``empty`` sin afirmar
que no pertenece; sin ``empresas.json`` la herramienta devuelve un ``error`` declarado.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.tools import empresas_del_grupo as mod
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.infrastructure.settings import Settings
from carvajal.institutional import Company, InstitutionalData, load_institutional

GOLD = Path("data") / "gold"
EMPRESAS = Path("data") / "institucional" / "empresas.json"
# Unidades de la lista «Nuestros Sectores» de quienes_somos.md (R12).
SECTORES = {
    "Carvajal Empaques",
    "Carvajal Pulpa y Papel",
    "Carvajal Educación",
    "Carvajal Soluciones de Comunicación",
    "Carvajal Tecnología y Servicios",
    "Américas BPS",
    "Carvajal Servicios",
    "Inmobiliario",
    "Carvajal Espacios",
}
MARCAS = ["Bioform", "Norma", "Mepal", "LiquidezYa"]
CITA_PROPAL = "PROPAL hace parte de la Organización Carvajal S.A."
SIN_DATO = "no tengo ese dato en las fuentes de Carvajal"


def _deps(institutional: InstitutionalData | None) -> ToolDeps:
    return ToolDeps(
        vector_store=cast(Any, None),
        embedder=cast(Any, None),
        settings=Settings(_env_file=None),
        now=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
        institutional=institutional,
    )


def _run(args: dict[str, Any], institutional: InstitutionalData | None) -> tuple[_Draft, _Sources]:
    sources = _Sources(())
    draft = mod.run(
        mod.EmpresasDelGrupoArgs.model_validate(args),
        _deps(institutional),
        ToolContext(question="¿Qué empresas tiene Carvajal?"),
        sources,
    )
    return draft, sources


def _por_numero(sources: _Sources) -> dict[int, dict[str, Any]]:
    return {fuente["n"]: fuente for fuente in sources.entries}


@pytest.fixture(scope="module")
def datos() -> InstitutionalData:
    return load_institutional()


@pytest.fixture(scope="module")
def items() -> list[dict[str, Any]]:
    return json.loads(EMPRESAS.read_text(encoding="utf-8"))["items"]


# --- Lista completa con cita ----------------------------------------------------------------


def test_sin_nombre_lista_las_unidades_de_nuestros_sectores_y_las_marcas(
    datos: InstitutionalData, items: list[dict[str, Any]]
) -> None:
    # Arrange
    unidades = [item["nombre"] for item in items if item["tipo"] == "unidad"]

    # Act
    draft, sources = _run({}, datos)

    # Assert
    assert draft.status == "ok"
    nombres_unidades = [unidad["nombre"] for unidad in draft.data["unidades"]]
    assert nombres_unidades == unidades  # orden del archivo curado
    assert set(nombres_unidades) >= SECTORES
    assert [marca["nombre"] for marca in draft.data["marcas"]] == MARCAS
    assert len(sources.ids) == len(items)


def test_cada_empresa_lleva_su_fuente_oficial_con_la_url_del_archivo(
    datos: InstitutionalData, items: list[dict[str, Any]]
) -> None:
    # Arrange
    url_por_nombre = {item["nombre"]: item["url"] for item in items}

    # Act
    draft, sources = _run({}, datos)

    # Assert
    por_numero = _por_numero(sources)
    for unidad in draft.data["unidades"]:
        fuente = por_numero[unidad["n"]]
        assert unidad["url"] == url_por_nombre[unidad["nombre"]]
        assert fuente["url"] == unidad["url"]
        assert fuente["url"].startswith("https://www.carvajal.com/")
    for entrada in [*draft.data["unidades"], *draft.data["marcas"]]:
        fuente = por_numero[entrada["n"]]
        assert fuente["chunk_id"].startswith("empresa:")
        assert fuente["layer"] == "core"
        assert fuente["source_type"] == "oficial"
        assert fuente["title"] == entrada["nombre"]


def test_cada_fuente_tiene_su_propio_numero_aunque_compartan_pagina(
    datos: InstitutionalData,
) -> None:
    """Varias filas citan ``quienes_somos``: cada una necesita su ``chunk_id`` y su [n]."""

    # Act
    draft, sources = _run({}, datos)

    # Assert
    numeros = [entrada["n"] for entrada in [*draft.data["unidades"], *draft.data["marcas"]]]
    chunk_ids = [fuente["chunk_id"] for fuente in sources.entries]
    assert len(set(numeros)) == len(numeros)
    assert len(set(chunk_ids)) == len(chunk_ids)


def test_el_excerpt_es_la_cita_literal_y_nombra_la_empresa(datos: InstitutionalData) -> None:
    """La compuerta exige que cada nombre devuelto aparezca en el ``excerpt`` (R11)."""

    # Act
    draft, sources = _run({}, datos)

    # Assert
    por_numero = _por_numero(sources)
    for entrada in [*draft.data["unidades"], *draft.data["marcas"]]:
        fuente = por_numero[entrada["n"]]
        pagina = (GOLD / f"{fuente['slug']}.md").read_text(encoding="utf-8")
        assert entrada["nombre"].casefold() in fuente["excerpt"].casefold()
        fragmentos = fuente["excerpt"].split(mod.EXCERPT_SEPARATOR)
        assert all(fragmento in pagina for fragmento in fragmentos)
        cifras = re.findall(r"\d+(?:[.,]\d+)?", json.dumps(entrada, ensure_ascii=False))
        assert all(cifra == str(entrada["n"]) for cifra in cifras)


# --- Nombre listado y alias -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "unidades", "marcas"),
    [
        ("Norma", [], ["Norma"]),
        ("Carvajal Empaques", ["Carvajal Empaques"], []),
        ("empaques", ["Carvajal Empaques"], []),
        ("  carvajal   EDUCACION ", ["Carvajal Educación"], []),
        ("Américas BPS", ["Américas BPS"], []),
        ("Pulpa y Papel", ["Carvajal Pulpa y Papel"], []),
        ("Carvajal Servicios", ["Carvajal Servicios"], []),
    ],
)
def test_con_nombre_listado_devuelve_solo_la_coincidencia(
    datos: InstitutionalData, nombre: str, unidades: list[str], marcas: list[str]
) -> None:
    # Act
    draft, sources = _run({"nombre": nombre}, datos)

    # Assert
    assert draft.status == "ok"
    assert [unidad["nombre"] for unidad in draft.data["unidades"]] == unidades
    assert [marca["nombre"] for marca in draft.data["marcas"]] == marcas
    assert len(sources.ids) == len(unidades) + len(marcas)


@pytest.mark.parametrize(
    ("nombre", "unidades", "marcas"),
    [
        ("Editorial Norma", [], ["Norma"]),
        ("la empresa Carvajal Empaques", ["Carvajal Empaques"], []),
        ("Carvajal Empaques S.A.", ["Carvajal Empaques"], []),
    ],
)
def test_palabras_genericas_alrededor_del_nombre_siguen_coincidiendo(
    datos: InstitutionalData, nombre: str, unidades: list[str], marcas: list[str]
) -> None:
    # Act
    draft, _ = _run({"nombre": nombre}, datos)

    # Assert
    assert draft.status == "ok"
    assert [unidad["nombre"] for unidad in draft.data["unidades"]] == unidades
    assert [marca["nombre"] for marca in draft.data["marcas"]] == marcas


def test_el_alias_propal_lleva_a_pulpa_y_papel_con_su_cita(datos: InstitutionalData) -> None:
    # Arrange
    pagina = (GOLD / "pulpa_y_papel.md").read_text(encoding="utf-8")

    # Act
    draft, sources = _run({"nombre": "Propal"}, datos)

    # Assert
    assert draft.status == "ok"
    [unidad] = draft.data["unidades"]
    assert unidad["nombre"] == "Carvajal Pulpa y Papel"
    assert unidad["alias"] == "PROPAL"
    assert draft.data["marcas"] == []
    [fuente] = sources.entries
    assert fuente["n"] == unidad["n"]
    assert fuente["slug"] == "pulpa_y_papel"
    assert CITA_PROPAL in fuente["excerpt"]
    assert CITA_PROPAL in pagina


def test_la_coincidencia_por_nombre_no_marca_alias(datos: InstitutionalData) -> None:
    # Act
    draft, _ = _run({"nombre": "Carvajal Pulpa y Papel"}, datos)

    # Assert
    [unidad] = draft.data["unidades"]
    assert "alias" not in unidad


def test_carvajal_a_secas_devuelve_la_lista_completa(datos: InstitutionalData) -> None:
    # Act
    completo, _ = _run({}, datos)
    draft, _ = _run({"nombre": "Grupo Carvajal"}, datos)

    # Assert
    assert draft.status == "ok"
    assert draft.data == completo.data


# --- Nombre que el núcleo no menciona -------------------------------------------------------


@pytest.mark.parametrize(
    "nombre",
    [
        "Alpina",
        "Assenda",
        "y",
        "de",
        # Contienen la palabra distintiva de una unidad junto a otra marca ajena.
        "Servicios Nutresa",
        "Empaques Flexa",
        "Inmobiliario Alpina",
        "Norma Pacific",
    ],
)
def test_nombre_no_listado_da_empty_sin_afirmar_que_no_pertenece(
    datos: InstitutionalData, nombre: str
) -> None:
    # Act
    draft, sources = _run({"nombre": nombre}, datos)

    # Assert
    assert draft.status == "empty"
    assert draft.message == SIN_DATO
    assert "pertenece" not in draft.message
    assert draft.data == {}  # ni lista ni eco del nombre escrito por el modelo
    assert sources.ids == []
    assert sources.entries == []


# --- Capa no disponible: error declarado ----------------------------------------------------


def test_empresas_json_ausente_da_error_declarado(tmp_path: Path) -> None:
    # Arrange
    sin_empresas = load_institutional(tmp_path / "empresas.json", GOLD)

    # Act
    draft, sources = _run({}, sin_empresas)

    # Assert
    assert sin_empresas.companies_status == "no_disponible"
    assert draft.status == "error"
    assert draft.message
    assert "unidades" not in draft.data
    assert sources.entries == []


def test_sin_capa_institucional_da_error_declarado() -> None:
    # Act
    draft, sources = _run({"nombre": "Norma"}, None)

    # Assert
    assert draft.status == "error"
    assert sources.entries == []


def test_estado_ok_sin_filas_da_error_declarado() -> None:
    # Arrange
    vacia = InstitutionalData(companies=(), companies_status="ok")

    # Act
    draft, _ = _run({}, vacia)

    # Assert
    assert draft.status == "error"


def test_fila_sin_url_registra_la_fuente_como_tercero() -> None:
    """Sin URL oficial la fuente no se presenta como oficial."""

    # Arrange
    fila = Company(name="Norma", kind="marca", slug="quienes_somos", url="", quote="marca Norma")
    datos = InstitutionalData(companies=(fila,), companies_status="ok")

    # Act
    draft, sources = _run({}, datos)

    # Assert
    assert draft.status == "ok"
    [fuente] = sources.entries
    assert fuente["source_type"] == "tercero"


# --- Esquema, descripción y línea de estado -------------------------------------------------


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_nombre_vacio_cuenta_como_ausente(vacio: str | None) -> None:
    # Act
    args = mod.EmpresasDelGrupoArgs.model_validate({"nombre": vacio})

    # Assert
    assert args.nombre is None


def test_nombre_colapsa_espacios_y_tiene_tope_de_ochenta() -> None:
    # Act
    args = mod.EmpresasDelGrupoArgs.model_validate({"nombre": "  Carvajal \n Empaques "})

    # Assert
    assert args.nombre == "Carvajal Empaques"
    with pytest.raises(ValidationError):
        mod.EmpresasDelGrupoArgs.model_validate({"nombre": "x" * 81})


def test_el_esquema_no_tiene_campos_de_identidad_y_describe_en_espanol() -> None:
    # Act
    schema = mod.EmpresasDelGrupoArgs.model_json_schema()

    # Assert
    assert set(schema["properties"]) == {"nombre"}
    assert schema["properties"]["nombre"]["description"]
    assert schema.get("required", []) == []


def test_la_descripcion_sigue_el_patron_sin_cifras() -> None:
    # Assert
    assert len(mod.DESCRIPTION) <= 300
    assert "Úsala para:" in mod.DESCRIPTION
    assert "No la uses para:" in mod.DESCRIPTION
    assert not re.search(r"\d", mod.DESCRIPTION)
    assert "historia_carvajal" in mod.DESCRIPTION


def test_la_linea_de_estado_no_interpola_el_nombre() -> None:
    # Act
    linea = mod.status({"nombre": "Alpina"})

    # Assert
    assert linea == "Estoy revisando las empresas y marcas del grupo…"
    assert "Alpina" not in linea
