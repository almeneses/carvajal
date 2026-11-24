"""Pruebas de ``consultar_cotizacion`` (spec 045, T075; contrato agent-tools-045.md).

Cubren FR-026 y el principio III: solo cotizaciones del mismo dueño y de procedencia
``real``; un folio ajeno, inexistente, simulado o ambiguo da el mismo ``empty``, sin
fuentes ni valores. Sin folio se listan las reales del hilo (máximo 5, recorte en Python).
La vigencia se compara con ``_now_bogota`` (``deps.now`` a las 04:30 UTC ya es el día
siguiente en UTC, pero no en Bogotá) y nunca se recotiza.

Sin red ni base: un ``quote_lookup`` en memoria con filas como las que guarda el chat
(``_stamp_quote`` de ``apps/api/chat/service.py``) sobre una cotización real del motor con
los datos versionados del repositorio.
"""

import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from apps.api.chat.service import _stamp_quote
from carvajal.adapters.storage.quotes import QuoteRecord
from carvajal.agent.toolagent.tools import (
    DATA_CLOSE,
    DATA_OPEN,
    TOOL_NAMES,
    ToolContext,
    ToolDeps,
    run_tool,
)
from carvajal.agent.toolagent.tools import consultar_cotizacion as tool
from carvajal.agent.toolagent.tools._base import _Draft, _Sources
from carvajal.application.ports import ScoredChunk
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings

HOY = date(2026, 11, 27)
EMITIDA = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
OWNER = "visitante:ana"
OTRO = "visitante:beto"
HILO = "hilo-ana"
FOLIO = "a1b2c3d4e5f6"
CALIENTE = {"family": "vaso", "use": "bebida_caliente", "temperature": "caliente"}


# --- Dobles --------------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class NoIndex:
    """Índice que no debe consultarse: la herramienta solo lee cotizaciones guardadas."""

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        raise AssertionError("consultar_cotizacion no indexa.")

    def search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 8,
        layer: str | None = None,
        section: str | None = None,
    ) -> Sequence[ScoredChunk]:
        raise AssertionError("consultar_cotizacion no busca en el índice.")

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("consultar_cotizacion no borra.")


class MemoryQuotes:
    """``QuoteLookup`` en memoria con el mismo filtro por dueño que ``SqlQuoteRepository``."""

    def __init__(self, rows: Sequence[Mapping[str, Any]] = ()) -> None:
        self.rows = [QuoteRecord(dict(row)) for row in rows]
        self.calls: list[tuple[str, str, str]] = []

    def get(self, folio: str, owner_key: str) -> QuoteRecord | None:
        self.calls.append(("get", folio, owner_key))
        for row in self.rows:
            if row.folio == folio and row.owner_key == owner_key:
                return row
        return None

    def for_conversation(self, conversation_id: str, owner_key: str) -> list[QuoteRecord]:
        self.calls.append(("for_conversation", conversation_id, owner_key))
        return [
            row
            for row in self.rows
            if row.get("conversation_id") == conversation_id and row.owner_key == owner_key
        ]


class BrokenQuotes:
    """La base cae a mitad del turno."""

    def get(self, folio: str, owner_key: str) -> None:
        raise RuntimeError("conexión perdida")

    def for_conversation(self, conversation_id: str, owner_key: str) -> list[Any]:
        raise RuntimeError("conexión perdida")


# --- Datos ---------------------------------------------------------------------------------


