"""Pruebas del shell web con handlers falsos: sin red ni base de datos."""

import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api import main, runtime
from apps.api.chat import service as chat_service
from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.main import (
    DB_UNAVAILABLE,
    SERVICE_UNAVAILABLE,
    Handlers,
    HealthStatus,
    create_app,
)
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.application.ask import Answer, LLMError, Source
from carvajal.application.quota import QuotaResult

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició operaciones en 1904.")
ANSWER = Answer(text="Carvajal nació en 1904 [1].", sources=(SOURCE,))
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")


def make_client(
    ask: Callable[[str], Answer] | None = None,
    health: Callable[[], HealthStatus] | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
    *,
    identity: SignedCookieIdentity | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """Cliente con handlers falsos; por defecto responden bien."""

    app = create_app(
        Handlers(
            ask=ask or (lambda question: ANSWER),
            health=health or (lambda: HealthStatus(documents=12, chunks=34)),
            quota=quota or (lambda owner: QuotaResult(allowed=True)),
        )
    )
    app.state.chat_identity = identity or IDENTITY
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _ask(client: TestClient, question: str) -> Any:
    """Envía una pregunta con el token CSRF que emite el chat."""

    client.get("/faq")
    token = client.cookies[CSRF_COOKIE]
    return client.post("/ask", data={"question": question, CSRF_FIELD: token})


def failing(error: Exception) -> Callable[[str], Answer]:
    """Handler de preguntas que lanza el error indicado."""

    def ask(question: str) -> Answer:
        raise error

    return ask


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
    assert response.json() == {
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
    assert response.json() == {
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
    assert response.status_code == 307
    assert response.headers["location"] == "/chat"


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
    assert 'href="/configuracion"' in response.text
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
    assert 'href="/faq"' in response.text
    assert 'href="/configuracion"' in response.text


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


def test_ask_devuelve_respuesta_y_fuentes() -> None:
    # Arrange
    client = make_client()

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert
    assert response.status_code == 200
    assert "Carvajal nació en 1904" in response.text
    assert 'data-documento-slug="historia"' in response.text
    assert "0.912" in response.text
    assert "etiqueta-oficial" in response.text
    assert "Tipo de fuente:" in response.text
    assert "Capa: Núcleo" in response.text
    assert "texto-formateado" in response.text
    assert '<details class="citas citas-respuesta">' in response.text
    assert 'class="cita-fuente"' in response.text
    assert 'aria-haspopup="dialog"' in response.text
    assert re.search(r'<details class="citas citas-respuesta">\s*<summary>', response.text)


def test_ask_formatea_el_texto_generado_y_sus_citas() -> None:
    # Arrange
    answer = Answer(
        text='## Título\n\n**Dato citado** [1].\n\n> "Una cita textual."',
        sources=(SOURCE,),
    )
    client = make_client(ask=lambda question: answer)

    # Act
    response = _ask(client, "¿?")

    # Assert
    assert response.status_code == 200
    assert "<h2>Título</h2>" in response.text
    assert "<strong>Dato citado</strong>" in response.text
    assert 'href="#cita-1"' in response.text
    assert "<blockquote>" in response.text
    assert "<em>" not in response.text  # la cita va en blockquote, no en énfasis suelto


def test_pregunta_vacia_devuelve_422() -> None:
    # Arrange
    client = make_client()

    # Act
    response = _ask(client, "   ")

    # Assert
    assert response.status_code == 422
    assert "La pregunta no puede estar vacía." in response.text


def test_pregunta_vacia_no_consume_cuota() -> None:
    """La cuota se consulta solo cuando hay una pregunta que responder."""

    # Arrange
    consultas: list[str] = []

    def quota(owner_key: str) -> QuotaResult:
        consultas.append(owner_key)
        return QuotaResult(allowed=True)

    client = make_client(quota=quota)

    # Act
    _ask(client, "   ")

    # Assert
    assert consultas == []


def test_ask_agota_la_cuota_diaria() -> None:
    """Al agotar el cupo, 429 con la hora de reinicio y un aviso legible."""

    # Arrange
    client = make_client(quota=lambda owner: QuotaResult(allowed=False, retry_after=120))

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert "límite diario" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]


def test_la_cuota_bloquea_antes_de_llamar_al_modelo() -> None:
    """Sin cupo no se gasta una llamada al LLM."""

    # Arrange
    preguntas: list[str] = []

    def ask(question: str) -> Answer:
        preguntas.append(question)
        return ANSWER

    client = make_client(ask=ask, quota=lambda owner: QuotaResult(allowed=False))

    # Act
    _ask(client, "¿Cuándo nació?")

    # Assert
    assert preguntas == []


def test_ask_usa_el_dueno_de_la_cookie_valida() -> None:
    """El cupo se cobra al visitante que ya traía una cookie firmada."""

    # Arrange
    consultas: list[str] = []

    def quota(owner_key: str) -> QuotaResult:
        consultas.append(owner_key)
        return QuotaResult(allowed=True)

    client = make_client(quota=quota)
    client.cookies.set(COOKIE_NAME, IDENTITY.issue("dueno-a"))

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert
    assert response.status_code == 200
    assert consultas == ["dueno-a"]
    assert "set-cookie" not in response.headers


def test_ask_emite_identidad_y_la_reutiliza_en_la_siguiente_pregunta() -> None:
    """Sin cookie, /ask genera el dueño, lo firma y lo conserva entre turnos."""

    # Arrange
    consultas: list[str] = []

    def quota(owner_key: str) -> QuotaResult:
        consultas.append(owner_key)
        return QuotaResult(allowed=True)

    client = make_client(quota=quota)

    # Act
    primera = _ask(client, "¿Cuándo nació?")
    segunda = _ask(client, "¿Dónde nació?")

    # Assert
    assert primera.status_code == 200 and segunda.status_code == 200
    assert COOKIE_NAME in primera.headers["set-cookie"]
    assert "set-cookie" not in segunda.headers
    assert len(consultas) == 2
    assert consultas[0] == consultas[1]
    assert len(consultas[0]) == 32


def test_fallo_de_base_al_consultar_la_cuota_devuelve_503() -> None:
    """Un fallo de BD en la cuota es 503, no 500, y no llega al LLM."""

    # Arrange
    preguntas: list[str] = []

    def ask(question: str) -> Answer:
        preguntas.append(question)
        return ANSWER

    def quota(owner_key: str) -> QuotaResult:
        raise SQLAlchemyError("connection to 127.0.0.1:5432 as user carvajal failed")

    client = make_client(ask=ask, quota=quota)

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert "127.0.0.1" not in response.text and "carvajal" not in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert preguntas == []


def test_error_interno_no_filtra_detalles() -> None:
    # Arrange: un ValueError interno (p. ej. dimensión del vector) no es Culpa
    # del cliente y su mensaje no debe llegar al HTML.
    client = make_client(
        ask=failing(ValueError("El vector debe tener 768 dimensiones; recibidas 1024.")),
        raise_server_exceptions=False,
    )

    # Act
    response = _ask(client, "¿?")

    # Assert
    assert response.status_code == 500
    assert "1024" not in response.text
    assert "dimensiones" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        EmbeddingError("No se pudo contactar con Ollama."),
        LLMError("El proveedor de LLM devolvió una respuesta vacía."),
    ],
)
def test_fallos_de_proveedor_devuelven_503(error: Exception) -> None:
    # Arrange
    client = make_client(ask=failing(error))

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert str(error) not in response.text
    assert "Traceback" not in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]


