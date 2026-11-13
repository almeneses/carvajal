"""Pruebas del cliente OIDC de Google: PKCE y URL de consentimiento."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from apps.api.auth import google


def test_pkce_deriva_el_reto_del_verificador() -> None:
    """El reto es el SHA-256 del verificador en base64url sin relleno."""

    # Act
    verificador, reto = google.pkce()

    # Assert
    esperado = (
        base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert len(verificador) >= 43
    assert reto == esperado


def test_la_url_lleva_cliente_redirect_estado_y_pkce() -> None:
    """La pantalla de Google recibe todo lo necesario para el canje."""

    # Act
    url = google.authorization_url(
        client_id="cid",
        redirect_uri="http://localhost:8000/auth/callback",
        state="estado",
        challenge="reto",
    )

    # Assert
    partes = urlparse(url)
    parametros = parse_qs(partes.query)
    assert partes.netloc == "accounts.google.com"
    assert parametros["client_id"] == ["cid"]
    assert parametros["redirect_uri"] == ["http://localhost:8000/auth/callback"]
    assert parametros["state"] == ["estado"]
    assert parametros["code_challenge"] == ["reto"]
    assert parametros["code_challenge_method"] == ["S256"]
    assert parametros["scope"] == ["openid email profile"]
