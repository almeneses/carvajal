"""Pruebas HTTP del chat con un proveedor falso: sin red ni base de datos."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    VISITOR_COOKIE,
    SignedCookieIdentity,
)
from apps.api.chat.service import ThreadAccessError
from apps.api.chat.streaming import ChatEvent
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.views.chat import (
    EMPTY_QUESTION,
    SERVICE_UNAVAILABLE,
    TOO_LONG_QUESTION,
)
from carvajal.adapters.embeddings import EmbeddingError
from carvajal.adapters.storage.chat import ChatMessage, Conversation, ConversationSummary
from carvajal.application.ask import Answer, LLMError, Source
from carvajal.application.quota import QuotaResult

SOURCE = Source("historia-1", "historia", "historia", "core", 0.912, "Inició en 1904.")
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
WHEN = datetime(2026, 11, 12, tzinfo=UTC)


@dataclass
class FakeChat:
    """Doble en memoria del servicio; solo persiste si la respuesta termina bien."""

    by_owner: dict[str, list[ChatMessage]] = field(default_factory=dict)
    by_thread: dict[str, list[ChatMessage]] = field(default_factory=dict)
    summaries: dict[str, list[ConversationSummary]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    turn_calls: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    history_calls: list[str] = field(default_factory=list)
    thread_calls: list[str | None] = field(default_factory=list)
    error: Exception | None = None
    history_error: Exception | None = None

    def history(
        self,
        owner_key: str,
        thread_id: str | None = None,
    ) -> Sequence[ChatMessage]:
        self.history_calls.append(owner_key)
        self.thread_calls.append(thread_id)
        if self.history_error is not None:
            raise self.history_error
        if thread_id is not None:
            return list(self.by_thread.get(thread_id, []))
        return list(self.by_owner.get(owner_key, []))

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        return list(self.summaries.get(owner_key, []))

    def new_conversation(self, owner_key: str) -> Conversation:
        return Conversation(
            id="hilo-nuevo",
            owner_key=owner_key,
            created_at=WHEN,
            updated_at=WHEN,
        )

    def ask(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Answer:
        self.calls.append((owner_key, question))
        self.turn_calls.append((owner_key, client_turn_id, thread_id))
        if self.error is not None:
            raise self.error
        answer = Answer(text=f"Respuesta a: {question}", sources=(SOURCE,))
        self.by_owner.setdefault(owner_key, []).extend(
            [
                _message("user", question, sources=None),
                _message("assistant", answer.text, sources=[asdict(SOURCE)]),
            ]
        )
        return answer

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, question))
        self.turn_calls.append((owner_key, client_turn_id, thread_id))
        yield ChatEvent("meta", {"sources": []})
        yield ChatEvent("token", {"text": f"Respuesta a: {question}"})
        yield ChatEvent(
            "done",
            {
                "conversation_id": thread_id or "conversacion",
                "message_id": "m-bot",
                "thread_id": thread_id or "conversacion",
                "route": "replay",
                "quote_id": None,
            },
        )


@dataclass
class FakeIdentity:
    """Identidad fija para probar la inyección sin pasar por cookies reales."""

    owner_key: str = "dueno-fijo"

    def read(self, request: object) -> str:
        return self.owner_key

    def issue(self, owner_key: str) -> str:
        return f"firma::{owner_key}"

    def new_owner_key(self) -> str:
        return "dueno-nuevo"


def _message(role: str, content: str, *, sources: list[dict[str, object]] | None) -> ChatMessage:
    return ChatMessage(
        id=f"{role}-{content}",
        conversation_id="conversacion",
        role=role,
        content=content,
        sources=sources,
        created_at=WHEN,
    )


def make_client(
    chat: FakeChat | None = None,
    identity: SignedCookieIdentity | FakeIdentity | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
    *,
    base_url: str = "http://testserver",
) -> tuple[TestClient, FakeChat]:
    """Cliente con proveedor, identidad y cuota inyectados; por defecto responden bien."""

    provider = chat or FakeChat()
    app = create_app()
    app.state.chat = provider
    app.state.chat_identity = identity or IDENTITY
    app.state.quota = quota or (lambda owner: QuotaResult(allowed=True))
    return TestClient(app, base_url=base_url), provider


def _cookie(owner_key: str) -> str:
    return IDENTITY.issue(owner_key)


def _post(client: TestClient, path: str, data: dict[str, str], **kwargs: Any) -> Any:
    """Envía un POST con el token CSRF emitido por una página."""

    if CSRF_COOKIE not in client.cookies:
        client.get("/api/csrf")
    return client.post(
        path,
        data={**data, CSRF_FIELD: client.cookies[CSRF_COOKIE]},
        **kwargs,
    )


def test_get_sin_cookie_muestra_el_estado_vacio_y_emite_cookie() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Todavía no hay mensajes" in response.text
    assert "<textarea" in response.text
    assert "data-chat-sugerencia" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.history_calls == []


def test_la_cookie_http_es_httponly_lax_y_sin_secure() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert f"{COOKIE_NAME}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={COOKIE_MAX_AGE}" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_la_cookie_https_marca_secure() -> None:
    # Arrange
    client, _ = make_client(base_url="https://testserver")

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_get_con_cookie_muestra_el_historial_en_orden() -> None:
    # Arrange
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "primera pregunta", sources=None),
        _message("assistant", "**primera respuesta** [1].", sources=[asdict(SOURCE)]),
        _message("user", "segunda pregunta", sources=None),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert chat.history_calls == ["dueno-a"]
    primera = response.text.index("primera pregunta")
    respuesta = response.text.index("primera respuesta")
    segunda = response.text.index("segunda pregunta")
    assert primera < respuesta < segunda
    assert "Fuentes citadas" not in response.text
    assert "#cita-1" not in response.text
    assert "<strong>primera respuesta</strong>." in response.text
    assert """data-voz-fuentes='["historia"]'""" in response.text


def test_get_con_cookie_manipulada_regenera_sin_romper() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, "dueno-a.firma-falsa")

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert "Todavía no hay mensajes" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.history_calls == []


def test_post_vacio_devuelve_422_sin_persistir() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = _post(client, "/chat", {"question": "   "})

    # Assert
    assert response.status_code == 422
    assert "<!doctype html>" in response.text
    assert "<header" in response.text
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert EMPTY_QUESTION in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []
    assert chat.by_owner == {}


def test_post_demasiado_largo_devuelve_422_sin_persistir() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = _post(client, "/chat", {"question": "x" * 501})

    # Assert
    assert response.status_code == 422
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert TOO_LONG_QUESTION in response.text
    assert "500" in response.text
    assert ">" + "x" * 501 + "</textarea>" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []
    assert chat.by_owner == {}


def test_post_ok_redirige_emite_cookie_y_conserva_el_historial() -> None:
    # Arrange
    client, chat = make_client()

    # Act
    response = _post(
        client,
        "/chat",
        {"question": "¿Cuándo nació?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert len(chat.calls) == 1
    assert chat.calls[0][1] == "¿Cuándo nació?"

    # Act: la recarga con la cookie que dejó el POST conserva la conversación.
    reloaded = client.get("/chat")

    # Assert
    assert reloaded.status_code == 200
    assert "¿Cuándo nació?" in reloaded.text
    assert "Respuesta a: ¿Cuándo nació?" in reloaded.text
    assert reloaded.text.index("¿Cuándo nació?") < reloaded.text.index("Respuesta a:")


def test_post_usa_el_dueno_de_la_cookie_valida() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = _post(
        client,
        "/chat",
        {"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert chat.calls == [("dueno-a", "¿Qué es Carvajal?")]


def test_el_hilo_unificado_conserva_voz_y_consulta_institucional() -> None:
    """T026: el chat único mantiene dictado/lectura y responde con fuentes."""

    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))
    portada = client.get("/chat")

    # Act
    respuesta = _post(client, "/chat", {"question": "¿Qué es Carvajal?"})

    # Assert
    assert portada.status_code == 200
    assert "/static/chat-voice.js" in portada.text
    assert "data-voz-dictar" in portada.text
    assert respuesta.status_code == 200
    assert chat.calls == [("dueno-a", "¿Qué es Carvajal?")]
    assert "¿Qué es Carvajal?" in respuesta.text
    assert """data-voz-fuentes='["historia"]'""" in respuesta.text


def test_post_con_cookie_invalida_regenera_la_identidad() -> None:
    # Arrange
    client, chat = make_client()
    client.cookies.set(COOKIE_NAME, "dueno-manipulado.firma-falsa")

    # Act
    response = _post(
        client,
        "/chat",
        {"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert len(chat.calls) == 1
    assert chat.calls[0][0] != "dueno-manipulado"


@pytest.mark.parametrize(
    "error",
    [
        LLMError("detalle-interno-llm"),
        EmbeddingError("detalle-interno-embedder"),
        SQLAlchemyError("detalle-interno-base"),
    ],
)
def test_fallos_del_proveedor_devuelven_503_sin_filtrar_ni_persistir(error: Exception) -> None:
    # Arrange
    client, chat = make_client(chat=FakeChat(error=error))

    # Act
    response = _post(client, "/chat", {"question": "¿Cuándo nació?"})

    # Assert
    assert response.status_code == 503
    assert "<form" in response.text
    assert 'id="pregunta"' in response.text
    assert ">¿Cuándo nació?</textarea>" in response.text
    assert SERVICE_UNAVAILABLE in response.text
    assert str(error) not in response.text
    assert "Traceback" not in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.by_owner == {}


def test_el_error_503_conserva_el_historial_previo() -> None:
    # Arrange
    client, chat = make_client(chat=FakeChat(error=LLMError("caído")))
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))
    chat.by_owner["dueno-a"] = [_message("user", "pregunta previa", sources=None)]

    # Act
    response = _post(client, "/chat", {"question": "pregunta nueva\n</textarea>"})

    # Assert
    assert response.status_code == 503
    assert "pregunta previa" in response.text
    assert ">pregunta nueva\n&lt;/textarea&gt;</textarea>" in response.text
    assert chat.calls == [("dueno-a", "pregunta nueva\n</textarea>")]
    assert [message.content for message in chat.by_owner["dueno-a"]] == ["pregunta previa"]


def test_fallo_de_base_al_leer_el_historial_devuelve_503_generico() -> None:
    # Arrange
    client, _ = make_client(
        chat=FakeChat(history_error=SQLAlchemyError("127.0.0.1:5432 rechazó la conexión"))
    )
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert "127.0.0.1" not in response.text
    assert "rechazó la conexión" not in response.text


def test_el_historial_escapa_html() -> None:
    # Arrange
    peligro = "<script>alert(1)</script>"
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [_message("assistant", peligro, sources=None)]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert peligro not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_la_identidad_se_puede_inyectar_sin_cookies_reales() -> None:
    # Arrange
    client, chat = make_client(identity=FakeIdentity(owner_key="dueno-fijo"))

    # Act
    response = _post(
        client,
        "/chat",
        {"question": "¿Qué es Carvajal?"},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert "firma::dueno-fijo" in response.headers["set-cookie"]
    assert chat.calls == [("dueno-fijo", "¿Qué es Carvajal?")]


def test_post_chat_agota_la_cuota_antes_de_llamar_al_proveedor() -> None:
    """El cupo también frena el chat normal, no solo /ask."""

    # Arrange
    client, chat = make_client(quota=lambda owner: QuotaResult(False, 120))

    # Act
    response = _post(client, "/chat", {"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert "límite diario" in response.text
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert chat.calls == []


def test_fallo_de_bd_en_la_cuota_del_chat_devuelve_503() -> None:
    """Un fallo al consultar la cuota no se convierte en 500 ni llama al proveedor."""

    # Arrange
    def quota(owner: str) -> QuotaResult:
        raise SQLAlchemyError("base caída")

    client, chat = make_client(quota=quota)

    # Act
    response = _post(client, "/chat", {"question": "¿Qué es Carvajal?"})

    # Assert
    assert response.status_code == 503
    assert SERVICE_UNAVAILABLE in response.text
    assert chat.calls == []


def test_get_thread_selecciona_el_historial_y_marca_el_hilo_activo() -> None:
    # Arrange
    client, chat = make_client()
    chat.summaries["dueno-a"] = [
        ConversationSummary("hilo-1", "pregunta anterior", WHEN, WHEN),
        ConversationSummary("hilo-2", "pregunta del hilo 2", WHEN, WHEN),
    ]
    chat.by_thread["hilo-2"] = [_message("user", "pregunta del hilo 2", sources=None)]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat?thread=hilo-2")

    # Assert
    assert response.status_code == 200
    assert chat.thread_calls == ["hilo-2"]
    assert "pregunta del hilo 2" in response.text
    assert 'href="/chat?thread=hilo-2"' in response.text
    assert 'id="panel-hilos"' in response.text
    assert 'data-abrir="0"' in response.text
    assert response.text.index('id="hilo"') < response.text.index('class="formulario composer"')


def test_nuevo_hilo_redirige_al_hilo_nuevo_y_abre_el_panel() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = _post(client, "/chat/new", {}, follow_redirects=False)

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat?thread=hilo-nuevo&panel=hilos"


def test_el_panel_de_hilos_puede_abrirse_desde_la_url() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat?panel=hilos")

    # Assert
    assert response.status_code == 200
    assert 'data-abrir="1"' in response.text


def test_la_cookie_de_visitante_carga_los_hilos_del_hash() -> None:
    # Arrange: el hash de localStorage se refleja en carvajal_visitante.
    client, chat = make_client()
    visitante = "c" * 64
    esperado = IDENTITY._visitor_owner(visitante)
    chat.by_owner[esperado] = [_message("user", "hilo del visitante", sources=None)]
    client.cookies.set(VISITOR_COOKIE, visitante)

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert chat.history_calls == [esperado]
    assert "hilo del visitante" in response.text


def test_el_chat_reserva_el_titulo_de_la_fuente_extendida_para_voz() -> None:
    # Arrange: los metadatos de una fuente extendida no son parte de la vista.
    fuente = Source(
        "informe:0",
        "informe-empaques",
        "prensa",
        "extended",
        0.8,
        "Informe de terceros.",
        title="Informe d'Empaques",
        source_type="tercero",
        origin="Cámara de Comercio de Cali",
        category="informe",
        entity="Carvajal Empaques",
    )
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [_message("assistant", "Respuesta [1].", sources=[asdict(fuente)])]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    assert response.status_code == 200
    assert r"""data-voz-fuentes='["Informe d\u0027Empaques"]'""" in response.text
    assert "Informe de terceros." not in response.text
    assert "Cámara de Comercio de Cali" not in response.text
    assert "<p>Respuesta.</p>" in response.text


def test_el_boton_de_dictar_no_envia_y_conserva_el_formulario() -> None:
    """T007: el micrófono es un control aparte del envío escrito."""

    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    assert response.status_code == 200
    inicio = html.index("data-voz-dictar")
    fragmento = html[max(0, inicio - 220) : inicio + 220]
    assert 'type="button"' in fragmento
    assert 'action="/chat"' in html
    assert 'method="post"' in html
    assert f'name="{CSRF_FIELD}"' in html
    assert 'name="question"' in html
    assert 'maxlength="500"' in html
    assert "data-chat-stream" in html
    assert "/static/chat-voice.js" in html


def test_el_estado_de_voz_y_el_aviso_son_accesibles() -> None:
    """T007: nombre accesible, estado anunciado y aviso previo etiquetado."""

    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    estado = html.index("data-voz-estado")
    fragmento_estado = html[max(0, estado - 160) : estado + 120]
    assert 'role="status"' in fragmento_estado
    assert 'aria-live="polite"' in fragmento_estado
    inicio = html.index("data-voz-aviso")
    fragmento_aviso = html[max(0, inicio - 160) : inicio + 400]
    assert "<dialog" in fragmento_aviso
    assert 'aria-labelledby="voz-aviso-titulo"' in fragmento_aviso
    assert 'id="voz-aviso-titulo"' in html
    assert "data-voz-aviso-aceptar" in html
    assert "data-voz-aviso-cancelar" in html
    assert "escribir" in html
    assert 'aria-label="Dictar una pregunta"' in html


def test_el_historial_ofrece_escuchar_solo_en_los_turnos_del_asistente() -> None:
    """T013: control de lectura por turno, con pausa, reanudación y parada."""

    # Arrange
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "pregunta", sources=None),
        _message("assistant", "respuesta", sources=None),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    assert response.status_code == 200
    assert html.count('data-voz-accion="escuchar"') == 2  # historial + plantilla del stream
    for accion in ("pausar", "reanudar", "detener"):
        assert f'data-voz-accion="{accion}"' in html
    assert html.count('class="turno-voz"') == 2
    inicio = html.index('data-voz-accion="escuchar"')
    assert 'aria-pressed="false"' in html[inicio : inicio + 120]
    assert "data-voz-turno" in html


def test_el_stream_notifica_el_turno_completo_solo_tras_done() -> None:
    """T015: el aviso interno no se emite por error ni sin completar."""

    # Arrange
    from apps.api.runtime import APP_DIR

    # Act
    js = (APP_DIR / "static" / "chat-stream.js").read_text(encoding="utf-8")

    # Assert
    assert 'new window.CustomEvent("chat:turn-complete"' in js
    assert "completado && !errorRecibido" in js
    assert "detail:" in js
    assert "threadId" in js


def test_la_autolectura_tiene_control_etiquetado_y_oculto_sin_js() -> None:
    """B1: FR-007 exige un control real, no solo lógica en el cliente."""

    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    assert "data-voz-autolectura-campo" in html
    assert 'type="checkbox"' in html
    assert "data-voz-autolectura" in html
    assert "Leer respuestas en voz alta" in html
    inicio = html.index("data-voz-autolectura-campo")
    assert "hidden" in html[max(0, inicio - 60) : inicio + 140]


def test_el_historial_incluye_sus_propios_controles_de_lectura() -> None:
    """T013: los controles se verifican dentro de #hilo, no por coincidencia global."""

    # Arrange
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "pregunta", sources=None),
        _message("assistant", "respuesta", sources=None),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    historial = html.split('id="hilo"', 1)[1].split("</section>", 1)[0]
    for accion in ("escuchar", "pausar", "reanudar", "detener"):
        assert f'data-voz-accion="{accion}"' in historial
    assert historial.count("data-voz-turno") == 1
    inicio = historial.index('data-voz-accion="escuchar"')
    assert 'aria-pressed="false"' in historial[max(0, inicio - 160) : inicio + 120]


