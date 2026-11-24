"""Pruebas del adaptador de voz de OpenAI (spec 047) con ``httpx.MockTransport``.

Sin red ni clave real: cada prueba responde desde un transporte falso. Cubren el cuerpo de
las peticiones de transcripción y síntesis, los errores (HTTP, red, plazo, respuesta
inválida), el circuito, el tope diario con su reinicio a medianoche de Bogotá, que la clave
y el contenido nunca salen en ``repr`` ni en logs, ``build_speech``, ``voice_health`` y la
configuración.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from carvajal.adapters.speech import build_speech, voice_health
from carvajal.adapters.speech.openai import (
    AUTH_OPEN_S,
    FAILURE_OPEN_S,
    STT_PROMPT,
    OpenAISpeechClient,
    SpeechUnavailable,
)
from carvajal.infrastructure.settings import Settings

KEY = "sk-prueba-no-real"
AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 600
SECRET_TEXT = "me llamo ana restrepo y quiero 400 vasos"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: Clock | None = None,
    now: Callable[[], datetime] | None = None,
    max_calls_per_day: int = 500,
) -> OpenAISpeechClient:
    return OpenAISpeechClient(
        api_key=KEY,
        base_url="https://api.openai.test/v1",
        tts_instructions="Habla en español de Colombia.",
        timeout_s=2.0,
        max_calls_per_day=max_calls_per_day,
        transport=httpx.MockTransport(handler),
        clock=clock or Clock(),
        now=now or (lambda: datetime(2026, 11, 24, 15, 0, tzinfo=UTC)),
    )


def _ok_transcription(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"text": "  cotiza   400 vasos  "})


# --- transcripción -------------------------------------------------------------------------


def test_transcribe_envia_modelo_idioma_pista_y_audio() -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok_transcription(request)

    client = _client(handler)

    # Act
    text = client.transcribe(AUDIO, "audio/webm;codecs=opus")

    # Assert
    assert text == "cotiza 400 vasos"
    request = seen[0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = request.content
    assert b'name="model"\r\n\r\ngpt-4o-mini-transcribe' in body
    assert b'name="language"\r\n\r\nes' in body
    assert b'name="response_format"\r\n\r\njson' in body
    assert STT_PROMPT.encode() in body
    assert b'filename="voz.webm"' in body
    assert b"Content-Type: audio/webm" in body
    assert AUDIO in body
    assert client.calls_today == 1


def test_transcribe_sin_texto_es_respuesta_invalida() -> None:
    # Arrange
    client = _client(lambda _request: httpx.Response(200, json={"otro": 1}))

    # Act / Assert
    with pytest.raises(SpeechUnavailable) as error:
        client.transcribe(AUDIO, "audio/webm")
    assert error.value.reason == "invalid"
    assert client.last_error == "invalid"


# --- síntesis ------------------------------------------------------------------------------


def test_synthesize_envia_voz_instrucciones_y_formato() -> None:
    # Arrange
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=b"ID3-audio")

    client = _client(handler)

    # Act
    audio = client.synthesize("Hola, soy Carva.")

    # Assert
    assert audio.content == b"ID3-audio"
    assert audio.media_type == "audio/mpeg"
    assert seen == [
        {
            "model": "gpt-4o-mini-tts",
            "input": "Hola, soy Carva.",
            "voice": "coral",
            "response_format": "mp3",
            "instructions": "Habla en español de Colombia.",
        }
    ]


def test_synthesize_vacio_es_invalido() -> None:
    # Arrange
    client = _client(lambda _request: httpx.Response(200, content=b""))

    # Act / Assert
    with pytest.raises(SpeechUnavailable) as error:
        client.synthesize("Hola.")
    assert error.value.reason == "invalid"


# --- errores y circuito --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "reason", "last_error"),
    [
        (httpx.Response(500), "error", "http_5xx"),
        (httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}), "error", "http_429"),
        (httpx.Response(401), "auth", "auth"),
        (httpx.Response(429, json={"error": {"code": "insufficient_quota"}}), "auth", None),
    ],
)
def test_errores_http(response: httpx.Response, reason: str, last_error: str | None) -> None:
    # Arrange
    client = _client(lambda _request: response)

    # Act / Assert
    with pytest.raises(SpeechUnavailable) as error:
        client.synthesize("Hola.")
    assert error.value.reason == reason
    assert client.last_error == (last_error or "insufficient_quota")


@pytest.mark.parametrize("status", [400, 413, 415, 422])
def test_un_4xx_de_la_entrada_es_rejected_y_no_cuenta_como_fallo(status: int) -> None:
    # Arrange: OpenAI rechaza el audio (corrupto, corto o de otro formato).
    clock = Clock()
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status)

    client = _client(handler, clock=clock, max_calls_per_day=10)

    # Act: más rechazos que los que abrirían el circuito.
    reasons = []
    for _ in range(5):
        with pytest.raises(SpeechUnavailable) as error:
            client.transcribe(AUDIO, "audio/webm")
        reasons.append(error.value.reason)

    # Assert: ni circuito, ni ``last_error`` global, ni gasto del tope diario.
    assert reasons == ["rejected"] * 5
    assert len(calls) == 5
    assert client.available()
    assert client.failures == 0
    assert client.last_error is None
    assert client.calls_today == 0


def test_los_rechazos_no_borran_los_fallos_reales_del_proveedor() -> None:
    # Arrange: dos 503 del proveedor, un 400 de la entrada y otro 503.
    responses = [httpx.Response(503), httpx.Response(503), httpx.Response(400), httpx.Response(503)]
    client = _client(lambda _request: responses.pop(0))

    # Act
    for _ in range(4):
        with pytest.raises(SpeechUnavailable):
            client.synthesize("Hola.")

    # Assert: el 400 no reinicia la cuenta; el tercer 503 abre el circuito.
    assert client.unavailable_status() == "circuit_open"


def test_el_tope_diario_es_atomico_con_hilos_concurrentes() -> None:
    # Arrange: 1 llamada libre y 16 hilos que la piden a la vez.
    import threading

    calls: list[int] = []
    barrier = threading.Barrier(16)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=b"a")

    client = _client(handler, max_calls_per_day=1)
    outcomes: list[str] = []

    def call() -> None:
        barrier.wait()
        try:
            client.synthesize("Hola.")
            outcomes.append("ok")
        except SpeechUnavailable as error:
            outcomes.append(error.reason)

    threads = [threading.Thread(target=call) for _ in range(16)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Assert: una sola pasa; las demás ven el tope sin llegar a OpenAI.
    assert outcomes.count("ok") == 1
    assert outcomes.count("cap") == 15
    assert len(calls) == 1
    assert client.calls_today == 1


def test_plazo_vencido_es_timeout() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    client = _client(handler)

    # Act / Assert
    with pytest.raises(SpeechUnavailable) as error:
        client.transcribe(AUDIO, "audio/webm")
    assert error.value.reason == "timeout"


def test_error_de_red() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    client = _client(handler)

    # Act / Assert
    with pytest.raises(SpeechUnavailable) as error:
        client.synthesize("Hola.")
    assert error.value.reason == "error"
    assert client.last_error == "red"


def test_tres_fallos_abren_el_circuito_60_segundos() -> None:
    # Arrange
    clock = Clock()
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    client = _client(handler, clock=clock)

    # Act
    for _ in range(3):
        with pytest.raises(SpeechUnavailable):
            client.synthesize("Hola.")

    # Assert: abierto, sin nuevas peticiones, y se cierra al pasar el plazo.
    assert client.unavailable_status() == "circuit_open"
    with pytest.raises(SpeechUnavailable) as error:
        client.synthesize("Hola.")
    assert error.value.reason == "circuit_open"
    assert len(calls) == 3
    clock.now += FAILURE_OPEN_S + 1
    assert client.available()


def test_clave_rechazada_abre_15_minutos() -> None:
    # Arrange
    clock = Clock()
    client = _client(lambda _request: httpx.Response(401), clock=clock)

    # Act
    with pytest.raises(SpeechUnavailable):
        client.transcribe(AUDIO, "audio/webm")

    # Assert
    assert client.unavailable_status() == "circuit_open"
    clock.now += AUTH_OPEN_S - 1
    assert not client.available()
    clock.now += 2
    assert client.available()


def test_un_ok_reinicia_los_fallos() -> None:
    # Arrange
    responses = [httpx.Response(500), httpx.Response(500), httpx.Response(200, content=b"a")]
    client = _client(lambda _request: responses.pop(0))

    # Act
    for _ in range(2):
        with pytest.raises(SpeechUnavailable):
            client.synthesize("Hola.")
    client.synthesize("Hola.")

    # Assert
    assert client.failures == 0
    assert client.available()


def test_tope_diario_y_reinicio_a_medianoche_de_bogota() -> None:
    # Arrange: 23:30 en Bogotá (04:30 UTC del día siguiente).
    moment = [datetime(2026, 11, 25, 4, 30, tzinfo=UTC)]
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=b"a")

    client = _client(handler, now=lambda: moment[0], max_calls_per_day=2)

    # Act
    client.synthesize("uno")
    client.synthesize("dos")

    # Assert: el tercero no llega a OpenAI.
    with pytest.raises(SpeechUnavailable) as error:
        client.synthesize("tres")
    assert error.value.reason == "cap"
    assert len(calls) == 2
    moment[0] = datetime(2026, 11, 25, 5, 1, tzinfo=UTC)  # 00:01 en Bogotá
    assert client.available()
    assert client.calls_today == 0


def test_sin_clave_no_esta_disponible_ni_hace_peticiones() -> None:
    # Arrange
    calls: list[int] = []
    client = OpenAISpeechClient(
        api_key="  ",
        transport=httpx.MockTransport(lambda _r: calls.append(1) or httpx.Response(200)),
    )

    # Act / Assert
    assert client.unavailable_status() == "off"
    with pytest.raises(SpeechUnavailable) as error:
        client.transcribe(AUDIO, "audio/webm")
    assert error.value.reason == "off"
    assert calls == []


# --- privacidad ----------------------------------------------------------------------------


def test_la_clave_el_texto_y_el_audio_no_salen_en_repr_ni_en_logs() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, json={"text": SECRET_TEXT})
        return httpx.Response(500)

    client = _client(handler)

    # Act
    with capture_logs() as logs:
        client.transcribe(AUDIO, "audio/webm")
        with pytest.raises(SpeechUnavailable):
            client.synthesize(SECRET_TEXT)

    # Assert
    dumped = json.dumps(logs, default=str)
    assert KEY not in repr(client)
    assert KEY not in dumped
    assert "ana restrepo" not in dumped
    assert "400 vasos" not in dumped
    assert [entry["event"] for entry in logs] == ["voice.call", "voice.call"]
    assert logs[0]["kind"] == "stt" and logs[0]["bytes_in"] == len(AUDIO)
    assert logs[1]["kind"] == "tts" and logs[1]["status"] == "error"


# --- construcción, salud y configuración ---------------------------------------------------


def test_build_speech_solo_con_openai_y_clave() -> None:
    # Arrange
    browser = Settings(voice_provider="browser", openai_api_key=KEY)
    without_key = Settings(voice_provider="openai", openai_api_key="")
    non_ascii = Settings(voice_provider="openai", openai_api_key="sk-“mala”")
    enabled = Settings(voice_provider="OpenAI", openai_api_key=KEY)

    # Act / Assert
    assert build_speech(browser) is None
    assert build_speech(without_key) is None
    with capture_logs() as logs:
        assert build_speech(non_ascii) is None
    assert "mala" not in json.dumps(logs)
    client = build_speech(enabled)
    assert isinstance(client, OpenAISpeechClient)
    assert client.stt_model == "gpt-4o-mini-transcribe"
    assert client.tts_model == "gpt-4o-mini-tts"
    assert client.tts_voice == "coral"
    assert client.max_calls_per_day == 500


def test_voice_health_sin_cliente_y_con_cliente() -> None:
    # Arrange
    off = Settings(voice_provider="browser", openai_api_key=KEY)
    on = Settings(voice_provider="openai", openai_api_key=KEY)
    client = _client(lambda _r: httpx.Response(200, content=b"a"), max_calls_per_day=1)
    client.synthesize("Hola.")

    # Act
    without = voice_health(off)
    configured = voice_health(on)
    serving = voice_health(on, client)
    operator = voice_health(on, client, detail=True)

    # Assert: lo público no dice cuánto falta para el tope ni el último error.
    assert without == {
        "provider": "browser",
        "configured": False,
        "available": False,
        "stt_model": None,
        "tts_model": None,
    }
    assert configured["configured"] is True and configured["available"] is True
    assert serving["available"] is False  # tope de 1 ya gastado
    assert "calls_today" not in serving and "last_error" not in serving
    assert operator["calls_today"] == 1 and operator["last_error"] is None
    assert KEY not in json.dumps([without, configured, serving, operator])


def test_settings_de_voz_por_defecto_y_sin_clave_en_repr() -> None:
    # Act
    settings = Settings(openai_api_key=KEY)

    # Assert
    assert settings.voice_provider == "browser"
    assert settings.voice_server_configured is False
    assert settings.voice_stt_model == "gpt-4o-mini-transcribe"
    assert settings.voice_tts_model == "gpt-4o-mini-tts"
    assert settings.voice_tts_voice == "coral"
    assert "Colombia" in settings.voice_tts_instructions
    assert settings.voice_max_audio_seconds == 60
    assert settings.voice_max_upload_bytes == 0
    # Derivado de la duración: 60 s a 64 kbps, el doble de lo que graba el navegador.
    assert settings.voice_upload_limit_bytes == 480_000
    assert settings.voice_max_calls_per_day == 500
    assert (settings.voice_rate_limit, settings.voice_rate_window_s) == (20, 600)
    assert settings.voice_ip_rate_limit == 60
    assert settings.voice_global_rate_per_min == 30
    assert settings.voice_timeout_s == 20.0
    assert KEY not in repr(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("voice_provider", "azure"),
        ("voice_max_upload_bytes", 30_000_000),
        ("voice_max_upload_bytes", 5_000),
        ("voice_ip_rate_limit", 0),
        ("voice_global_rate_per_min", 0),
        ("voice_max_audio_seconds", 1),
        ("voice_rate_limit", 0),
        ("voice_timeout_s", 0),
    ],
)
def test_settings_de_voz_rechazan_valores_fuera_de_rango(field: str, value: Any) -> None:
    # Act / Assert
    with pytest.raises(ValueError):
        Settings(**{field: value})


def test_el_tope_de_bytes_explicito_manda_sobre_el_derivado() -> None:
    # Act
    explicit = Settings(voice_max_upload_bytes=300_000)
    longer = Settings(voice_max_audio_seconds=120)

    # Assert
    assert explicit.voice_upload_limit_bytes == 300_000
    assert longer.voice_upload_limit_bytes == 960_000


def test_instrucciones_vacias_usan_el_estilo_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: compose pasa ``${VOICE_TTS_INSTRUCTIONS:-}`` vacía.
    monkeypatch.setenv("VOICE_TTS_INSTRUCTIONS", "")

    # Act
    settings = Settings()

    # Assert
    assert "asesora comercial" in settings.voice_tts_instructions


def test_settings_leen_la_voz_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("VOICE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("VOICE_TTS_VOICE", "nova")

    # Act
    settings = Settings()

    # Assert
    assert settings.voice_server_configured is True
    assert settings.voice_tts_voice == "nova"
