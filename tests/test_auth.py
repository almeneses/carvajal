"""Pruebas del acceso con Google: login, callback, logout y navegación."""

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import google
from apps.api.auth.deps import AUTH_COOKIE
from apps.api.auth.router import OAUTH_COOKIE
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSRF_COOKIE
from carvajal.adapters.storage.auth import SessionUser, User
from carvajal.application.ask import Answer

RESPUESTA = Answer("ok", ())


class FakeAuthRepository:
    """Repositorio en memoria con la misma interfaz que el SQL."""

    def __init__(self) -> None:
        self.usuarios: dict[tuple[str, str], User] = {}
        self.sesiones: dict[str, tuple[str, datetime]] = {}

    def upsert_user(self, *, provider: str, subject: str, email: str, name: str) -> User:
        clave = (provider, subject)
        actual = self.usuarios.get(clave)
        usuario = User(actual.id if actual else f"u{len(self.usuarios) + 1}", email, name)
        self.usuarios[clave] = usuario
        return usuario

    def create_session(self, *, session_id: str, user_id: str, expires_at: datetime) -> None:
        self.sesiones[session_id] = (user_id, expires_at)

    def find_session(self, session_id: str) -> SessionUser | None:
        datos = self.sesiones.get(session_id)
        if datos is None:
            return None
        usuario = next(u for u in self.usuarios.values() if u.id == datos[0])
        return SessionUser(usuario, datos[1])

    def revoke_session(self, session_id: str) -> None:
        self.sesiones.pop(session_id, None)


def _app(repositorio: FakeAuthRepository | None = None) -> TestClient:
    app = create_app(
        Handlers(
            ask=lambda question: RESPUESTA,
            health=lambda: HealthStatus(0, 0),
        )
    )
    app.state.auth_repository = repositorio or FakeAuthRepository()
    return TestClient(app)


def _configura_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-de-prueba")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto-de-prueba")
    monkeypatch.setenv("AUTH_REDIRECT_URI", "http://testserver/auth/callback")


def _state(client: TestClient) -> str:
    respuesta = client.get("/auth/login", follow_redirects=False)
    destino = urlparse(respuesta.headers["location"])
    return parse_qs(destino.query)["state"][0]


def _simula_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google, "access_token", lambda **kwargs: "token")
    monkeypatch.setattr(
        google,
        "userinfo",
        lambda token: google.GoogleUser("sub-1", "ana@example.com", "Ana"),
    )


def _entra(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configura_google(monkeypatch)
    _simula_google(monkeypatch)
    estado = _state(client)
    client.get(f"/auth/callback?code=abc&state={estado}", follow_redirects=False)


def test_login_redirige_a_google_con_state_y_pkce(monkeypatch: pytest.MonkeyPatch) -> None:
    """La URL de consentimiento lleva cliente, redirect, state y reto S256."""

    # Arrange
    _configura_google(monkeypatch)
    client = _app()

    # Act
    respuesta = client.get("/auth/login", follow_redirects=False)

    # Assert
    assert respuesta.status_code == 302
    destino = urlparse(respuesta.headers["location"])
    parametros = parse_qs(destino.query)
    assert destino.netloc == "accounts.google.com"
    assert parametros["client_id"] == ["cliente-de-prueba"]
    assert parametros["redirect_uri"] == ["http://testserver/auth/callback"]
    assert parametros["code_challenge_method"] == ["S256"]
    assert parametros["scope"] == ["openid email profile"]
    assert len(parametros["state"][0]) > 10
    assert OAUTH_COOKIE in respuesta.cookies


def test_login_sin_configurar_responde_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin credenciales el modo anónimo sigue, pero el login avisa."""

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")

    respuesta = _app().get("/auth/login", follow_redirects=False)

    assert respuesta.status_code == 503
    assert "no está configurado" in respuesta.text


def test_callback_crea_usuario_y_sesion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con state válido se da de alta el usuario y se emite la cookie de sesión."""

    # Arrange
    _configura_google(monkeypatch)
    _simula_google(monkeypatch)
    repositorio = FakeAuthRepository()
    client = _app(repositorio)
    estado = _state(client)

    # Act
    respuesta = client.get(f"/auth/callback?code=abc&state={estado}", follow_redirects=False)

    # Assert
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/"
    assert AUTH_COOKIE in respuesta.cookies
    assert list(repositorio.usuarios) == [("google", "sub-1")]
    assert len(repositorio.sesiones) == 1


def test_callback_rechaza_un_state_ajeno(monkeypatch: pytest.MonkeyPatch) -> None:
    """El state del callback debe coincidir con el de la cookie firmada."""

    # Arrange
    _configura_google(monkeypatch)
    client = _app()
    _state(client)

    # Act
    respuesta = client.get("/auth/callback?code=abc&state=otro", follow_redirects=False)

    # Assert
    assert respuesta.status_code == 400
    assert "expiró" in respuesta.text


def test_callback_traduce_el_fallo_del_proveedor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un fallo del canje no filtra el error técnico al visitante."""

    # Arrange
    _configura_google(monkeypatch)

    def falla(**kwargs: object) -> str:
        raise google.AuthError("token endpoint caído")

    monkeypatch.setattr(google, "access_token", falla)
    client = _app()
    estado = _state(client)

    # Act
    respuesta = client.get(f"/auth/callback?code=abc&state={estado}", follow_redirects=False)

    # Assert
    assert respuesta.status_code == 502
    assert "token endpoint" not in respuesta.text


def test_logout_revoca_la_sesion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Salir invalida la sesión en la base y borra la cookie."""

    # Arrange
    repositorio = FakeAuthRepository()
    client = _app(repositorio)
    _entra(client, monkeypatch)
    client.get("/")
    token_csrf = client.cookies[CSRF_COOKIE]

    # Act
    respuesta = client.post(
        "/auth/logout",
        data={"csrf_token": token_csrf},
        follow_redirects=False,
    )

    # Assert
    assert respuesta.status_code == 303
    assert repositorio.sesiones == {}
    assert "max-age=0" in respuesta.headers["set-cookie"].lower()


def test_logout_sin_csrf_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    """El POST de salida exige el token del formulario."""

    # Arrange
    repositorio = FakeAuthRepository()
    client = _app(repositorio)
    _entra(client, monkeypatch)

    # Act
    respuesta = client.post("/auth/logout", follow_redirects=False)

    # Assert
    assert respuesta.status_code == 403
    assert len(repositorio.sesiones) == 1


def test_la_navegacion_muestra_entrar_o_salir(monkeypatch: pytest.MonkeyPatch) -> None:
    """La cabecera refleja el estado de la sesión."""

    # Arrange
    client = _app()

    # Act / Assert: anónimo
    assert "Entrar" in client.get("/").text

    # Act: sesión iniciada
    _entra(client, monkeypatch)

    # Assert
    portada = client.get("/").text
    assert "Salir" in portada
    assert "Ana" in portada