def _deps(lookup: Any = None, *, now: datetime | None = None) -> ToolDeps:
    return ToolDeps(
        vector_store=NoIndex(),
        embedder=FakeEmbedder(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        today=HOY,
        now=now,
        quote_lookup=lookup,
    )


def _engine_quote() -> dict[str, Any]:
    """Cotización real del motor con los datos versionados (vaso caliente en Bogotá)."""

    result = run_tool(
        "cotizar",
        {**CALIENTE, "quantity": 1000, "city": "Bogotá", "delivery": False},
        _deps(),
        ToolContext(),
    )
    assert result.status == "ok" and result.quote is not None
    return copy.deepcopy(result.quote)


QUOTE = _engine_quote()


def _row(
    folio: str = FOLIO,
    *,
    owner: str = OWNER,
    thread: str = HILO,
    quote: Mapping[str, Any] | None = None,
    commercial_valid_until: date | None = None,
) -> dict[str, Any]:
    """Fila de ``quotes`` tal como la sella y guarda el chat (``_stamp_quote``)."""

    payload, record = _stamp_quote(copy.deepcopy(dict(quote or QUOTE)), folio=folio, moment=EMITIDA)
    row = {**record, "payload": payload, "owner_key": owner, "conversation_id": thread}
    if commercial_valid_until is not None:
        # Ninguna oferta versionada publica hoy vigencia de vendedor: la fecha es del
        # escenario, para comprobar la comparación con el reloj de Bogotá.
        row["commercial_valid_until"] = commercial_valid_until
    return row


def _simulated() -> dict[str, Any]:
    quote = copy.deepcopy(QUOTE)
    quote["primary"]["price_simulated"] = True
    return quote


def _ambiguous() -> dict[str, Any]:
    """Sin fuente publicada atribuible: la matriz de procedencia la marca ``ambiguo``."""

    quote = copy.deepcopy(QUOTE)
    quote["primary"]["source_url"] = None
    quote["primary"]["captured_at"] = None
    quote["commercial"]["source"] = {}
    return quote


def _run(
    args: Mapping[str, Any],
    lookup: Any,
    *,
    owner: str = OWNER,
    thread: str = HILO,
    now: datetime | None = None,
) -> tuple[_Draft, _Sources]:
    sources = _Sources(())
    draft = tool.run(
        tool.ConsultarCotizacionArgs.model_validate(dict(args)),
        _deps(lookup, now=now),
        ToolContext(question="¿Mi cotización sigue vigente?", owner_key=owner, thread_id=thread),
        sources,
    )
    return draft, sources


def _scalars(value: Any) -> list[str]:
    """Valores hoja de los datos devueltos (sin ``n`` ni booleanos), como texto."""

    if isinstance(value, Mapping):
        return [text for key, item in value.items() if key != "n" for text in _scalars(item)]
    if isinstance(value, list):
        return [text for item in value for text in _scalars(item)]
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


# --- ok con folio --------------------------------------------------------------------------


def test_folio_propio_real_devuelve_el_desglose_guardado_sin_recotizar() -> None:
    # Arrange
    lookup = MemoryQuotes([_row()])
    primary = QUOTE["primary"]

    # Act
    draft, _ = _run({"folio": FOLIO}, lookup)

    # Assert
    assert draft.status == "ok"
    [quote] = draft.data["cotizaciones"]
    assert quote["folio"] == FOLIO
    assert quote["emitida"] == "2026-11-27"
    assert quote["referencia"] == primary["name"]
    assert quote["vendedor"] == primary["seller"]
    assert quote["subtotal"] == QUOTE["commercial"]["subtotal"]
    assert quote["total"] == QUOTE["total"]
    assert quote["moneda"] == "COP"
    assert quote["unidades"]["pedidas"] == 1000
    assert quote["iva"]["estado"] == primary["tax"]["status"]
    assert "procedencia" not in quote
    # Ninguna oferta versionada publica vigencia: no se afirma ni vigente ni vencida.
    assert quote["vigencia_comercial"] is None
    assert quote["vigente"] is None
    # Solo lee: no hay cotización nueva ni tarjeta que el servicio sellaría con otro folio.
    assert draft.quote is None and draft.quotes is None
    assert lookup.calls == [("get", FOLIO, OWNER)]


def test_fuentes_cotizacion_y_precio_guardado_con_su_tipo() -> None:
    # Arrange
    lookup = MemoryQuotes([_row()])

    # Act
    draft, sources = _run({"folio": FOLIO}, lookup)

    # Assert
    chunk_ids = [entry["chunk_id"] for entry in sources.entries]
    assert chunk_ids[0] == f"cotizacion:{FOLIO}"
    assert f"cotizacion:{FOLIO}:precio" in chunk_ids
    assert draft.data["cotizaciones"][0]["n"] == sources.entries[0]["n"]
    assert sources.ids == [entry["n"] for entry in sources.entries]
    main = sources.entries[0]
    assert main["url"] == QUOTE["primary"]["source_url"]
    assert main["source_type"] == "tercero"  # distribuidor, no carvajal.com
    assert f"Folio #{FOLIO}" in main["excerpt"]


def test_cada_valor_devuelto_aparece_en_el_extracto_de_su_fuente() -> None:
    # Arrange
    lookup = MemoryQuotes([_row(commercial_valid_until=date(2026, 12, 31))])

    # Act
    draft, sources = _run({"folio": FOLIO}, lookup, now=datetime(2026, 11, 28, 15, tzinfo=UTC))

    # Assert
    [quote] = draft.data["cotizaciones"]
    excerpt = next(e["excerpt"] for e in sources.entries if e["n"] == quote["n"])
    missing = [value for value in _scalars(quote) if value not in excerpt]
    assert missing == []
    assert draft.data["fecha_consulta"] in excerpt


# --- empty idéntico ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([_row(owner=OTRO)], id="ajeno"),
        pytest.param([], id="inexistente"),
        pytest.param([_row(quote=_simulated())], id="simulado"),
        pytest.param([_row(quote=_ambiguous())], id="ambiguo"),
    ],
)
def test_folio_ajeno_inexistente_simulado_o_ambiguo_da_el_mismo_empty(
    rows: list[dict[str, Any]],
) -> None:
    # Arrange
    reference, _ = _run({"folio": "ffffffffffff"}, MemoryQuotes())

    # Act
    draft, sources = _run({"folio": FOLIO}, MemoryQuotes(rows))

    # Assert
    assert draft.status == "empty"
    assert (draft.message, draft.data, draft.missing) == (
        reference.message,
        reference.data,
        reference.missing,
    )
    assert draft.data == {}
    assert sources.entries == [] and sources.ids == []
    assert draft.quote is None and draft.quotes is None


