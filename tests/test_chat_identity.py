"""Pruebas unitarias de la cookie firmada del chat: firma y secreto."""

import pytest
from fastapi import Request
from starlette.types import Scope

from apps.api.chat.identity import (
    COOKIE_NAME,
    SignedCookieIdentity,
    build_identity,
)
from carvajal.infrastructure.settings import Settings

SECRET = "secreto-de-prueba"


def _identity(secret: str = SECRET) -> SignedCookieIdentity:
    return SignedCookieIdentity(secret.encode("utf-8"))


def _request_with(value: str) -> Request:
    scope: Scope = {
        "type": "http",
        "headers": [(b"cookie", f"{COOKIE_NAME}={value}".encode())],
    }
    return Request(scope)


def test_issue_y_verify_hacen_ida_y_vuelta() -> None:
    # Arrange
    identity = _identity()

    # Act
    value = identity.issue("abc123")

    # Assert
    assert value.startswith("abc123.")
    assert identity.verify(value) == "abc123"


def test_read_devuelve_el_dueno_de_una_cookie_valida() -> None:
    # Arrange
    identity = _identity()
    request = _request_with(identity.issue("dueno-1"))

    # Act
    owner_key = identity.read(request)

    # Assert
    assert owner_key == "dueno-1"


def test_cookie_manipulada_no_verifica() -> None:
    # Arrange
    identity = _identity()
    value = identity.issue("dueno-1")
    manipulated = f"{value[:-1]}{'0' if value[-1] != '0' else '1'}"

    # Act
    verified = identity.verify(manipulated)

    # Assert
    assert verified is None
    assert identity.read(_request_with(manipulated)) is None


@pytest.mark.parametrize("value", ["", "sin-firma", "dueno.", ".firma"])
def test_valores_sin_firma_valida_no_verifican(value: str) -> None:
    # Arrange
    identity = _identity()

    # Act
    verified = identity.verify(value)

    # Assert
    assert verified is None


def test_new_owner_key_es_hexadecimal_de_32_caracteres() -> None:
    # Arrange
    identity = _identity()

    # Act
    owner_key = identity.new_owner_key()

    # Assert
    assert len(owner_key) == 32
    assert all(character in "0123456789abcdef" for character in owner_key)


def test_sin_secreto_configurado_se_usa_uno_estable_por_proceso() -> None:
    # Arrange: el campo vacío activa el secreto de reserva del proceso.
    first = build_identity(Settings(_env_file=None, session_secret=""))
    second = build_identity(Settings(_env_file=None, session_secret=""))

    # Act
    value = first.issue("dueno")

    # Assert
    assert second.verify(value) == "dueno"


def test_el_secreto_configurado_gana_al_de_reserva() -> None:
    # Arrange
    first = build_identity(Settings(_env_file=None, session_secret="secreto-uno"))
    second = build_identity(Settings(_env_file=None, session_secret="secreto-dos"))

    # Act
    value = first.issue("dueno")

    # Assert
    assert first.verify(value) == "dueno"
    assert second.verify(value) is None


def test_un_valor_random_no_es_una_identidad_valida() -> None:
    # Arrange
    identity = _identity()
    values = ["basura", "a.b", "123.456"]

    # Act / Assert
    assert all(identity.verify(value) is None for value in values)


def test_una_firma_no_ascii_no_lanza_y_no_verifica() -> None:
    # Arrange
    identity = _identity()

    # Act
    owner_key = identity.verify("dueno.ñ")

    # Assert
    assert owner_key is None
