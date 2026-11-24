"""Pruebas HTTP de la voz del chat por el servidor (spec 047): ``/api/voz/*`` y ``/health``.

Sin red ni clave real: OpenAI responde desde ``httpx.MockTransport`` y el chat es un doble
en memoria. Cubren la validación del audio (tipo, firma y tamaño antes de leer el cuerpo),
los 503 que devuelven al navegador a su voz, el CSRF, la identidad del chat, el límite por
sesión, el tope diario, el circuito, que ``/hablar`` solo lee respuestas propias guardadas
(nunca texto libre), la caché, el recorte del texto, el bloque ``voice`` de ``/health``, la
página del chat y la CSP con ``media-src blob:`` solo en el documento del chat. También el
repositorio real con SQLite: un id de otro dueño no se encuentra.
"""

import json
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.chat.identity import COOKIE_NAME, VISITOR_COOKIE, SignedCookieIdentity
from apps.api.chat.service import ChatService
from apps.api.chat.streaming import ChatEvent
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSP_CHAT, CSRF_COOKIE, CSRF_FIELD
from apps.api.views.voz import CSRF_HEADER
from apps.api.voice import TRUNCATED_NOTE, RateLimiter, VoiceRuntime, cap_text
from carvajal.adapters.speech.openai import OpenAISpeechClient
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import ChatMessage, Conversation, SqlChatRepository
from carvajal.application.quota import QuotaResult

KEY = "sk-prueba-no-real"
IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
WHEN = datetime(2026, 11, 24, tzinfo=UTC)
OWNER = "dueno-a"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 2000
OGG = b"OggS" + b"\x00" * 2000
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000
QUOTE: dict[str, Any] = {
    "kind": "cotizacion",
    "city": "Cali",
    "primary": {
        "name": "Vaso de papel 12 oz",
        "currency": "COP",
        "presentation": {"basis": "Caja", "units": 1000, "count": 2, "subtotal": 370000},
    },
    "commercial": {"tax": {"status": "incluido", "rate": 0.19}},
    "total": 370000,
    "quote": {"folio": "a3f09c12b7e4"},
}


def _message(
    message_id: str,
    role: str,
    content: str,
    *,
    owner: str = OWNER,
    sources: list[dict[str, Any]] | None = None,
) -> tuple[str, ChatMessage]:
    return owner, ChatMessage(
        id=message_id,
        conversation_id=f"hilo-{owner}",
        role=role,
        content=content,
        sources=sources,
        created_at=WHEN,
    )


@dataclass
class FakeChat:
    """Doble del servicio de chat con ``assistant_message`` como el real."""

    messages: dict[str, tuple[str, ChatMessage]] = field(default_factory=dict)
    lookups: list[tuple[str, str]] = field(default_factory=list)
    error: Exception | None = None

    def add(self, item: tuple[str, ChatMessage]) -> None:
        self.messages[item[1].id] = item

    def assistant_message(self, owner_key: str, message_id: str) -> ChatMessage | None:
        self.lookups.append((owner_key, message_id))
        if self.error is not None:
            raise self.error
        found = self.messages.get(message_id)
        if found is None or found[0] != owner_key or found[1].role != "assistant":
            return None
        return found[1]

    def history(self, owner_key: str, thread_id: str | None = None) -> list[ChatMessage]:
        return [message for owner, message in self.messages.values() if owner == owner_key]

    def conversations(self, owner_key: str) -> list[Any]:
        return []

    def new_conversation(self, owner_key: str) -> Conversation:
        return Conversation(id="hilo", owner_key=owner_key, created_at=WHEN, updated_at=WHEN)

    def stream(self, owner_key: str, question: str, **_: Any) -> Iterator[ChatEvent]:
        yield ChatEvent("done", {})


@dataclass
class OpenAIFake:
    """Transporte falso de OpenAI que anota cada petición."""

    requests: list[httpx.Request] = field(default_factory=list)
    transcript: str = "cotiza 400 vasos de 12 onzas"
    status: int = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": self.transcript})
        return httpx.Response(200, content=b"ID3-mp3")

    def tts_inputs(self) -> list[str]:
        return [
            json.loads(request.content)["input"]
            for request in self.requests
            if request.url.path.endswith("/audio/speech")
        ]