def test_procedencia_de_las_filas_de_prueba() -> None:
    # Arrange
    rows = {
        "real": _row(),
        "simulado": _row(quote=_simulated()),
        "ambiguo": _row(quote=_ambiguous()),
    }

    # Act
    provenance = {name: QuoteRecord(row).provenance for name, row in rows.items()}

    # Assert
    assert provenance == {"real": "real", "simulado": "simulado", "ambiguo": "ambiguo"}


def test_sin_dueno_no_consulta_y_responde_el_mismo_empty() -> None:
    # Arrange
    lookup = MemoryQuotes([_row(owner="")])
    reference, _ = _run({"folio": FOLIO}, MemoryQuotes())

    # Act
    draft, sources = _run({"folio": FOLIO}, lookup, owner="")

    # Assert
    assert draft.status == "empty"
    assert (draft.message, draft.data) == (reference.message, reference.data)
    assert lookup.calls == []
    assert sources.entries == []


# --- sin folio: las del hilo ---------------------------------------------------------------


def test_sin_folio_lista_las_reales_del_hilo_con_tope_de_cinco() -> None:
    # Arrange
    reales = [f"{index:012x}" for index in range(1, 8)]
    rows = [
        _row(reales[0]),
        _row("0000000000aa", quote=_simulated()),
        *(_row(folio) for folio in reales[1:]),
        _row("0000000000bb", quote=_ambiguous()),
        _row("0000000000cc", thread="otro-hilo"),
        _row("0000000000dd", owner=OTRO),
    ]
    lookup = MemoryQuotes(rows)

    # Act
    draft, sources = _run({}, lookup)

    # Assert
    assert draft.status == "ok"
    folios = [quote["folio"] for quote in draft.data["cotizaciones"]]
    assert folios == reales[-5:]
    assert lookup.calls == [("for_conversation", HILO, OWNER)]
    registered = {entry["chunk_id"] for entry in sources.entries}
    assert not any(
        cid.startswith(("cotizacion:0000000000a", "cotizacion:0000000000b")) for cid in registered
    )
    assert {f"cotizacion:{folio}" for folio in reales[-5:]} <= registered


def test_sin_folio_y_solo_simuladas_da_el_mismo_empty() -> None:
    # Arrange
    reference, _ = _run({"folio": FOLIO}, MemoryQuotes())
    lookup = MemoryQuotes([_row(quote=_simulated()), _row("0000000000bb", quote=_ambiguous())])

    # Act
    draft, sources = _run({}, lookup)

    # Assert
    assert draft.status == "empty"
    assert (draft.message, draft.data) == (reference.message, reference.data)
    assert sources.entries == []


def test_sin_folio_y_sin_hilo_no_consulta() -> None:
    # Arrange
    lookup = MemoryQuotes([_row()])

    # Act
    draft, _ = _run({}, lookup, thread="")

    # Assert
    assert draft.status == "empty"
    assert lookup.calls == []


# --- vigencia con el reloj de Bogotá -------------------------------------------------------


