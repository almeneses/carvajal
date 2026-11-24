"""Pruebas de contrato de ``fecha_actual`` (spec 045, T074; contrato 17; research R11).

La herramienta da la fecha de hoy en ``America/Bogota`` con el mismo reloj que ``cotizar``
y ``consultar_cotizacion`` (``_now_bogota``), dice si es día hábil o festivo según el
calendario de Colombia de ``quotation/calendar.py`` y cuál es el próximo día hábil. Registra
una sola fuente ``reloj:{fecha}`` con ``source_type="sistema"`` cuyo excerpt contiene todos
los valores que devuelve, para que la compuerta respalde la respuesta.

Sin red ni LLM: se ejecuta el módulo directamente con ``deps.now`` fijo. Las fechas
esperadas son literales del calendario colombiano de 2026 y 2027.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from carvajal.agent.toolagent.evidence import unsupported_terms
from carvajal.agent.toolagent.tools import fecha_actual as mod
from carvajal.agent.toolagent.tools._base import (
    ToolContext,
    ToolDeps,
    _Sources,
    format_tool_content,
)
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

# --- Dobles --------------------------------------------------------------------------------


class _NoEmbedder:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("fecha_actual no consulta el índice.")


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
        raise AssertionError("fecha_actual no consulta el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("Las herramientas no borran.")


def _deps(now: datetime | None) -> ToolDeps:
    return ToolDeps(
        vector_store=_NoStore(),
        embedder=_NoEmbedder(),
        settings=Settings(_env_file=None),
        now=now,
    )


def _run(
    now: datetime | None,
    *,
    args: dict[str, Any] | None = None,
    existing: Sequence[dict[str, Any]] = (),
) -> tuple[Any, _Sources]:
    sources = _Sources(existing)
    draft = mod.run(
        mod.FechaActualArgs.model_validate(args or {}),
        _deps(now),
        ToolContext(question="¿Mañana es día hábil?", sources=existing),
        sources,
    )
    return draft, sources


def _only_source(sources: _Sources) -> dict[str, Any]:
    [entry] = sources.entries
    return entry


# --- ok: día hábil -------------------------------------------------------------------------


def test_dia_habil_devuelve_fecha_dia_y_proximo_habil() -> None:
    # Arrange: martes 24 de noviembre de 2026, 10:00 en Bogotá (15:00 UTC).
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)

    # Act
    draft, sources = _run(now)

    # Assert
    assert draft.status == "ok"
    assert draft.data == {
        "n": 1,
        "fecha": "2026-11-24",
        "dia_semana": "martes",
        "es_habil": True,
        "es_festivo": False,
        "proximo_dia_habil": "2026-11-25",
        "zona": "America/Bogota",
    }
    assert sources.ids == [1]
    assert draft.missing == []


def test_fuente_es_reloj_de_tipo_sistema_sin_url() -> None:
    # Arrange
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)

    # Act
    _, sources = _run(now)

    # Assert
    entry = _only_source(sources)
    assert entry["n"] == 1
    assert entry["chunk_id"] == "reloj:2026-11-24"
    assert entry["source_type"] == "sistema"
    assert entry["url"] == ""
    assert entry["slug"] == "reloj"


def test_excerpt_contiene_todos_los_valores_devueltos() -> None:
    # Arrange
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)

    # Act
    draft, sources = _run(now)

    # Assert: cada valor del resultado aparece literalmente en el excerpt.
    excerpt = _only_source(sources)["excerpt"]
    for value in (
        "2026-11-24",
        "martes",
        "24 de noviembre de 2026",
        "2026-11-25",
        "miércoles",
        "25 de noviembre de 2026",
        "America/Bogota",
    ):
        assert value in excerpt, value
    assert "Es día hábil: sí" in excerpt
    assert "Es festivo en Colombia: no" in excerpt
    assert draft.data["fecha"] in excerpt


def test_compuerta_respalda_una_respuesta_con_la_fecha() -> None:
    # Arrange: lo que el modelo diría con estos datos; la compuerta solo ve el excerpt.
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    _, sources = _run(now)
    excerpt = _only_source(sources)["excerpt"]
    answer = (
        "Hoy es martes 24 de noviembre de 2026 y es día hábil. "
        "El próximo día hábil es el miércoles 25 de noviembre de 2026 (2026-11-25)."
    )

    # Act
    terms = unsupported_terms(answer, allowed=[excerpt])

    # Assert
    assert terms == []


def test_compuerta_bloquea_una_fecha_distinta_a_la_del_reloj() -> None:
    # Arrange: el modelo inventa otra fecha; el excerpt no la respalda.
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    _, sources = _run(now)
    excerpt = _only_source(sources)["excerpt"]

    # Act
    terms = unsupported_terms("Hoy es 3 de diciembre de 2026.", allowed=[excerpt])

    # Assert
    assert "3" in terms
    assert "diciembre" in terms


# --- ok: fin de semana y festivos ----------------------------------------------------------


def test_sabado_no_es_habil_y_el_proximo_es_el_lunes() -> None:
    # Arrange: sábado 28 de noviembre de 2026 al mediodía en Bogotá.
    now = datetime(2026, 11, 28, 17, 0, tzinfo=UTC)

    # Act
    draft, sources = _run(now)

    # Assert
    assert draft.status == "ok"
    assert draft.data["fecha"] == "2026-11-28"
    assert draft.data["dia_semana"] == "sábado"
    assert draft.data["es_habil"] is False
    assert draft.data["es_festivo"] is False
    assert draft.data["proximo_dia_habil"] == "2026-11-30"
    excerpt = _only_source(sources)["excerpt"]
    assert "Es día hábil: no" in excerpt
    assert "lunes 30 de noviembre de 2026" in excerpt


def test_festivo_fijo_de_colombia_no_es_habil() -> None:
    # Arrange: martes 8 de diciembre de 2026 (Inmaculada Concepción, festivo fijo).
    now = datetime(2026, 12, 8, 15, 0, tzinfo=UTC)

    # Act
    draft, sources = _run(now)

    # Assert
    assert draft.status == "ok"
    assert draft.data["dia_semana"] == "martes"
    assert draft.data["es_habil"] is False
    assert draft.data["es_festivo"] is True
    assert draft.data["proximo_dia_habil"] == "2026-12-09"
    excerpt = _only_source(sources)["excerpt"]
    assert "Es festivo en Colombia: sí" in excerpt
    assert "miércoles 9 de diciembre de 2026" in excerpt


def test_viernes_antes_de_festivo_trasladado_salta_al_martes() -> None:
    # Arrange: viernes 8 de enero de 2027; Reyes (miércoles 6) se traslada al lunes 11.
    now = datetime(2027, 1, 8, 15, 0, tzinfo=UTC)

    # Act
    draft, _ = _run(now)

    # Assert
    assert draft.data["dia_semana"] == "viernes"
    assert draft.data["es_habil"] is True
    assert draft.data["es_festivo"] is False
    assert draft.data["proximo_dia_habil"] == "2027-01-12"


def test_lunes_festivo_trasladado_es_festivo() -> None:
    # Arrange: lunes 11 de enero de 2027 (Reyes Magos trasladado por la ley Emiliani).
    now = datetime(2027, 1, 11, 15, 0, tzinfo=UTC)

    # Act
    draft, _ = _run(now)

    # Assert
    assert draft.data["dia_semana"] == "lunes"
    assert draft.data["es_festivo"] is True
    assert draft.data["es_habil"] is False
    assert draft.data["proximo_dia_habil"] == "2027-01-12"


def test_domingo_no_es_festivo_pero_tampoco_habil() -> None:
    # Arrange: domingo 29 de noviembre de 2026.
    now = datetime(2026, 11, 29, 15, 0, tzinfo=UTC)

    # Act
    draft, _ = _run(now)

    # Assert
    assert draft.data["dia_semana"] == "domingo"
    assert draft.data["es_habil"] is False
    assert draft.data["es_festivo"] is False
    assert draft.data["proximo_dia_habil"] == "2026-11-30"


# --- Zona horaria --------------------------------------------------------------------------


def test_cuatro_y_media_utc_es_el_dia_anterior_en_bogota() -> None:
    # Arrange: 04:30 UTC del miércoles 25 son las 23:30 del martes 24 en Bogotá.
    now = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)

    # Act
    draft, sources = _run(now)

    # Assert
    assert draft.data["fecha"] == "2026-11-24"
    assert draft.data["dia_semana"] == "martes"
    assert draft.data["proximo_dia_habil"] == "2026-11-25"
    assert _only_source(sources)["chunk_id"] == "reloj:2026-11-24"


def test_hora_sin_zona_se_toma_como_hora_de_bogota() -> None:
    # Arrange: igual que ``_now_bogota``: un ``deps.now`` sin zona ya es hora de Bogotá.
    now = datetime(2026, 11, 24, 23, 30)

    # Act
    draft, _ = _run(now)

    # Assert
    assert draft.data["fecha"] == "2026-11-24"


def test_sin_reloj_fijo_usa_el_reloj_del_sistema_en_bogota() -> None:
    # Arrange / Act
    draft, sources = _run(None)

    # Assert: la fecha es válida y coincide con la de la fuente.
    assert draft.status == "ok"
    assert _only_source(sources)["chunk_id"] == f"reloj:{draft.data['fecha']}"
    assert datetime.fromisoformat(draft.data["fecha"])


# --- Registro de fuentes -------------------------------------------------------------------


def test_numera_despues_de_las_fuentes_del_turno() -> None:
    # Arrange: el turno ya trae una fuente [1].
    existing = [
        {
            "n": 1,
            "chunk_id": "c-1",
            "slug": "home",
            "section": "home",
            "layer": "core",
            "score": 0.9,
            "excerpt": "Carvajal.",
            "url": "https://www.carvajal.com/",
            "title": "Inicio",
            "source_type": "oficial",
        }
    ]
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)

    # Act
    draft, sources = _run(now, existing=existing)

    # Assert
    assert draft.data["n"] == 2
    assert sources.ids == [2]
    assert [entry["n"] for entry in sources.entries] == [1, 2]


def test_dos_llamadas_el_mismo_dia_reutilizan_la_fuente() -> None:
    # Arrange
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    first, sources = _run(now)

    # Act
    second, again = _run(now, existing=sources.entries)

    # Assert: misma ``chunk_id``, mismo número; no se duplica.
    assert second.data["n"] == first.data["n"] == 1
    assert len(again.entries) == 1


def test_contenido_para_el_modelo_lleva_los_datos_delimitados() -> None:
    # Arrange
    now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    draft, _ = _run(now)
    payload = {"status": draft.status, "message": draft.message, "missing": [], **draft.data}

    # Act
    content = format_tool_content("fecha_actual", payload)

    # Assert
    assert "<datos>" in content
    assert "</datos>" in content
    assert "2026-11-24" in content
    assert "2026-11-25" in content


# --- Esquema, descripción y línea de estado ------------------------------------------------


def test_esquema_sin_campos_ignora_argumentos_extra() -> None:
    # Arrange / Act
    args = mod.FechaActualArgs.model_validate({"ciudad": "Cali"})

    # Assert: sin campos, y nada del modelo cambia la zona.
    assert mod.FechaActualArgs.model_fields == {}
    assert args.model_dump() == {}


def test_descripcion_sigue_el_patron_sin_cifras() -> None:
    # Arrange
    description = mod.DESCRIPTION

    # Act
    digits = [char for char in description if char.isdigit()]

    # Assert
    assert len(description) <= 300
    assert "Úsala para:" in description
    assert "No la uses para:" in description
    assert digits == []
    assert "Bogot" not in description


@pytest.mark.parametrize("args", [{}, {"ciudad": "Cali"}, {"fecha": "2026-01-01"}])
def test_linea_de_estado_fija_sin_valores(args: dict[str, Any]) -> None:
    # Arrange / Act
    line = mod.status(args)

    # Assert
    assert line == "Estoy revisando la fecha de hoy…"
    assert len(line) <= 40