def _ogg(seconds: float, size: int = 2000) -> bytes:
    """Ogg Opus mínimo cuya última página declara ``seconds`` (granulo a 48 kHz)."""

    def page(granule: int, payload: bytes) -> bytes:
        lacing = bytes([255] * (len(payload) // 255) + [len(payload) % 255])
        header = b"OggS" + bytes([0, 0]) + struct.pack("<qIII", granule, 1, 0, 0)
        return header + bytes([len(lacing)]) + lacing + payload

    head = page(0, b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, 48_000, 0, 0))
    filler = b"\x00" * max(0, size - len(head) - 40)
    return head + page(int(seconds * 48_000) + 312, filler)


def _runtime(
    fake: OpenAIFake,
    *,
    limit: int = 20,
    max_calls: int = 500,
    max_upload: int = 100_000,
    tts_max_chars: int = 1200,
    ip_limit: int | None = None,
    global_limit: int | None = None,
) -> VoiceRuntime:
    client = OpenAISpeechClient(
        api_key=KEY,
        base_url="https://api.openai.test/v1",
        max_calls_per_day=max_calls,
        transport=httpx.MockTransport(fake),
    )
    return VoiceRuntime(
        client=client,
        limiter=RateLimiter(limit, 600),
        max_upload_bytes=max_upload,
        tts_max_chars=tts_max_chars,
        max_audio_seconds=60,
        ip_limiter=None if ip_limit is None else RateLimiter(ip_limit, 600),
        global_limiter=None if global_limit is None else RateLimiter(global_limit, 60),
    )


def _app(
    runtime: VoiceRuntime | None = None, chat: FakeChat | None = None
) -> tuple[TestClient, FakeChat]:
    provider = chat or FakeChat()
    app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
    app.state.chat = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: QuotaResult(allowed=True)
    if runtime is not None:
        app.state.voice = runtime
    return TestClient(app), provider


def _session(client: TestClient, owner: str = OWNER) -> str:
    """Cookie del chat y token CSRF, como tras abrir ``/chat``."""

    client.cookies.set(COOKIE_NAME, IDENTITY.issue(owner))
    client.get("/api/csrf")
    return client.cookies[CSRF_COOKIE]


def _transcribe(
    client: TestClient,
    audio: bytes = WEBM,
    media_type: str = "audio/webm;codecs=opus",
    *,
    token: str | None = None,
    header: bool = True,
) -> Any:
    data = {} if token is None or header else {CSRF_FIELD: token}
    headers = {CSRF_HEADER: token} if header and token else {}
    return client.post(
        "/api/voz/transcribir",
        data=data,
        files={"audio": ("dictado.webm", audio, media_type)},
        headers=headers,
    )


def _speak(client: TestClient, message_id: str, token: str) -> Any:
    return client.post("/api/voz/hablar", data={"message_id": message_id, CSRF_FIELD: token})


# --- /api/voz/transcribir ------------------------------------------------------------------


def test_transcribir_devuelve_el_texto_sin_guardar_nada() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = _transcribe(client, token=token)

    # Assert
    assert response.status_code == 200
    assert response.json() == {"texto": "cotiza 400 vasos de 12 onzas"}
    assert response.headers["cache-control"] == "no-store"
    assert len(fake.requests) == 1
    assert WEBM in fake.requests[0].content


@pytest.mark.parametrize(
    ("audio", "media_type"), [(OGG, "audio/ogg;codecs=opus"), (MP4, "audio/mp4")]
)
def test_transcribir_acepta_ogg_y_mp4(audio: bytes, media_type: str) -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = _transcribe(client, audio, media_type, token=token)

    # Assert
    assert response.status_code == 200


def test_transcribir_acepta_el_csrf_por_cabecera() -> None:
    # Arrange
    client, _ = _app(_runtime(OpenAIFake()))
    token = _session(client)

    # Act
    response = _transcribe(client, token=token, header=True)

    # Assert
    assert response.status_code == 200


def test_sin_voz_del_servidor_responde_503_off_sin_red() -> None:
    # Arrange: configuración por defecto (VOICE_PROVIDER=browser).
    client, _ = _app()
    token = _session(client)

    # Act
    transcribed = _transcribe(client, token=token)
    spoken = _speak(client, "m-1", token)

    # Assert
    for response in (transcribed, spoken):
        assert response.status_code == 503
        assert response.json()["reason"] == "off"
        assert "navegador" in response.json()["detail"]


def test_openai_sin_clave_tambien_es_503_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("VOICE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    client, _ = _app()
    token = _session(client)

    # Act
    response = _transcribe(client, token=token)

    # Assert
    assert response.status_code == 503
    assert response.json()["reason"] == "off"


def test_transcribir_sin_csrf_es_403_sin_llamar_a_openai() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    _session(client)

    # Act
    response = _transcribe(client, token="token-falso")

    # Assert
    assert response.status_code == 403
    assert response.json()["reason"] == "csrf"
    assert fake.requests == []


def test_transcribir_exige_la_sesion_del_chat() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    client.get("/api/csrf")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = _transcribe(client, token=token)

    # Assert
    assert response.status_code == 403
    assert response.json()["reason"] == "no_session"
    assert fake.requests == []


def test_un_cuerpo_mayor_que_el_tope_se_rechaza_antes_de_leerlo() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake, max_upload=10_000))
    token = _session(client)

    # Act
    response = _transcribe(client, WEBM + b"\x00" * 40_000, token=token)

    # Assert
    assert response.status_code == 413
    assert response.json()["reason"] == "too_large"
    assert fake.requests == []


