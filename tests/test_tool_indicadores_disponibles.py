"""Pruebas de contrato de ``indicadores_disponibles`` (spec 045, T073; contrato 13).

La herramienta enumera los indicadores financieros publicados de ``data/indicators.json``
con sus períodos, **sin valores** (los da ``indicador_financiero``), y registra una sola
fuente ``tool:indicadores:catalogo`` cuyo excerpt contiene los nombres y los períodos que
devuelve, con los documentos de origen (de terceros).

Sin red ni LLM: se ejecuta el módulo directamente con el JSON versionado del repositorio.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.toolagent.tools import indicadores_disponibles as mod
from carvajal.agent.toolagent.tools._base import (
    ToolContext,
    ToolDeps,
    _Sources,
    format_tool_content,
)
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

INDICATORS_JSON = Path(__file__).resolve().parents[1] / "data" / "indicators.json"
RECORDS: list[dict[str, Any]] = json.loads(INDICATORS_JSON.read_text(encoding="utf-8"))[
    "indicators"
]
EXPECTED_NAMES = list(dict.fromkeys(str(record["name"]) for record in RECORDS))
EXPECTED_PERIODS = {
    name: sorted({str(r["period"]) for r in RECORDS if r["name"] == name})
    for name in EXPECTED_NAMES
}


# --- Dobles --------------------------------------------------------------------------------


class _NoEmbedder:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("indicadores_disponibles no consulta el índice.")


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
        raise AssertionError("indicadores_disponibles no consulta el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("Las herramientas no borran.")


def _deps() -> ToolDeps:
    return ToolDeps(
        vector_store=_NoStore(), embedder=_NoEmbedder(), settings=Settings(_env_file=None)
    )


def _run(
    args: dict[str, Any] | None = None, *, existing: Sequence[dict[str, Any]] = ()
) -> tuple[Any, _Sources]:
    sources = _Sources(existing)
    draft = mod.run(
        mod.IndicadoresDisponiblesArgs.model_validate(args or {}),
        _deps(),
        ToolContext(question="¿Qué cifras financieras me puedes dar?", sources=existing),
        sources,
    )
    return draft, sources


# --- ok: catálogo completo -----------------------------------------------------------------


def test_datos_reales_tienen_seis_indicadores_y_diez_registros() -> None:
    # Arrange / Act: el contrato se apoya en el JSON versionado.
    total_periods = sum(len(periods) for periods in EXPECTED_PERIODS.values())

    # Assert
    assert len(EXPECTED_NAMES) == 6
    assert total_periods == 10


def test_ok_devuelve_los_seis_indicadores_con_sus_periodos() -> None:
    # Act
    draft, _ = _run()

    # Assert
    assert draft.status == "ok"
    indicadores = draft.data["indicadores"]
    assert [item["nombre"] for item in indicadores] == EXPECTED_NAMES
    for item in indicadores:
        assert item["periodos"] == EXPECTED_PERIODS[item["nombre"]]
        assert set(item) == {"nombre", "alias", "unidad", "periodos"}
        assert item["unidad"]
    assert draft.missing == []


def test_alias_y_unidad_salen_del_json_sin_repetir_el_nombre() -> None:
    # Act
    draft, _ = _run()

    # Assert
    by_name = {item["nombre"]: item for item in draft.data["indicadores"]}
    for record in RECORDS:
        item = by_name[record["name"]]
        assert record["unit"] in item["unidad"]
        for alias in record.get("aliases", ()):
            if alias.replace("_", " ") != record["name"].replace("_", " "):
                assert alias in item["alias"]
    assert "ebitda" not in by_name["ebitda"]["alias"]
    assert all(len(item["alias"]) == len(set(item["alias"])) for item in by_name.values())


def test_no_devuelve_valores_ni_citas_de_los_indicadores() -> None:
    # Act
    draft, sources = _run()
    content = format_tool_content("indicadores_disponibles", draft.data)
    excerpt = sources.entries[0]["excerpt"]

    # Assert: ni el valor mostrado, ni el número, ni la cita textual de ningún registro.
    forbidden_keys = {"valor", "value", "display", "cita", "quote"}
    for item in draft.data["indicadores"]:
        assert not forbidden_keys & set(item)
    for record in RECORDS:
        assert record["display"] not in content
        assert record["display"] not in excerpt
        assert record["quote"] not in content
        assert record["quote"] not in excerpt
    for magnitude in ("3,96", "3.96", "351.631", "83.076", "26.602", "1,07", "1.95", "13.000"):
        assert magnitude not in content
        assert magnitude not in excerpt


# --- Fuente --------------------------------------------------------------------------------


def test_registra_una_fuente_de_catalogo_de_terceros() -> None:
    # Act
    draft, sources = _run()

    # Assert
    assert sources.ids == [1]
    assert draft.data["n"] == 1
    [entry] = sources.entries
    assert entry["chunk_id"] == "tool:indicadores:catalogo"
    assert entry["n"] == 1
    assert entry["layer"] == "core"
    assert entry["source_type"] == "tercero"
    assert draft.data["tipo_fuente"] == "tercero"
    assert entry["title"]


def test_excerpt_enumera_nombres_y_periodos() -> None:
    # Act
    draft, sources = _run()
    excerpt = sources.entries[0]["excerpt"]

    # Assert: cada nombre y cada período devuelto aparece literalmente en el excerpt.
    for item in draft.data["indicadores"]:
        assert item["nombre"] in excerpt
        assert item["nombre"].replace("_", " ") in excerpt
        assert item["unidad"] in excerpt
        for period in item["periodos"]:
            assert period in excerpt
        for alias in item["alias"]:
            assert alias in excerpt


def test_excerpt_nombra_el_mes_de_los_periodos_anio_mes() -> None:
    # Act
    _, sources = _run()
    excerpt = sources.entries[0]["excerpt"]

    # Assert: «2024-09» también se lee como «septiembre de 2024» (la compuerta vigila meses).
    assert "2024-09 (septiembre de 2024)" in excerpt
    assert "2025-09 (septiembre de 2025)" in excerpt


def test_excerpt_cita_los_documentos_de_origen() -> None:
    # Act
    _, sources = _run()
    excerpt = sources.entries[0]["excerpt"]

    # Assert
    for host in ("caliescribe.com", "larepublica.co", "elpais.com.co"):
        assert host in excerpt
    assert "terceros" in excerpt


def test_numera_despues_de_las_fuentes_del_turno() -> None:
    # Arrange
    previous = {
        "chunk_id": "historia:3",
        "slug": "historia",
        "section": "historia",
        "layer": "core",
        "score": 0.9,
        "excerpt": "Carvajal fue fundada en Cali en 1904.",
        "url": "https://www.carvajal.com/historia/",
        "title": "Historia",
        "source_type": "oficial",
        "n": 1,
    }

    # Act
    draft, sources = _run(existing=[previous])

    # Assert
    assert sources.ids == [2]
    assert draft.data["n"] == 2
    assert [entry["n"] for entry in sources.entries] == [1, 2]


def test_segunda_llamada_reutiliza_el_mismo_numero() -> None:
    # Arrange
    _, first = _run()

    # Act
    draft, second = _run(existing=first.entries)

    # Assert
    assert second.ids == [1]
    assert draft.data["n"] == 1
    assert len(second.entries) == 1


def test_contenido_es_json_serializable() -> None:
    # Act
    draft, _ = _run()
    content = format_tool_content("indicadores_disponibles", {"status": "ok", **draft.data})

    # Assert
    assert "<datos>" in content
    assert "ingresos_consolidados" in content


# --- Ausencia y errores --------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("data/indicators.json"), ValueError("sin indicadores")]
)
def test_json_ausente_o_vacio_es_error_declarado_sin_fuentes(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    # Arrange
    def broken() -> tuple[dict[str, Any], ...]:
        raise failure

    monkeypatch.setattr(mod, "_load", broken)

    # Act
    draft, sources = _run()

    # Assert
    assert draft.status == "error"
    assert "indicadores" not in draft.data
    assert sources.ids == []
    assert sources.entries == []


def test_registro_malformado_es_error_declarado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(mod, "_load", lambda: ({"name": "ebitda"},))

    # Act
    draft, sources = _run()

    # Assert
    assert draft.status == "error"
    assert sources.entries == []


def test_catalogo_sin_registros_es_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(mod, "_load", tuple)

    # Act
    draft, sources = _run()

    # Assert
    assert draft.status == "empty"
    assert sources.entries == []


def test_fuente_oficial_solo_si_todos_los_documentos_son_oficiales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    official = {
        "name": "ingresos_consolidados",
        "aliases": ["ingresos"],
        "period": "2024",
        "unit": "billones COP",
        "source_document": "informe",
        "source_url": "https://www.carvajal.com/informe/",
    }
    monkeypatch.setattr(mod, "_load", lambda: (official,))

    # Act
    draft, sources = _run()

    # Assert
    assert draft.status == "ok"
    assert sources.entries[0]["source_type"] == "oficial"
    assert "carvajal.com" in sources.entries[0]["excerpt"]


# --- Esquema, descripción y línea de estado ------------------------------------------------


def test_esquema_sin_campos() -> None:
    # Act
    schema = mod.IndicadoresDisponiblesArgs.model_json_schema()

    # Assert
    assert schema.get("properties", {}) == {}
    assert mod.IndicadoresDisponiblesArgs.model_validate({}) is not None


def test_descripcion_sigue_el_patron_sin_cifras() -> None:
    # Act
    description = mod.DESCRIPTION

    # Assert
    assert len(description) <= 300
    assert "Úsala para:" in description
    assert "No la uses para:" in description
    assert "indicador_financiero" in description
    assert not any(char.isdigit() for char in description)


def test_linea_de_estado_sin_valores() -> None:
    # Act
    line = mod.status({"nombre": "ebitda", "periodo": "2024"})

    # Assert
    assert line == "Estoy revisando qué cifras financieras tengo…"
    assert "ebitda" not in line
    assert not any(char.isdigit() for char in line)
    assert "indicador_financiero" not in line
    assert "indicadores_disponibles" not in line
