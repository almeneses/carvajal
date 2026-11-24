"""``historia_carvajal`` (spec 045, T071; contrato *historia_carvajal*, research R11 y R12).

Lee los hitos reales de ``data/gold/historia.md`` (``carvajal.institutional``): cada hito
devuelto es texto literal con su fuente ``historia:{anio}`` oficial; un año sin hito se
declara con los años cercanos que sí tienen, sin interpolar; si falta ``historia.md``, la
herramienta devuelve un ``error`` declarado, nunca una excepción.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.tools import historia_carvajal as mod
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Draft, _Sources
from carvajal.infrastructure.settings import Settings
from carvajal.institutional import InstitutionalData, Milestone, load_institutional

GOLD = Path("data") / "gold"
HISTORIA = GOLD / "historia.md"
# Texto literal del hito de 1960 en ``data/gold/historia.md`` (captura oficial).
HITO_1960 = (
    "Editorial Norma, producción de textos y de interés general. Llega la primera prensa "
    "rotativa offset para publicaciones masivas."
)


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
        mod.HistoriaCarvajalArgs.model_validate(args),
        _deps(institutional),
        ToolContext(question="¿Qué pasó en la historia de Carvajal?"),
        sources,
    )
    return draft, sources


@pytest.fixture(scope="module")
def datos() -> InstitutionalData:
    return load_institutional()


# --- Año con hito ---------------------------------------------------------------------------


def test_anio_con_hito_devuelve_el_texto_literal(datos: InstitutionalData) -> None:
    # Arrange
    historia = HISTORIA.read_text(encoding="utf-8")

    # Act
    draft, sources = _run({"anio": 1960}, datos)

    # Assert
    assert draft.status == "ok"
    [hito] = draft.data["hitos"]
    assert hito["anio"] == 1960
    assert hito["texto"] == HITO_1960
    assert HITO_1960 in historia
    assert hito["n"] == sources.ids[0]
    assert draft.data["hay_mas"] is False


def test_la_fuente_es_historia_anio_oficial_con_la_url_del_indice(
    datos: InstitutionalData,
) -> None:
    # Act
    draft, sources = _run({"anio": 1960}, datos)

    # Assert
    [fuente] = sources.entries
    assert fuente["chunk_id"] == "historia:1960"
    assert fuente["slug"] == "historia"
    assert fuente["layer"] == "core"
    assert fuente["source_type"] == "oficial"
    assert fuente["url"] == datos.history_url
    assert fuente["url"].startswith("https://www.carvajal.com/")
    assert draft.data["hitos"][0]["n"] == fuente["n"]


def test_cada_dato_devuelto_aparece_en_el_excerpt_de_su_fuente(datos: InstitutionalData) -> None:
    """La compuerta exige que años, cifras y nombres estén en el ``excerpt`` (R11)."""

    # Act
    draft, sources = _run({"desde": 1958, "hasta": 1961}, datos)

    # Assert
    por_numero = {fuente["n"]: fuente for fuente in sources.entries}
    for hito in draft.data["hitos"]:
        excerpt = por_numero[hito["n"]]["excerpt"]
        assert str(hito["anio"]) in excerpt
        assert hito["texto"] in excerpt
        cifras = re.findall(r"\d+(?:[.,]\d+)?", hito["texto"])
        assert all(cifra in excerpt for cifra in cifras)


def test_un_anio_con_varios_parrafos_conserva_cada_linea_literal(
    datos: InstitutionalData,
) -> None:
    # Arrange
    historia = HISTORIA.read_text(encoding="utf-8")

    # Act
    draft, _ = _run({"anio": 1958}, datos)

    # Assert
    [hito] = draft.data["hitos"]
    lineas = hito["texto"].splitlines()
    assert len(lineas) >= 2
    assert all(linea in historia for linea in lineas)


# --- Año sin hito: se declara, sin interpolar -----------------------------------------------


@pytest.mark.parametrize(
    ("args", "cercanos"),
    [
        ({"anio": 1962}, [1961, 1964]),  # encabezado vacío en la captura
        ({"anio": 1950}, [1948, 1952]),
        ({"anio": 1900}, [1904]),  # antes del primer hito
        ({"anio": 2050}, [2024]),  # después del último hito
        ({"desde": 1941, "hasta": 1947}, [1940, 1948]),
    ],
)
def test_anio_sin_hito_ofrece_los_anios_cercanos_sin_interpolar(
    datos: InstitutionalData, args: dict[str, Any], cercanos: list[int]
) -> None:
    # Act
    draft, sources = _run(args, datos)

    # Assert
    assert draft.status == "empty"
    assert draft.data["anios_cercanos"] == cercanos
    assert "hitos" not in draft.data
    assert sources.ids == []
    assert sources.entries == []


# --- Rangos ---------------------------------------------------------------------------------


def test_rango_con_mas_de_diez_hitos_devuelve_los_diez_primeros_y_avisa(
    datos: InstitutionalData,
) -> None:
    # Arrange
    esperados = [m.year for m in datos.milestones if 1970 <= m.year <= 2000]

    # Act
    draft, sources = _run({"desde": 1970, "hasta": 2000}, datos)

    # Assert
    assert len(esperados) > mod.MAX_MILESTONES
    assert draft.status == "ok"
    assert [hito["anio"] for hito in draft.data["hitos"]] == esperados[: mod.MAX_MILESTONES]
    assert draft.data["hay_mas"] is True
    assert len(sources.ids) == mod.MAX_MILESTONES
    assert [fuente["chunk_id"] for fuente in sources.entries] == [
        f"historia:{anio}" for anio in esperados[: mod.MAX_MILESTONES]
    ]


def test_rango_corto_devuelve_todos_en_orden(datos: InstitutionalData) -> None:
    # Act
    draft, _ = _run({"desde": 1956, "hasta": 1961}, datos)

    # Assert
    assert draft.status == "ok"
    assert [hito["anio"] for hito in draft.data["hitos"]] == [1956, 1958, 1959, 1960, 1961]
    assert draft.data["hay_mas"] is False


def test_desde_sin_hasta_llega_hasta_el_hito_mas_reciente(datos: InstitutionalData) -> None:
    # Arrange
    esperados = [m.year for m in datos.milestones if m.year >= 2020]

    # Act
    draft, _ = _run({"desde": 2020}, datos)

    # Assert
    assert draft.status == "ok"
    assert [hito["anio"] for hito in draft.data["hitos"]] == esperados
    assert esperados[-1] == max(m.year for m in datos.milestones)


def test_las_fuentes_se_numeran_sobre_las_del_turno(datos: InstitutionalData) -> None:
    # Arrange
    previa = {
        "chunk_id": "corpus:previo",
        "slug": "home",
        "section": "home",
        "layer": "core",
        "score": 0.5,
        "excerpt": "Fragmento anterior del turno.",
        "url": "https://www.carvajal.com/",
        "title": "Inicio",
        "source_type": "oficial",
    }
    sources = _Sources(())
    sources.add([previa])
    turno = _Sources(sources.entries)

    # Act
    draft = mod.run(
        mod.HistoriaCarvajalArgs.model_validate({"anio": 1960}),
        _deps(datos),
        ToolContext(sources=sources.entries),
        turno,
    )

    # Assert
    assert draft.data["hitos"][0]["n"] == 2
    assert turno.ids == [2]


# --- Historia no disponible: error declarado ------------------------------------------------


def test_historia_md_ausente_da_error_declarado(tmp_path: Path) -> None:
    # Arrange
    sin_historia = load_institutional(tmp_path / "empresas.json", tmp_path / "gold")

    # Act
    draft, sources = _run({"anio": 1960}, sin_historia)

    # Assert
    assert sin_historia.history_status == "no_disponible"
    assert draft.status == "error"
    assert draft.message
    assert "hitos" not in draft.data
    assert sources.entries == []


def test_sin_capa_institucional_da_error_declarado() -> None:
    # Act
    draft, sources = _run({"anio": 1960}, None)

    # Assert
    assert draft.status == "error"
    assert sources.entries == []


def test_estado_ok_sin_url_de_la_historia_da_error_declarado() -> None:
    """Sin URL no hay fuente que citar: se declara el error en lugar de citar en blanco."""

    # Arrange
    sin_url = InstitutionalData(
        milestones=(Milestone(year=1960, text=HITO_1960),),
        history_status="ok",
        history_url="",
    )

    # Act
    draft, sources = _run({"anio": 1960}, sin_url)

    # Assert
    assert draft.status == "error"
    assert sources.entries == []


# --- Esquema, descripción y línea de estado -------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"hasta": 1990},
        {"desde": 1990, "hasta": 1980},
        {"anio": 1960, "desde": 1950},
        {"anio": 1899},
        {"anio": 2101},
        {"anio": "mil novecientos"},
    ],
)
def test_argumentos_invalidos_se_rechazan(args: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        mod.HistoriaCarvajalArgs.model_validate(args)


def test_argumentos_validos_y_textos_vacios_como_ausentes() -> None:
    # Act
    por_texto = mod.HistoriaCarvajalArgs.model_validate({"anio": "1960", "desde": ""})
    rango = mod.HistoriaCarvajalArgs.model_validate({"desde": 1990, "hasta": 1990})

    # Assert
    assert por_texto.anio == 1960
    assert por_texto.desde is None
    assert (rango.desde, rango.hasta) == (1990, 1990)


def test_el_esquema_no_tiene_campos_de_identidad_y_describe_en_espanol() -> None:
    # Act
    campos = mod.HistoriaCarvajalArgs.model_fields

    # Assert
    assert set(campos) == {"anio", "desde", "hasta"}
    assert all(campo.description for campo in campos.values())


def test_la_descripcion_sigue_el_patron_y_no_lleva_cifras() -> None:
    # Act
    descripcion = mod.DESCRIPTION

    # Assert
    assert len(descripcion) <= 300
    assert "Úsala para" in descripcion
    assert "No la uses para" in descripcion
    assert "buscar_corpus" in descripcion
    assert "empresas_del_grupo" in descripcion
    assert not re.search(r"\d", descripcion)


def test_la_linea_de_estado_no_interpola_valores() -> None:
    # Act
    texto = mod.status({"anio": 1960, "desde": 1950, "hasta": 1970})

    # Assert
    assert texto == "Estoy buscando en la historia de Carvajal…"
    assert not re.search(r"\d", texto)