def test_un_audio_mayor_que_el_tope_dentro_del_margen_tambien_es_413() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake, max_upload=1_500))
    token = _session(client)

    # Act
    response = _transcribe(client, WEBM, token=token)

    # Assert
    assert response.status_code == 413
    assert fake.requests == []


@pytest.mark.parametrize(
    ("audio", "media_type"),
    [
        (WEBM, "text/plain"),
        (WEBM, "audio/wav"),
        (OGG, "audio/webm"),
        (b"<html>" + b"\x00" * 2000, "audio/ogg"),
    ],
)
def test_tipo_o_firma_de_audio_no_admitidos_son_415(audio: bytes, media_type: str) -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = _transcribe(client, audio, media_type, token=token)

    # Assert
    assert response.status_code == 415
    assert response.json()["reason"] == "unsupported_type"
    assert fake.requests == []


def test_audio_demasiado_corto_o_transcripcion_vacia_son_422() -> None:
    # Arrange
    fake = OpenAIFake(transcript="   ")
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    short = _transcribe(client, WEBM[:100], token=token)
    empty = _transcribe(client, token=token)

    # Assert
    assert short.status_code == 422 and short.json()["reason"] == "empty"
    assert empty.status_code == 422 and empty.json()["reason"] == "empty"
    assert len(fake.requests) == 1


def test_sin_campo_audio_es_400() -> None:
    # Arrange
    client, _ = _app(_runtime(OpenAIFake()))
    token = _session(client)

    # Act
    response = client.post(
        "/api/voz/transcribir", data={"otro": "campo"}, headers={CSRF_HEADER: token}
    )

    # Assert
    assert response.status_code == 400
    assert response.json()["reason"] == "bad_request"


def test_el_limite_por_sesion_responde_429_con_retry_after() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake, limit=2))
    token = _session(client)

    # Act
    responses = [_transcribe(client, token=token) for _ in range(3)]

    # Assert
    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[2].json()["reason"] == "rate_limited"
    assert int(responses[2].headers["retry-after"]) > 0
    assert len(fake.requests) == 2


def test_el_limite_es_por_sesion() -> None:
    # Arrange
    runtime = _runtime(OpenAIFake(), limit=1)
    client_a, _ = _app(runtime)
    client_b, _ = _app(runtime)
    token_a = _session(client_a, "dueno-a")
    token_b = _session(client_b, "dueno-b")

    # Act
    first = _transcribe(client_a, token=token_a)
    other = _transcribe(client_b, token=token_b)

    # Assert
    assert first.status_code == 200
    assert other.status_code == 200


