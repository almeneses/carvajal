"""Contrato de ``guia_materiales`` (spec 045, T059; contracts/agent-tools-045.md).

La herramienta lee la guía de materiales versionada (``data/catalog/empaques/materiales.csv``
y ``manifest.json``): nada de red ni de LLM. Cada dato devuelto debe aparecer en el
``excerpt`` de su fuente ``guia:{material}`` para que la compuerta de evidencia lo respalde.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from carvajal.agent.toolagent.evidence import unsupported_terms
from carvajal.agent.toolagent.tools import guia_materiales as mod
from carvajal.agent.toolagent.tools._base import ToolContext, ToolDeps, _Sources
from carvajal.catalog.loader import DEFAULT_DIR, MANIFEST, CatalogError, load_catalog
from carvajal.infrastructure.settings import Settings

AVISO = (
    "Guía de materiales de Mi Tiendita Centroamérica (Lineamientos Generales, pág. 3), "
    "transcrita a mano"
)
IDENTITY_FIELDS = {"owner_key", "thread_id", "conversation_id", "conversacion", "dueno"}


def _deps(**overrides: Any) -> ToolDeps:
    return ToolDeps(
        vector_store=None,  # ty: ignore[invalid-argument-type]
        embedder=None,  # ty: ignore[invalid-argument-type]
        settings=Settings(_env_file=None),
        catalog=overrides.pop("catalog", load_catalog()),
        **overrides,
    )


def _run(args: dict[str, Any], deps: ToolDeps | None = None) -> tuple[Any, _Sources]:
    sources = _Sources(())
    draft = mod.run(
        mod.GuiaMaterialesArgs.model_validate(args),
        deps or _deps(),
        ToolContext(question="pregunta de prueba"),
        sources,
    )
    return draft, sources


def _excerpt(sources: _Sources, number: int) -> str:
    return str(sources.entries[number - 1]["excerpt"])


# --- Material y aplicación -----------------------------------------------------------------


def test_pet_no_es_apto_para_comida_caliente_con_su_fuente() -> None:
    # Arrange
    args = {"material": "PET", "aplicacion": "comida_caliente"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    [regla] = draft.data["reglas"]
    assert regla["material"] == "PET"
    assert regla["nivel"] == "no_apto"
    assert regla["contenido_caliente"] is False
    assert regla["microondas"] is False
    assert (regla["temp_min_c"], regla["temp_max_c"]) == (-20, 40)
    assert regla["codigo_reciclaje"] == "1"
    assert draft.data["aviso"] == AVISO
    assert sources.ids == [regla["n"]]
    entry = sources.entries[regla["n"] - 1]
    assert entry["chunk_id"] == "guia:PET"
    assert entry["layer"] == "core"
    assert entry["url"] == "https://elsalvador.carvajalempaques.com"
    assert AVISO in entry["excerpt"]
    assert "comida caliente: no apto" in entry["excerpt"]


def test_solo_material_devuelve_la_regla_sin_nivel_y_todas_sus_aplicaciones() -> None:
    # Arrange
    args = {"material": "PP"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    [regla] = draft.data["reglas"]
    assert regla["nivel"] is None
    assert regla["microondas"] is True
    assert regla["aplicaciones"]["sopas"] == "recomendable"
    assert len(regla["aplicaciones"]) == 9
    assert sources.entries[0]["chunk_id"] == "guia:PP"


@pytest.mark.parametrize("texto", ["icopor", "Icopor", "unicel", "poliestireno expandido", "eps"])
def test_los_nombres_coloquiales_del_eps_se_normalizan(texto: str) -> None:
    # Arrange
    args = {"material": texto}

    # Act
    parsed = mod.GuiaMaterialesArgs.model_validate(args)

    # Assert
    assert parsed.material == "EPS"


def test_icopor_consulta_la_regla_del_eps() -> None:
    # Arrange
    args = {"material": "icopor", "aplicacion": "sopas"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    [regla] = draft.data["reglas"]
    assert regla["material"] == "EPS"
    assert regla["nivel"] == "recomendable"
    assert sources.entries[0]["chunk_id"] == "guia:EPS"


# --- Solo aplicación -----------------------------------------------------------------------


def test_solo_aplicacion_ordena_los_materiales_por_nivel() -> None:
    # Arrange: sopas en la guía: PP y EPS recomendables, PS aplicable, PET y aluminio no aptos.
    args = {"aplicacion": "sopas"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "ok"
    reglas = draft.data["reglas"]
    assert [regla["material"] for regla in reglas] == ["PP", "EPS", "PS", "PET", "ALUMINIO"]
    assert [regla["nivel"] for regla in reglas] == [
        "recomendable",
        "recomendable",
        "aplicable",
        "no_apto",
        "no_apto",
    ]
    assert sources.ids == [regla["n"] for regla in reglas]
    assert {entry["chunk_id"] for entry in sources.entries} == {
        "guia:PP",
        "guia:EPS",
        "guia:PS",
        "guia:PET",
        "guia:ALUMINIO",
    }
    # Papel y pulpa no están en la guía: se declara, no se descartan.
    assert draft.data["materiales_sin_regla"] == ["PAPEL", "PULPA"]
    assert all("aplicaciones" not in regla for regla in reglas)


@pytest.mark.parametrize(
    ("texto", "esperada"),
    [
        ("comida caliente", "comida_caliente"),
        ("Comida_Fría", "comida_fria"),
        ("Sopa", "sopas"),
        ("caldos", "sopas"),
        ("sándwich", "sandwiches"),
        ("papas fritas", "frituras"),
        ("pan", "panaderia"),
        ("aderezos", "salsas"),
        ("fruta", "frutas"),
        ("ensalada", "ensaladas"),
    ],
)
def test_la_aplicacion_acepta_sinonimos(texto: str, esperada: str) -> None:
    # Arrange
    args = {"aplicacion": texto}

    # Act
    parsed = mod.GuiaMaterialesArgs.model_validate(args)

    # Assert
    assert parsed.aplicacion == esperada


# --- Materiales sin regla ------------------------------------------------------------------


@pytest.mark.parametrize("material", ["PAPEL", "PULPA", "cartón", "bagazo"])
def test_papel_y_pulpa_no_tienen_regla_en_la_guia(material: str) -> None:
    # Arrange
    args = {"material": material, "aplicacion": "comida_caliente"}

    # Act
    draft, sources = _run(args)

    # Assert
    assert draft.status == "empty"
    assert draft.data["sin_regla"] is True
    assert draft.message == "la guía no cubre este material; cada ficha declara su uso"
    assert "reglas" not in draft.data
    assert sources.ids == []


def test_los_materiales_sin_regla_salen_del_manifiesto() -> None:
    # Arrange
    manifest = json.loads((Path(DEFAULT_DIR) / MANIFEST).read_text(encoding="utf-8"))

    # Act
    without_rule = mod.materials_without_rule()

    # Assert
    assert without_rule == tuple(manifest["materials_without_rule"])


# --- Refrigeración deducida ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("material", "deducida"),
    [("PS", True), ("ALUMINIO", True), ("PP", False), ("EPS", False), ("PET", False)],
)
def test_la_refrigeracion_de_ps_y_aluminio_es_deducida(material: str, deducida: bool) -> None:
    # Arrange
    args = {"material": material}

    # Act
    draft, sources = _run(args)

    # Assert
    [regla] = draft.data["reglas"]
    assert regla["refrigeracion"] is True
    assert regla["refrigeracion_deducida"] is deducida
    assert ("deducida" in _excerpt(sources, regla["n"])) is deducida


def test_la_deduccion_coincide_con_la_nota_del_manifiesto() -> None:
    # Arrange
    manifest = json.loads((Path(DEFAULT_DIR) / MANIFEST).read_text(encoding="utf-8"))
    notes = " ".join(manifest["sources"]["materiales"]["notes"])

    # Act
    deduced = mod.DEDUCED_REFRIGERATION

    # Assert: la constante transcribe la nota «refrigeration=true para PS y aluminio…».
    assert "refrigeration=true para PS y aluminio se deduce" in notes
    assert deduced == frozenset({"PS", "ALUMINIO"})


# --- Evidencia -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {"aplicacion": "comida_caliente"},
        {"material": "ALUMINIO"},
        {"material": "PS", "aplicacion": "frutas"},
    ],
)
def test_cada_dato_devuelto_aparece_en_el_excerpt_de_su_fuente(args: dict[str, Any]) -> None:
    # Arrange
    draft, sources = _run(args)

    # Act
    unsupported = {
        regla["material"]: unsupported_terms(
            json.dumps({k: v for k, v in regla.items() if k != "n"}, ensure_ascii=False),
            allowed=[_excerpt(sources, regla["n"])],
        )
        for regla in draft.data["reglas"]
    }
    aviso = unsupported_terms(draft.data["aviso"], allowed=[_excerpt(sources, 1)])

    # Assert
    assert all(not terms for terms in unsupported.values()), unsupported
    assert not aviso


def test_las_fuentes_se_numeran_sobre_las_del_turno() -> None:
    # Arrange: el turno ya trae una fuente; la de la guía sigue la numeración.
    previa = {
        "n": 1,
        "chunk_id": "c1",
        "slug": "doc",
        "section": "s",
        "layer": "core",
        "score": 0.9,
        "excerpt": "texto",
        "url": "",
        "title": "Doc",
        "source_type": "oficial",
    }
    sources = _Sources([previa])

    # Act
    draft = mod.run(
        mod.GuiaMaterialesArgs.model_validate({"material": "PET"}),
        _deps(),
        ToolContext(question="¿el PET sirve para comida caliente?"),
        sources,
    )

    # Assert
    assert draft.data["reglas"][0]["n"] == 2
    assert sources.ids == [2]


# --- Errores y esquema ---------------------------------------------------------------------


def test_sin_material_ni_aplicacion_el_esquema_rechaza_la_llamada() -> None:
    # Arrange
    args: dict[str, Any] = {}

    # Act / Assert
    with pytest.raises(ValidationError):
        mod.GuiaMaterialesArgs.model_validate(args)


@pytest.mark.parametrize(
    "args", [{"material": "vidrio"}, {"aplicacion": "bebida_caliente"}, {"material": "  "}]
)
def test_valores_desconocidos_se_rechazan(args: dict[str, Any]) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        mod.GuiaMaterialesArgs.model_validate(args)


def test_un_catalogo_que_no_carga_devuelve_error_declarado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    def falla() -> None:
        raise CatalogError("materiales.csv: no existe el archivo del catálogo.")

    monkeypatch.setattr(mod, "load_catalog", falla)
    deps = ToolDeps(
        vector_store=None,  # ty: ignore[invalid-argument-type]
        embedder=None,  # ty: ignore[invalid-argument-type]
        settings=Settings(_env_file=None),
    )

    # Act
    draft, sources = _run({"material": "PET"}, deps)

    # Assert
    assert draft.status == "error"
    assert sources.ids == []


def test_la_linea_de_estado_no_interpola_valores() -> None:
    # Act
    linea = mod.status({"material": "PET", "aplicacion": "sopas"})

    # Assert
    assert linea == "Estoy revisando la guía de materiales…"


def test_la_descripcion_sigue_el_patron_y_el_tope() -> None:
    # Act
    descripcion = mod.DESCRIPTION

    # Assert
    assert len(descripcion) <= 300
    assert "Úsala para:" in descripcion
    assert "No la uses para:" in descripcion
    assert not any(char.isdigit() for char in descripcion)


def test_el_esquema_no_tiene_identidad_y_describe_cada_campo() -> None:
    # Act
    schema = mod.GuiaMaterialesArgs.model_json_schema()

    # Assert
    assert not set(schema["properties"]) & IDENTITY_FIELDS
    assert set(schema["properties"]) == {"material", "aplicacion"}
    assert all(prop.get("description") for prop in schema["properties"].values())
    assert not any(char.isdigit() for char in json.dumps(schema, ensure_ascii=False))