def test_vigencia_se_compara_con_la_fecha_de_bogota_no_con_la_utc() -> None:
    # Arrange: 04:30 UTC del 1 de diciembre son las 23:30 del 30 de noviembre en Bogotá.
    lookup = MemoryQuotes([_row(commercial_valid_until=date(2026, 11, 30))])

    # Act
    draft, _ = _run({"folio": FOLIO}, lookup, now=datetime(2026, 12, 1, 4, 30, tzinfo=UTC))

    # Assert
    [quote] = draft.data["cotizaciones"]
    assert draft.data["fecha_consulta"] == "2026-11-30"
    assert quote["vigencia_comercial"] == "2026-11-30"
    assert quote["vigente"] is True


def test_vigencia_vencida_en_bogota() -> None:
    # Arrange: 05:30 UTC del 1 de diciembre ya es el 1 de diciembre en Bogotá.
    lookup = MemoryQuotes([_row(commercial_valid_until=date(2026, 11, 30))])

    # Act
    draft, _ = _run({"folio": FOLIO}, lookup, now=datetime(2026, 12, 1, 5, 30, tzinfo=UTC))

    # Assert
    [quote] = draft.data["cotizaciones"]
    assert draft.data["fecha_consulta"] == "2026-12-01"
    assert quote["vigente"] is False


# --- errores declarados --------------------------------------------------------------------


def test_sin_quote_lookup_devuelve_error_declarado() -> None:
    # Act
    draft, sources = _run({"folio": FOLIO}, None)

    # Assert
    assert draft.status == "error"
    assert draft.data == {}
    assert sources.entries == []


def test_falla_de_la_base_devuelve_error_sin_lanzar() -> None:
    # Act
    by_folio, _ = _run({"folio": FOLIO}, BrokenQuotes())
    by_thread, _ = _run({}, BrokenQuotes())

    # Assert
    assert by_folio.status == "error"
    assert by_thread.status == "error"
    assert "conexión" not in by_folio.message


# --- esquema, descripción y línea de estado ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A1B2C3D4E5F6", FOLIO),
        ("#a1b2 c3d4 e5f6", FOLIO),
        (" # A1B2C3D4E5F6 ", FOLIO),
        ("", None),
        (None, None),
    ],
)
def test_folio_se_normaliza(raw: str | None, expected: str | None) -> None:
    # Act
    args = tool.ConsultarCotizacionArgs.model_validate({"folio": raw})

    # Assert
    assert args.folio == expected


@pytest.mark.parametrize("raw", ["a1b2c3", "a1b2c3d4e5f6a7", "zzzzzzzzzzzz", 123456789012])
def test_folio_invalido_no_pasa_la_validacion(raw: Any) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        tool.ConsultarCotizacionArgs.model_validate({"folio": raw})


def test_esquema_sin_campos_de_identidad() -> None:
    # Act
    fields = set(tool.ConsultarCotizacionArgs.model_fields)

    # Assert
    assert fields == {"folio"}
    assert tool.ConsultarCotizacionArgs.model_fields["folio"].description


def test_descripcion_sigue_el_patron_sin_cifras() -> None:
    # Act
    description = tool.DESCRIPTION

    # Assert
    assert len(description) <= 300
    assert "Úsala para" in description and "No la uses para" in description
    assert "cotizar" in description
    assert not any(char.isdigit() for char in description)


def test_linea_de_estado_no_interpola_el_folio() -> None:
    # Act
    line = tool.status({"folio": FOLIO})

    # Assert
    assert line == "Estoy buscando tu cotización…"
    assert FOLIO not in line


# --- integrada en el registro --------------------------------------------------------------


@pytest.mark.skipif(
    "consultar_cotizacion" not in TOOL_NAMES,
    reason="consultar_cotizacion aún no está en el registro (lo añade el integrador).",
)
def test_run_tool_entrega_los_datos_delimitados_y_numera_las_fuentes() -> None:
    # Arrange
    lookup = MemoryQuotes([_row()])
    context = ToolContext(question="¿sigue vigente?", owner_key=OWNER, thread_id=HILO)

    # Act
    result = run_tool(
        "consultar_cotizacion", {"folio": f"#{FOLIO.upper()}"}, _deps(lookup), context
    )
    broken = run_tool("consultar_cotizacion", {"folio": FOLIO}, _deps(BrokenQuotes()), context)

    # Assert
    assert result.status == "ok"
    assert result.args == {"folio": FOLIO}
    assert DATA_OPEN in result.content and DATA_CLOSE in result.content
    assert result.payload["cotizaciones"][0]["n"] in result.source_ids
    assert result.quotes == [] and result.quote is None
    assert broken.status == "error" and broken.sources == []