def test_tope_diario_alcanzado_es_503_cap() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake, max_calls=1))
    token = _session(client)

    # Act
    first = _transcribe(client, token=token)
    second = _transcribe(client, token=token)

    # Assert
    assert first.status_code == 200
    assert second.status_code == 503
    assert second.json()["reason"] == "cap"
    assert len(fake.requests) == 1


def test_circuito_abierto_es_503_circuit_open() -> None:
    # Arrange
    fake = OpenAIFake(status=500)
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    failures = [_transcribe(client, token=token) for _ in range(3)]
    blocked = _transcribe(client, token=token)

    # Assert
    # El motivo interno (``error``) queda en el log; el navegador recibe uno genérico.
    assert [response.json()["reason"] for response in failures] == ["unavailable"] * 3
    assert blocked.status_code == 503
    assert blocked.json()["reason"] == "circuit_open"
    assert len(fake.requests) == 3


def test_transcribir_no_acepta_el_csrf_solo_en_el_formulario() -> None:
    # Arrange
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = _transcribe(client, token=token, header=False)

    # Assert
    assert response.status_code == 403
    assert response.json()["reason"] == "csrf"
    assert fake.requests == []


def test_transcribir_comprueba_sesion_y_limite_antes_de_leer_el_cuerpo() -> None:
    # Arrange: un multipart roto daría 400 si se leyera antes de la identidad y el límite.
    client, _ = _app(_runtime(OpenAIFake(), limit=1))
    token = _session(client)
    broken = {
        CSRF_HEADER: token,
        "content-type": "multipart/form-data; boundary=x",
    }
    stranger, _ = _app(_runtime(OpenAIFake()))
    stranger.get("/api/csrf")
    stranger_token = stranger.cookies[CSRF_COOKIE]

    # Act
    no_session = stranger.post(
        "/api/voz/transcribir",
        content=b"--x\r\nroto",
        headers={**broken, CSRF_HEADER: stranger_token},
    )
    first = client.post("/api/voz/transcribir", content=b"--x\r\nroto", headers=broken)
    limited = client.post("/api/voz/transcribir", content=b"--x\r\nroto", headers=broken)

    # Assert
    assert no_session.status_code == 403 and no_session.json()["reason"] == "no_session"
    assert first.status_code == 400
    assert limited.status_code == 429


def test_rotar_la_identidad_no_salta_el_limite_por_ip() -> None:
    # Arrange: cada petición con una cookie de visitante distinta (la fabrica el cliente).
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake, limit=20, ip_limit=2))
    client.get("/api/csrf")
    token = client.cookies[CSRF_COOKIE]

    # Act
    responses = []
    for index in range(3):
        client.cookies.set(VISITOR_COOKIE, f"{index:064x}")
        responses.append(_transcribe(client, token=token))

    # Assert
    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[2].headers["Retry-After"]
    assert len(fake.requests) == 2


def test_el_limite_global_frena_a_quien_rota_identidad_e_ip() -> None:
    # Arrange: dos sesiones distintas con límite global de 2 por minuto.
    fake = OpenAIFake()
    runtime = _runtime(fake, global_limit=2)
    client_a, _ = _app(runtime)
    client_b, _ = _app(runtime)
    token_a = _session(client_a, "dueno-a")
    token_b = _session(client_b, "dueno-b")

    # Act
    first = _transcribe(client_a, token=token_a)
    second = _transcribe(client_b, token=token_b)
    third = _transcribe(client_b, token=token_b)

    # Assert
    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 429)
    assert len(fake.requests) == 2


def test_un_rechazo_por_un_limite_no_gasta_el_cupo_de_los_otros() -> None:
    # Arrange
    runtime = _runtime(OpenAIFake(), limit=1, ip_limit=5)

    # Act
    first = runtime.admit("dueno-a", "10.0.0.1")
    blocked = [runtime.admit("dueno-a", "10.0.0.1") for _ in range(4)]
    other = runtime.admit("dueno-b", "10.0.0.1")

    # Assert: los 4 rechazos por sesión no contaron contra la IP.
    assert first == 0
    assert all(wait > 0 for wait in blocked)
    assert other == 0