def test_fallo_inesperado_no_se_convierte_en_503() -> None:
    # Arrange
    client = make_client(
        ask=failing(RuntimeError("detalle interno")), raise_server_exceptions=False
    )

    # Act
    response = _ask(client, "¿Cuándo nació?")

    # Assert: el 500 es genérico y no filtra el mensaje interno.
    assert response.status_code == 500
    assert SERVICE_UNAVAILABLE in response.text
    assert "detalle interno" not in response.text


def test_chat_carga_htmx_local() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "/static/htmx.min.js" in response.text
    assert (main.APP_DIR / "static" / "htmx.min.js").is_file()


def test_la_salida_escapa_html() -> None:
    # Arrange
    peligro = "<script>alert(1)</script>"
    source = Source("x-1", "doc", "seccion", "core", 0.5, peligro)
    client = make_client(ask=lambda question: Answer(text=peligro, sources=(source,)))

    # Act
    response = _ask(client, "¿?")

    # Assert
    assert response.status_code == 200
    assert peligro not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_create_app_difiere_los_handlers_a_la_primera_peticion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    calls: list[int] = []

    def fake_handlers() -> Handlers:
        calls.append(1)
        return Handlers(ask=lambda question: ANSWER, health=lambda: HealthStatus(1, 2))

    monkeypatch.setattr(main, "_default_handlers", fake_handlers)
    app = create_app()
    assert calls == []

    # Act
    response = TestClient(app).get("/health")

    # Assert
    assert response.status_code == 200
    assert calls == [1]
    assert response.json() == {
        "status": "ok",
        "documents": 1,
        "chunks": 2,
        "index": {"status": "not_configured"},
    }


def test_los_handlers_reales_no_construyen_el_llm_al_arrancar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construir handlers no importa LangChain ni exige credenciales del LLM."""

    # Arrange: la fábrica de sesiones y los clientes se sustituyen por dobles.
    llamadas: list[int] = []

    def fake_components() -> tuple[object, object]:
        llamadas.append(1)
        return object(), object()

    monkeypatch.setattr(main, "session_factory", lambda: object())
    monkeypatch.setattr(main, "ask_components", fake_components)
    main._default_handlers.cache_clear()

    try:
        # Act: construir los handlers reales no debe tocar los clientes.
        handlers = main._default_handlers()
    finally:
        main._default_handlers.cache_clear()

    # Assert
    assert isinstance(handlers, Handlers)
    assert llamadas == []


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


def test_la_fuente_escapa_el_extracto_en_los_atributos() -> None:
    # Arrange: comillas y ángulos no deben romper el atributo del botón.
    source = Source("x-1", "doc", "seccion", "core", 0.5, 'Texto con "comillas" & <b>')
    client = make_client(ask=lambda question: Answer(text="Hola", sources=(source,)))

    # Act
    response = _ask(client, "¿?")

    # Assert
    assert response.status_code == 200
    assert 'data-documento-excerpt="Texto con &#34;comillas&#34; &amp; &lt;b&gt;"' in response.text