def test_el_stream_no_notifica_turno_completo_en_la_rama_de_error() -> None:
    """T015: la notificación nace solo del bloque done, nunca del error."""

    # Arrange
    from apps.api.runtime import APP_DIR

    # Act
    js = (APP_DIR / "static" / "chat-stream.js").read_text(encoding="utf-8")

    # Assert
    bloque_done = js.split('case "done":', 1)[1].split("case ", 1)[0]
    bloque_error = js.split('case "error":', 1)[1].split("default:", 1)[0]
    assert "completado = true" in bloque_done
    assert "notificarTurnoCompleto" not in bloque_error
    assert "errorRecibido = true" in bloque_error


def test_manos_libres_se_ofrece_con_aviso_de_autoenvio_y_apagado() -> None:
    """T019: activación explícita, aviso de autoenvío y estado apagado al cargar."""

    # Arrange
    client, _ = make_client()

    # Act
    response = client.get("/chat")

    # Assert
    html = response.text
    assert response.status_code == 200
    assert "data-voz-manos-libres" in html
    assert 'aria-pressed="false"' in html
    assert "data-voz-manos-aviso" in html
    assert "data-voz-manos-aceptar" in html
    assert "data-voz-manos-cancelar" in html
    assert 'id="voz-manos-titulo"' in html
    assert "sin que la revises" in html
    assert "borrador vacío" in html