def test_el_limitador_olvida_las_claves_mas_antiguas() -> None:
    # Arrange
    limiter = RateLimiter(1, 600, max_keys=3)

    # Act: miles de claves inventadas.
    for index in range(5000):
        limiter.hit(f"clave-{index}")
    kept = limiter.hit("clave-4999")
    forgotten = limiter.hit("clave-0")

    # Assert: memoria acotada; la reciente sigue limitada y la olvidada vuelve a pasar.
    assert len(limiter) == 3
    assert kept > 0
    assert forgotten == 0


def test_un_audio_que_declara_mas_duracion_que_el_tope_es_413_sin_llamar_a_openai() -> None:
    # Arrange: pocos bytes (Opus a bitrate bajo) pero 10 minutos de audio.
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    long_audio = _transcribe(client, _ogg(600), "audio/ogg;codecs=opus", token=token)
    short_audio = _transcribe(client, _ogg(20), "audio/ogg;codecs=opus", token=token)

    # Assert
    assert long_audio.status_code == 413
    assert long_audio.json()["reason"] == "too_long"
    assert short_audio.status_code == 200
    assert len(fake.requests) == 1


def test_un_audio_que_openai_rechaza_es_422_y_no_abre_el_circuito() -> None:
    # Arrange: firma WebM válida y basura detrás; OpenAI responde 400.
    fake = OpenAIFake(status=400)
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    rejected = [_transcribe(client, token=token) for _ in range(4)]
    fake.status = 200
    after = _transcribe(client, token=token)

    # Assert: la voz sigue disponible para todos.
    assert [response.status_code for response in rejected] == [422] * 4
    assert {response.json()["reason"] for response in rejected} == {"rejected"}
    assert after.status_code == 200


def test_los_motivos_internos_salen_como_unavailable() -> None:
    # Arrange: la clave rechazada (401) no se cuenta al navegador.
    fake = OpenAIFake(status=401)
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    auth = _transcribe(client, token=token)
    blocked = _transcribe(client, token=token)

    # Assert
    assert auth.status_code == 503 and auth.json()["reason"] == "unavailable"
    assert blocked.status_code == 503 and blocked.json()["reason"] == "circuit_open"
    assert "auth" not in auth.text


# --- /api/voz/hablar -----------------------------------------------------------------------


def test_hablar_sintetiza_la_respuesta_guardada_del_propio_hilo() -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(
        _message(
            "m-1",
            "assistant",
            "Carvajal nació en **1904** [1]. Más en https://www.carvajal.com/historia",
            sources=[{"title": "Historia", "url": "https://www.carvajal.com/historia"}],
        )
    )
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    response = _speak(client, "m-1", token)

    # Assert
    assert response.status_code == 200
    assert response.content == b"ID3-mp3"
    assert response.headers["content-type"] == "audio/mpeg"
    # El middleware de seguridad fija «no-store» en toda respuesta dinámica.
    assert "no-store" in response.headers["cache-control"]
    [spoken] = fake.tts_inputs()
    assert spoken.startswith("Carvajal nació en 1904.")
    assert "http" not in spoken and "[1]" not in spoken and "**" not in spoken
    assert spoken.endswith("Según el sitio de Carvajal.")


def test_hablar_lee_la_cotizacion_corta_del_resultado_comercial() -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(
        _message(
            "m-2",
            "assistant",
            "Aquí tienes la cotización.",
            sources=[{"result_type": "commercial", "result": {"quotes": [QUOTE]}}],
        )
    )
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    response = _speak(client, "m-2", token)

    # Assert
    assert response.status_code == 200
    [spoken] = fake.tts_inputs()
    assert "Son 2 cajas de 1.000 unidades de Vaso de papel 12 onzas en Cali" in spoken
    assert "por un total de 370.000 pesos con IVA incluido" in spoken


