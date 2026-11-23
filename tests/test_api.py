"""Pruebas del shell web con handlers falsos: sin red ni base de datos."""

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api import main, runtime
from apps.api.chat import service as chat_service
from apps.api.chat.identity import SignedCookieIdentity
from apps.api.main import (
    DB_UNAVAILABLE,
    Handlers,
    HealthStatus,
    create_app,
)
from carvajal.application.ask import Answer, Source
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.monitoring import monitoring_status
from carvajal.infrastructure.settings import Settings

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició operaciones en 1904.")
ANSWER = Answer(text="Carvajal nació en 1904 [1].", sources=(SOURCE,))
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")


def make_client(
    health: Callable[[], HealthStatus] | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
    *,
    identity: SignedCookieIdentity | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """Cliente con handlers falsos; por defecto responden bien."""

    app = create_app(
        Handlers(
            health=health or (lambda: HealthStatus(documents=12, chunks=34)),
            quota=quota or (lambda owner: QuotaResult(allowed=True)),
        )
    )
    app.state.chat_identity = identity or IDENTITY
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def unhealthy() -> Callable[[], HealthStatus]:
    def health() -> HealthStatus:
        raise SQLAlchemyError("connection to 127.0.0.1:5432 as user carvajal failed")

    return health


def test_health_devuelve_estado_y_conteos() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 200
    payload = response.json()
    assert payload.pop("monitoring") == monitoring_status(Settings()).as_health()
    catalog = payload.pop("catalog")
    assert catalog["status"] == "ok"
    assert catalog["version"] == "2026-11-18"
    assert payload == {
        "status": "ok",
        "documents": 12,
        "chunks": 34,
        "index": {"status": "not_configured"},
    }


def test_health_degradado_no_filtra_detalles() -> None:
    # Arrange
    client = make_client(health=unhealthy())

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 503
    payload = response.json()
    payload.pop("monitoring")
    assert payload == {
        "status": "degraded",
        "detail": DB_UNAVAILABLE,
        "index": {"status": "unknown"},
    }
    assert "127.0.0.1" not in response.text and "carvajal" not in response.text


def test_live_responde_aunque_la_base_no_este() -> None:
    """La vitalidad del contenedor no depende de Postgres."""

    # Arrange: el readiness falla, pero /live no debe consultarlo.
    client = make_client(health=unhealthy())

    # Act
    response = client.get("/live")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_la_portada_redirige_al_chat() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/", follow_redirects=False)

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"


def test_la_lectura_publica_no_depende_del_token_de_operador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("OPERATOR_TOKEN", "token-de-prueba")
    client = make_client()

    # Act / Assert
    for ruta in ("/chat", "/api/csrf", "/live"):
        assert client.get(ruta).status_code == 200, ruta
    assert client.get("/faq", follow_redirects=False).status_code == 303
    assert client.get("/configuracion").status_code == 200
    assert 'href="/configuracion"' not in client.get("/chat").text


def test_no_existen_endpoints_web_de_publicacion_del_corpus() -> None:
    # Arrange
    client = make_client()

    # Act / Assert: publicar sigue siendo un paso de la consola.
    for ruta in (
        "/configuracion/sync",
        "/configuracion/index",
        "/api/configuracion",
        "/api/captura",
        "/api/sync",
    ):
        assert client.post(ruta).status_code in {404, 405}, ruta


def test_chat_renderiza_el_formulario_y_el_panel() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert 'name="question"' in response.text
    assert 'for="pregunta"' in response.text and 'id="pregunta"' in response.text
    assert 'id="panel-documento"' in response.text
    assert "/static/texto.js" in response.text
    assert "/static/panel.js" in response.text
    assert "/static/panel.css" in response.text
    assert 'href="/configuracion"' not in response.text
    assert response.text.index('id="hilo"') < response.text.index('class="formulario composer"')


def test_chat_aplica_el_tema_y_la_accesibilidad_basica() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert 'lang="es"' in response.text
    assert "<title>Chat · Asistente Carvajal</title>" in response.text
    assert "Saltar al contenido" in response.text
    assert 'alt="Carvajal"' in response.text
    assert 'aria-live="polite"' in response.text and 'id="hilo"' in response.text
    assert "corpus público de Carvajal" in response.text


def test_la_navegacion_enlaza_las_pantallas_y_oculta_el_asistente() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert 'href="/#preguntar"' not in response.text
    assert 'id="preguntar"' not in response.text
    assert 'href="/corpus"' in response.text
    assert 'href="/chat"' in response.text
    assert 'href="/faq"' not in response.text
    assert 'href="/cotizador"' not in response.text
    assert 'href="/configuracion"' not in response.text


def test_las_pestanas_marcan_la_pantalla_activa() -> None:
    # Arrange
    client = make_client()

    # Act
    chat = client.get("/chat")

    # Assert: una sola pestaña activa por pantalla, con aria-current.
    assert chat.text.count('aria-current="page"') == 1
    assert re.search(r'href="/chat"\s+aria-current="page"', chat.text)


def test_el_logo_se_sirve_como_svg() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/static/logo.svg")

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert len(response.content) > 1000
    assert "<svg" in response.text


def test_los_assets_del_panel_y_del_formateador_se_sirven() -> None:
    # Arrange
    client = make_client()

    # Act / Assert
    for nombre in (
        "panel.css",
        "panel.js",
        "texto.js",
        "hilos.js",
        "configuracion.css",
        "captura.js",
        "identidad.js",
    ):
        response = client.get(f"/static/{nombre}")
        assert response.status_code == 200, nombre
        assert (main.APP_DIR / "static" / nombre).is_file(), nombre


def test_chat_carga_htmx_local() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "/static/htmx.min.js" in response.text
    assert (main.APP_DIR / "static" / "htmx.min.js").is_file()


def test_chat_reserva_un_estado_accesible_sin_cargador_inicial() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert: el historial llega renderizado; solo el stream anuncia la espera.
    assert response.status_code == 200
    assert 'id="chat-cargando"' not in response.text
    assert '<p id="chat-stream-estado" class="estado-chat" role="status"></p>' in response.text


def test_chat_carga_los_controles_de_voz_sin_estilos_inline() -> None:
    """La base de la 040 se sirve desde archivos propios, bajo la CSP vigente."""

    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")
    css = client.get("/static/chat.css")
    js = client.get("/static/chat-voice.js")

    # Assert
    assert response.status_code == 200
    assert "/static/chat-voice.js" in response.text
    assert "data-voz hidden" in response.text
    assert "data-voz-dictar" in response.text
    assert 'type="button"' in response.text
    assert 'style="' not in response.text
    assert js.status_code == 200
    assert "SpeechRecognition" in js.text
    assert css.status_code == 200
    assert ".voz-estado" in css.text


def test_create_app_difiere_los_handlers_a_la_primera_peticion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    calls: list[int] = []

    def fake_handlers() -> Handlers:
        calls.append(1)
        return Handlers(health=lambda: HealthStatus(1, 2))

    monkeypatch.setattr(main, "_default_handlers", fake_handlers)
    app = create_app()
    assert calls == []

    # Act
    response = TestClient(app).get("/health")

    # Assert
    assert response.status_code == 200
    assert calls == [1]
    payload = response.json()
    payload.pop("monitoring")
    catalog = payload.pop("catalog")
    assert catalog["status"] == "ok"
    assert catalog["version"] == "2026-11-18"
    assert payload == {
        "status": "ok",
        "documents": 1,
        "chunks": 2,
        "index": {"status": "not_configured"},
    }


def test_los_handlers_reales_no_construyen_el_llm_al_arrancar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construir handlers no abre la base ni resuelve los clientes del chat."""

    # Arrange: la fábrica de sesiones se sustituye por un doble.
    monkeypatch.setattr(main, "session_factory", lambda: object())
    main._default_handlers.cache_clear()

    try:
        # Act: construir los handlers reales no debe tocar los clientes.
        handlers = main._default_handlers()
    finally:
        main._default_handlers.cache_clear()

    # Assert
    assert isinstance(handlers, Handlers)


def test_el_servicio_de_chat_difiere_los_clientes_a_la_primera_pregunta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abrir /chat no debe construir el embedder ni el LLM solo para leer historial."""

    # Arrange: la sesión y los clientes se sustituyen por dobles.
    llamadas: list[int] = []

    def fake_components() -> tuple[object, object]:
        llamadas.append(1)
        return object(), object()

    monkeypatch.setattr(runtime, "session_factory", lambda: object())
    monkeypatch.setattr(runtime, "ask_components", fake_components)
    chat_service.default_chat_service.cache_clear()

    try:
        # Act: crear el servicio real no debe resolver los clientes.
        service = chat_service.default_chat_service()
    finally:
        chat_service.default_chat_service.cache_clear()

    # Assert
    assert isinstance(service, chat_service.ChatService)
    assert llamadas == []