def test_el_stream_distingue_manos_libres_solo_en_el_cliente() -> None:
    """T021: marcador local, sin campos nuevos ni reenvío automático."""

    # Arrange
    from apps.api.runtime import APP_DIR

    # Act
    stream = (APP_DIR / "static" / "chat-stream.js").read_text(encoding="utf-8")
    voz = (APP_DIR / "static" / "chat-voice.js").read_text(encoding="utf-8")

    # Assert
    assert 'form.dataset.vozOrigen === "manos-libres"' in stream
    assert "chat:turn-failed" in stream
    assert "handsFree: Boolean(manosLibres)" in stream
    assert "formulario.requestSubmit()" in voz
    assert "chat:turn-failed" in voz
    assert "vozOrigen" in voz
    # El origen no se envía: sin campos nuevos en el cuerpo del POST.
    assert "vozOrigen:" not in stream
    assert 'name="voz' not in (APP_DIR / "templates" / "chat.html").read_text(encoding="utf-8")


def test_el_fallo_de_manos_libres_devuelve_siempre_la_pregunta() -> None:
    """T021: sin done el turno no se persiste y el borrador vuelve siempre."""

    # Arrange
    from apps.api.runtime import APP_DIR

    # Act
    stream = (APP_DIR / "static" / "chat-stream.js").read_text(encoding="utf-8")

    # Assert
    assert "if (!input.value.trim()) input.value = pregunta;" in stream
    assert "if (!texto.textContent) turnoAsistente.remove();" in stream