@pytest.mark.parametrize(
    "item",
    [
        _message("m-ajeno", "assistant", "Respuesta de otra persona.", owner="dueno-b"),
        _message("m-usuario", "user", "Texto que escribió la persona."),
    ],
)
def test_hablar_no_sintetiza_mensajes_ajenos_ni_del_usuario(
    item: tuple[str, ChatMessage],
) -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(item)
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    response = _speak(client, item[1].id, token)

    # Assert
    assert response.status_code == 404
    assert response.json()["reason"] == "not_found"
    assert fake.requests == []
    assert chat.lookups == [(OWNER, item[1].id)]


@pytest.mark.parametrize("message_id", ["", "../x", "a" * 65, "hola mundo"])
def test_hablar_rechaza_ids_invalidos(message_id: str) -> None:
    # Arrange
    fake = OpenAIFake()
    client, chat = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = _speak(client, message_id, token)

    # Assert
    assert response.status_code == 422
    assert chat.lookups == []


def test_hablar_no_acepta_texto_libre() -> None:
    # Arrange: un cliente que intenta usar el endpoint como TTS público.
    fake = OpenAIFake()
    client, _ = _app(_runtime(fake))
    token = _session(client)

    # Act
    response = client.post(
        "/api/voz/hablar", data={"text": "Lee esto gratis", "input": "x", CSRF_FIELD: token}
    )

    # Assert
    assert response.status_code == 422
    assert fake.requests == []


def test_hablar_sin_csrf_o_sin_sesion_se_rechaza() -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(_message("m-1", "assistant", "Hola."))
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    without_csrf = _speak(client, "m-1", "token-falso")
    client.cookies.delete(COOKIE_NAME)
    without_session = _speak(client, "m-1", token)

    # Assert
    assert without_csrf.status_code == 403
    assert without_session.status_code == 403
    assert without_session.json()["reason"] == "no_session"
    assert fake.requests == []


def test_hablar_dos_veces_usa_la_cache() -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(_message("m-1", "assistant", "Hola, soy Carva."))
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    first = _speak(client, "m-1", token)
    second = _speak(client, "m-1", token)

    # Assert
    assert first.content == second.content == b"ID3-mp3"
    assert len(fake.requests) == 1


def test_hablar_recorta_respuestas_largas_en_un_fin_de_frase() -> None:
    # Arrange
    fake = OpenAIFake()
    chat = FakeChat()
    chat.add(_message("m-1", "assistant", "Esta es una frase de prueba. " * 40))
    client, _ = _app(_runtime(fake, tts_max_chars=200), chat)
    token = _session(client)

    # Act
    response = _speak(client, "m-1", token)

    # Assert
    assert response.status_code == 200
    [spoken] = fake.tts_inputs()
    assert len(spoken) <= 200
    assert spoken.endswith(TRUNCATED_NOTE)
    assert spoken.startswith("Esta es una frase de prueba.")


def test_el_recorte_no_parte_una_cifra_por_su_punto_de_miles() -> None:
    # Arrange: el presupuesto cae justo detrás del primer punto de «1.234.500».
    text = "Hola. " + "x" * 50 + " el total es 1.234.500 pesos con IVA incluido. " * 3
    limit = 114
    assert text[: limit - len(TRUNCATED_NOTE) - 1].endswith("1.")

    # Act
    spoken = cap_text(text, limit)

    # Assert
    assert spoken == f"Hola. {TRUNCATED_NOTE}"
    assert "1." not in spoken.replace(TRUNCATED_NOTE, "")


def test_el_recorte_sin_fin_de_frase_corta_en_un_espacio() -> None:
    # Arrange
    text = "el total es 1.234.500 pesos " * 20

    # Act
    spoken = cap_text(text, 120)

    # Assert: nunca una cifra a medias.
    head = spoken.removesuffix(f" {TRUNCATED_NOTE}")
    assert len(spoken) <= 120
    assert head.split()[-1] in {"pesos", "1.234.500", "es", "total", "el"}


