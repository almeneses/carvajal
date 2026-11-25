"""Idempotencia del folio y separación del resultado comercial en el turno."""

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from apps.api.chat.service import (
    _is_duplicate_turn,
    _quote_folio,
    _sources_from_payload,
    _stamp_quote,
)
from carvajal.application.ask import Source


def _products() -> dict[str, object]:
    return {
        "quote": {
            "kind": "cotizacion",
            "primary": {"currency": "COP"},
            "commercial": {
                "shipping": {"cost": None},
                "tax": {"status": "incluido"},
            },
            "total": "3751",
        }
    }


def _issue_quote(
    products: dict[str, object], *, conversation_id: str, client_turn_id: str | None = None
) -> tuple[dict[str, object], dict[str, object]]:
    """Sello de emisión con el folio derivado del hilo y del turno (spec 054: al confirmar)."""

    quote = products["quote"]
    assert isinstance(quote, dict)
    folio = _quote_folio(conversation_id, client_turn_id)
    payload, record = _stamp_quote(
        {**quote, "primary": {"currency": "COP", "sku": "x"}}, folio=folio, moment=datetime.now(UTC)
    )
    return {"quote": payload}, record


def _folio(products: dict[str, object]) -> str:
    quote = products["quote"]
    assert isinstance(quote, dict)
    nested = quote["quote"]
    assert isinstance(nested, dict)
    return str(nested["id"])


def test_el_folio_se_deriva_del_hilo_y_del_turno_para_el_reenvio() -> None:
    # Arrange / Act
    primero, registro_primero = _issue_quote(
        _products(), conversation_id="hilo-1", client_turn_id="turno-1"
    )
    segundo, registro_segundo = _issue_quote(
        _products(), conversation_id="hilo-1", client_turn_id="turno-1"
    )
    otro_turno, _ = _issue_quote(_products(), conversation_id="hilo-1", client_turn_id="turno-2")
    otro_hilo, _ = _issue_quote(_products(), conversation_id="hilo-2", client_turn_id="turno-1")

    # Assert
    assert registro_primero is not None and registro_segundo is not None
    assert _folio(primero) == _folio(segundo) == registro_primero["id"] == registro_segundo["id"]
    assert len(_folio(primero)) == 12
    assert _folio(otro_turno) != _folio(primero)
    assert _folio(otro_hilo) != _folio(primero)


def test_sin_client_turn_id_el_folio_no_se_reutiliza() -> None:
    # Arrange / Act
    primero, _ = _issue_quote(_products(), conversation_id="hilo-1")
    segundo, _ = _issue_quote(_products(), conversation_id="hilo-1")

    # Assert
    assert _folio(primero) != _folio(segundo)


def test_solo_el_duplicado_del_turno_habilita_la_reproduccion() -> None:
    class Diag:
        constraint_name = "uq_messages_turn"

    class Orig(Exception):
        diag = Diag()

    duplicado = IntegrityError("INSERT", {}, Orig())
    otra_integridad = IntegrityError(
        "INSERT", {}, Exception('violates foreign key constraint "fk_quotes"')
    )

    assert _is_duplicate_turn(duplicado)
    assert not _is_duplicate_turn(otra_integridad)


def test_las_fuentes_ignoran_explicitamente_el_resultado_comercial() -> None:
    # Arrange
    source = Source("historia-1", "historia", "historia", "core", 0.9, "Inició en 1904.")
    payload = [
        asdict(source),
        {"result_type": "commercial", "result": {"quote": {"id": "folio-1"}}},
        {"chunk_id": "incompleto"},
    ]

    # Act
    fuentes = _sources_from_payload(payload)

    # Assert
    assert fuentes == (source,)