def test_el_post_html_propaga_el_identificador_de_turno() -> None:
    client, chat = make_client(identity=FakeIdentity(owner_key="dueno-fijo"))

    respuesta = _post(
        client,
        "/chat",
        {"question": "¿Cuándo nació?", "client_turn_id": "turno-html-1"},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert chat.turn_calls == [("dueno-fijo", "turno-html-1", None)]


def test_el_stream_html_propaga_el_identificador_de_turno() -> None:
    client, chat = make_client(identity=FakeIdentity(owner_key="dueno-fijo"))

    respuesta = _post(
        client,
        "/chat/stream",
        {"question": "¿Cuándo nació?", "client_turn_id": "turno-sse-1"},
    )

    assert respuesta.status_code == 200
    assert chat.turn_calls == [("dueno-fijo", "turno-sse-1", None)]
    assert "event: done" in respuesta.text


def test_el_stream_de_la_api_propaga_el_identificador_de_turno() -> None:
    client, chat = make_client(identity=FakeIdentity(owner_key="dueno-fijo"))

    respuesta = _post(
        client,
        "/api/chat/stream",
        {"question": "¿Cuándo nació?", "client_turn_id": "turno-api-1"},
    )

    assert respuesta.status_code == 200
    assert chat.turn_calls == [("dueno-fijo", "turno-api-1", None)]


def _resultado_comercial() -> dict[str, Any]:
    return {
        "ficha": {
            "sku": "7704918100199",
            "name": "Vaso rígido traslúcido 9 oz Wau",
            "material": "PP",
            "capacity_oz": 9.0,
            "units_per_box": 2750,
            "uses": ["bebida_caliente"],
            "source_url": "https://example.com/ficha",
            "captured_at": "2026-11-18",
        },
        "offers": [
            {
                "offer_id": "CO-OFERTA-1",
                "seller": "EDEXA",
                "seller_kind": "distribuidor",
                "presentation": "paquete de 50",
                "amount": "3751",
                "currency": "COP",
                "tax_status": "incluido",
                "tax_rate": None,
                "source_url": "https://example.com/precio",
                "captured_at": "2026-11-18",
                "valid_until": None,
                "availability": "desconocido",
                "site_id": "CO-SEDE-1",
            }
        ],
        "sites": [
            {
                "site_id": "CO-SEDE-1",
                "name": "EDEXA",
                "city": "Bogotá",
                "address": "Km 1.3",
                "phone": "601 000 0000",
                "precision": "ciudad",
                "source_url": "https://example.com/directorio",
                "captured_at": "2026-10-23",
                "linked": True,
                "availability": "desconocido",
            }
        ],
        "cities": ["Bogotá", "Cali"],
        "quote": {
            "kind": "cotizacion",
            "city": "Bogotá",
            "total": "3751",
            "primary": {
                "currency": "COP",
                "units_requested": 20,
                "presentation": {
                    "basis": "paquete",
                    "count": 1,
                    "units_bought": 50,
                    "units_surplus": 30,
                    "price_per_presentation": "3751",
                    "subtotal": "3751",
                    "units_requested": 20,
                },
            },
            "commercial": {
                "subtotal": "3751",
                "total": "3751",
                "valid_until": None,
                "shipping": {"requested": False, "status": "not_requested", "cost": None},
            },
            "quote": {
                "id": "folio-abc",
                "folio": "folio-abc",
                "issued_at": "2026-11-23T10:00:00+00:00",
            },
            "pending_lines": [],
        },
        "clarifications": [],
        "request": {"city": "Bogotá"},
    }


def test_el_conteo_de_citas_ignora_el_resultado_comercial() -> None:
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "cotiza 20 vasos", sources=None),
        _message(
            "assistant",
            "Subtotal [1] y cierre [2].",
            sources=[
                asdict(SOURCE),
                {"result_type": "commercial", "result": _resultado_comercial()},
            ],
        ),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    respuesta = client.get("/chat")

    assert respuesta.status_code == 200
    assert "[1]" not in respuesta.text
    # Solo hay una cita real: [2] queda como texto y no se descarta por el
    # resultado comercial sintético.
    assert "[2]" in respuesta.text