def test_hablar_con_la_base_caida_o_openai_caido_es_503() -> None:
    # Arrange
    fake = OpenAIFake(status=503)
    chat = FakeChat()
    chat.add(_message("m-1", "assistant", "Hola."))
    client, _ = _app(_runtime(fake), chat)
    token = _session(client)

    # Act
    provider_down = _speak(client, "m-1", token)
    chat.error = SQLAlchemyError("caída")
    db_down = _speak(client, "m-1", token)

    # Assert
    assert provider_down.status_code == 503 and provider_down.json()["reason"] == "unavailable"
    assert db_down.status_code == 503 and db_down.json()["reason"] == "unavailable"


# --- /health, página del chat y CSP --------------------------------------------------------


def test_health_publica_la_voz_sin_secretos(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    default, _ = _app()
    monkeypatch.setenv("VOICE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    configured, _ = _app()

    # Act
    off = default.get("/health")
    on = configured.get("/health")

    # Assert
    assert off.json()["voice"]["provider"] == "browser"
    assert off.json()["voice"]["configured"] is False
    assert off.json()["voice"]["available"] is False
    assert on.json()["voice"]["provider"] == "openai"
    assert on.json()["voice"]["configured"] is True
    assert on.json()["voice"]["available"] is True
    assert KEY not in on.text


def test_health_refleja_el_tope_del_cliente_ya_construido() -> None:
    # Arrange
    client, _ = _app(_runtime(OpenAIFake(), max_calls=0))

    # Act
    voice = client.get("/health").json()["voice"]

    # Assert: la configuración del entorno sigue siendo browser; el runtime manda.
    assert voice["available"] is False


def test_la_pagina_ofrece_la_voz_del_servidor_solo_si_esta_disponible() -> None:
    # Arrange
    browser, _ = _app()
    server, _ = _app(_runtime(OpenAIFake()))
    capped, _ = _app(_runtime(OpenAIFake(), max_calls=0))

    # Act
    pages = [client.get("/chat").text for client in (browser, server, capped)]

    # Assert
    assert 'data-voz-servidor="0"' in pages[0]
    assert "este sitio no recibe ni" in pages[0]
    assert 'data-voz-servidor="1"' in pages[1]
    assert "lo transcribe con" in pages[1] and "OpenAI" in pages[1]
    assert 'data-voz-max-segundos="60"' in pages[1]
    assert 'data-voz-servidor="0"' in pages[2]


def test_el_hilo_expone_el_id_de_las_respuestas_del_asistente() -> None:
    # Arrange
    chat = FakeChat()
    chat.add(_message("m-user", "user", "Hola"))
    chat.add(_message("m-bot", "assistant", "Hola, soy Carva."))
    client, _ = _app(_runtime(OpenAIFake()), chat)
    browser, _ = _app(chat=chat)
    for each in (client, browser):
        each.cookies.set(COOKIE_NAME, IDENTITY.issue(OWNER))

    # Act
    page = client.get("/chat").text
    browser_page = browser.get("/chat").text

    # Assert: solo con la voz del servidor; la del navegador no lo necesita.
    assert 'data-voz-mensaje="m-bot"' in page
    assert 'data-voz-mensaje="m-user"' not in page
    assert "data-voz-mensaje" not in browser_page


def test_solo_el_documento_del_chat_admite_audio_blob() -> None:
    # Arrange
    client, _ = _app()
    token = _session(client)

    # Act
    chat_page = client.get("/chat")
    health = client.get("/health")
    spoken = _speak(client, "m-1", token)

    # Assert
    assert chat_page.headers["content-security-policy"] == CSP_CHAT
    assert "media-src 'self' blob:" in CSP_CHAT
    assert "unsafe-inline" not in CSP_CHAT
    for response in (health, spoken):
        assert "blob:" not in response.headers["content-security-policy"]


def test_la_pagina_de_error_del_post_del_chat_tambien_admite_audio_blob() -> None:
    # Arrange: la página de error de POST /chat es el mismo chat.html con «Escuchar».
    client, _ = _app(_runtime(OpenAIFake()))
    token = _session(client)

    # Act
    error_page = client.post("/chat", data={"question": "", CSRF_FIELD: token})
    new_error = client.post("/chat/new", data={CSRF_FIELD: "token-falso"})

    # Assert
    assert 'data-voz-servidor="1"' in error_page.text
    assert error_page.headers["content-security-policy"] == CSP_CHAT
    assert new_error.headers["content-security-policy"] == CSP_CHAT


def test_health_solo_muestra_el_consumo_de_voz_al_operador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("OPERATOR_TOKEN", "token-de-operador-de-prueba")
    client, _ = _app(_runtime(OpenAIFake(status=401)))
    token = _session(client)
    _transcribe(client, token=token)

    # Act
    public = client.get("/health").json()["voice"]
    operator = client.get(
        "/health", headers={"X-Operator-Token": "token-de-operador-de-prueba"}
    ).json()["voice"]

    # Assert
    assert "calls_today" not in public and "last_error" not in public
    assert operator["calls_today"] == 1
    assert operator["last_error"] == "auth"


# --- repositorio real ----------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Iterator[Callable[[], Any]]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield sessionmaker(motor)
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


def test_el_repositorio_solo_devuelve_mensajes_del_dueno(
    session_factory: Callable[[], Any],
) -> None:
    # Arrange
    with session_factory() as session:
        repository = SqlChatRepository(session)
        conversation = repository.create("dueno-a")
        user, assistant = repository.append_turn(
            conversation.id, question="¿Qué es Carvajal?", answer="Una empresa de Cali."
        )
        session.commit()

    # Act
    with session_factory() as session:
        repository = SqlChatRepository(session)
        own = repository.owned_message(assistant.id, "dueno-a")
        foreign = repository.owned_message(assistant.id, "dueno-b")
        question = repository.owned_message(user.id, "dueno-a")
        missing = repository.owned_message("", "dueno-a")

    # Assert
    assert own is not None and own.content == "Una empresa de Cali."
    assert foreign is None
    assert question is not None and question.role == "user"
    assert missing is None


def _real_chat(session_factory: Callable[[], Any]) -> ChatService:
    """``ChatService`` real sobre SQLite; ``assistant_message`` no usa embedder ni LLM."""

    return ChatService(cast(Any, session_factory), embedder=cast(Any, None), llm=cast(Any, None))


def test_el_servicio_real_solo_entrega_respuestas_del_asistente_del_dueno(
    session_factory: Callable[[], Any],
) -> None:
    # Arrange: el filtro de rol vive en ``ChatService.assistant_message``, no en el doble.
    with session_factory() as session:
        repository = SqlChatRepository(session)
        conversation = repository.create("dueno-a")
        user, assistant = repository.append_turn(
            conversation.id, question="¿Qué es Carvajal?", answer="Una empresa de Cali."
        )
        session.commit()
    service = _real_chat(session_factory)

    # Act
    own = service.assistant_message("dueno-a", assistant.id)
    own_question = service.assistant_message("dueno-a", user.id)
    foreign = service.assistant_message("dueno-b", assistant.id)

    # Assert: ni el mensaje del usuario ni el de otro dueño se pueden sintetizar.
    assert own is not None and own.content == "Una empresa de Cali."
    assert own_question is None
    assert foreign is None


def test_hablar_con_el_servicio_real_no_lee_el_mensaje_del_usuario(
    session_factory: Callable[[], Any],
) -> None:
    # Arrange
    with session_factory() as session:
        repository = SqlChatRepository(session)
        conversation = repository.create(OWNER)
        user, assistant = repository.append_turn(
            conversation.id, question="Texto que escribió la persona.", answer="Respuesta."
        )
        session.commit()
    fake = OpenAIFake()
    runtime = _runtime(fake)
    client, _ = _app(runtime)
    cast(Any, client.app).state.chat = _real_chat(session_factory)
    token = _session(client)

    # Act
    question = _speak(client, user.id, token)
    answer = _speak(client, assistant.id, token)

    # Assert
    assert question.status_code == 404
    assert answer.status_code == 200
    assert fake.tts_inputs() == ["Respuesta."]