def test_la_recarga_restaura_ficha_oferta_sede_y_folio() -> None:
    client, chat = make_client()
    chat.by_owner["dueno-a"] = [
        _message("user", "cotiza 20 vasos", sources=None),
        _message(
            "assistant",
            "Cotización lista.",
            sources=[
                asdict(SOURCE),
                {"result_type": "commercial", "result": _resultado_comercial()},
            ],
        ),
    ]
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    respuesta = client.get("/chat")

    assert respuesta.status_code == 200
    assert "Vaso rígido traslúcido 9 oz Wau" in respuesta.text
    assert "#folio-abc" in respuesta.text
    assert "EDEXA" in respuesta.text
    assert "vende este SKU" in respuesta.text
    assert "En Cali" in respuesta.text
    assert "data-chat-sugerencia" in respuesta.text
    assert "¿Cuándo nació?" in respuesta.text or "cotiza 20 vasos" in respuesta.text


class _Aislado(FakeChat):
    def history(
        self,
        owner_key: str,
        thread_id: str | None = None,
    ) -> Sequence[ChatMessage]:
        if thread_id is not None:
            raise ThreadAccessError(thread_id)
        return super().history(owner_key, thread_id)


def test_un_hilo_ajeno_no_se_renderiza() -> None:
    client, _ = make_client(chat=_Aislado())
    client.cookies.set(COOKIE_NAME, _cookie("dueno-a"))

    respuesta = client.get("/chat?thread=ajeno")

    assert respuesta.status_code == 404


def test_el_panel_de_depuracion_muestra_la_decision_del_gatekeeper() -> None:
    """Spec 046 (T050): campo «Decisión» del panel, que solo se ve con ``?debug=1``."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "apps" / "api"
    template = (root / "templates" / "_chat_stream.html").read_text(encoding="utf-8")
    script = (root / "static" / "chat-stream.js").read_text(encoding="utf-8")

    assert 'data-campo="decision"' in template
    assert "Decisión:" in template
    assert "textoDecision(datos.gatekeeper)" in script
    assert 'get("debug") === "1"' in script
